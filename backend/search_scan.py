"""Whole-history search flights (issue #2030, stage 3 PR 3B).

Reuses :class:`backend.cold_scan.ColdScanManager` (single-flight, cancel flag, abandonment, bounded
pool) on a SEPARATE pool, so a search can never queue behind a cold index/snapshot scan nor block
the default executor that serves the critical-path reads. No process-wide lock or queue: coupling
between sessions is limited to the small pool.

Rules (per key = session, or ``<sid>/archive/<aid>``):

- **Single-flight** per (key, normalized query, cursor): identical concurrent requests share one
  scan, whichever client sent them.
- **Supersede per (key, client)**: a request carrying a ``client_id`` (a per-tab random id) for a
  DIFFERENT query supersedes that client's older waiters on the key (they get ``None``); other
  clients' waiters, and requests without a ``client_id``, are never superseded. A shared scan stops
  only when its last waiter is gone. Continuation pages of the same query never supersede.
- **Flight cap per key** (``max_flights_per_key``): more distinct live search flights on one key are
  refused with :class:`SearchBusyError` (503 ``search_busy``).
- **Disconnect / waiter cap**: the last waiter leaving a flight (client disconnect, handler
  cancel, or the waiter cap ``max_wall_s + 2 s`` expiring, which raises :class:`SearchBusyError`)
  cancels the scan; a queued job whose flight was cancelled never starts.
- **Abandonment**: ``invalidate(key)`` (session deleted / reset) cancels every flight of the key (a
  stop/terminate does not: the history file is unchanged); nothing is cached anywhere (results are cheap to recompute, so they cannot go stale).
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
DEFAULT_MAX_FLIGHTS_PER_KEY = 2
WAITER_SLACK_S = 2.0
_DISCONNECT_POLL_S = 0.2
_KIND_PREFIX = "search:"


class SearchBusyError(Exception):
    """No search slot freed up within the waiter cap (route answers 503 ``search_busy``)."""


class _Waiter:
    __slots__ = ("client_id", "superseded")

    def __init__(self, client_id: str | None):
        self.client_id = client_id
        self.superseded = asyncio.Event()


class SearchScanManager:
    def __init__(self, workers: int | None = DEFAULT_SEARCH_WORKERS,
                 max_flights_per_key: int = DEFAULT_MAX_FLIGHTS_PER_KEY):
        self._scans = ColdScanManager(workers, name="history-search")
        self.max_flights_per_key = max(1, int(max_flights_per_key or 1))
        self._waiters: dict[tuple[str, str], list[_Waiter]] = {}
        # Diagnostics (tests / load harness).
        self.superseded = 0
        self.disconnected = 0
        self.timed_out = 0
        self.refused = 0

    # ------------------------------------------------------------------ introspection

    @property
    def workers(self) -> int:
        return self._scans.workers

    @property
    def inflight_count(self) -> int:
        return self._scans.inflight_count

    @property
    def waiter_count(self) -> int:
        return sum(len(w) for w in self._waiters.values())

    @property
    def max_running(self) -> int:
        return self._scans.max_running

    @property
    def running(self) -> int:
        return self._scans.running

    def inflight_keys(self) -> set[str]:
        return self._scans.inflight_keys()

    # ------------------------------------------------------------------ search

    def _live_flights(self, key: str) -> set[str]:
        """Kinds of the key's search flights that still have a non-superseded waiter."""
        return {
            kind for (k, kind), waiters in self._waiters.items()
            if k == key and any(not w.superseded.is_set() for w in waiters)
        }

    async def search(
        self,
        key: str,
        reader: Any,
        query: str,
        cursor: str | None,
        caps: SearchCaps,
        *,
        client_id: str | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> SearchPage | None:
        """One search page, or ``None`` when this request was superseded / abandoned / the client
        went away. Raises ``SearchQueryError`` (bad query), ``CursorInvalidError`` (bad cursor),
        :class:`SearchBusyError` (flight cap / waiter cap)."""
        matcher = QueryMatcher(query)  # validates before any scheduling
        qhash = hashlib.sha1(matcher.key.encode()).hexdigest()[:12]
        kind = f"{_KIND_PREFIX}{qhash}:{hashlib.sha1((cursor or '').encode()).hexdigest()[:12]}"

        client_id = client_id[:64] if client_id else None
        stale = [  # this client's older queries on the key (nobody else's) that this one replaces
            w for (k, other_kind), waiters in self._waiters.items()
            if client_id and k == key and not other_kind.startswith(f"{_KIND_PREFIX}{qhash}:")
            for w in waiters if w.client_id == client_id and not w.superseded.is_set()
        ]
        # Cap check first (a refused request must not cancel the client's older query); the
        # flights this request is about to supersede do not count against the cap.
        live = {
            k for k in self._live_flights(key)
            if any(not w.superseded.is_set() and w not in stale for w in self._waiters[(key, k)])
        }
        if kind not in live and len(live) >= self.max_flights_per_key:
            self.refused += 1
            raise SearchBusyError(key)
        for w in stale:
            w.superseded.set()
        self.superseded += len(stale)

        async def body(handle) -> SearchPage:
            return await handle.run(lambda cancel: reader.search(query, cursor, caps, cancel))

        slot = (key, kind)
        waiter = _Waiter(client_id)
        self._waiters.setdefault(slot, []).append(waiter)
        flight = asyncio.ensure_future(
            self._scans.single_flight(
                key, kind, body, empty=lambda: None, timeout=caps.max_wall_s + WAITER_SLACK_S,
            )
        )
        watcher = asyncio.ensure_future(self._watch(is_disconnected)) if is_disconnected else None
        replaced = asyncio.ensure_future(waiter.superseded.wait())
        try:
            done, _ = await asyncio.wait(
                {flight, replaced} | ({watcher} if watcher else set()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if flight not in done:
                if replaced not in done:
                    self.disconnected += 1
                return None
            try:
                page = flight.result()
            except TimeoutError as e:
                self.timed_out += 1
                raise SearchBusyError(key) from e
            return None if page is None or page.cancelled or waiter.superseded.is_set() else page
        except asyncio.CancelledError:
            # The HTTP handler was cancelled (the framework cancels it when the client goes
            # away): the `finally` below stops the scan if no other waiter remains.
            self.disconnected += 1
            raise
        finally:
            for task in (watcher, replaced):
                if task is not None:
                    task.cancel()
            if not flight.done():
                flight.cancel()
            waiters = self._waiters.get(slot, [])
            if waiter in waiters:
                waiters.remove(waiter)
            if not waiters:
                self._waiters.pop(slot, None)
                # Last waiter gone (disconnect / supersede / waiter cap / error): stop the scan.
                self._scans.invalidate(key, kinds=(kind,))

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
