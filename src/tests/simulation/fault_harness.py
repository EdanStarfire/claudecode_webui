"""Deterministic fault-simulation harness for the browser/Frontend-relay/Backend
poll transport (issue #1999, US2/AC3/AC4).

Composes the REAL, unmodified `shared.event_queue.EventQueue` and
`src.poll_relay.PollRelay` against two fakes standing in for the two real
processes either side of the relay:

- `FakeBackend`: a real `EventQueue` per stream, behind a `backend_client`-
  compatible `get_json()` interface — the same shape `PollRelay` already
  depends on — so the real relay code runs unmodified, polling this fake
  exactly as it would poll `backend/routers/poll.py` over HTTP.
- `FakeFrontend`: owns a `PollRelay` instance plus its own local `EventQueue`s
  (exactly what `src/routers/poll.py` reads from), pointed at a `FakeBackend`.

`BrowserClient` is a scripted poll-loop client (pausable, to simulate a frozen
tab) that mirrors `src/routers/poll.py`'s own reset/evicted handling: on
either signal it adopts the fresh cursor and continues — a full REST resync
is a frontend-JS-store concern (covered by AC1/AC2's equivalence harness and
`polling.test.js`), out of scope for this transport-layer harness.

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
from shared.event_queue import EventQueue, reset_occurred
from src.poll_relay import PollRelay

UI_PATH = "/api/poll/ui"


def session_path(session_id: str) -> str:
    return f"/api/poll/session/{session_id}"


class FakeBackend:
    """Stands in for Backend's real poll endpoints (backend/routers/poll.py) —
    same `wait_for_events()` -> `events_since()` -> `reset_occurred()` logic,
    against a real `EventQueue` per stream, reached via a `get_json()` method
    shaped exactly like `src/backend_client.py`'s real one so the real,
    unmodified `PollRelay._poll_once()` can call it directly, in-process.
    """

    def __init__(self) -> None:
        self._session_queues: dict[str, EventQueue] = {}
        self._ui_queue = EventQueue()
        self._down = False
        self._extra_delay = 0.0

    def queue_for(self, session_id: str) -> EventQueue:
        if session_id not in self._session_queues:
            self._session_queues[session_id] = EventQueue()
        return self._session_queues[session_id]

    @property
    def ui_queue(self) -> EventQueue:
        return self._ui_queue

    def go_down(self) -> None:
        """AC3 primitive: Backend stops answering polls at all (connection refused)."""
        self._down = True

    def come_up(self) -> None:
        self._down = False

    def restart(self) -> None:
        """AC3 primitive: Backend process restart — every stream gets a fresh
        `EventQueue` (cursor space discontinuity), reproducing the cursor-reset
        scenario `EventQueue.append(cursor=...)` already detects (#1889).
        Implicitly comes back up (a restarted process is, by definition, up).
        """
        for session_id in list(self._session_queues):
            self._session_queues[session_id] = EventQueue()
        self._ui_queue = EventQueue()
        self._down = False

    def evict_to(self, session_id: str, n: int) -> None:
        """AC3 primitive: shrink this session's retained-event window to `n` —
        the next appends evict older history past that window, exactly like a
        real long-running `EventQueue` hitting `MAX_SIZE` (shared/event_queue.py),
        just at a small `n` a test can reach without 5000 real appends. Reuses
        the queue's own public `MAX_SIZE` class attribute (an instance-level
        override), not private state poking.
        """
        self.queue_for(session_id).MAX_SIZE = n

    def slow_ready(self, delay: float) -> None:
        """AC3 primitive: every subsequent poll response is delayed by `delay`
        seconds before Backend "answers" — models a Backend that's up but slow
        to become ready (e.g. mid-startup). 0 clears it."""
        self._extra_delay = delay

    async def get_json(
        self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        if self._down:
            raise httpx.ConnectError("FakeBackend is down", request=httpx.Request("GET", path))
        if self._extra_delay:
            await asyncio.sleep(self._extra_delay)

        params = params or {}
        since = int(params.get("since", 0))
        poll_timeout = float(params.get("timeout", 30))
        effective_timeout = min(poll_timeout, 30.0)

        queue = self._ui_queue if path == UI_PATH else self.queue_for(path.rsplit("/", 1)[-1])
        await queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor, evicted = queue.events_since(since)
        reset = reset_occurred(next_cursor, since)
        return {"events": events, "next_cursor": next_cursor, "reset": reset, "evicted": evicted}


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

    async def poll_ui(self, since: int, timeout: float = 1.0) -> dict[str, Any]:
        self.poll_relay.start_ui_relay()
        effective_timeout = min(float(timeout), 30.0)
        await self.ui_queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor, evicted = self.ui_queue.events_since(since)
        reset = reset_occurred(next_cursor, since)
        return {"events": events, "next_cursor": next_cursor, "reset": reset, "evicted": evicted}

    async def poll_session(self, session_id: str, since: int, timeout: float = 1.0) -> dict[str, Any]:
        self.poll_relay.ensure_session_relay(session_id)
        queue = self.session_queues[session_id]
        effective_timeout = min(float(timeout), 30.0)
        await queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor, evicted = queue.events_since(since)
        reset = reset_occurred(next_cursor, since)
        return {"events": events, "next_cursor": next_cursor, "reset": reset, "evicted": evicted}


class BrowserClient:
    """Scripted poll-loop client against a `FakeFrontend` — mirrors the
    browser's own cursor-tracking loop (frontend/src/stores/polling.js).
    Pausable (simulates a frozen tab, AC4's fourth fault type).

    `received` is the delivery log used for the exactly-once assertion: a list
    of (cursor, event) pairs, one per event actually handed to this client —
    deduped by cursor before comparison, since a retried poll after a
    transient error can legitimately re-observe the same still-buffered
    range without that counting as a duplicate delivery.
    """

    def __init__(self, frontend: FakeFrontend, session_id: str | None = None) -> None:
        self._frontend = frontend
        self._session_id = session_id
        self.cursor = 0
        self.received: list[tuple[int, dict]] = []
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

        if body["reset"] or body["evicted"]:
            if body["reset"]:
                self.reset_count += 1
            if body["evicted"]:
                self.evicted_count += 1
            # A real browser resyncs via a full REST reload here (message.js's
            # loadMessages(), see polling.js) — out of scope for this
            # transport-layer harness (AC1/AC2's equivalence harness and
            # polling.test.js already cover that reload path). Adopting the
            # fresh cursor and continuing is enough to verify the transport
            # itself delivers everything AFTER the resync exactly once.
            self.cursor = body["next_cursor"]
            return

        events = body["events"]
        next_cursor = body["next_cursor"]
        start_cursor = next_cursor - len(events) + 1
        for i, event in enumerate(events):
            self.received.append((start_cursor + i, event))
        self.cursor = next_cursor

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

    def received_deduped(self) -> list[tuple[int, dict]]:
        """Dedupes by each event's own `_fault_harness_seq` tag (see
        `run_fault_scenario`) when present, falling back to raw cursor
        otherwise. Cursor alone is NOT a safe dedup key across a
        `backend_restart` fault: Backend's cursor numbering restarts from 1
        after a restart, so a pre-restart and a post-restart event can
        legitimately share the same cursor value while being two entirely
        different events — collapsing them by cursor alone would produce a
        false "delivered twice" positive (or mask a genuine duplicate).
        """
        seen: set[Any] = set()
        deduped = []
        for cursor, event in self.received:
            key = event.get("_fault_harness_seq", cursor) if isinstance(event, dict) else cursor
            if key in seen:
                continue
            seen.add(key)
            deduped.append((cursor, event))
        return deduped


# AC4's four named fault types, plus "none" as the non-fault baseline (T2's fault matrix).
FAULT_TYPES = ("none", "backend_restart", "frontend_restart", "eviction", "freeze")

# Faults that destroy already-buffered-but-undelivered history (a client that hadn't yet
# caught up loses that range, by design — the same real-world tradeoff #1889 documents).
# Faults NOT in this set are required to lose nothing at all: every appended event must
# eventually be delivered, exactly once.
_DESTRUCTIVE_FAULTS = frozenset({"backend_restart", "eviction"})

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
        # Give the relay's poll loop one full cycle to observe the fresh,
        # near-empty post-restart queue before more events pile up. Without
        # this, a relay that (by coincidence) already caught up to exactly
        # cursor N before the restart can poll a post-restart queue that
        # (again by coincidence, e.g. a symmetric pre/post-fault event split)
        # has ALSO reached cursor N by the time it looks — EventQueue has no
        # epoch/generation concept, only a monotonic-per-instance cursor, so
        # `events_since(N)` against a same-valued current cursor reads as
        # "already caught up" rather than "these are N entirely different
        # events from a new epoch," silently losing them with no reset
        # signal. A real relay is exceedingly unlikely to hit this by chance;
        # a deterministic test with round event counts hits it reliably.
        await asyncio.sleep(_relay_timeout_module._POLL_TIMEOUT_SECONDS * 1.5)
    elif fault == "frontend_restart":
        await frontend.restart()
    elif fault == "eviction":
        backend.evict_to(session_id, n=_EVICTION_WINDOW)
    elif fault == "freeze":
        client.paused = True
        await asyncio.sleep(0.2)
        client.paused = False
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

    Each event is appended as a shallow copy tagged with a unique
    `_fault_harness_seq` (the caller's `events` list is never mutated — the
    same shared fixture list is reused across every parametrized scenario).
    Identity for the exactly-once assertions is this seq, not the raw cursor:
    `backend_restart` resets Backend's cursor numbering back to 1, so a
    pre-restart and a post-restart event can legitimately share a cursor value
    while being two different events.

    Returns a dict with the harness objects plus `expected_seqs` (every seq
    ever appended) and `tail_seqs` (the last ~10% of seqs, appended once the
    stream is well past the fault) so callers can apply the right strength of
    exactly-once assertion per `_DESTRUCTIVE_FAULTS`: a destructive fault may
    legitimately drop events genuinely in flight at the moment it fires, but
    must not keep dropping things indefinitely — `tail_seqs` is the "the
    transport has recovered" check; `expected_seqs` is the stronger "nothing
    was lost at all" check, valid only for non-destructive faults.
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
    burst_len = _EVICTION_BURST_LEN if fault == "eviction" else 0
    burst_remaining = 0
    expected_seqs: list[int] = []
    # The tail: events appended once the stream is well past the fault point.
    # Used (instead of "every event after the fault index") for the "the
    # transport recovers and resumes exactly-once delivery" assertion — a
    # destructive fault is allowed to drop events genuinely in flight at the
    # moment it fires, but must not keep dropping things indefinitely.
    tail_start = len(events) - max(1, len(events) // 10)
    tail_seqs: list[int] = []

    for i, event in enumerate(events):
        if i == fault_index:
            await _inject_fault(fault, backend, frontend, client, session_id)
            burst_remaining = burst_len

        tagged = {**event, "_fault_harness_seq": i}
        # Re-resolve the queue on every append rather than caching it once:
        # backend.restart() REPLACES the EventQueue object behind this session_id
        # (a fresh instance, not a mutation of the existing one) — a cached
        # reference from before the fault would keep writing into the orphaned
        # pre-restart queue that PollRelay no longer polls.
        backend.queue_for(session_id).append(tagged)
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
        delivered_seqs = {e.get("_fault_harness_seq") for _, e in client.received_deduped()}
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
    }
