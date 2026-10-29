"""Whole-history search flights (issue #2030, stage 3 PR 3B).

Reuses :class:`backend.cold_scan.ColdScanManager` (single-flight, cancel flag, abandonment, bounded
pool) on a SEPARATE pool, so a search can never queue behind a cold index/snapshot scan nor block
the default executor that serves the critical-path reads. No process-wide lock or queue: coupling
between sessions is limited to the small pool.

Rules (per key = session, or ``<sid>/archive/<aid>``):

- **Single-flight** per (key, normalized query, cursor): identical concurrent requests share one scan.
- **Supersede**: starting a search for a *different* query cancels the key's older search flights
  (their waiters get ``None``). Continuation pages of the same query do not cancel each other.
- **Disconnect / waiter cap**: the last waiter leaving a flight (client disconnect, or the waiter
  cap ``max_wall_s + 2 s`` expiring, which raises :class:`SearchBusyError`) cancels the scan; a
  queued job whose flight was cancelled never starts.
- **Abandonment**: ``invalidate(key)`` (session deleted / reset / stopped) cancels every flight of
  the key; nothing is cached anywhere (results are cheap to recompute, so they cannot go stale).
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .cold_scan import ColdScanManager
from .history_search import QueryMatcher, SearchCaps, SearchPage

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_WORKERS = 2
WAITER_SLACK_S = 2.0
_DISCONNECT_POLL_S = 0.2
_KIND_PREFIX = "search:"


class SearchBusyError(Exception):
    """No search slot freed up within the waiter cap (route answers 503 ``search_busy``)."""


class SearchScanManager:
    def __init__(self, workers: int | None = DEFAULT_SEARCH_WORKERS):
        self._scans = ColdScanManager(workers, name="history-search")
        self._waiters: dict[tuple[str, str], int] = {}
        # Diagnostics (tests / load harness).
        self.superseded = 0
        self.disconnected = 0
        self.timed_out = 0

    # ------------------------------------------------------------------ introspection

    @property
    def workers(self) -> int:
        return self._scans.workers

    @property
    def inflight_count(self) -> int:
        return self._scans.inflight_count

    @property
    def waiter_count(self) -> int:
        return len(self._waiters)

    @property
    def max_running(self) -> int:
        return self._scans.max_running

    @property
    def running(self) -> int:
        return self._scans.running

    def inflight_keys(self) -> set[str]:
        return self._scans.inflight_keys()

    # ------------------------------------------------------------------ search

    async def search(
        self,
        key: str,
        reader: Any,
        query: str,
        cursor: str | None,
        caps: SearchCaps,
        *,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> SearchPage | None:
        """One search page, or ``None`` when the flight was superseded / abandoned / the client
        went away. Raises ``SearchQueryError`` (bad query), ``CursorInvalidError`` (bad cursor),
        :class:`SearchBusyError` (waiter cap)."""
        matcher = QueryMatcher(query)  # validates before any scheduling
        qhash = hashlib.sha1(matcher.key.encode()).hexdigest()[:12]
        kind = f"{_KIND_PREFIX}{qhash}:{hashlib.sha1((cursor or '').encode()).hexdigest()[:12]}"

        stale = tuple(
            k for k in self._scans.inflight_kinds(key)
            if k.startswith(_KIND_PREFIX) and not k.startswith(f"{_KIND_PREFIX}{qhash}:")
        )
        if stale:
            self.superseded += len(stale)
            self._scans.invalidate(key, kinds=stale)

        async def body(handle) -> SearchPage:
            return await handle.run(lambda cancel: reader.search(query, cursor, caps, cancel))

        slot = (key, kind)
        self._waiters[slot] = self._waiters.get(slot, 0) + 1
        flight = asyncio.ensure_future(
            self._scans.single_flight(
                key, kind, body, empty=lambda: None, timeout=caps.max_wall_s + WAITER_SLACK_S,
            )
        )
        watcher = asyncio.ensure_future(self._watch(is_disconnected)) if is_disconnected else None
        try:
            done, _ = await asyncio.wait(
                {flight} | ({watcher} if watcher else set()), return_when=asyncio.FIRST_COMPLETED
            )
            if flight not in done:
                self.disconnected += 1
                return None
            try:
                page = flight.result()
            except TimeoutError as e:
                self.timed_out += 1
                raise SearchBusyError(key) from e
            return None if page is None or page.cancelled else page
        except asyncio.CancelledError:
            # The HTTP handler was cancelled (the framework cancels it when the client goes
            # away): the `finally` below stops the scan if no other waiter remains.
            self.disconnected += 1
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
            if not flight.done():
                flight.cancel()
            remaining = self._waiters.get(slot, 1) - 1
            if remaining <= 0:
                self._waiters.pop(slot, None)
                # Last waiter gone (disconnect / waiter cap / error): stop the scan.
                self._scans.invalidate(key, kinds=(kind,))
            else:
                self._waiters[slot] = remaining

    @staticmethod
    async def _watch(is_disconnected: Callable[[], Awaitable[bool]]) -> None:
        while True:
            await asyncio.sleep(_DISCONNECT_POLL_S)
            try:
                if await is_disconnected():
                    return
            except Exception:
                logger.debug("is_disconnected failed; treating the client as still connected",
                             exc_info=True)

    # ------------------------------------------------------------------ lifecycle

    def invalidate(self, key: str) -> None:
        """Session deleted / reset / stopped: abandon every search flight of ``key``."""
        self._scans.invalidate(key)

    def shutdown(self) -> None:
        self._scans.shutdown()
