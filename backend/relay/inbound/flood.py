"""Flood limits for monitored content (issue #2103 stage 3b-i). Pure, injectable clock."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable


class RollingLimiter:
    """At most *limit* admissions per rolling *window* seconds (one key)."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float]) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._stamps: deque[float] = deque()

    def _trim(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()

    def allow(self) -> bool:
        now = self._clock()
        self._trim(now)
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True

    def count(self) -> int:
        self._trim(self._clock())
        return len(self._stamps)


class KeyedLimiters:
    """One limiter per key (monitor id), bounded."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float], max_keys: int = 500) -> None:
        self._args = (limit, window, clock)
        self._max = max_keys
        self._items: dict[str, RollingLimiter] = {}

    def allow(self, key: str) -> bool:
        limiter = self._items.pop(key, None) or RollingLimiter(*self._args)
        self._items[key] = limiter
        while len(self._items) > self._max:
            del self._items[next(iter(self._items))]
        return limiter.allow()

    def discard(self, key: str) -> None:
        self._items.pop(key, None)


class SessionEnvelopeLimiter:
    """Envelopes per session per hour (the 12/hour rule): ``allowed`` peeks, ``record`` counts."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float]) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._stamps: dict[str, deque[float]] = {}

    def allowed(self, session_id: str) -> bool:
        stamps = self._stamps.get(session_id)
        if not stamps:
            return True
        now = self._clock()
        while stamps and now - stamps[0] >= self._window:
            stamps.popleft()
        return len(stamps) < self._limit

    def record(self, session_id: str) -> None:
        self._stamps.setdefault(session_id, deque()).append(self._clock())

    def discard(self, session_id: str) -> None:
        self._stamps.pop(session_id, None)
