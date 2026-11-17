"""Backend-startup recovery and drain of the pending delivery ledger (issue #2362).

``initialize()`` loads every session's ledger through ``pending_recovery`` (before the relay inbound
service starts, so its ``get_item()`` dedupe sees replayed items); ``arm()`` is the LAST init step: it
audits the relay monitor envelopes cancelled at load and starts one background boot drain. The drain
arms each session's when-idle loop sequentially (``BOOT_DRAIN_BATCH`` at once, ``BOOT_DRAIN_SPACING_SECONDS``
between starts), honoring ``comm.auto_start``. ``queue_paused`` sessions arm on resume
(``pause_queue(False)``); PAUSED / ERROR sessions are deferred until their state changes
(``on_state_change``). Never blocks startup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .models.legion_models import DeliveryPolicy
from .pending_recovery import (
    BOOT_DRAIN_BATCH,
    BOOT_DRAIN_SPACING_SECONDS,
    RecoveryReport,
    expire_stale,
    recover_session,
)
from .relay.inbound.monitor_delivery import classify_replayed, replay_ttl
from .session_lifecycle import is_auto_startable, is_errored, is_paused
from .timestamp_utils import get_unix_timestamp

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)


def _deferred_state(state) -> bool:
    """PAUSED (dead SDK after boot) and ERROR sessions must not be drained yet."""
    return is_paused(state) or is_errored(state)


class PendingBoot:
    def __init__(self, coordinator: SessionCoordinator) -> None:
        self._coordinator = coordinator
        self._recovery: list[RecoveryReport] = []
        self._drain_task: asyncio.Task | None = None
        self._deferred: set[str] = set()
        self._arm_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        """Recover every session's ledger. One session's failure never aborts the others."""
        coord = self._coordinator
        self._recovery = []
        try:
            sessions = await coord.session_manager.list_sessions()
        except Exception:
            logger.exception("Failed to list sessions for pending recovery")
            return
        now = get_unix_timestamp()
        for session in sessions:
            try:
                session_dir = await coord.session_manager.get_session_directory(session.session_id)
                if not session_dir:
                    continue
                report = await recover_session(
                    coord.pending_store, session.session_id, session_dir, now=now,
                    classify=classify_replayed, ttl_override=replay_ttl,
                )
                self._recovery.append(report)
                if report.failed_not_replayed or report.expired or report.cancelled:
                    logger.info(
                        f"Pending recovery for {session.session_id}: {len(report.kept)} kept, "
                        f"{len(report.failed_not_replayed)} not replayed, {len(report.expired)} "
                        f"expired, {len(report.cancelled)} cancelled"
                    )
            except Exception:
                logger.exception(f"Pending recovery failed for session {session.session_id}")

    async def arm(self) -> None:
        """Flush the replay-cancel audit rows, then start the staggered boot drain."""
        reports, self._recovery = self._recovery, []
        try:
            await self._audit_cancellations(reports)
            candidates = []
            for report in reports:
                pending = self._when_idle(report.session_id)
                if pending:
                    candidates.append((min(i.created_at for i in pending), report.session_id))
            if candidates:
                order = [sid for _, sid in sorted(candidates)]
                self._drain_task = asyncio.create_task(self._run_drain(order), name="pending-boot-drain")
        except Exception:
            logger.exception("Failed to arm pending drains")

    async def stop(self) -> None:
        tasks = [t for t in (self._drain_task, *self._arm_tasks) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)  # children are awaited by their parents
        self._coordinator.delivery_gate.stop_all_when_idle()

    def on_state_change(self, session_id: str, new_state) -> None:
        """A deferred session left PAUSED / ERROR: arm it in a background task (arming may start
        the session and wait for readiness, which must not block the state-change chain)."""
        # A stopped session (TERMINATED / CREATED) stays deferred: never auto-start one the owner stopped.
        if session_id not in self._deferred or _deferred_state(new_state) or is_auto_startable(new_state):
            return
        self._deferred.discard(session_id)
        task = asyncio.create_task(self._arm_deferred(session_id), name=f"pending-arm-{session_id}")
        self._arm_tasks.add(task)
        task.add_done_callback(self._arm_tasks.discard)

    async def _arm_deferred(self, session_id: str) -> None:
        try:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if info is None or getattr(info, "queue_paused", False):
                return
            await self._arm_session(session_id)
        except Exception:
            logger.exception(f"Deferred pending drain failed for session {session_id}")

    def _when_idle(self, session_id: str) -> list:
        return [
            i for i in self._coordinator.pending_store.get_pending(session_id)
            if i.comm.delivery == DeliveryPolicy.WHEN_IDLE
        ]

    async def _audit_cancellations(self, reports: list[RecoveryReport]) -> None:
        """One relay audit row per monitor envelope cancelled at load (no message text)."""
        coord = self._coordinator
        for report in reports:
            for item in report.cancelled:
                relay_id = item.comm.sender_id
                if not relay_id:
                    continue
                meta = (item.comm.metadata or {}).get("relay_inbound") or {}
                try:
                    fields: dict[str, Any] = {}
                    info = await coord.session_manager.get_session_info(report.session_id)
                    if info is not None:
                        fields["session_name"] = getattr(info, "name", None)
                        fields["handle"] = (getattr(info, "config", None) or {}).get("relay_handle")
                    await coord.relay.audit.record(
                        relay_id, event="monitor_replay_cancelled", outcome="cancelled",
                        code="backend_restart", session_id=report.session_id,
                        envelope_id=item.comm.comm_id, count=meta.get("count"),
                        omitted=meta.get("omitted"), **fields,
                    )
                except Exception:
                    logger.exception("Failed to audit replay-cancelled monitor envelope")

    async def _run_drain(self, session_ids: list[str]) -> None:
        gate = asyncio.Semaphore(BOOT_DRAIN_BATCH)
        tasks: set[asyncio.Task] = set()

        async def _arm(session_id: str) -> None:
            try:
                await self._arm_session(session_id)
            except Exception:
                logger.exception(f"Boot drain arm failed for session {session_id}")
            finally:
                gate.release()

        try:
            for session_id in session_ids:
                info = await self._coordinator.session_manager.get_session_info(session_id)
                if info is None:
                    continue
                if getattr(info, "queue_paused", False):
                    # the loop idles (no start) while paused and resumes by itself on ANY resume path
                    self._coordinator.delivery_gate.ensure_when_idle_running(session_id)
                    continue
                if _deferred_state(info.state):
                    self._deferred.add(session_id)
                    info = await self._coordinator.session_manager.get_session_info(session_id)
                    if info is None or _deferred_state(info.state):
                        continue  # still deferred: on_state_change arms it
                    self._deferred.discard(session_id)  # it moved while we were adding it
                await gate.acquire()
                task = asyncio.create_task(_arm(session_id))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                if is_auto_startable(info.state):
                    await asyncio.sleep(BOOT_DRAIN_SPACING_SECONDS)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _arm_session(self, session_id: str) -> None:
        """Bring a session up (only if a pending item asked for auto_start) and start its drain."""
        coord = self._coordinator
        session_dir = await coord.session_manager.get_session_directory(session_id)
        if session_dir:
            await expire_stale(
                coord.pending_store, session_id, session_dir, now=get_unix_timestamp(),
                ttl_override=replay_ttl,
            )
        pending = self._when_idle(session_id)
        if not pending:
            return
        gate = coord.delivery_gate
        if any(i.comm.auto_start for i in pending):
            result = await gate.ensure_session_active(session_id, auto_start=True)
            if not result.success:
                logger.warning(f"Boot drain: session {session_id} not ready ({result.error}); arming anyway")
        gate.ensure_when_idle_running(session_id)
