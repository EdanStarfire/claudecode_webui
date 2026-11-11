"""Per-relay caches shared by unread monitors and DM digests (issue #2103 stage 3c-ii).

A scan of the owner's direct conversations costs one list call plus one info call per conversation;
several monitors due together share it. Only successful results are cached; errors propagate to
the caller (a rate limit stops the run, nothing is cached for it). Single flight per key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from . import constants as const


class TtlCache:
    def __init__(self, mono: Callable[[], float], ttl: float, max_entries: int = 500) -> None:
        self._mono = mono
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    def peek(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is not None and self._mono() - hit[0] < self._ttl:
            return hit[1]
        return None

    async def get(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        cached = self.peek(key)
        if cached is not None:
            return cached
        async with self._lock:  # single flight: a second caller reuses the first one's result
            cached = self.peek(key)
            if cached is not None:
                return cached
            value = await fetch()
            if len(self._data) >= self._max:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (self._mono(), value)
            return value

    def clear(self) -> None:
        self._data.clear()


class ConversationListCache(TtlCache):
    """``users.conversations`` results by type set (TTL 10 min)."""

    def __init__(self, mono: Callable[[], float]) -> None:
        super().__init__(mono, const.LIST_CACHE_SECONDS, 8)


class ConversationInfoCache(TtlCache):
    """``conversations.info`` results by conversation id (TTL 60 s)."""

    def __init__(self, mono: Callable[[], float]) -> None:
        super().__init__(mono, const.INFO_CACHE_SECONDS, 500)
