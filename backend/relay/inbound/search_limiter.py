"""Per-relay spacing between backfill searches (issue #2103 stage 3b-ii). Bounds Tier-2 use of
``search.messages``; the agent's own ``search`` tool is separate (the adapter retries rate limits)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from . import constants as const


class SearchLimiter:
    def __init__(
        self, mono: Callable[[], float], sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        spacing: float = const.SEARCH_MIN_SPACING_SECONDS,
    ) -> None:
        self._mono = mono
        self._sleep = sleep
        self._spacing = spacing
        self._last = -1e9
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Returns once at least ``spacing`` seconds passed since the previous search started."""
        async with self._lock:
            delay = self._spacing - (self._mono() - self._last)
            if delay > 0:
                await self._sleep(delay)
            self._last = self._mono()
