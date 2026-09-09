"""
Bounded in-memory event queue for HTTP long-polling.
"""

import asyncio


class EventQueue:
    """Bounded in-memory event queue for HTTP long-polling."""

    MAX_SIZE = 5000

    def __init__(self):
        self._events: list[dict] = []
        self._cursor: int = 0
        self._oldest_cursor: int = 1
        self._waiters: list[asyncio.Event] = []

    def append(self, event: dict, cursor: int | None = None) -> int:
        if cursor is not None:
            if self._events and cursor == self._cursor:
                return self._cursor  # exact redelivery of the last-known event — idempotent skip
            if not self._events or cursor != self._cursor + 1:
                # Source's numbering doesn't extend contiguously from what we have
                # (source was reset — e.g. Backend process restart, which can
                # produce a *lower* cursor than what we last saw — or evicted past
                # our last known position, a forward gap — or this is the very
                # first event this queue has ever seen). Trust the new value and
                # drop now-orphaned local history rather than mis-slicing against a
                # broken oldest_cursor invariant; any `since` that lands in the
                # dropped range legitimately falls into the existing "too old,
                # here's everything we have" branch of events_since() below.
                # Checking contiguity here (not just `cursor <= self._cursor`)
                # matters: a restart's lower cursor must still hit this reset
                # branch instead of being mistaken for an already-seen duplicate
                # and silently dropped.
                self._events = []
                self._oldest_cursor = cursor
            self._cursor = cursor
        else:
            self._cursor += 1
        self._events.append(event)
        if len(self._events) > self.MAX_SIZE:
            self._events.pop(0)
            self._oldest_cursor += 1
        for waiter in self._waiters:
            waiter.set()
        self._waiters.clear()
        return self._cursor

    def events_since(self, cursor: int) -> tuple[list[dict], int]:
        if not self._events:
            return [], self._cursor
        if cursor < self._oldest_cursor - 1:
            return list(self._events), self._cursor
        start_idx = max(0, cursor - self._oldest_cursor + 1)
        return self._events[start_idx:], self._cursor

    @property
    def current_cursor(self) -> int:
        return self._cursor

    async def wait_for_events(self, cursor: int, timeout: float) -> None:
        _, current = self.events_since(cursor)
        if current > cursor:
            return
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
        except TimeoutError:
            pass
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
