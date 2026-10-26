"""
Bounded in-memory event queue for HTTP long-polling.
"""

import asyncio
import logging
import secrets

logger = logging.getLogger(__name__)


class EventQueue:
    """Bounded in-memory event queue for HTTP long-polling."""

    MAX_SIZE = 5000

    def __init__(self, on_append=None):
        """
        Args:
            on_append: Optional callable(event: dict) invoked synchronously on every
                append() — a generic observer hook for callers that need visibility
                into every event this queue delivers without threading a parameter
                through each of its (potentially many, scattered) push call sites.
                Must be synchronous and cheap; exceptions are swallowed so a broken
                hook never breaks event delivery. (First use: issue #1998's raw-
                fidelity session recorder.)
        """
        self._events: list[dict] = []
        self._cursor: int = 0
        self._oldest_cursor: int = 1
        self._waiters: list[asyncio.Event] = []
        self._on_append = on_append
        # Issue #2305: identifies which reset-generation this queue's cursor space belongs
        # to. Changes on every reset(), so pollers can tell "cursor space restarted".
        self.generation: str = secrets.token_hex(8)

    def reset(self) -> None:
        """Discard every retained event and restart the cursor space (issue #2305).

        In place rather than replacing the queue object, so in-flight polls and
        `on_append` hooks holding a reference keep working. A new `generation` token
        lets pollers detect the discontinuity. Waiters are woken so a long-poll
        holding a pre-reset cursor re-evaluates immediately.
        """
        self._events.clear()
        self._cursor = 0
        self._oldest_cursor = 1
        self.generation = secrets.token_hex(8)
        for waiter in self._waiters:
            waiter.set()
        self._waiters.clear()

    def append(self, event: dict) -> int:
        """Append an event, auto-incrementing the local cursor.

        Epic #1990 stage 5a: every writer on every queue instance uses this same
        auto-increment discipline uniformly now — Frontend's local queues no longer
        adopt Backend's own cursor numbering (that coupling is exactly what let a
        Backend restart desync or collide with local numbering, issue #1890/#1886).
        A Backend restart is now surfaced as an explicit `stream_reset` event
        (src/poll_relay.py), not inferred from cursor arithmetic.
        """
        self._cursor += 1
        self._events.append(event)
        if len(self._events) > self.MAX_SIZE:
            self._events.pop(0)
            self._oldest_cursor += 1
        for waiter in self._waiters:
            waiter.set()
        self._waiters.clear()
        if self._on_append is not None:
            try:
                self._on_append(event)
            except Exception:
                logger.exception("EventQueue on_append hook failed")
        return self._cursor

    def events_since(self, cursor: int) -> tuple[list[dict], int, bool]:
        """Returns (events, next_cursor, evicted).

        `evicted` is true only when `cursor` predates the buffer's oldest
        retained event — i.e. genuinely-lost history, not a resettable cursor
        space (a `next_cursor` lower than `cursor` instead — e.g. a Backend
        restart — which callers detect via the explicit `stream_reset` marker,
        not this field). Callers that need an explicit signal for "some
        history was silently dropped" (e.g. a stall-heal deciding whether to
        fall back to a full resync) should check this field.
        """
        if not self._events:
            return [], self._cursor, False
        evicted = cursor < self._oldest_cursor - 1
        if cursor > self._cursor or evicted:
            # Either `since` exceeds what this queue instance has ever handed
            # out (the queue must have reset to a lower cursor space, e.g. a
            # Backend restart) or it predates the buffer's oldest retained
            # event (evicted history). Both cases mean the caller can't be
            # served a slice — hand back everything currently buffered.
            return list(self._events), self._cursor, evicted
        start_idx = max(0, cursor - self._oldest_cursor + 1)
        return self._events[start_idx:], self._cursor, False

    @property
    def current_cursor(self) -> int:
        return self._cursor

    async def wait_for_events(self, cursor: int, timeout: float) -> None:
        events, current, _evicted = self.events_since(cursor)
        if events or current > cursor:
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
