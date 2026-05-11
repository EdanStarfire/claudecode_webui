"""
HistoryRotator — background service that prunes schedule_history.jsonl to a
configurable retention window while preserving aggregate metrics.

Rotation is triggered by:
  1. Periodic timer (rotation_interval_seconds, default 300s).
  2. Append-count threshold (rotation_trigger_count, default 100 appends).

File I/O runs in asyncio.to_thread so the event loop is never blocked.
Atomicity is guaranteed by write-to-tmp + os.replace.

Per-legion asyncio.Lock is shared with SchedulerService._append_execution so
appends cannot interleave with a rotation that is mid-rewrite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config_manager import HistoryRetentionConfig
    from src.legion_system import LegionSystem

logger = logging.getLogger(__name__)


class HistoryRotator:
    """Background service for pruning schedule_history.jsonl per legion."""

    def __init__(self, system: LegionSystem, config: HistoryRetentionConfig):
        self.system = system
        self.config = config
        self._task: asyncio.Task | None = None
        self._running = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending: set[str] = set()  # legion IDs queued but not yet processed
        self._locks: dict[str, asyncio.Lock] = {}  # per-legion lock
        self._last_rotation: dict[str, float] = {}  # legion_id -> monotonic time

    # ── Public API ──

    def appender_lock(self, legion_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-legion lock shared with the appender."""
        if legion_id not in self._locks:
            self._locks[legion_id] = asyncio.Lock()
        return self._locks[legion_id]

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("HistoryRotator disabled by config — rotation skipped")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("HistoryRotator started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("HistoryRotator stopped")

    async def request_rotation(self, legion_id: str) -> None:
        """Queue a rotation for legion_id (no-op if already queued or disabled)."""
        if not self.config.enabled:
            return
        if legion_id not in self._pending:
            self._pending.add(legion_id)
            await self._queue.put(legion_id)

    # ── Internal loop ──

    async def _loop(self) -> None:
        last_periodic = asyncio.get_event_loop().time()
        while self._running:
            try:
                elapsed = asyncio.get_event_loop().time() - last_periodic
                remaining = max(0.1, self.config.rotation_interval_seconds - elapsed)
                wait = min(remaining, 10.0)  # check queue at most every 10s

                try:
                    legion_id = await asyncio.wait_for(self._queue.get(), timeout=wait)
                    self._pending.discard(legion_id)
                    await self.rotate_legion(legion_id)
                except TimeoutError:
                    pass

                # Periodic rotation pass
                now = asyncio.get_event_loop().time()
                if now - last_periodic >= self.config.rotation_interval_seconds:
                    last_periodic = now
                    await self._periodic_rotation()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("HistoryRotator loop error: %s", e)

    async def _periodic_rotation(self) -> None:
        data_dir = self.system.session_coordinator.data_dir
        legions_dir = data_dir / "legions"
        if not legions_dir.exists():
            return
        for legion_dir in legions_dir.iterdir():
            if legion_dir.is_dir():
                await self.request_rotation(legion_dir.name)

    # ── Rotation ──

    async def rotate_legion(self, legion_id: str) -> None:
        """Rotate schedule_history.jsonl for one legion under the per-legion lock."""
        lock = self.appender_lock(legion_id)
        async with lock:
            started = time.monotonic()
            pruned = await asyncio.to_thread(self._rotate_sync, legion_id)
            if pruned > 0:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "HistoryRotator: legion=%s pruned=%d duration_ms=%d",
                    legion_id,
                    pruned,
                    elapsed_ms,
                )

    def _rotate_sync(self, legion_id: str) -> int:
        """Synchronous rotation work (runs inside asyncio.to_thread).

        Returns the number of pruned entries (0 = no-op).
        """
        from src.models.schedule_models import ScheduleExecution

        data_dir = self.system.session_coordinator.data_dir
        legion_dir = data_dir / "legions" / legion_id
        history_file = legion_dir / "schedule_history.jsonl"

        if not history_file.exists():
            return 0

        # Read all entries
        entries: list[ScheduleExecution] = []
        try:
            with open(history_file) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(ScheduleExecution.from_dict(json.loads(line)))
                    except Exception:
                        logger.warning(
                            "HistoryRotator: skipping malformed line in %s", history_file
                        )
        except Exception as e:
            logger.error("HistoryRotator: failed to read %s: %s", history_file, e)
            return 0

        before = len(entries)
        if before == 0:
            return 0

        # Retention window: keep entries satisfying EITHER condition
        cutoff = datetime.now(UTC).timestamp() - self.config.max_age_days * 86400
        by_age = {e.execution_id for e in entries if e.actual_time >= cutoff}
        by_count = {e.execution_id for e in entries[-self.config.max_entries :]}
        keep_ids = by_age | by_count

        retained = [e for e in entries if e.execution_id in keep_ids]
        after = len(retained)
        pruned = before - after

        if pruned == 0:
            return 0

        # Atomically rewrite history file
        tmp_history = history_file.with_suffix(".tmp")
        try:
            with open(tmp_history, "w") as fh:
                for e in retained:
                    fh.write(json.dumps(e.to_dict()) + "\n")
            os.replace(tmp_history, history_file)
        except Exception as e:
            logger.error("HistoryRotator: failed to rewrite %s: %s", history_file, e)
            tmp_history.unlink(missing_ok=True)
            return 0

        # Seed metrics file if absent (migration path for pre-existing histories)
        metrics_file = legion_dir / "schedule_metrics.json"
        if not metrics_file.exists():
            self._seed_metrics_sync(legion_id, entries, metrics_file)

        return pruned

    def _seed_metrics_sync(self, legion_id: str, entries, metrics_file) -> None:
        """Build a metrics snapshot from raw entries and write it atomically."""
        from src.models.schedule_models import ScheduleMetrics, is_error_status

        cache: dict[str, ScheduleMetrics] = {}
        for ex in entries:
            if ex.status == "retry":
                continue
            sid = ex.schedule_id
            if sid not in cache:
                cache[sid] = ScheduleMetrics(schedule_id=sid)
            m = cache[sid]
            m.last_run = ex.actual_time
            m.last_status = ex.status
            if is_error_status(ex.status):
                m.total_runs += 1
                m.total_errors += 1
                m.consecutive_errors += 1
                m.last_error_time = ex.actual_time
                m.last_error_message = ex.error_message
            else:
                m.total_runs += 1
                m.consecutive_errors = 0
                m.last_success_time = ex.actual_time

        tmp = metrics_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({sid: m.to_dict() for sid, m in cache.items()}, indent=2))
            os.replace(tmp, metrics_file)
        except Exception as e:
            logger.error("HistoryRotator: failed to seed metrics for %s: %s", legion_id, e)
            tmp.unlink(missing_ok=True)
