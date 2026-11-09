"""Token buckets for accepted instructions (issue #2103 stage 3a, loop protection layer 4)."""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(self, per_minute: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._last = clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class KeyedBuckets:
    """One bucket per key (thread), bounded so a flood of keys cannot grow memory."""

    def __init__(self, per_minute: float, burst: int, clock=time.monotonic, max_keys: int = 500):
        self._args = (per_minute, burst, clock)
        self._max = max_keys
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str) -> bool:
        bucket = self._buckets.pop(key, None) or TokenBucket(*self._args)
        self._buckets[key] = bucket  # most recently used last
        while len(self._buckets) > self._max:
            del self._buckets[next(iter(self._buckets))]
        return bucket.allow()
