"""
Bounded in-memory event queue for HTTP long-polling.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def reset_occurred(next_cursor: int, since: int) -> bool:
    """Whether an `events_since(since)` result reflects a queue reset.

    A `next_cursor` lower than the `since` a caller asked for is only
    possible if the queue's cursor space started over beneath the caller —
    proof of a reset (e.g. Backend restart), not just an empty poll.
    """
    return next_cursor < since


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

    def append(self, event: dict, cursor: int | None = None) -> int:
        """Append an event, either auto-incrementing the local cursor (cursor=None)
        or adopting an externally-authoritative cursor value.

        A given queue instance must pick exactly one discipline and stick to it:
        the `cursor` param's contiguity/reset logic below assumes every write to
        this instance comes from the same authoritative numbering scheme. Mixing
        an auto-incrementing writer with a cursor-adopting writer on the same
        instance breaks that assumption — an auto-increment bump can collide with
        or desync the adopted cursor space, causing a dropped event (dedup branch)
        or a full local-history wipe (reset branch) on the next adopted write
        (issue #1890).
        """
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
        space (that case is `reset_occurred()`, checked independently by
        callers against `next_cursor`/`since`). Callers that need an explicit
        signal for "some history was silently dropped" (e.g. a stall-heal
        deciding whether to fall back to a full resync) should check this
        field rather than inferring it from `reset_occurred()`.
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
