"""Poll consumers and the shared event buffer (issue #2038).

Three `PollConsumer`s (UI stream, main session, Test Minion session) run as
concurrent `asyncio.Task`s, each long-polling the Frontend API's multiplexed
`POST /api/poll` endpoint (issue #2130, epic #1990 stage 5c) with a single-
stream request body and appending every event it sees — tagged with its
stream — into one `SharedEventBuffer`. Scenario steps call `wait_for()`
against that shared buffer so out-of-band events (background task chatter,
comm replies from other streams) never look like a mismatch — they're just
skipped.

Reconnection: a consumer treats several consecutive transport failures as a
signal the server may have restarted — it waits for `GET /ready` to report
true, then resumes long-polling with its cursor reset to whatever the first
post-recovery response hands back. The same path serves a full app restart
and any incidental transport blip.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from shared.event_envelope import EventEnvelope
from shared.poll_protocol import parse_poll_response

Stream = Literal["ui", "session", "minion"]


@dataclass(frozen=True)
class TaggedEvent:
    source: Stream
    event: EventEnvelope
    seq: int


class WaitTimeoutError(Exception):
    def __init__(
        self,
        description: str,
        matched_so_far: list[TaggedEvent],
        last_n_events: list[TaggedEvent],
    ) -> None:
        self.description = description
        self.matched_so_far = matched_so_far
        self.last_n_events = last_n_events
        seen = [f"{e.source}:{e.event.type}" for e in last_n_events]
        super().__init__(
            f"Timed out waiting for: {description}. "
            f"Matched {len(matched_so_far)} event(s) so far. "
            f"Last {len(seen)} events seen: {seen}"
        )


class SharedEventBuffer:
    """Append-only, growing list of every event seen on any stream. Waiters
    each track their own resume index into this list, so a step only ever
    sees events appended after the point it started waiting.
    """

    def __init__(self) -> None:
        self._events: list[TaggedEvent] = []
        self.condition = asyncio.Condition()

    async def append(self, source: Stream, event: EventEnvelope) -> None:
        async with self.condition:
            self._events.append(TaggedEvent(source=source, event=event, seq=len(self._events)))
            self.condition.notify_all()

    def snapshot(self) -> list[TaggedEvent]:
        return self._events

    def __len__(self) -> int:
        return len(self._events)


async def wait_for_ready(base_url: str, *, timeout: float, poll_interval: float = 0.5) -> None:
    """Polls `GET /ready` until `{"ready": true}` — mirrors `RestartModal.vue`'s
    existing pattern, minus the browser's `window.location.reload()`."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{base_url.rstrip('/')}/ready", timeout=5.0)
                if resp.status_code == 200 and resp.json().get("ready") is True:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Server at {base_url} did not become ready within {timeout}s")


class PollConsumer:
    """Long-polls one poll endpoint and feeds every event it receives into a
    shared buffer, tagged with `stream`. Tracks its own cursor exactly like
    `polling.js`/`fault_harness.py`'s `BrowserClient` do.
    """

    def __init__(
        self,
        *,
        stream: Stream,
        base_url: str,
        token: str | None,
        buffer: SharedEventBuffer,
        session_id: str | None = None,
        poll_timeout: float = 25.0,
        reconnect_after_failures: int = 3,
        reconnect_timeout: float = 60.0,
    ) -> None:
        self.stream = stream
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.buffer = buffer
        self.session_id = session_id
        self.poll_timeout = poll_timeout
        self.reconnect_after_failures = reconnect_after_failures
        self.reconnect_timeout = reconnect_timeout
        self.cursor = 0
        self.consecutive_failures = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def _multiplex_body(self, timeout: int) -> dict:
        """Issue #2130 (epic #1990 stage 5c, phase 4): a single-stream request
        against the multiplexed `POST /api/poll` endpoint, carrying only this
        consumer's own stream — unwrap() below pulls the matching block back out
        of the multiplexed response, so the rest of this class (and callers'
        `parse_poll_response()` contract) is unaffected by the endpoint change.
        """
        if self.session_id is None:
            return {"timeout": timeout, "ui": {"since": self.cursor}}
        return {"timeout": timeout, "sessions": {self.session_id: {"since": self.cursor}}}

    def _unwrap(self, body: dict) -> dict:
        if self.session_id is None:
            return body["ui"]
        return body["sessions"][self.session_id]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _reconnect(self) -> None:
        await wait_for_ready(self.base_url, timeout=self.reconnect_timeout)
        self.consecutive_failures = 0

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        try:
            resp = await client.post(
                "/api/poll",
                json=self._multiplex_body(int(self.poll_timeout)),
                headers=self._headers(),
                timeout=self.poll_timeout + 10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.reconnect_after_failures:
                await self._reconnect()
            else:
                await asyncio.sleep(min(1.0 * self.consecutive_failures, 5.0))
            return

        self.consecutive_failures = 0
        batch = parse_poll_response(self._unwrap(resp.json()))
        # A reset/evicted response still carries a complete event batch, so
        # events are always buffered and the cursor is always adopted,
        # regardless of that flag. Full-app-restart recovery is handled by
        # the transport-failure branch above, not here.
        for event in batch.events:
            await self.buffer.append(self.stream, event)
        self.cursor = batch.next_cursor

    async def fast_forward(self, settle_s: float = 3.0, max_rounds: int = 20) -> None:
        """Moves the cursor to the stream's current head, discarding every event
        already queued (a `timeout=0` poll returns all of them at once). Used on
        resume so events from before the reattach — notably a failed attempt at
        the scenario being retried — can never match that scenario's waits.

        Re-polls until one taken `settle_s` after the previous returns nothing:
        while no client polled, Frontend's poll relay may have aged this stream
        out of its subscription set, so its local queue is stale (the first poll
        can come back empty) and only that first poll re-subscribes it — the
        relay's catch-up from Backend lands afterwards and would otherwise be
        replayed."""
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            for round_index in range(max_rounds):
                if round_index:
                    await asyncio.sleep(settle_s)
                resp = await client.post(
                    "/api/poll",
                    json=self._multiplex_body(0),
                    headers=self._headers(),
                    timeout=30.0,
                )
                resp.raise_for_status()
                parsed = parse_poll_response(self._unwrap(resp.json()))
                self.cursor = parsed.next_cursor
                if round_index and not parsed.events and not parsed.reset:
                    return

    async def run(self) -> None:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            while not self._stop.is_set():
                await self._poll_once(client)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError(f"PollConsumer({self.stream}) already started")
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.CancelledError, TimeoutError):
                pass


Predicate = Callable[[TaggedEvent], bool]


async def wait_for(
    buffer: SharedEventBuffer,
    predicate: Predicate,
    *,
    resume_index: int = 0,
    count: int = 1,
    timeout: float,
    description: str,
    last_n: int = 20,
) -> tuple[list[TaggedEvent], int]:
    """Waits for `count` events matching `predicate`, scanning the shared
    buffer from `resume_index` onward (any order, irrelevant events skipped).

    Returns `(matched_events, next_resume_index)` — `next_resume_index` is
    one past the last event scanned, so a caller's next `wait_for()` call on
    the same buffer never re-matches something this call already consumed.
    """
    deadline = time.monotonic() + timeout
    matched: list[TaggedEvent] = []
    idx = resume_index

    while True:
        events = buffer.snapshot()
        while idx < len(events):
            tagged = events[idx]
            idx += 1
            if predicate(tagged):
                matched.append(tagged)
                if len(matched) >= count:
                    return matched, idx

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitTimeoutError(description, matched, events[-last_n:])

        async with buffer.condition:
            try:
                await asyncio.wait_for(buffer.condition.wait(), timeout=min(remaining, 1.0))
            except TimeoutError:
                pass
