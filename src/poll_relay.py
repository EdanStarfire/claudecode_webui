"""Poll-relay: background long-poll tasks that fan Backend's poll streams out to
local per-session/UI EventQueues (issue #498).

Backend owns the real EventQueues (SessionCoordinator pushes events there
directly, same-process). Frontend's own /api/poll/ui and /api/poll/session/{id}
handlers (src/routers/poll.py) still read from LOCAL EventQueues exactly as
before the split — what changed is who *populates* them: instead of direct
in-process coordinator callbacks, a background task per stream continuously
long-polls the matching Backend endpoint and re-appends what it gets back.
This is the "poll-relay" pattern (as opposed to the generic reverse-proxy):
needed here specifically because multiple browser tabs share one local
EventQueue, which would otherwise mean multiple duplicate upstream long-poll
connections to Backend for the same stream.

Per-session relay tasks self-stop after an idle window with no local browser
poll (issue #498 review finding): Backend's poll_session handler calls
session_manager.mark_viewed() on every request it receives, since pre-split
that request only ever happened because a real browser was actively polling
(issue #1598's whole premise). Post-split, a relay task that ran forever once
started would keep calling Backend's poll endpoint indefinitely even after
every browser tab closed, permanently marking the session "viewed" and
defeating #1598's unread-detection fix. ensure_session_relay() is called on
every local poll request and refreshes the last-activity timestamp; the loop
checks it and stops itself once idle, restarting lazily next time a real
browser poll arrives.

Epic #1990 stage 5a: every local queue — UI and every session — now uses the
SAME plain auto-increment append discipline uniformly (issue #1890's
mixed-discipline collision can no longer happen by construction). Session
queues previously adopted Backend's own cursor numbering 1:1 (issue #1886);
that coupling is exactly what broke across a Backend restart (cursor space
restarts at 0; the same value can mean two different generations — issue
#1889). `_backend_cursor` tracks, per stream, the LAST Backend-space cursor
this relay has consumed — distinct from (and no longer equal to) the local
queue's own auto-increment cursor — so a session relay task restarting after
an idle-stop still resumes Backend polling from exactly where it left off
(not from Backend cursor 0, and not from the now-unrelated local queue
cursor). `_last_epoch` tracks the last Backend `epoch` seen per stream; an
observed change emits an explicit `stream_reset` event into the local queue
BEFORE appending that poll's own (already-new-epoch) events, making a
Backend restart a visible, ordered event instead of a cursor-magnitude
inference.

**The Frontend-issued history cursor (AC3).** Since session queues no longer
mirror Backend's own cursor numbering, `GET /api/sessions/{id}/messages`
(`src/routers/sessions.py`) can no longer just hand the browser Backend's own
`event_cursor` — the browser would be resuming its NEXT poll against the
wrong (Backend-space) numbering entirely. `wait_until_relayed()` resolves a
Backend-space `(epoch, backend_cursor)` pair to the exact corresponding LOCAL
cursor by recording one checkpoint per processed batch per stream —
`(epoch, backend_cursor_before, backend_cursor_after, local_cursor_before,
local_cursor_after)` — and interpolating: the checkpoint whose range straddles
the requested `backend_cursor` gives `local_cursor_before + (backend_cursor -
backend_cursor_before)`. This is exact for the SPECIFIC `backend_cursor` a
caller asked for, unlike a single mutable "latest processed" pointer, which
would be correct only if the relay happened to stop advancing the instant it
reached that value — in reality the relay may have already moved past it by
the time a waiter wakes up, and returning its current position would silently
skip whatever lies between the two. Checkpoints are pruned after each append:
kept only if within `_CHECKPOINT_GRACE_SECONDS` of a single relay-to-Frontend
HTTP round trip, or still needed to straddle the smallest Backend-cursor among
currently-registered waiters for that stream, or the single latest checkpoint
(so a resolution is always possible even when fully idle).
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from shared.event_emitter import emit
from shared.event_envelope import FRONTEND_LOCAL_BACKEND_ID, QUEUE_SESSION, QUEUE_UI
from shared.event_queue import EventQueue

logger = logging.getLogger(__name__)

# Backoff on relay-loop errors (Backend unreachable, restarting, etc.) — never
# busy-loop against a down Backend.
_ERROR_BACKOFF_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 30
# Client-side HTTP timeout must have margin above Backend's own long-poll wait
# ceiling (_POLL_TIMEOUT_SECONDS) — equal values mean a response arriving right
# at the server's deadline races the client's own timeout with zero slack for
# network latency or scheduling jitter (issue #498 review finding).
_POLL_CLIENT_TIMEOUT_SECONDS = _POLL_TIMEOUT_SECONDS + 10
# A real browser tab re-polls almost immediately after each long-poll response
# (worst case ~_POLL_TIMEOUT_SECONDS between requests when idle). 3x that
# ceiling tolerates jitter/a slow client without mistaking a live tab for a
# closed one.
_SESSION_IDLE_TIMEOUT_SECONDS = _POLL_TIMEOUT_SECONDS * 3
# Checkpoints only need to survive long enough to answer a wait_until_relayed()
# call triggered by a Backend response the Frontend just received — one HTTP
# round trip, not the full catch-up timeout below. Generous versus that round
# trip, matching the scale of this module's other small timing constants.
_CHECKPOINT_GRACE_SECONDS = 30.0
# How long wait_until_relayed() gives the relay to catch up to an epoch it
# hasn't observed yet before concluding the requested epoch is genuinely stale
# (see that method's docstring) — bounded by its own error-backoff interval
# scale, generous enough for one failed poll + one successful retry.
_STALE_EPOCH_GRACE_SECONDS = 5.0
# How long GET /messages (src/routers/sessions.py) will wait for the relay to
# catch up to the exact Backend-space cursor it was just handed, before falling
# back to a best-effort local cursor. Generous versus the common case (the
# relay's own in-flight long-poll to Backend resolves near-instantly once
# Backend's current cursor already exceeds what the relay last asked for —
# see module docstring) without blocking a user-facing request anywhere near
# the relay's own 30s upstream long-poll ceiling.
_HISTORY_CURSOR_WAIT_TIMEOUT_SECONDS = 10.0


@dataclass
class _Checkpoint:
    epoch: str
    backend_cursor_before: int
    backend_cursor_after: int
    local_cursor_before: int
    local_cursor_after: int
    recorded_at: float  # time.monotonic()


@dataclass
class WaitUntilRelayedResult:
    outcome: str  # "resolved" | "stale_epoch" | "timeout"
    local_cursor: int | None  # None only for "stale_epoch"


class PollRelay:
    """Owns the background relay tasks fanning Backend's poll streams into local queues."""

    def __init__(self, backend_client, ui_queue: EventQueue, session_queues: dict[str, EventQueue]):
        self._backend_client = backend_client
        self._ui_queue = ui_queue
        self._session_queues = session_queues
        self._ui_task: asyncio.Task | None = None
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._session_last_activity: dict[str, float] = {}
        self._stopped = False
        # Epic #1990 stage 5a: last Backend epoch observed per stream key ("ui" or a
        # session_id) — the value the Frontend's own poll responses surface, and what an
        # observed change against triggers a `stream_reset` emission for.
        self._last_epoch: dict[str, str] = {}
        # Last Backend-space cursor consumed per stream — survives a session relay
        # task's idle-stop/restart (unlike a local variable scoped to _relay_loop), so
        # resuming polls Backend from exactly where this relay left off rather than from
        # 0 or from the now-independent local queue cursor. See module docstring.
        self._backend_cursor: dict[str, int] = {}
        # Sticky, consume-once flag: Backend reporting its OWN `evicted=True` (this
        # relay's remembered Backend-cursor fell outside Backend's retained window —
        # e.g. a burst too large for Backend's own buffer) means events were lost
        # between this relay and Backend, independent of any epoch/restart signal.
        # The local queue itself never evicts on this (nothing about ITS OWN retained
        # window changed), so there's no other way for a local poller to learn of it.
        self._pending_evicted: dict[str, bool] = {}
        # Per-stream checkpoint history (oldest-first) for wait_until_relayed()'s
        # Backend-cursor -> local-cursor interpolation, and the asyncio.Event-based
        # wake list letting a waiter resume promptly rather than polling in a loop.
        self._checkpoints: dict[str, list[_Checkpoint]] = {}
        self._pending_waiter_cursors: dict[str, list[int]] = {}
        self._stream_activity: dict[str, list[asyncio.Event]] = {}
        # (epoch, backend_cursor, local_cursor) as of the most recent poll, updated on
        # EVERY poll — including empty ones, unlike _checkpoints (which skip empty
        # batches). Resolves a requested backend_cursor that falls in a "quiet gap"
        # with no checkpoint covering it (nothing happened, so nothing to interpolate)
        # — most commonly backend_cursor=0 for a brand-new session, or any request
        # for "wherever the relay currently is" when it's already caught up.
        self._frontier: dict[str, tuple[str, int, int]] = {}

    def consume_pending_evicted(self, stream_key: str) -> bool:
        """Read-and-clear: surfaced to exactly one subsequent local poll response."""
        return self._pending_evicted.pop(stream_key, False)

    def last_epoch(self, stream_key: str) -> str | None:
        """Most recent Backend epoch observed for this stream, or None if no poll has
        completed yet (e.g. a session whose relay hasn't started)."""
        return self._last_epoch.get(stream_key)

    def start_ui_relay(self) -> None:
        """Start the single background task relaying Backend's /api/poll/ui stream."""
        if self._ui_task is not None and not self._ui_task.done():
            return
        self._ui_task = asyncio.create_task(
            self._relay_loop("/api/poll/ui", self._ui_queue, stream_key="ui"), name="poll_relay_ui"
        )

    def ensure_session_relay(self, session_id: str) -> None:
        """Start a background relay task for this session if one isn't already running,
        and record that a local browser poll just happened (keeps the relay alive —
        see module docstring for why this matters for issue #1598's mark_viewed semantics).
        """
        self._session_last_activity[session_id] = time.monotonic()
        existing = self._session_tasks.get(session_id)
        if existing is not None and not existing.done():
            return
        if session_id not in self._session_queues:
            self._session_queues[session_id] = EventQueue()
        path = f"/api/poll/session/{session_id}"
        self._session_tasks[session_id] = asyncio.create_task(
            self._relay_loop(path, self._session_queues[session_id], session_id=session_id, stream_key=session_id),
            name=f"poll_relay_session_{session_id}",
        )

    async def _relay_loop(
        self, path: str, queue: EventQueue, session_id: str | None = None, stream_key: str = "ui"
    ) -> None:
        cursor = self._backend_cursor.get(stream_key, 0)
        family = QUEUE_SESSION if session_id is not None else QUEUE_UI
        while not self._stopped:
            if session_id is not None:
                last_activity = self._session_last_activity.get(session_id, 0.0)
                if time.monotonic() - last_activity > _SESSION_IDLE_TIMEOUT_SECONDS:
                    logger.info("Poll-relay for session %s idle, stopping", session_id)
                    self._session_tasks.pop(session_id, None)
                    self._session_last_activity.pop(session_id, None)
                    return
            try:
                events, next_cursor, epoch, backend_evicted = await self._poll_once(path, cursor)
                if backend_evicted:
                    self._pending_evicted[stream_key] = True

                previous_epoch = self._last_epoch.get(stream_key)
                if previous_epoch is not None and epoch != previous_epoch:
                    # Observed Backend restart: make it an explicit, ordered event in
                    # the local queue BEFORE this poll's own (already-new-epoch)
                    # events, rather than inferring it from cursor magnitude.
                    emit(
                        queue, family, "stream_reset",
                        {"old_epoch": previous_epoch, "new_epoch": epoch},
                        scope=session_id,
                        backend_id=FRONTEND_LOCAL_BACKEND_ID,
                    )
                self._last_epoch[stream_key] = epoch

                # Every local queue — UI and every session — uses the same plain
                # auto-increment discipline now (issue #1890's mixed-discipline
                # collision can no longer happen by construction).
                local_cursor_before = queue.current_cursor
                for event in events:
                    queue.append(event)
                cursor = next_cursor
                self._backend_cursor[stream_key] = next_cursor
                self._frontier[stream_key] = (epoch, next_cursor, queue.current_cursor)

                if events:
                    backend_cursor_before = next_cursor - len(events)
                    self._record_checkpoint(
                        stream_key,
                        _Checkpoint(
                            epoch=epoch,
                            backend_cursor_before=backend_cursor_before,
                            backend_cursor_after=next_cursor,
                            local_cursor_before=local_cursor_before,
                            local_cursor_after=queue.current_cursor,
                            recorded_at=time.monotonic(),
                        ),
                    )
                self._notify_stream(stream_key)
            except asyncio.CancelledError:
                raise
            except httpx.RequestError:
                # No local log here — BackendClient.get_json() already recorded this
                # failure via its shared reachability tracker (issue #1844); logging
                # it again here on every 2s retry would just re-flood error.log.
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            except httpx.HTTPStatusError:
                logger.exception("Poll-relay for %s: Backend returned an error response", path)
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            except Exception:
                logger.exception("Poll-relay for %s: unexpected error, retrying", path)
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)

    async def _poll_once(self, path: str, cursor: int) -> tuple[list[dict], int, str, bool]:
        body = await self._backend_client.get_json(
            path,
            params={"since": cursor, "timeout": _POLL_TIMEOUT_SECONDS},
            timeout=_POLL_CLIENT_TIMEOUT_SECONDS,
        )
        return (
            body.get("events", []), body.get("next_cursor", cursor),
            body.get("epoch", ""), body.get("evicted", False),
        )

    def _record_checkpoint(self, stream_key: str, checkpoint: "_Checkpoint") -> None:
        checkpoints = self._checkpoints.setdefault(stream_key, [])
        checkpoints.append(checkpoint)
        self._prune_checkpoints(stream_key)

    def _prune_checkpoints(self, stream_key: str) -> None:
        checkpoints = self._checkpoints.get(stream_key)
        if not checkpoints:
            return
        now = time.monotonic()
        # Every pending waiter's own requested cursor must stay covered, not just
        # the smallest one — multiple concurrent GET /messages calls for the same
        # session can register waiters at very different backend_cursor values
        # (wait_until_relayed()'s `pending` list already supports this), and a
        # checkpoint aging past the grace window while still being the only one
        # covering a non-minimum waiter must survive pruning too.
        pending = self._pending_waiter_cursors.get(stream_key) or []
        last_index = len(checkpoints) - 1
        kept = [
            cp for i, cp in enumerate(checkpoints)
            if i == last_index
            or (now - cp.recorded_at) <= _CHECKPOINT_GRACE_SECONDS
            or any(cp.backend_cursor_before <= p <= cp.backend_cursor_after for p in pending)
        ]
        self._checkpoints[stream_key] = kept

    def _resolve_checkpoint(self, stream_key: str, epoch: str, backend_cursor: int) -> int | None:
        for cp in self._checkpoints.get(stream_key, []):
            if cp.epoch == epoch and cp.backend_cursor_before <= backend_cursor <= cp.backend_cursor_after:
                return cp.local_cursor_before + (backend_cursor - cp.backend_cursor_before)
        # No checkpoint covers it — either nothing has happened yet for this epoch
        # (backend_cursor=0, a brand-new/never-polled session) or the relay reached
        # (or passed) backend_cursor via empty polls with no events in between, so
        # there was nothing to interpolate: the local cursor was constant across
        # that quiet span, equal to wherever the relay currently sits.
        frontier = self._frontier.get(stream_key)
        if frontier is not None:
            frontier_epoch, frontier_backend_cursor, frontier_local_cursor = frontier
            if frontier_epoch == epoch and backend_cursor <= frontier_backend_cursor:
                return frontier_local_cursor
        elif backend_cursor <= 0:
            return 0  # relay hasn't polled yet, but there's nothing to catch up to anyway
        return None

    def _notify_stream(self, stream_key: str) -> None:
        waiters = self._stream_activity.get(stream_key)
        if not waiters:
            return
        for waiter in waiters:
            waiter.set()
        self._stream_activity[stream_key] = []

    def current_local_cursor(self, stream_key: str) -> int:
        queue = self._ui_queue if stream_key == "ui" else self._session_queues.get(stream_key)
        return queue.current_cursor if queue is not None else 0

    async def wait_until_relayed(
        self, session_id: str, backend_cursor: int, epoch: str, timeout: float = _HISTORY_CURSOR_WAIT_TIMEOUT_SECONDS
    ) -> WaitUntilRelayedResult:
        """Resolve a Backend-space `(epoch, backend_cursor)` pair — as handed out by
        Backend's own `GET /messages` at the moment it answered — to the exact
        corresponding LOCAL cursor this relay's queue would need the browser to resume
        polling from. See module docstring for why this must be an exact
        per-request resolution rather than a snapshot of "whatever's newest right now".

        Three outcomes:
        - "resolved": a checkpoint for this exact `epoch` already straddles
          `backend_cursor` (immediately, or as soon as the relay catches up).
        - "stale_epoch": the stream's last-seen epoch has moved past the requested
          one — `backend_cursor` belongs to a generation this relay will never see
          again (Backend restarted again in between). Returned once confirmed
          (after the short grace window below), not at full `timeout` — the caller
          is expected to retry once against a fresh Backend read rather than wait
          out a timeout that can never resolve.
        - "timeout": the relay never caught up to `backend_cursor` within `timeout`
          (e.g. Backend genuinely slow/unreachable) — `local_cursor` is a best-effort
          current snapshot, not an exact resolution. Deliberately not lossy on its
          own: the independently-emitted `stream_reset` for any epoch transition
          still arrives through the normal relay path on the browser's next poll.

        Epochs are opaque random tokens with no inherent ordering, so a mismatch
        against this stream's last-seen epoch at the very start of the wait is
        ambiguous: Backend may have already moved past `epoch` (genuinely stale),
        or this relay simply hasn't observed `epoch` yet — e.g. still inside its
        own `_ERROR_BACKOFF_SECONDS` after the connection drop the restart itself
        caused, which a fresh direct-to-Backend read (as `src/routers/sessions.py`
        makes right before calling this) can easily beat. `_STALE_EPOCH_GRACE_SECONDS`
        gives the relay one short, bounded chance to catch up before concluding
        genuine staleness, rather than aborting on the very first observed mismatch.
        """
        stream_key = session_id
        pending = self._pending_waiter_cursors.setdefault(stream_key, [])
        pending.append(backend_cursor)
        try:
            loop = asyncio.get_event_loop()

            last_epoch = self._last_epoch.get(stream_key)
            if last_epoch is not None and last_epoch != epoch:
                grace_deadline = loop.time() + _STALE_EPOCH_GRACE_SECONDS
                while (
                    loop.time() < grace_deadline
                    and self._last_epoch.get(stream_key) == last_epoch
                ):
                    await self._wait_for_stream_activity(stream_key, grace_deadline - loop.time())
                if self._last_epoch.get(stream_key) != epoch:
                    return WaitUntilRelayedResult(outcome="stale_epoch", local_cursor=None)

            deadline = loop.time() + timeout
            while True:
                local_cursor = self._resolve_checkpoint(stream_key, epoch, backend_cursor)
                if local_cursor is not None:
                    return WaitUntilRelayedResult(outcome="resolved", local_cursor=local_cursor)

                current_epoch = self._last_epoch.get(stream_key)
                if current_epoch is not None and current_epoch != epoch:
                    return WaitUntilRelayedResult(outcome="stale_epoch", local_cursor=None)

                remaining = deadline - loop.time()
                if remaining <= 0:
                    return WaitUntilRelayedResult(
                        outcome="timeout", local_cursor=self.current_local_cursor(stream_key)
                    )

                await self._wait_for_stream_activity(stream_key, remaining)
        finally:
            pending.remove(backend_cursor)
            self._prune_checkpoints(stream_key)

    async def _wait_for_stream_activity(self, stream_key: str, timeout: float) -> None:
        """Block until `_notify_stream(stream_key)` fires (a poll cycle completed —
        whether or not it produced a checkpoint) or `timeout` elapses, whichever
        comes first."""
        waiter = asyncio.Event()
        self._stream_activity.setdefault(stream_key, []).append(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
        except TimeoutError:
            pass
        finally:
            activity = self._stream_activity.get(stream_key)
            if activity is not None and waiter in activity:
                activity.remove(waiter)

    async def stop(self) -> None:
        """Cancel all relay tasks (called during app shutdown)."""
        self._stopped = True
        tasks = [t for t in [self._ui_task, *self._session_tasks.values()] if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
