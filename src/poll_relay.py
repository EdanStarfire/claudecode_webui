"""Poll-relay: one consolidated background long-poll task fanning Backend's
multiplexed poll stream out to local per-session/UI EventQueues (issue #498,
epic #1990 stage 5c).

Backend owns the real EventQueues (SessionCoordinator pushes events there
directly, same-process). Frontend's own `/api/poll` handler (`src/routers/poll.py`
— the legacy `/api/poll/ui`/`/api/poll/session/{id}` routes this module used to
relay through were removed in stage 5c phase 5) reads from LOCAL EventQueues
exactly as before the #498 split — what changed is who *populates* them: one
background task continuously long-polls Backend's own
`POST /api/poll` (`backend/routers/poll.py`, which owns the real EventQueues
SessionCoordinator writes to) with a subscription set — UI plus every session a
local browser request has touched recently — and re-appends what it gets back.

**Consolidation (epic #1990 stage 5c).** Before this stage, one relay task ran
per stream (one for UI, one per actively-viewed session), each independently
long-polling its own single-stream endpoint and self-stopping after an idle
window. Now there is exactly ONE task, always running once started, carrying a
subscription set recomputed every cycle from `_session_last_activity` (UI is
always subscribed). A session dropping out of the subscription set is no longer
"a task stopping" — it's simply omitted from the next request body, and this
module's own per-stream bookkeeping dicts (`_checkpoints`, `_frontier`,
`_pending_evicted`, `_session_last_activity`) are explicitly pruned for that
session_id (issue #2305: `_backend_cursor` and `_last_epoch` are deliberately
kept, so re-entry resumes instead of replaying Backend's whole queue). (US3:
today this state lived inside a dying task's own closure and was GC'd for free; the single consolidated task has no such
closure teardown, so pruning is now this module's own explicit responsibility).
`touch_session(session_id)` (replacing `ensure_session_relay()` — renamed, not
kept as a compat alias) registers/refreshes interest and lazily creates the
local `EventQueue`; it no longer starts a dedicated task.

Epic #1990 stage 5a: every local queue — UI and every session — uses the SAME
plain auto-increment append discipline uniformly (issue #1890's mixed-discipline
collision can no longer happen by construction). Session queues previously
adopted Backend's own cursor numbering 1:1 (issue #1886); that coupling is
exactly what broke across a Backend restart (cursor space restarts at 0; the
same value can mean two different generations — issue #1889). `_backend_cursor`
tracks, per stream, the LAST Backend-space cursor this relay has consumed —
distinct from (and no longer equal to) the local queue's own auto-increment
cursor — so resuming a subscription polls Backend from exactly where this relay
left off (not from Backend cursor 0, and not from the now-unrelated local queue
cursor). `_last_epoch` tracks the last Backend `epoch` seen per stream; an
observed change emits an explicit `stream_reset` event into the local queue
BEFORE appending that poll's own (already-new-epoch) events, making a Backend
restart a visible, ordered event instead of a cursor-magnitude inference.

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
(so a resolution is always possible even when fully idle). This machinery is
unchanged by the stage 5c consolidation — only the outer transport loop changed.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass

import httpx

from shared.event_emitter import emit
from shared.event_envelope import FRONTEND_LOCAL_BACKEND_ID, QUEUE_SESSION, QUEUE_UI
from shared.event_queue import EventQueue

logger = logging.getLogger(__name__)

# Issue #2160 AC1: minted once per Frontend process, mirroring shared/event_envelope.py's
# PROCESS_EPOCH for Backend. A Frontend-tier restart never changes Backend's own
# PROCESS_EPOCH (Backend didn't restart), so that value alone can't signal "the
# Frontend process the browser was talking to is gone" — this one can, and is
# deliberately never persisted (same "every restart is a new epoch by design"
# rationale as Backend's).
FRONTEND_PROCESS_EPOCH: str = secrets.token_hex(8)

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

_STREAM_KEY_UI = "ui"


def _family_and_scope(stream_key: str) -> tuple[str, str | None]:
    """The UI stream has no scope (it's not session-specific); every other stream_key
    is a session_id, scoped to itself. Shared by `_process_stream_response()` and
    `_maybe_seed_stream_reset()` — both need to know which queue family/scope an
    `emit()` call targets for a given stream_key."""
    if stream_key == _STREAM_KEY_UI:
        return QUEUE_UI, None
    return QUEUE_SESSION, stream_key


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
    """Owns the single consolidated background task fanning Backend's
    multiplexed poll stream into local queues."""

    def __init__(self, backend_client, ui_queue: EventQueue, session_queues: dict[str, EventQueue]):
        self._backend_client = backend_client
        self._ui_queue = ui_queue
        self._session_queues = session_queues
        self._task: asyncio.Task | None = None
        # Gates "ui" inclusion in the subscription set — mirrors pre-consolidation
        # behavior exactly (a dedicated ui task only ever started on demand) rather
        # than unconditionally racing an idle ui_queue's wait_for_events() on every
        # single cycle regardless of whether anything has ever asked for it. Besides
        # matching intent, this avoids silently cutting into a session stream's own
        # effective poll cadence when nothing is actually subscribed to ui — a real
        # throughput cost, not just a cosmetic one (a session polling against a
        # small, permanently-shrunk retained window can't tolerate extra per-cycle
        # overhead from racing a stream nobody asked for).
        self._ui_started = False
        # Issue #2160 AC1 (post-review fix): deliberately SEPARATE from `_ui_started` above.
        # `_ui_started` is consumed by `warm_ui_relay()`'s unconditional boot-time call
        # (`src/web_server.py`), before any real browser has ever connected — using it as
        # the seed-eligibility gate too meant that boot-time call (always `since=0`, nothing
        # to seed) permanently consumed the ONLY chance to seed a `stream_reset` for a real
        # resuming browser's later `start_ui_relay(since=...)` call, silently making AC1's
        # seeding dead code for the UI stream specifically (session streams never had this
        # bug — touch_session() has no equivalent boot-time pre-warm). This flag tracks
        # only "has start_ui_relay()'s own one-shot seed check already run", independent of
        # the task/subscription lifecycle `_ui_started` tracks.
        self._ui_seed_checked = False
        self._session_last_activity: dict[str, float] = {}
        # What the CURRENTLY in-flight (or most recently built) request's
        # subscription set is — distinct from `_current_subscription()`'s live
        # recomputation. A newly touched stream not yet covered by this needs to
        # interrupt an in-flight long-poll rather than wait for it to time out
        # (see `_subscription_changed`).
        self._active_subscription: set[str] = set()
        # The in-flight _poll_once() Task, if any — touch_session()/start_ui_relay()
        # cancel it directly when they add a stream not already covered by
        # `_active_subscription`, rather than waiting out its full
        # `_POLL_TIMEOUT_SECONDS` before the relay even asks Backend about the new
        # stream. This was a real, observed regression (epic #1990 stage 5c
        # review): the Frontend starts its UI relay unconditionally at process
        # boot (`src/web_server.py`), so by the time a browser's first session
        # poll arrives, the single consolidated task is routinely already deep
        # into a ui-only long-poll. Direct cancellation (rather than racing an
        # extra waiter task every single cycle via `asyncio.wait()`) keeps the
        # common, no-interruption cycle's cost identical to a bare `await` —
        # measurably significant under this module's own worst case (a session
        # whose Backend-side retained window has been shrunk by eviction, where
        # even a small added per-cycle latency can widen a loss that wouldn't
        # otherwise occur).
        self._in_flight_task: asyncio.Task | None = None
        # Issue #2161: one-shot fast-probe flag, mirroring the identical mechanism on
        # the browser tier (frontend/src/stores/polling.js). Initialized True so this
        # relay's very first-ever cycle is also fast. Set True (unconditionally, when
        # `is_new`) by touch_session()/start_ui_relay() at the exact same spot they
        # already call _interrupt_if_in_flight() — read-and-cleared synchronously in
        # _relay_loop() before `await task`, so it can never degrade into a busy-poll.
        self._fast_probe_pending = True
        self._stopped = False
        # Epic #1990 stage 5a: last Backend epoch observed per stream key ("ui" or a
        # session_id) — the value the Frontend's own poll responses surface, and what an
        # observed change against triggers a `stream_reset` emission for.
        self._last_epoch: dict[str, str] = {}
        # Last Backend-space cursor consumed per stream — survives a session aging out
        # and back into the subscription set (unlike a local variable scoped to one
        # task iteration), so resuming polls Backend from exactly where this relay left
        # off rather than from 0 or from the now-independent local queue cursor. See
        # module docstring.
        self._backend_cursor: dict[str, int] = {}
        # Issue #2305: sessions that dropped out of the subscription set, mapped to the
        # Backend cursor they will resume from — only used to log re-entry. The cursor
        # itself (and `_last_epoch`) is deliberately RETAINED across age-out: popping
        # them made re-entry poll Backend from 0 and re-append the whole queue.
        self._aged_out: dict[str, int] = {}
        # Sessions seeded explicitly while a request for them was in flight: that
        # request's answer must be discarded, not allowed to overwrite the seed.
        self._seed_overrides: set[str] = set()
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
        completed yet (e.g. a session that's never been subscribed)."""
        return self._last_epoch.get(stream_key)

    def warm_ui_relay(self) -> None:
        """Issue #2160 AC1 (post-review fix): the boot-time-only entry point for
        `src/web_server.py`'s unconditional startup call — pre-warms the consolidated
        relay task for the UI stream exactly as epic #1990 stage 5c's own cold-start-race
        fix requires (so a browser's very first poll doesn't race a cold task start),
        WITHOUT touching `start_ui_relay()`'s own separate `_ui_seed_checked` one-shot
        window. At boot there is no in-flight request to interrupt and `_fast_probe_pending`
        already starts `True` (see `__init__`), so there is nothing else this needs to do
        beyond starting the task and marking the UI stream as subscribed.
        """
        self._ui_started = True
        self._ensure_task_running()

    def start_ui_relay(self, since: int = 0) -> None:
        """Register interest in the UI stream (no idle timeout — once started, it
        stays subscribed for the process lifetime) and ensure the single
        consolidated relay task is running. The real entry point for every actual
        browser `POST /api/poll` request (`src/routers/poll.py`) — `warm_ui_relay()`
        above is for the Frontend's own boot-time pre-warm only.

        `since` (issue #2160 AC1): the browser's own UI cursor as of this call. On this
        process's first-ever call to THIS method specifically (tracked by
        `_ui_seed_checked`, deliberately independent of `_ui_started` — see that flag's
        own comment in `__init__` for why), a nonzero `since` is proof the browser
        already holds a cursor from before this Frontend process existed (a brand-new
        UI stream always arrives with `since=0`) — see `_maybe_seed_stream_reset()`.
        """
        is_new = not self._ui_started
        self._ui_started = True
        self._ensure_task_running()
        if is_new:
            self._fast_probe_pending = True
            self._interrupt_if_in_flight()
        if not self._ui_seed_checked:
            self._ui_seed_checked = True
            self._maybe_seed_stream_reset(_STREAM_KEY_UI, self._ui_queue, since)

    def touch_session(
        self, session_id: str, since: int = 0, seed_backend_cursor: int | None = None
    ) -> None:
        """Register/refresh interest in relaying this session's stream, and ensure
        the local `EventQueue` exists. Called on every local request involving this
        session_id — both `/api/poll`'s subscription-set handler and `GET /messages`'
        history-cursor handoff — so a real browser's continued interest keeps the
        session in the subscription set (see module docstring's US3 discussion for
        why this must be refreshed, not just checked once).

        Replaces `ensure_session_relay()` (epic #1990 stage 5c) — renamed, not kept
        as a compat alias. Unlike its predecessor, this no longer starts a dedicated
        task: the single consolidated task (started via `start_ui_relay()`) picks up
        this session_id on its next cycle purely from `_session_last_activity` — and
        if this session isn't already covered by whatever the relay's current
        in-flight request is polling for, interrupts it immediately rather than
        leaving this session waiting out that request's full long-poll timeout.

        `since` (issue #2160 AC1): the browser's own cursor for this session as of
        this call — threaded through to `_maybe_seed_stream_reset()` on this
        session's local queue's first-ever creation in this process's lifetime.

        Issue #2305: Backend cursors are retained across age-out, so a session only has
        no remembered cursor when its local queue is brand new too — polling Backend from
        0 then fills an EMPTY local queue and duplicates nothing. `seed_backend_cursor`
        (`GET /messages`, which already holds history up to that Backend cursor) starts
        the relay exactly there instead, if the session has no cursor yet.
        """
        is_new = session_id not in self._active_subscription
        self._session_last_activity[session_id] = time.monotonic()
        resume_cursor = self._aged_out.pop(session_id, None)
        if resume_cursor is not None:
            logger.info(
                "Poll-relay: session %s re-entered subscription, resuming from backend cursor %d epoch %s",
                session_id, resume_cursor, self._last_epoch.get(session_id),
            )
        queue_is_new = session_id not in self._session_queues
        if queue_is_new:
            self._session_queues[session_id] = EventQueue()
        if seed_backend_cursor is not None and session_id not in self._backend_cursor:
            self._seed_backend_cursor(session_id, seed_backend_cursor, "GET /messages history cursor")
        self._ensure_task_running()
        if is_new:
            self._fast_probe_pending = True
            self._interrupt_if_in_flight()
        if queue_is_new:
            self._maybe_seed_stream_reset(session_id, self._session_queues[session_id], since)

    def _seed_backend_cursor(self, session_id: str, cursor: int, reason: str) -> None:
        logger.info(
            "Poll-relay: seeding session %s backend cursor to %d (%s)", session_id, cursor, reason
        )
        self._backend_cursor[session_id] = cursor
        if session_id in self._active_subscription:
            # The in-flight request (polling from the cursor we just replaced) must not
            # overwrite this seed with its own answer.
            self._seed_overrides.add(session_id)
            self._interrupt_if_in_flight()

    def _maybe_seed_stream_reset(self, stream_key: str, queue: EventQueue, since: int) -> None:
        """Issue #2160 AC1: make a Frontend-tier restart a deterministic, explicit
        `stream_reset` signal instead of relying on cursor-replay coincidence +
        browser dedup. Called only on a local queue's first-ever creation in this
        process's lifetime (by `start_ui_relay()`/`touch_session()` above) — a
        nonzero `since` there is proof the caller already holds a cursor from
        before this Frontend process existed, since every genuinely-new stream's
        first-ever poll arrives with `since=0` (confirmed against the bootstrap
        `GET /api/poll/.../cursor` endpoints, which are never wired to this method).
        Seeds the marker before anything else can be appended or multiplexed
        against this queue. `old_epoch=""` is a value Backend's real epoch can
        never produce — self-documenting in `--debug-polling` logs as a
        Frontend-tier generation marker, not a genuine Backend-epoch transition.
        """
        if since <= 0:
            return
        family, scope = _family_and_scope(stream_key)
        emit(
            queue, family, "stream_reset",
            {"old_epoch": "", "new_epoch": FRONTEND_PROCESS_EPOCH},
            scope=scope,
            backend_id=FRONTEND_LOCAL_BACKEND_ID,
        )

    def _interrupt_if_in_flight(self) -> None:
        if self._in_flight_task is not None and not self._in_flight_task.done():
            self._in_flight_task.cancel()

    def _ensure_task_running(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._relay_loop(), name="poll_relay")

    def _current_subscription(self) -> set[str]:
        now = time.monotonic()
        active_sessions = {
            session_id
            for session_id, last_activity in self._session_last_activity.items()
            if now - last_activity <= _SESSION_IDLE_TIMEOUT_SECONDS
        }
        if self._ui_started:
            return {_STREAM_KEY_UI} | active_sessions
        return active_sessions

    def _prune_aged_out_sessions(self, subscription: set[str]) -> None:
        """US3: explicit bookkeeping release for every session that just dropped out
        of the subscription set. Pre-consolidation, this state lived inside a dying
        per-session task's own closure and was freed automatically; the single
        consolidated task has no such closure teardown to rely on."""
        aged_out = [
            session_id for session_id in self._session_last_activity if session_id not in subscription
        ]
        for session_id in aged_out:
            resume_cursor = self._backend_cursor.get(session_id)
            logger.info(
                "Poll-relay subscription for session %s aged out, pruning bookkeeping "
                "(resume_cursor=%s)", session_id, resume_cursor,
            )
            self._session_last_activity.pop(session_id, None)
            # _last_epoch / _backend_cursor are kept on purpose (issue #2305): re-entry
            # resumes from the remembered cursor, and a Backend restart or session reset
            # during the gap still surfaces as an epoch change -> stream_reset.
            if resume_cursor is not None:
                self._aged_out[session_id] = resume_cursor
            self._checkpoints.pop(session_id, None)
            self._pending_evicted.pop(session_id, None)
            self._frontier.pop(session_id, None)
            # _pending_waiter_cursors / _stream_activity are keyed by in-flight
            # wait_until_relayed() callers, which self-clean via their own `finally`
            # blocks — never pruned here, a waiter may still be registered.

    async def _relay_loop(self) -> None:
        while not self._stopped:
            subscription = self._current_subscription()
            self._prune_aged_out_sessions(subscription)
            if not subscription:
                # Every session aged out before ui was ever started (or at all) —
                # nothing to poll. Avoid both a wasted call and a busy-spin; the
                # task stays alive (it never self-stops) and picks back up the
                # moment touch_session()/start_ui_relay() is called again.
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                continue
            session_ids = subscription - {_STREAM_KEY_UI}
            self._seed_overrides = set()
            body = {
                "timeout": 0 if self._fast_probe_pending else _POLL_TIMEOUT_SECONDS,
                "sessions": {
                    session_id: {"since": self._backend_cursor.get(session_id, 0)}
                    for session_id in session_ids
                },
            }
            if _STREAM_KEY_UI in subscription:
                body["ui"] = {"since": self._backend_cursor.get(_STREAM_KEY_UI, 0)}
            # Issue #2161: consume the one-shot flag for THIS cycle's request,
            # synchronously, before `await task` below.
            self._fast_probe_pending = False

            # This request's subscription is now authoritative for "is a stream
            # new" purposes until the NEXT cycle — touch_session()/start_ui_relay()
            # compare against this to decide whether to call _interrupt_if_in_flight().
            self._active_subscription = subscription
            task = asyncio.ensure_future(self._poll_once(body))
            self._in_flight_task = task
            try:
                response = await task
            except asyncio.CancelledError:
                if self._stopped:
                    raise
                # Interrupted by touch_session()/start_ui_relay() adding a stream
                # this now-stale request didn't cover — restart immediately with
                # the fresh subscription rather than waiting out this request's
                # full _POLL_TIMEOUT_SECONDS (see `_in_flight_task`'s docstring).
                continue
            except httpx.RequestError:
                # No local log here — BackendClient.request_json() already recorded
                # this failure via its shared reachability tracker (issue #1844);
                # logging it again here on every 2s retry would just re-flood
                # error.log.
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                continue
            except httpx.HTTPStatusError:
                logger.exception("Poll-relay: Backend returned an error response")
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                continue
            except Exception:
                logger.exception("Poll-relay: unexpected error, retrying")
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                continue
            finally:
                self._in_flight_task = None

            ui_body = response.get("ui")
            if ui_body is not None:
                self._process_stream_response(_STREAM_KEY_UI, self._ui_queue, ui_body)
            for session_id, stream_body in response.get("sessions", {}).items():
                queue = self._session_queues.get(session_id)
                if queue is None:
                    continue
                self._process_stream_response(session_id, queue, stream_body)

    async def _poll_once(self, body: dict) -> dict:
        return await self._backend_client.request_json(
            "POST", "/api/poll", json=body, timeout=_POLL_CLIENT_TIMEOUT_SECONDS,
        )

    def _process_stream_response(self, stream_key: str, queue: EventQueue, stream_body: dict) -> None:
        if stream_key in self._seed_overrides:
            # Explicitly seeded while this request was in flight (and the
            # task had already completed, so it could not be cancelled).
            self._seed_overrides.discard(stream_key)
            return
        events = stream_body.get("events", [])
        next_cursor = stream_body.get("next_cursor", self._backend_cursor.get(stream_key, 0))
        epoch = stream_body.get("epoch", "")
        backend_evicted = stream_body.get("evicted", False)
        if backend_evicted:
            self._pending_evicted[stream_key] = True

        family, scope = _family_and_scope(stream_key)
        previous_epoch = self._last_epoch.get(stream_key)
        if previous_epoch is not None and epoch != previous_epoch:
            # Observed Backend restart: make it an explicit, ordered event in
            # the local queue BEFORE this poll's own (already-new-epoch)
            # events, rather than inferring it from cursor magnitude.
            emit(
                queue, family, "stream_reset",
                {"old_epoch": previous_epoch, "new_epoch": epoch},
                scope=scope,
                backend_id=FRONTEND_LOCAL_BACKEND_ID,
            )
        self._last_epoch[stream_key] = epoch

        # Every local queue — UI and every session — uses the same plain
        # auto-increment discipline now (issue #1890's mixed-discipline
        # collision can no longer happen by construction).
        local_cursor_before = queue.current_cursor
        for event in events:
            queue.append(event)
        if stream_key not in self._backend_cursor and stream_key != _STREAM_KEY_UI:
            logger.info(
                "Poll-relay: session %s first poll complete, backend cursor %d epoch %s",
                stream_key, next_cursor, epoch,
            )
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
        queue = self._ui_queue if stream_key == _STREAM_KEY_UI else self._session_queues.get(stream_key)
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
        """Cancel the relay task (called during app shutdown)."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
