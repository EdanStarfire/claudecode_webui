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

    def append(self, event: dict) -> int:
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
