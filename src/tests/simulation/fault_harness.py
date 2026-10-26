"""Deterministic fault-simulation harness for the browser/Frontend-relay/Backend
poll transport (issue #1999, US2/AC3/AC4).

Composes the REAL, unmodified `shared.event_queue.EventQueue` and
`src.poll_relay.PollRelay` against two fakes standing in for the two real
processes either side of the relay:

- `FakeBackend`: a real `EventQueue` per stream, behind a `backend_client`-
  compatible `request_json()` interface — the same shape `PollRelay` already
  depends on — so the real relay code runs unmodified, polling this fake
  exactly as it would poll `backend/routers/poll.py`'s multiplexed
  `POST /api/poll` over HTTP (epic #1990 stage 5c phase 2/4 — the legacy
  single-stream GET shape this fake used to simulate has no remaining caller).
- `FakeFrontend`: owns a `PollRelay` instance plus its own local `EventQueue`s
  (exactly what `src/routers/poll.py`'s legacy single-stream GET endpoints —
  still present through stage 5c phase 5 — read from), pointed at a
  `FakeBackend`. Its own `poll_ui()`/`poll_session()` keep that legacy single-
  stream external shape (mirroring `BrowserClient`'s one-stream-at-a-time
  view) but delegate internally to the same real `shared/poll_multiplex.py`
  helper `FakeBackend` uses, rather than reimplementing the race/assembly
  logic a second time by hand.

`BrowserClient` is a scripted poll-loop client (pausable, to simulate a frozen
tab) that mirrors `src/routers/poll.py`'s own reset/evicted handling: on
either signal it adopts the fresh cursor and continues — a full REST resync
is a frontend-JS-store concern (covered by AC1/AC2's equivalence harness and
`polling.test.js`), out of scope for this transport-layer harness.

Epic #1990 stage 5a: a Backend restart is detected via `epoch` comparison
(see `src/poll_relay.py`), not cursor magnitude — `FakeFrontend`'s own `reset`
field is derived the same way real `src/routers/poll.py` is: "does this batch
contain a `stream_reset` marker" (now computed by `shared/poll_multiplex.py`
itself, consumed via its `StreamResult.reset`). Local (Frontend-side) queues
are pure auto-increment now (no more `EventQueue.append(cursor=...)` cursor
adoption, issue #1886/#1889/#1890) — a session queue no longer mirrors
Backend's own cursor numbering 1:1.

No fake clock: this reuses the codebase's established determinism pattern
(monkeypatched tiny module-level time constants — see `src/poll_relay.py`'s
`_ERROR_BACKOFF_SECONDS`/`_SESSION_IDLE_TIMEOUT_SECONDS` and
`src/tests/test_poll_relay.py`'s existing use of `pytest.MonkeyPatch`) plus
explicit `asyncio.Event`-gated checkpoints at fault-injection points, rather
than a from-scratch virtual asyncio event loop — an explicit, owner-confirmed
scope decision (see PLAN_1999's Risks section).
"""

import asyncio
from typing import Any

import httpx

import src.poll_relay as _relay_timeout_module
from shared.event_envelope import EventEnvelope
from shared.event_queue import EventQueue
from shared.poll_multiplex import multiplex_poll
from shared.poll_protocol import parse_poll_response
from src.poll_relay import PollRelay

# Internal multiplex stream key, not a URL path — epic #1990 stage 5c phase 5
# removed the legacy GET-path dispatch this constant used to double as.
_STREAM_KEY_UI = "ui"

# run_fault_scenario() stamps this onto every event it appends, as a stable,
# cursor-independent delivery identity (epic #1990 stage 5a — see its docstring
# for why cursor arithmetic alone stopped being safe). Namespaced well past
# anything a recorded fixture's own content would plausibly use.
_FAULT_HARNESS_SEQ_KEY = "__fault_harness_seq__"


async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll `predicate()` until it's truthy or `timeout` elapses. Returns whether it
    became truthy. Shared by this module's own callers and by harness-adjacent test
    files (e.g. backend/tests/test_fault_harness_real_backend_client.py) that would
    otherwise each hand-roll the same deadline-loop shape.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


class FakeBackend:
    """Stands in for Backend's real poll endpoints (backend/routers/poll.py).

    `request_json()` is the sole entry point now (epic #1990 stage 5c phase 4
    removed the legacy single-stream `get_json()` shape entirely, once the real
    `PollRelay` it was written to serve switched to the multiplexed `POST
    /api/poll` in phase 2 — `get_json()` had no remaining caller). Reached via
    a `backend_client`-compatible interface shaped exactly like
    `src/backend_client.py`'s real one, so the real relay code runs unmodified,
    polling this fake exactly as it would poll `backend/routers/poll.py` over
    HTTP. `request_json()` delegates to the REAL `shared/poll_multiplex.py`
    helper against this fake's own real per-stream `EventQueue`s — no inline
    reimplementation of the race/quota/assembly logic. `FakeFrontend`'s own
    consumer-facing `poll_ui()`/`poll_session()` methods delegate to the same
    shared helper now too, for the identical fidelity reason — their external
    single-stream shape is unchanged (mirrors `BrowserClient`'s own one-
    stream-at-a-time view), only their internals stopped reimplementing
    `wait_for_events()`/`events_since()`/marker-detection by hand.
    """

    def __init__(self) -> None:
        self._session_queues: dict[str, EventQueue] = {}
        self._ui_queue = EventQueue()
        self._down = False
        self._extra_delay = 0.0
        self._epoch = 0

    def queue_for(self, session_id: str) -> EventQueue:
        if session_id not in self._session_queues:
            self._session_queues[session_id] = EventQueue()
        return self._session_queues[session_id]

    @property
    def ui_queue(self) -> EventQueue:
        return self._ui_queue

    @property
    def epoch(self) -> int:
        """Bumped on every restart() — identifies which cursor-numbering
        generation a given cursor value belongs to (issue #2063 §6)."""
        return self._epoch

    def go_down(self) -> None:
        """AC3 primitive: Backend stops answering polls at all (connection refused)."""
        self._down = True

    def come_up(self) -> None:
        self._down = False

    def restart(self) -> None:
        """AC3 primitive: Backend process restart — every stream gets a fresh
        `EventQueue` (cursor space discontinuity) AND a bumped `epoch` (epic
        #1990 stage 5a) — the signal `PollRelay` actually watches for, independent
        of whether a given poll happens to also observe a lower cursor. Implicitly
        comes back up (a restarted process is, by definition, up).
        """
        for session_id in list(self._session_queues):
            self._session_queues[session_id] = EventQueue()
        self._ui_queue = EventQueue()
        self._down = False
        self._epoch += 1

    def evict_to(self, session_id: str, n: int) -> None:
        """AC3 primitive: shrink this session's retained-event window to `n` —
        the next appends evict older history past that window, exactly like a
        real long-running `EventQueue` hitting `MAX_SIZE` (shared/event_queue.py),
        just at a small `n` a test can reach without 5000 real appends. Reuses
        the queue's own public `MAX_SIZE` class attribute (an instance-level
        override), not private state poking.
        """
        self.queue_for(session_id).MAX_SIZE = n

    def reset_session(self, session_id: str) -> None:
        """Issue #2305 primitive: session reset — same process, the session's queue is
        reset in place (cursor space restarts, new generation => new epoch), exactly
        like backend/web_server.py's `_on_session_reset`."""
        self.queue_for(session_id).reset()

    def _session_epoch(self, session_id: str) -> str:
        return f"{self._epoch}:{self.queue_for(session_id).generation}"

    def slow_ready(self, delay: float) -> None:
        """AC3 primitive: every subsequent poll response is delayed by `delay`
        seconds before Backend "answers" — models a Backend that's up but slow
        to become ready (e.g. mid-startup). 0 clears it."""
        self._extra_delay = delay

    async def request_json(
        self, method: str, path: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """`backend_client`-compatible POST entry point — what the real,
        unmodified `PollRelay` now calls (see class docstring). Only
        `POST /api/poll` is implemented; nothing in this harness sends anything
        else through this method today."""
        assert method == "POST" and path == "/api/poll", f"unexpected request_json call: {method} {path}"
        if self._down:
            raise httpx.ConnectError("FakeBackend is down", request=httpx.Request(method, path))
        if self._extra_delay:
            await asyncio.sleep(self._extra_delay)

        body = json or {}
        poll_timeout = min(float(body.get("timeout", 30)), 30.0)
        streams: dict[str, tuple[EventQueue, int]] = {}
        ui_query = body.get("ui")
        if ui_query is not None:
            streams[_STREAM_KEY_UI] = (self._ui_queue, int(ui_query.get("since", 0)))
        session_queries = body.get("sessions") or {}
        for session_id, query in session_queries.items():
            streams[session_id] = (self.queue_for(session_id), int(query.get("since", 0)))

        results = await multiplex_poll(streams, timeout=poll_timeout)

        response: dict[str, Any] = {}
        if ui_query is not None:
            result = results[_STREAM_KEY_UI]
            response["ui"] = {
                "events": result.events, "next_cursor": result.next_cursor,
                "reset": result.reset, "evicted": result.evicted, "epoch": str(self._epoch),
            }
        response["sessions"] = {
            session_id: {
                "events": results[session_id].events, "next_cursor": results[session_id].next_cursor,
                "reset": results[session_id].reset, "evicted": results[session_id].evicted,
                "epoch": self._session_epoch(session_id),
            }
            for session_id in session_queries
        }
        return response


class FakeFrontend:
    """Stands in for Frontend's poll-relay side (src/poll_relay.py +
    src/routers/poll.py) — a real `PollRelay` plus the local `EventQueue`s it
    fans Backend's streams into, pointed at one `FakeBackend`.
    """

    def __init__(self, backend: FakeBackend) -> None:
        self._backend = backend
        self._build()

    def _build(self) -> None:
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {}
        self.poll_relay = PollRelay(self._backend, self.ui_queue, self.session_queues)

    async def restart(self) -> None:
        """AC4: Frontend process restart — a genuinely distinct fault from
        Backend restart. `PollRelay`'s in-memory relay state (session tasks,
        `_session_last_activity`) and the local `EventQueue`s are discarded and
        rebuilt fresh against the SAME, unaffected `FakeBackend` — the next
        local poll re-seeds each local queue from Backend's CURRENT cursor via
        `PollRelay`'s normal `_relay_loop` startup (`cursor = queue.current_cursor`
        on a fresh, empty queue = 0, then the first `_poll_once()` call adopts
        whatever Backend hands back), independent of whether Backend's own
        cursor space changed at all.
        """
        await self.poll_relay.stop()
        self._build()

    async def stop(self) -> None:
        await self.poll_relay.stop()

    async def _poll_one_local_stream(
        self, stream_key: str, queue: EventQueue, since: int, timeout: float
    ) -> dict[str, Any]:
        """Shared by poll_ui()/poll_session(): races+assembles exactly one local
        stream via the REAL shared/poll_multiplex.py helper — no inline
        reimplementation of wait_for_events()/events_since()/marker-detection.
        quota_per_stream is set to the queue's own MAX_SIZE (effectively
        unbounded for any event count this harness realistically exercises):
        poll_ui()/poll_session() simulate the LEGACY single-stream endpoints,
        which never truncated a batch, so adopting the newer endpoint's default
        quota here would be a real (if usually invisible) behavior change, not
        a pure refactor.
        """
        effective_timeout = min(float(timeout), 30.0)
        results = await multiplex_poll(
            {stream_key: (queue, since)}, timeout=effective_timeout, quota_per_stream=EventQueue.MAX_SIZE
        )
        result = results[stream_key]
        evicted = result.evicted or self.poll_relay.consume_pending_evicted(stream_key)
        return {
            "events": result.events, "next_cursor": result.next_cursor,
            "reset": result.reset, "evicted": evicted,
            "epoch": self.poll_relay.last_epoch(stream_key) or "",
        }

    async def poll_ui(self, since: int, timeout: float = 1.0) -> dict[str, Any]:
        self.poll_relay.start_ui_relay(since=since)
        return await self._poll_one_local_stream("ui", self.ui_queue, since, timeout)

    async def poll_session(self, session_id: str, since: int, timeout: float = 1.0) -> dict[str, Any]:
        self.poll_relay.touch_session(session_id, since=since)
        queue = self.session_queues[session_id]
        return await self._poll_one_local_stream(session_id, queue, since, timeout)


class BrowserClient:
    """Scripted poll-loop client against a `FakeFrontend` — mirrors the
    browser's own cursor-tracking loop (frontend/src/stores/polling.js).
    Pausable (simulates a frozen tab, AC4's fourth fault type).

    `received` is the delivery log used for the exactly-once assertion: a list
    of (identity, event) pairs, one per event actually handed to this client.
    Epic #1990 stage 5a: `identity` is the event's own `_FAULT_HARNESS_SEQ_KEY`
    content tag (see `run_fault_scenario`), not cursor arithmetic — a
    `stream_reset` marker occupies its own slot in the LOCAL queue's cursor
    space with no Backend-side counterpart, so local cursor and Backend cursor
    are no longer the same numbering (unlike pre-stage-5a, when session queues
    adopted Backend's cursor 1:1). `received_deduped()`'s dedup-by-identity
    still matters: a retried poll after a transient error can legitimately
    re-observe the same still-buffered range without that counting as a
    duplicate delivery.
    """

    def __init__(self, frontend: FakeFrontend, session_id: str | None = None) -> None:
        self._frontend = frontend
        self._session_id = session_id
        self.cursor = 0
        self.received: list[tuple[int, EventEnvelope]] = []
        self.reset_count = 0
        self.evicted_count = 0
        self.error_count = 0
        self.paused = False

    async def poll_once(self, timeout: float = 0.2) -> None:
        if self.paused:
            return
        if self._session_id is None:
            body = await self._frontend.poll_ui(self.cursor, timeout=timeout)
        else:
            body = await self._frontend.poll_session(self._session_id, self.cursor, timeout=timeout)

        batch = parse_poll_response(body)
        if batch.reset or batch.evicted:
            if batch.reset:
                self.reset_count += 1
            if batch.evicted:
                self.evicted_count += 1
            # A real browser resyncs via a full REST reload here (message.js's
            # loadMessages(), see polling.js) — out of scope for this
            # transport-layer harness (AC1/AC2's equivalence harness and
            # polling.test.js already cover that reload path).
            #
            # Epic #1990 stage 5a: a `stream_reset` marker can arrive mid-batch,
            # with other already-new-epoch events appended after it in the SAME
            # poll response (src/poll_relay.py emits the marker, then appends that
            # poll's own events, into one local queue before this client's next
            # poll). Those post-marker events are genuinely new and already in
            # hand — recording them is the harness-level equivalent of a real
            # browser's resync-via-reload recovering them through a different
            # path; without this, this dumb client would otherwise report them
            # as never delivered despite the transport having them available
            # right here. Events at-or-before the marker are superseded by the
            # (real-browser-only) resync and intentionally not recorded here.
            last_marker_idx = max(
                (i for i, e in enumerate(batch.events) if e.type == "stream_reset"),
                default=-1,
            )
            for i, event in enumerate(batch.events):
                if i > last_marker_idx:
                    self._record(event)
            self.cursor = batch.next_cursor
            return

        for event in batch.events:
            self._record(event)
        self.cursor = batch.next_cursor

    def _record(self, event: EventEnvelope) -> None:
        # Falls back to the delivered object's own identity for callers that don't
        # go through run_fault_scenario()'s tagging (e.g. backend/tests/
        # test_fault_harness_real_backend_client.py, which appends raw untagged
        # dicts directly) — never falsely dedupes two distinct deliveries, since
        # each is a freshly-constructed EventEnvelope object even when content is
        # identical. Content-tagged identity remains preferred when present.
        identity = event.data.get(_FAULT_HARNESS_SEQ_KEY, id(event))
        self.received.append((identity, event))

    async def run_until(self, stop_event: asyncio.Event, poll_timeout: float = 0.05) -> None:
        """Poll in a loop until `stop_event` is set — tolerates transient
        connection errors (e.g. FakeBackend.go_down()) by backing off briefly,
        mirroring polling.js's own retry-with-backoff loop."""
        while not stop_event.is_set():
            if self.paused:
                # A real yield point is required here even though there's
                # nothing to do: poll_once() returns immediately (without
                # awaiting anything) while paused, so without this sleep the
                # loop would spin without ever suspending — starving every
                # other coroutine on the event loop (including whatever is
                # waiting to unpause this client) for as long as the pause lasts.
                await asyncio.sleep(0.01)
                continue
            try:
                await self.poll_once(timeout=poll_timeout)
            except httpx.RequestError:
                self.error_count += 1
                await asyncio.sleep(0.01)

    def received_deduped(self) -> list[tuple[int, EventEnvelope]]:
        """Dedupes by the event's own `_FAULT_HARNESS_SEQ_KEY` content identity —
        a retried poll after a transient error can legitimately re-observe the
        same still-buffered range without that counting as a duplicate
        delivery. Content-based (not cursor-based): see the class docstring for
        why cursor arithmetic alone is no longer a safe identity post-stage-5a.
        """
        seen: set[int] = set()
        deduped = []
        for identity, event in self.received:
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append((identity, event))
        return deduped


# AC4's four named fault types, plus "none" as the non-fault baseline (T2's fault matrix).
# AC6 adds "backend_down"/"slow_ready" (previously-unused FakeBackend primitives) and
# the combined "eviction_during_backend_restart" fault.
FAULT_TYPES = (
    "none",
    "backend_restart",
    "frontend_restart",
    "eviction",
    "freeze",
    "backend_down",
    "slow_ready",
    "eviction_during_backend_restart",
)

# Faults that destroy already-buffered-but-undelivered history (a client that hadn't yet
# caught up loses that range, by design — the same real-world tradeoff #1889 documents).
# Faults NOT in this set are required to lose nothing at all: every appended event must
# eventually be delivered, exactly once. "backend_down"/"slow_ready" answer late or not
# at all but never drop already-buffered history, so neither belongs here.
_DESTRUCTIVE_FAULTS = frozenset(
    {"backend_restart", "eviction", "eviction_during_backend_restart"}
)

# Hold duration shared by "freeze"/"backend_down"/"slow_ready" — short enough to stay
# well within run_fault_scenario's default settle_timeout=5.0.
_FAULT_HOLD_SECONDS = 0.2


def _restart_settle_seconds() -> float:
    """Settle delay shared by every fault that calls backend.restart(): gives the
    relay's poll loop one full cycle to observe the fresh, near-empty post-restart
    queue before more events pile up. Without this, a relay that (by coincidence)
    already caught up to exactly cursor N before the restart can poll a post-restart
    queue that (again by coincidence, e.g. a symmetric pre/post-fault event split) has
    ALSO reached cursor N by the time it looks — EventQueue has no epoch/generation
    concept, only a monotonic-per-instance cursor, so `events_since(N)` against a
    same-valued current cursor reads as "already caught up" rather than "these are N
    entirely different events from a new epoch," silently losing them with no reset
    signal. A real relay is exceedingly unlikely to hit this by chance; a deterministic
    test with round event counts hits it reliably — see test_fault_simulation.py's
    dedicated xfail test, which deliberately skips this mitigation to engineer the
    collision in isolation.

    A function, not a module-level constant: `_relay_timeout_module._POLL_TIMEOUT_
    SECONDS` is monkeypatched per-test (see test_fault_simulation.py's
    `_fast_poll_timing` fixture) — baking the multiplication into a constant computed
    once at import time would freeze in the unpatched real value instead of picking up
    the test's patched one.
    """
    return _relay_timeout_module._POLL_TIMEOUT_SECONDS * 1.5

def resolve_delivered_seqs(
    received: list[tuple[int, EventEnvelope]],
    seq_identity: dict[int, int],
) -> set[int]:
    """Reverse-maps received_deduped()'s content-tag identities back to the
    caller's own per-event `i` index, via run_fault_scenario()'s `seq_identity`
    map (trivial — `seq_identity[i] == i` — but kept as its own parameter so
    this function's signature didn't need to change when identity stopped
    being cursor-derived, epic #1990 stage 5a)."""
    delivered_identities = {identity for identity, _ in received}
    return {i for i, identity in seq_identity.items() if identity in delivered_identities}


# The "eviction" fault's retained-window size and its companion no-yield append burst
# (run_fault_scenario) are two halves of one mechanism, not independent constants: the
# burst must append more events than the window retains, or a relay polling every few
# milliseconds can drain the queue incrementally and never actually observe an evicted
# gap. Deriving the burst from the window keeps that relationship explicit instead of
# two magic numbers that could silently drift apart.
_EVICTION_WINDOW = 5
_EVICTION_BURST_LEN = _EVICTION_WINDOW * 4


async def _inject_fault(
    fault: str, backend: FakeBackend, frontend: FakeFrontend, client: BrowserClient, session_id: str
) -> None:
    if fault == "none":
        return
    if fault == "backend_restart":
        backend.restart()
        await asyncio.sleep(_restart_settle_seconds())
    elif fault == "frontend_restart":
        await frontend.restart()
    elif fault == "eviction":
        backend.evict_to(session_id, n=_EVICTION_WINDOW)
    elif fault == "freeze":
        client.paused = True
        await asyncio.sleep(_FAULT_HOLD_SECONDS)
        client.paused = False
    elif fault == "backend_down":
        backend.go_down()
        await asyncio.sleep(_FAULT_HOLD_SECONDS)
        backend.come_up()
    elif fault == "slow_ready":
        backend.slow_ready(_FAULT_HOLD_SECONDS)
        await asyncio.sleep(_FAULT_HOLD_SECONDS)
        backend.slow_ready(0)
    elif fault == "eviction_during_backend_restart":
        # No yield between restart and evict_to — same "deny the relay a chance
        # to observe the intermediate state" reasoning the plain "eviction" fault
        # already relies on (see run_fault_scenario's burst-append comment):
        # exercises both hazards compounding (fresh cursor space AND a tiny
        # retained window) rather than either alone. Same cursor-collision
        # hazard as the plain "backend_restart" fault above (this fault
        # restarts too) — same mitigation, see _restart_settle_seconds().
        backend.restart()
        backend.evict_to(session_id, n=_EVICTION_WINDOW)
        await asyncio.sleep(_restart_settle_seconds())
    else:
        raise ValueError(f"Unknown fault type: {fault!r} — expected one of {FAULT_TYPES}")


async def run_fault_scenario(
    fault: str,
    events: list[dict],
    session_id: str = "fault-sim-session",
    fault_at_fraction: float = 0.5,
    settle_timeout: float = 5.0,
) -> dict[str, Any]:
    """Replays `events` into a fresh `FakeBackend`'s session queue (AC3's payload:
    a recorded fixture's queue_event stream, "injected against the real event
    queue and relay code with recorded events as payload"), injecting `fault`
    partway through, then drives a `BrowserClient` until it catches up.

    Each event is appended as a shallow copy stamped with its own
    `_FAULT_HARNESS_SEQ_KEY` tag (the caller's `events` list is never mutated —
    the same shared fixture list is reused across every parametrized scenario).
    Identity for the exactly-once assertions is that tag's value directly, not
    cursor arithmetic (epic #1990 stage 5a): a `stream_reset` marker now occupies
    its own slot in the LOCAL queue's cursor space with no Backend-side
    counterpart, so local cursor and Backend cursor are no longer the same
    numbering — reconstructing identity from "local cursor at receive time"
    would systematically misalign against "Backend cursor at append time" by
    however many markers have been inserted so far. Stamping the event's own
    content sidesteps that entirely (and mirrors real production dedup, which
    is also content-derived — `EventEnvelope.event_id` — not raw cursor
    arithmetic imposed externally). `seq_identity[i]` is simply `i` itself now;
    kept as its own dict (rather than inlining `range(len(events))`) so
    `resolve_delivered_seqs()`'s signature didn't need to change.

    Returns a dict with the harness objects plus `expected_seqs` (every seq
    ever appended), `tail_seqs` (the last ~10% of seqs, appended once the
    stream is well past the fault), and `seq_identity` (the per-seq identity
    map `resolve_delivered_seqs()` needs) so callers can apply the right
    strength of exactly-once assertion per `_DESTRUCTIVE_FAULTS`: a destructive
    fault may legitimately drop events genuinely in flight at the moment it
    fires, but must not keep dropping things indefinitely — `tail_seqs` is the
    "the transport has recovered" check; `expected_seqs` is the stronger
    "nothing was lost at all" check, valid only for non-destructive faults.
    """
    if fault not in FAULT_TYPES:
        raise ValueError(f"Unknown fault type: {fault!r} — expected one of {FAULT_TYPES}")

    backend = FakeBackend()
    frontend = FakeFrontend(backend)
    client = BrowserClient(frontend, session_id=session_id)

    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(client.run_until(stop_event, poll_timeout=0.05))

    fault_index = int(len(events) * fault_at_fraction)
    # A burst of appends right after a destructive fault, with no yield in
    # between: denies the relay a chance to poll mid-burst, so eviction (or a
    # restart's now-tiny post-restart backlog) has actually happened by the
    # time it next looks — otherwise a relay polling every few milliseconds
    # can outrun a fault that only shrinks the retained window, and the fault
    # would never become observable.
    burst_len = (
        _EVICTION_BURST_LEN if fault in ("eviction", "eviction_during_backend_restart") else 0
    )
    burst_remaining = 0
    expected_seqs: list[int] = []
    # The tail: events appended once the stream is well past the fault point.
    # Used (instead of "every event after the fault index") for the "the
    # transport recovers and resumes exactly-once delivery" assertion — a
    # destructive fault is allowed to drop events genuinely in flight at the
    # moment it fires, but must not keep dropping things indefinitely.
    tail_start = len(events) - max(1, len(events) // 10)
    tail_seqs: list[int] = []
    seq_identity: dict[int, int] = {}

    for i, event in enumerate(events):
        if i == fault_index:
            await _inject_fault(fault, backend, frontend, client, session_id)
            burst_remaining = burst_len

        # Re-resolve the queue on every append rather than caching it once:
        # backend.restart() REPLACES the EventQueue object behind this session_id
        # (a fresh instance, not a mutation of the existing one) — a cached
        # reference from before the fault would keep writing into the orphaned
        # pre-restart queue that PollRelay no longer polls.
        backend.queue_for(session_id).append({**event, _FAULT_HARNESS_SEQ_KEY: i})
        seq_identity[i] = i
        expected_seqs.append(i)
        if i >= tail_start:
            tail_seqs.append(i)

        if burst_remaining > 0:
            burst_remaining -= 1
        else:
            await asyncio.sleep(0)  # yield so the poll loop observes each append promptly

    # Let the client catch up to Backend's latest cursor before stopping it.
    target_cursor = backend.queue_for(session_id).current_cursor
    loop = asyncio.get_event_loop()
    deadline = loop.time() + settle_timeout
    while loop.time() < deadline:
        delivered_seqs = resolve_delivered_seqs(client.received_deduped(), seq_identity)
        if set(tail_seqs) <= delivered_seqs and not client.paused:
            break
        await asyncio.sleep(0.02)

    stop_event.set()
    await poll_task
    await frontend.stop()

    return {
        "backend": backend,
        "frontend": frontend,
        "client": client,
        "expected_seqs": expected_seqs,
        "tail_seqs": tail_seqs,
        "target_cursor": target_cursor,
        "seq_identity": seq_identity,
    }
