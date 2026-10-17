"""
Archive Coordinator for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 4): owns the archive-query block, archive-and-clear
(scheduler-driven) cleanup, and the compaction-boundary/history-distillation
pair. This is a mechanical extraction — SessionCoordinator keeps every
public method as a one-line delegate, and every per-session shared dict
stays constructed on the coordinator; ArchiveCoordinator receives the same
dict objects by reference, never copies.

`backend/legion/archive_manager.py` already owns the real archive mechanics
and `backend/history_distiller.py` already owns the real summarization
work — every method here is already a thin adapter layer on top of those,
unaffected by this move.

Unlike SessionLauncher/MessagePipeline, this is a terminal content
extraction: `_group_resources_by_filename`/`_apply_resource_filters` are
imported directly from `session_resources.py` (their permanent home, PR 5),
and the handful of permanent coordinator methods this module calls
(`_archive_session_for_reset`, `terminate_session`, `_notify_session_reset`,
`register_uploaded_resource`) are all reached directly via
`self._coordinator.X`, same as SessionLauncher's "injected coordinator
methods" passthroughs.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from shared.logging_config import get_logger

from .data_storage import DataStorageManager
from .distillation_trigger import schedule_history_distillation
from .models.messages import LegacyMessageFormatError
from .session_resources import _apply_resource_filters, _group_resources_by_filename

# Get specialized logger for coordinator actions (same category as the
# coordinator's own coord_logger — this is still "coordinator actions" from
# an operator's perspective, just physically relocated).
coord_logger = get_logger('coordinator', category='COORDINATOR')
logger = logging.getLogger(__name__)


class ArchiveCoordinator:
    """Owns archive-query reads, archive-and-clear cleanup, and the
    compaction-boundary/history-distillation pair.
    """

    def __init__(self, *, coordinator: Any):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), not a value
        # snapshotted here at construction time — same discipline as
        # SessionLauncher/MessagePipeline/ToolCallTracker (PR 1-3).
        self._coordinator = coordinator

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    template_manager = property(lambda self: self._coordinator.template_manager)
    profile_manager = property(lambda self: self._coordinator.profile_manager)
    legion_system = property(lambda self: self._coordinator.legion_system)

    # --- Shared per-session state: live passthrough to the coordinator -------
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _task_leg_registries = property(lambda self: self._coordinator._task_leg_registries)

    # Injected coordinator methods — never reimplemented here. All permanent
    # (not pending extraction elsewhere).
    _archive_session_for_reset = property(lambda self: self._coordinator._archive_session_for_reset)
    terminate_session = property(lambda self: self._coordinator.terminate_session)
    _notify_session_reset = property(lambda self: self._coordinator._notify_session_reset)
    register_uploaded_resource = property(lambda self: self._coordinator.register_uploaded_resource)

    # ==================== ARCHIVE METHODS ====================

    async def get_archives(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List archives for a session, paginated."""
        if not self.legion_system:
            return {"archives": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_archives = await self.legion_system.archive_manager.get_archives(session_id)
        total = len(all_archives)
        sliced = all_archives[offset : offset + limit]
        return {
            "archives": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50
    ) -> dict:
        """Read paginated messages from an archive, converted to websocket format."""
        if not self.legion_system:
            return {"messages": [], "total_count": 0, "offset": offset, "has_more": False}
        result = await self.legion_system.archive_manager.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit
        )

        result["messages"] = [msg for msg in result.get("messages", []) if msg]
        return result

    async def get_archive_state(self, session_id: str, archive_id: str) -> dict | None:
        """Read state and metadata from an archive."""
        if not self.legion_system:
            return None
        return await self.legion_system.archive_manager.get_archive_state(session_id, archive_id)

    async def get_archive_resources(
        self,
        session_id: str,
        archive_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ) -> dict:
        """List resource metadata from an archive, paginated with optional filter/sort."""
        if not self.legion_system:
            return {"resources": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_resources = await self.legion_system.archive_manager.get_archive_resources(
            session_id, archive_id
        )

        all_resources = _group_resources_by_filename(all_resources)
        all_resources = _apply_resource_filters(all_resources, search, format_filter, sort)

        total = len(all_resources)
        sliced = all_resources[offset : offset + limit]
        return {
            "resources": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_archive_resource_by_id(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> dict | None:
        """Look up a single archived resource's metadata by ID against the raw (ungrouped) log.

        Issue #1680: mirrors get_session_resource_by_id() for archive byte-serving.
        """
        if not self.legion_system:
            return None
        all_resources = await self.legion_system.archive_manager.get_archive_resources(
            session_id, archive_id
        )
        return next((r for r in all_resources if r.get("resource_id") == resource_id), None)

    async def get_archive_resource_file(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> bytes | None:
        """Get raw file bytes for a resource in an archive."""
        if not self.legion_system:
            return None
        return await self.legion_system.archive_manager.get_archive_resource_file(
            session_id, archive_id, resource_id
        )

    async def list_project_deleted_agents(self, project_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List deleted agents with archives for a project, paginated."""
        if not self.legion_system:
            return {"agents": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_agents = await self.legion_system.archive_manager.list_project_deleted_agents(project_id)
        total = len(all_agents)
        sliced = all_agents[offset : offset + limit]
        return {
            "agents": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def erase_history(self, session_id: str) -> bool:
        """Erase distilled history for a session."""
        if not self.legion_system:
            return False
        return await self.legion_system.archive_manager.erase_history(session_id)

    async def erase_archives(self, session_id: str) -> bool:
        """Erase archives for a session."""
        if not self.legion_system:
            return False
        return await self.legion_system.archive_manager.erase_archives(session_id)

    async def check_history_archives(self, session_id: str) -> dict:
        """Check existence of history and archives for a session."""
        if not self.legion_system:
            return {"has_history": False, "has_archives": False}
        return await self.legion_system.archive_manager.check_history_archives_exist(session_id)

    # ==================== ARCHIVE-AND-CLEAR ====================

    async def archive_and_clear_session(self, session_id: str) -> bool:
        """Archive session data and clear messages, then terminate.

        Used by the scheduler after an ephemeral schedule run completes.
        Archives with the current timestamp (completion time), clears messages,
        and terminates the session — leaving it in TERMINATED state ready for
        the next scheduled fire.

        Unlike reset_session(), this does NOT restart the session after clearing.

        Args:
            session_id: The session to archive and clear

        Returns:
            True if cleanup succeeded
        """
        try:
            coord_logger.info(f"Archive-and-clear session {session_id}")

            # Archive session data (copies messages, state, resources to timestamped dir)
            await self._archive_session_for_reset(session_id)

            # Clear message history
            storage = self._storage_managers.get(session_id)
            if not storage:
                session_dir = await self.session_manager.get_session_directory(session_id)
                storage = DataStorageManager(session_dir)
                await storage.initialize()
                self._storage_managers[session_id] = storage

            if storage:
                await storage.clear_messages()
                coord_logger.info(f"Cleared message history for session {session_id}")

            # Issue #1746: Drop cached task leg registry — it would otherwise
            # keep serving stale legs from before the message history wipe.
            self._task_leg_registries.pop(session_id, None)

            # Notify frontend to clear messages
            await self._notify_session_reset(session_id)

            # Terminate the session (disconnect SDK, set TERMINATED state)
            await self.terminate_session(session_id)

            coord_logger.info(f"Archive-and-clear complete for session {session_id}")
            return True

        except Exception:
            logger.exception(f"Failed to archive-and-clear session {session_id}")
            return False

    # ==================== COMPACTION-TIME HISTORY DISTILLATION ====================

    async def _handle_compact_boundary(self, session_id: str, parsed_message) -> None:
        """Handle a compact_boundary system message by scheduling history distillation.

        Only fires on exact 'compact_boundary' subtype. 'microcompact_boundary' is
        explicitly excluded — it represents a smaller in-context compaction (not a full
        context wipe) and does not warrant a dedicated summary. If a future product
        decision wants microcompactions summarized, extend the detection condition in
        _create_message_callback.

        No-ops when history_distillation_enabled is False in the session's effective
        config, matching the existing archive-time distillation gate.
        """
        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info is None:
                return
            boundary_ts = parsed_message.timestamp
            session_dir = self.session_manager.sessions_dir / session_id
            messages_file = session_dir / "messages.jsonl"
            await schedule_history_distillation(
                session_info,
                self.template_manager,
                self.profile_manager,
                task_coro_factory=lambda: self._distill_compaction(session_id, boundary_ts),
                debug_log_message=(
                    f"Launched compaction-time distillation for session {session_id} "
                    f"at ts={boundary_ts}"
                ),
                launch_check=messages_file.exists,
            )
        except Exception:
            logger.exception(
                f"Failed to schedule compaction distillation for session {session_id}"
            )

    async def _distill_compaction(self, session_id: str, boundary_ts: float) -> None:
        """Distill messages from the previous compaction boundary to this one.

        Scans messages.jsonl for prior compact_boundary entries to determine the
        slice window, writes a temp jsonl for that slice, calls distill_session_history,
        then registers the result as a session resource so the frontend can display it.
        """
        # Deferred import: NOT a circular-import break (confirmed during issue #2189
        # PR 11's AC6 cleanup) — kept late because test_session_coordinator.py's
        # test_issue_1628_distill_compaction_registers_resource patches
        # backend.history_distiller.distill_session_history directly, which only
        # takes effect if this name is looked up fresh per call rather than bound
        # once at module import.
        from backend.history_distiller import distill_session_history

        session_dir = self.session_manager.sessions_dir / session_id
        messages_file = session_dir / "messages.jsonl"

        if not messages_file.exists():
            return

        # Scan for all compact_boundary timestamps to determine slice window.
        boundary_timestamps: list[float] = []
        try:
            with open(messages_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stored_type = row.get("_type")
                    if stored_type:
                        raise LegacyMessageFormatError(session_id, stored_type)
                    if row.get("type") == "system":
                        meta = row.get("metadata") or {}
                        if meta.get("subtype") == "compact_boundary":
                            ts = row.get("timestamp")
                            if ts is not None:
                                boundary_timestamps.append(float(ts))
        except OSError:
            logger.exception(f"Failed to scan messages.jsonl for {session_id}")
            return

        # Determine previous boundary (slice starts at prev_ts, exclusive).
        boundary_timestamps_sorted = sorted(boundary_timestamps)
        prev_ts: float | None = None
        for ts in boundary_timestamps_sorted:
            if ts < boundary_ts:
                prev_ts = ts

        # Write slice to temp file.
        history_dir = session_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = str(int(boundary_ts))
        tmp_path = history_dir / f".compaction_{safe_ts}.tmp.jsonl"
        output_path = history_dir / f"{safe_ts}.md"

        try:
            with open(messages_file, encoding="utf-8") as src, \
                    open(tmp_path, "w", encoding="utf-8") as dst:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        row = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    ts = row.get("timestamp")
                    if ts is None:
                        continue
                    ts_f = float(ts)
                    # Include messages after prev boundary (exclusive) up to and
                    # including this boundary (inclusive).
                    if prev_ts is not None and ts_f <= prev_ts:
                        continue
                    if ts_f > boundary_ts:
                        continue
                    dst.write(stripped + "\n")

            boundary_iso = datetime.fromtimestamp(boundary_ts, tz=UTC).isoformat()
            success = await distill_session_history(
                tmp_path, output_path, session_id, boundary_iso
            )
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        if not success:
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            return

        # Register the markdown file as a session resource for frontend correlation.
        try:
            time_label = datetime.fromtimestamp(boundary_ts, tz=UTC).strftime("%H:%M")
            title = f"Compaction Summary — {time_label}"
            description = f"compaction:{int(boundary_ts)}"
            await self.register_uploaded_resource(
                session_id,
                str(output_path),
                title=title,
                description=description,
            )
            coord_logger.info(
                f"Registered compaction summary resource for session {session_id} at ts={boundary_ts}"
            )
        except Exception:
            logger.exception(
                f"Failed to register compaction summary resource for session {session_id}"
            )
