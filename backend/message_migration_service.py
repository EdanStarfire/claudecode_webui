"""
MessageMigrationService - Background asyncio service for issue #2084 stage 3-C.

Ticks on a fixed interval, picks one legacy (message_schema_version == 0, never
migrated) session, and runs it through message_migration.py's streaming driver.
One session per tick — deliberately conservative, avoiding a startup thundering
herd across every legacy session at once; bounded memory/CPU, revisitable for
throughput later without any data-shape change.

Not Legion-specific (every session, minion or not, can predate the canonical
MessageRecord schema) — lives alongside session_watchdog.py rather than under
backend/legion/, the same "universal background service" shape SchedulerService
uses for its loop but without SchedulerService's Legion-only scope.

`migrate_one()` is shared by the background tick and the on-demand trigger (§6):
`SessionManager.try_claim_message_migration()` is the race-free decision of
*whether* to migrate (atomic under the session's own lock); `DataStorageManager.
_write_lock` (acquired inside message_migration.migrate_session_messages()) is
the separate concern of serializing the actual file rewrite against a live
append — not against a second migration attempt, which the claim step already
rules out.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from .message_migration import migrate_session_messages
from .session_manager import SessionManager

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

migration_logger = get_logger('migration', category='MIGRATION')
logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30  # matches SchedulerService's cadence — not latency-sensitive


class MessageMigrationService:
    """Background service that migrates legacy sessions to canonical message shape."""

    def __init__(self, coordinator: "SessionCoordinator", session_manager: SessionManager):
        self._coordinator = coordinator
        self._session_manager = session_manager
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        migration_logger.info("MessageMigrationService started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        migration_logger.info("MessageMigrationService stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Message migration tick error")
            try:
                await asyncio.sleep(TICK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        candidate = await self._pick_candidate()
        if candidate is None:
            return
        await self.migrate_one(candidate)

    async def _pick_candidate(self) -> str | None:
        """First never-started legacy session found among loaded sessions.

        Uses the public list_sessions() rather than reaching into
        SessionManager's private _active_sessions dict directly.
        """
        for info in await self._session_manager.list_sessions():
            if info.message_schema_version != 0:
                continue
            if info.message_migration_status is not None:
                continue
            return info.session_id
        return None

    async def migrate_one(self, session_id: str) -> bool:
        """Migrate exactly one session if not already claimed/completed.

        Returns True if this call actually ran a migration (claimed it), False if
        another caller already owns/owned it (no-op) or the session no longer
        qualifies. Safe to call from both the background tick and the on-demand
        trigger for the same session concurrently.
        """
        claimed = await self._session_manager.try_claim_message_migration(session_id)
        if not claimed:
            return False

        session_info = await self._session_manager.get_session_info(session_id)
        storage = await self._coordinator.get_or_create_storage_manager(session_id)
        if not session_info or not storage:
            await self._session_manager.quarantine_message_migration(
                session_id, "session info or storage manager unavailable"
            )
            return True

        try:
            result = await migrate_session_messages(
                storage.session_dir,
                session_id,
                session_info.state,
                self._coordinator._convert_legacy_record_to_websocket,
                storage._write_lock,
            )
        except Exception as exc:
            migration_logger.exception(f"Migration failed for session {session_id}")
            await self._session_manager.quarantine_message_migration(session_id, str(exc))
            return True

        await self._session_manager.complete_message_migration(
            session_id, result.materialized_tool_calls
        )
        migration_logger.info(
            f"Migrated session {session_id}: {result.line_count} lines, "
            f"{result.materialized_tool_calls} materialized tool_call records"
        )
        return True
