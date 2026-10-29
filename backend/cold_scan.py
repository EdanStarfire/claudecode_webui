"""Cold-scan pool and per-(session, kind) single-flight (issue #2030, stage 2 PR A, §3.3).

A *cold scan* is an O(file) forward read of a session's ``messages.jsonl`` (the agents,
background-task and task-list snapshot hydrators, and the history index). The rules here keep
those scans from starving the first-paint path or each other:

- There is **no process-wide gate, lock or priority queue**. Coupling between sessions is
  limited to the bounded pool below.
- **Single-flight per (session, kind)**: concurrent requests for the same scan await one
  shielded task. A cancelled caller never cancels the scan, and a waiter may carry its own cap.
- **Per-session ordering only** (``after=``): a scan can chain behind the same session's other
  in-flight kinds through a plain ``await`` on those tasks (never a held lock); their failure
  or cancellation is ignored.
- **Dedicated bounded pool**, separate from the default executor, so the cheap critical-path
  work (``window=tail``, ``before=``, ``latest_init``, warm ``/tasks``) that keeps using
  ``asyncio.to_thread`` can never queue behind a cold scan.
- **Abandonment**: ``invalidate(key)`` (session deleted / reset / stopped) marks every in-flight
  scan of that key cancelled. A queued scan is dropped before it starts; a running scan notices
  the flag between read batches and stops; a cancelled scan is never committed to a cache.
"""

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_COLD_SCAN_WORKERS = 4


def resolve_pool_size(configured: int | None) -> int:
    """Pool size: configured value, else ``min(4, cpu_count)``; always clamped >= 1."""
    if configured is None:
        configured = min(DEFAULT_MAX_COLD_SCAN_WORKERS, os.cpu_count() or 2)
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return max(1, min(DEFAULT_MAX_COLD_SCAN_WORKERS, os.cpu_count() or 2))


class ScanAbandonedError(Exception):
    """The scan's session was deleted/reset/stopped before the scan started."""


class ScanHandle:
    """Per-flight handle given to a scan body: the cancel flag and the pool entry point."""

    def __init__(self, manager: "ColdScanManager", key: str = "", kind: str = ""):
        self._manager = manager
        self.key = key
        self.kind = kind
        self.cancel = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    async def run(self, fn: Callable[[threading.Event], T]) -> T:
        """Run ``fn(cancel_event)`` on the cold-scan pool. A queued job whose flight was
        invalidated is dropped before it starts (raises :class:`ScanAbandonedError`)."""
        cancel = self.cancel
        manager = self._manager

        def job() -> T:
            if cancel.is_set():
                raise ScanAbandonedError()
            manager._enter(self.key, self.kind)
            try:
                return fn(cancel)
            finally:
                manager._exit(self.key, self.kind)

        try:
            pending = asyncio.get_running_loop().run_in_executor(manager._pool, job)
        except RuntimeError as e:  # submit refused: pool already shut down (process cleanup)
            raise ScanAbandonedError() from e
        return await pending


class ColdScanManager:
    """Owns the dedicated pool and the in-flight registry."""

    def __init__(self, workers: int | None = None, name: str = "cold-scan"):
        self.workers = resolve_pool_size(workers)
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix=name)
        self._inflight: dict[tuple[str, str], tuple[asyncio.Task, ScanHandle]] = {}
        # Diagnostics (load test / metrics): scans actually executing in the pool right now.
        self._running = 0
        self.max_running = 0
        self._count_lock = threading.Lock()
        # Per-session concurrency high-water mark (load-test assertion: one session never has
        # two cold scans executing at once through the browser's request order).
        self._running_by_key: dict[str, int] = {}
        self._running_kinds: dict[str, list[str]] = {}
        self.max_running_per_key: dict[str, int] = {}
        # Per-session ordering diagnostics (§3.3 item 2): an index scan executing alongside the
        # same session's snapshot scans, or a snapshot scan that started while its index ran.
        self.index_over_snapshot = 0
        self.snapshot_over_index = 0

    # ------------------------------------------------------------------ introspection

    def inflight_kinds(self, key: str) -> list[str]:
        return [kind for (k, kind) in self._inflight if k == key]

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def inflight_keys(self) -> set[str]:
        return {k for (k, _) in self._inflight}

    # ------------------------------------------------------------------ single flight

    async def single_flight(
        self,
        key: str,
        kind: str,
        body: Callable[[ScanHandle], Awaitable[T]],
        *,
        empty: Callable[[], T],
        after: tuple[str, ...] = (),
        timeout: float | None = None,
    ) -> T:
        """Await the one in-flight scan of ``(key, kind)``, starting it if there is none.

        ``body(handle)`` is the whole flight (pending-buffer setup, ``await handle.run(scan)``,
        cache commit). ``empty()`` is returned to every waiter when the flight was abandoned.
        ``timeout`` caps this *waiter* only (``asyncio.TimeoutError``); the scan keeps running
        and still caches on completion.
        """
        entry = self._inflight.get((key, kind))
        if entry is None:
            handle = ScanHandle(self, key, kind)
            task = asyncio.ensure_future(self._execute(key, kind, handle, body, empty, after))
            task.add_done_callback(_retrieve_exception)
            self._inflight[(key, kind)] = (task, handle)
            entry = (task, handle)
        waiter = asyncio.shield(entry[0])
        if timeout is None:
            return await waiter
        return await asyncio.wait_for(waiter, timeout)

    async def _execute(
        self,
        key: str,
        kind: str,
        handle: ScanHandle,
        body: Callable[[ScanHandle], Awaitable[T]],
        empty: Callable[[], T],
        after: tuple[str, ...],
    ) -> T:
        try:
            if after:
                deps = [
                    entry[0]
                    for dep_kind in after
                    if (entry := self._inflight.get((key, dep_kind))) is not None
                ]
                if deps:
                    # Plain await (asyncio.wait neither raises nor cancels them): per-session
                    # ordering without holding anything across the wait.
                    await asyncio.wait(deps)
            if handle.cancelled:
                return empty()
            result = await body(handle)
            return empty() if handle.cancelled else result
        except ScanAbandonedError:
            return empty()
        finally:
            current = self._inflight.get((key, kind))
            if current is not None and current[1] is handle:
                del self._inflight[(key, kind)]

    def invalidate(self, key: str, kinds: tuple[str, ...] | None = None) -> None:
        """Abandon in-flight scans of ``key`` (all kinds, or just ``kinds``): they are dropped
        before starting / stop at the next batch and never commit. A later request starts a
        fresh flight."""
        for (k, kind), (_, handle) in list(self._inflight.items()):
            if k == key and (kinds is None or kind in kinds):
                handle.cancel.set()
                del self._inflight[(k, kind)]

    # ------------------------------------------------------------------ pool instrumentation

    def _enter(self, key: str, kind: str = "") -> None:
        with self._count_lock:
            running = self._running_kinds.setdefault(key, [])
            if kind == "index" and any(k != "index" for k in running):
                self.index_over_snapshot += 1
            elif kind != "index" and "index" in running:
                self.snapshot_over_index += 1
            running.append(kind)
            self._running += 1
            self.max_running = max(self.max_running, self._running)
            count = self._running_by_key.get(key, 0) + 1
            self._running_by_key[key] = count
            if count > 1:  # only overlaps are recorded: keeps this dict from growing per session
                self.max_running_per_key[key] = max(count, self.max_running_per_key.get(key, 0))

    def _exit(self, key: str, kind: str = "") -> None:
        with self._count_lock:
            running = self._running_kinds.get(key, [])
            if kind in running:
                running.remove(kind)
            if not running:
                self._running_kinds.pop(key, None)
            self._running -= 1
            remaining = self._running_by_key.get(key, 1) - 1
            if remaining <= 0:
                self._running_by_key.pop(key, None)
            else:
                self._running_by_key[key] = remaining

    @property
    def running(self) -> int:
        return self._running

    def shutdown(self) -> None:
        for _, handle in self._inflight.values():
            handle.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def _retrieve_exception(task: asyncio.Task) -> None:
    """Mark a flight's exception retrieved so a fully-abandoned flight never logs
    'Task exception was never retrieved'."""
    if not task.cancelled():
        exc: Any = task.exception()
        if exc is not None:
            logger.debug("cold scan flight failed: %r", exc)
