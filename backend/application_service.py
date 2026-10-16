"""ApplicationService: service layer between HTTP routes and SessionCoordinator.

Routes call only this class. This class calls coordinator public API
and coordinator sub-managers. No route handler should access
coordinator attributes directly.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shared.event_queue import EventQueue
from shared.git_restart import run_git_command as _run_git_command_impl

from .secret_usage import compute_secret_usage, empty_usage
from .slug_utils import slugify_secret

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator


class NoRefreshTokenError(Exception):
    """Issue #1871: raised when a standalone guided-authorization flow completes but
    the provider didn't issue a refresh_token. No secret is created in this case —
    an oauth2 secret without a refresh token can't support proactive background
    refresh, so surfacing this distinctly (instead of a generic failure) lets the UI
    explain why and what to try (e.g. requesting an offline-access scope)."""


def _compute_secret_health(refresh: dict | None) -> str:
    """Issue #1387: Derive token health state from refresh metadata.

    Returns one of: "valid", "expiring_soon", "expired", "refresh_failed".
    """
    if not refresh:
        return "valid"

    if refresh.get("last_refresh_error"):
        return "refresh_failed"

    expires_at_str = refresh.get("expires_at")
    if not expires_at_str:
        return "valid"

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        buffer = int(refresh.get("buffer_seconds", 300))
        now = datetime.now(tz=UTC)
        if now >= expires_at:
            return "expired"
        if now >= expires_at - timedelta(seconds=buffer):
            return "expiring_soon"
        return "valid"
    except Exception:
        return "valid"


class ApplicationService:
    """Service layer between HTTP routes and SessionCoordinator.

    Routes call only this class. This class calls coordinator public API
    and coordinator sub-managers. No route handler should access
    coordinator attributes directly.
    """

    def __init__(self, coordinator: SessionCoordinator) -> None:
        self.coordinator = coordinator
        # Issue #2189 (router-boundary PR A, cluster 7): relocated from BackendApp —
        # pure request-rate-limiting state for /api/system/restart, unrelated to
        # session/domain state, so coordinator has no reason to know about it.
        self._last_restart_time: float = 0

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def get_session_object(self, session_id: str):
        """Return raw SessionInfo object for attribute access."""
        return await self.coordinator.session_manager.get_session_info(session_id)

    def get_session_recording_enabled(self) -> bool:
        return self.coordinator.session_recording_enabled

    def get_session_manager(self):
        """Return the SessionManager instance (issue #2189 router-boundary PR A) —
        some callers (e.g. AuditQueryService, analytics row enrichment) need the
        manager object itself rather than one specific method."""
        return self.coordinator.session_manager

    # =========================================================================
    # Projects
    # =========================================================================

    async def create_project(
        self,
        name: str,
        working_directory: str,
        is_multi_agent: bool = False,
        max_concurrent_minions: int = 5,
        template_id: str | None = None,
    ) -> dict:
        project = await self.coordinator.project_manager.create_project(
            name=name,
            working_directory=working_directory,
            max_concurrent_minions=max_concurrent_minions,
        )
        return project.to_dict()

    async def list_projects(self, limit: int = 200, offset: int = 0) -> dict:
        projects = await self.coordinator.project_manager.list_projects()
        all_projects = [p.to_dict() for p in projects]
        total = len(all_projects)
        sliced = all_projects[offset : offset + limit]
        return {
            "projects": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_project(self, project_id: str) -> dict | None:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return None
        sessions = await self.coordinator.session_manager.get_sessions_by_ids(project.session_ids)
        result = project.to_dict()
        result["sessions"] = [s.to_dict() for s in sessions]
        return result

    async def reorder_projects(self, project_ids: list[str]) -> bool:
        return await self.coordinator.project_manager.reorder_projects(project_ids)

    async def update_project(self, project_id: str, **kwargs) -> dict | None:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return None
        success = await self.coordinator.project_manager.update_project(project_id, **kwargs)
        if not success:
            return None
        updated = await self.coordinator.project_manager.get_project(project_id)
        return updated.to_dict() if updated else None

    async def delete_project(self, project_id: str) -> dict:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return {"success": False, "error": "not_found"}
        success = await self.coordinator.project_manager.delete_project(project_id)
        return {"success": success}

    async def toggle_project_expansion(self, project_id: str) -> dict | None:
        success = await self.coordinator.project_manager.toggle_expansion(project_id)
        if not success:
            return None
        project = await self.coordinator.project_manager.get_project(project_id)
        return project.to_dict() if project else None

    async def reorder_project_sessions(self, project_id: str, session_ids: list[str]) -> dict | None:
        success = await self.coordinator.project_manager.reorder_project_sessions(
            project_id=project_id, session_ids=session_ids
        )
        if not success:
            return None
        await self.coordinator.session_manager.reorder_sessions(session_ids)
        project = await self.coordinator.project_manager.get_project(project_id)
        return project.to_dict() if project else None

    async def create_kanban_group(self, project_id: str, name: str) -> dict | None:
        project = await self.coordinator.project_manager.create_kanban_group(project_id, name)
        return project.to_dict() if project else None

    async def rename_kanban_group(self, project_id: str, group_id: str, name: str) -> dict | None:
        project = await self.coordinator.project_manager.rename_kanban_group(project_id, group_id, name)
        return project.to_dict() if project else None

    async def delete_kanban_group(self, project_id: str, group_id: str) -> dict | None:
        project = await self.coordinator.project_manager.delete_kanban_group(project_id, group_id)
        return project.to_dict() if project else None

    async def reorder_kanban_groups(self, project_id: str, group_ids: list[str]) -> dict | None:
        project = await self.coordinator.project_manager.reorder_kanban_groups(project_id, group_ids)
        return project.to_dict() if project else None

    async def assign_session_kanban_group(
        self, project_id: str, session_id: str, group_id: str | None
    ) -> dict | None:
        project = await self.coordinator.project_manager.assign_session_to_group(
            project_id, session_id, group_id
        )
        return project.to_dict() if project else None

    async def validate_project_exists(self, project_id: str) -> bool:
        project = await self.coordinator.project_manager.get_project(project_id)
        return project is not None

    # =========================================================================
    # Fleet control (issue #2159 stage 6c; relocated to service in #2189 PR A)
    # =========================================================================

    async def emergency_halt_all(self, legion_id: str) -> dict:
        return await self.coordinator.legion_system.legion_coordinator.emergency_halt_all(legion_id)

    async def resume_all_minions(self, legion_id: str) -> dict:
        return await self.coordinator.legion_system.legion_coordinator.resume_all(legion_id)

    async def get_fleet_snapshot(self, legion_id: str) -> dict | None:
        return await self.coordinator.project_manager.get_fleet_snapshot(legion_id)

    async def record_fleet_stopped(
        self, legion_id: str, session_ids: list[str], processing_session_ids: list[str]
    ):
        return await self.coordinator.project_manager.record_fleet_stopped(
            legion_id, session_ids, processing_session_ids
        )

    async def clear_fleet_stopped(self, legion_id: str, session_ids: list[str]):
        return await self.coordinator.project_manager.clear_fleet_stopped(legion_id, session_ids)

    # =========================================================================
    # Sessions
    # =========================================================================

    async def start_session(self, session_id: str, permission_callback: Any) -> bool:
        self.coordinator.clear_message_callbacks(session_id)
        return await self.coordinator.start_session(
            session_id, permission_callback=permission_callback
        )

    async def start_session_with_fresh_callback(self, session_id: str) -> bool:
        """Same clear+add-fresh-message-callback sequence as restart/reset's
        _with_fresh_callback siblings (issue #2189) — distinct from start_session()
        above, which only clears and doesn't re-register, since that's what the
        /start router endpoint has always done inline."""
        self.register_fresh_message_callback(session_id)
        return await self.coordinator.start_session(
            session_id,
            permission_callback=self.create_permission_callback(session_id),
        )

    async def create_session(
        self,
        session_id: str,
        project_id: str | None,
        config: Any,
        name: str | None,
        role: Any,
        capabilities: Any,
        permission_callback: Any,
    ) -> str:
        return await self.coordinator.create_session(
            session_id=session_id,
            project_id=project_id,
            config=config,
            name=name,
            role=role,
            capabilities=capabilities,
            permission_callback=permission_callback,
        )

    async def list_sessions(self, limit: int = 500, offset: int = 0) -> dict:
        return await self.coordinator.list_sessions(limit=limit, offset=offset)

    async def get_session_info(self, session_id: str) -> dict | None:
        return await self.coordinator.get_session_info(session_id)

    async def get_session_usage_aggregate(self, session_id: str) -> dict | None:
        return await self.coordinator.analytics_store.get_session_usage(session_id)

    async def get_descendants(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self.coordinator.get_descendants(session_id, limit=limit, offset=offset)

    async def mark_session_unread(self, session_id: str) -> bool:
        return await self.coordinator.session_manager.mark_unread(session_id)

    async def mark_session_read(self, session_id: str) -> bool:
        return await self.coordinator.session_manager.mark_read(session_id)

    def cleanup_pending_permissions_for_session(self, session_id: str) -> None:
        """Issue #2189: skips BackendApp._cleanup_pending_permissions_for_session
        entirely since that method was already a 1-line forward to this."""
        self.coordinator.permission_service.cleanup_pending_for_session(session_id)

    async def export_session_fixture(self, session_id: str, name: str):
        from .fixture_export import export_fixture
        return await export_fixture(self.coordinator, session_id, name)

    async def update_session_name(self, session_id: str, name: str) -> bool:
        return await self.coordinator.update_session_name(session_id, name)

    async def send_message(self, session_id: str, message: str, metadata: dict | None = None) -> bool:
        return await self.coordinator.send_message(session_id, message, metadata=metadata)

    async def cancel_auto_resume_schedule(self, session_id: str) -> bool:
        """False (no-op) when Legion isn't active for this instance — mirrors the
        router's own `if webui.coordinator.legion_system:` guard."""
        if not self.coordinator.legion_system:
            return False
        return await self.coordinator.legion_system.scheduler_service.cancel_auto_resume_schedule(
            session_id
        )

    async def get_session_messages(self, session_id: str, limit: int | None = 50, offset: int = 0) -> dict:
        return await self.coordinator.get_session_messages(session_id, limit=limit, offset=offset)

    async def get_background_agents(self, session_id: str) -> dict:
        return await self.coordinator.get_background_agents(session_id)

    async def restart_session(self, session_id: str, permission_callback: Any) -> bool:
        self.coordinator.clear_message_callbacks(session_id)
        return await self.coordinator.restart_session(
            session_id, permission_callback=permission_callback
        )

    async def disconnect_session(self, session_id: str) -> dict:
        success = await self.coordinator.disconnect_sdk(session_id)
        return {"success": success}

    async def delete_session_only(self, session_id: str, archive_reason: str = "user_deleted") -> dict:
        """Thin forward to coordinator.delete_session() with none of
        delete_session()'s extra project-lookup enrichment — used when the
        caller (e.g. delete_project()'s session cleanup loop, or a schedule
        deleting its own ephemeral agent) doesn't need the enriched fields."""
        return await self.coordinator.delete_session(session_id, archive_reason=archive_reason)

    async def delete_session(self, session_id: str) -> dict:
        project = await self.coordinator.find_project_for_session(session_id)
        result = await self.coordinator.delete_session(session_id)
        if not result.get("success"):
            return result
        if project:
            updated_project = await self.coordinator.project_manager.get_project(project.project_id)
            result["project_id"] = project.project_id
            result["project_deleted"] = updated_project is None
            result["updated_project"] = updated_project.to_dict() if updated_project else None
        return result

    async def terminate_session(self, session_id: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.terminate_session(session_id)

    async def terminate_session_only(self, session_id: str) -> bool:
        """Thin forward with no existence pre-check — used by callers (e.g. the
        /terminate router) that already did their own get_session_exists() check,
        so this avoids paying for a second lookup on top of that one."""
        return await self.coordinator.terminate_session(session_id)

    async def reset_session(self, session_id: str, permission_callback: Any) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.reset_session(
            session_id, permission_callback=permission_callback
        )

    async def update_session(self, session_id: str, **updates) -> bool:
        return await self.coordinator.session_manager.update_session(
            session_id,
            template_manager=self.coordinator.template_manager,
            **updates,
        )

    async def get_session_working_directory(self, session_id: str) -> str | None:
        """Return working_directory for file-serving and diff routes."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session.working_directory if session else None

    async def get_session_state(self, session_id: str) -> str | None:
        """Return the session state value, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session.state if session else None

    async def get_session_exists(self, session_id: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session is not None

    async def set_session_permission_mode(self, session_id: str, mode: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.set_permission_mode(session_id, mode)

    async def set_permission_mode(self, session_id: str, mode: str) -> bool:
        """Thin forward with no existence pre-check — coordinator.set_permission_mode()
        already does its own session lookup internally, so this avoids the
        /permission-mode router (which never pre-checked existence) paying for
        a redundant extra lookup on top of that."""
        return await self.coordinator.set_permission_mode(session_id, mode)

    async def interrupt_session(self, session_id: str) -> bool:
        return await self.coordinator.interrupt_session(session_id)

    def has_pending_permission(self, request_id: str) -> bool:
        return request_id in self.coordinator.permission_service.pending_permissions

    def resolve_permission(self, request_id: str, response: dict) -> bool:
        return self.coordinator.permission_service.resolve(request_id, response)

    def create_permission_callback(self, session_id: str):
        return self.coordinator.permission_service.create_permission_callback(session_id)

    async def get_proxy_logs(self, session_id: str, log_type: str = "http", limit: int = 200) -> dict:
        return await self.coordinator.get_proxy_logs(session_id, log_type=log_type, limit=limit)

    async def set_model(self, session_id: str, model: str) -> bool:
        return await self.coordinator.set_model(session_id, model)

    async def add_directory(self, session_id: str, directory: str) -> dict:
        return await self.coordinator.add_directory(session_id, directory)

    async def get_mcp_status(self, session_id: str) -> dict:
        return await self.coordinator.get_mcp_status(session_id)

    async def toggle_mcp_server(self, session_id: str, name: str, enabled: bool) -> None:
        await self.coordinator.toggle_mcp_server(session_id, name, enabled)

    async def reconnect_mcp_server(self, session_id: str, name: str) -> None:
        await self.coordinator.reconnect_mcp_server(session_id, name)

    def register_fresh_message_callback(self, session_id: str) -> None:
        """Clear any existing message callbacks and register a fresh one — the same
        clear+add sequence restart/reset do before actually restarting/resetting, so
        a stale callback from a prior connection can't linger (issue #2189: kept as
        its own step rather than folded silently into restart/reset, to match the
        two call sites that need it)."""
        self.coordinator.clear_message_callbacks(session_id)
        self.coordinator.add_message_callback(
            session_id, self.coordinator.create_message_callback(session_id)
        )

    async def restart_session_with_fresh_callback(self, session_id: str) -> bool:
        self.register_fresh_message_callback(session_id)
        return await self.coordinator.restart_session(
            session_id,
            permission_callback=self.create_permission_callback(session_id),
        )

    async def reset_session_with_fresh_callback(self, session_id: str) -> bool:
        self.register_fresh_message_callback(session_id)
        return await self.coordinator.reset_session(
            session_id,
            permission_callback=self.create_permission_callback(session_id),
        )

    async def erase_session_history(self, session_id: str) -> bool:
        return await self.coordinator.erase_history(session_id)

    # =========================================================================
    # System / git / restart (issue #2189 router-boundary PR A)
    # =========================================================================

    async def run_git_command(
        self, args: list[str], cwd: str, allow_nonzero: bool = False
    ) -> str | None:
        """Run a git command and return stdout, or None on error. Cluster 4 of the
        approved plan: the real logic was never on BackendApp/coordinator to begin
        with (BackendApp._run_git_command was already a 1-line forward to this same
        shared helper), so this skips both entirely."""
        return await _run_git_command_impl(args, cwd, allow_nonzero)

    def check_and_mark_restart_rate_limit(self) -> int | None:
        """Returns remaining-seconds if a restart was requested within the last 30s
        (reject), or None if this request is allowed (and marks the new timestamp)."""
        now = time.time()
        if now - self._last_restart_time < 30:
            return int(30 - (now - self._last_restart_time))
        self._last_restart_time = now
        return None

    def broadcast_server_restarting(self, pull_output: str, sync_output: str) -> None:
        self.coordinator.broadcast_server_restarting(pull_output, sync_output)

    async def cleanup_coordinator(self) -> None:
        await self.coordinator.cleanup()

    # =========================================================================
    # Diff helpers
    # =========================================================================

    async def get_session_diff_context(self, session_id: str) -> dict:
        """Return working_directory status for diff routes."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return {"exists": False}
        return {"exists": True, "working_directory": session.working_directory}

    async def get_session_messages_path(self, session_id: str) -> str | None:
        """Return absolute path to messages.jsonl for a session, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return None
        return str(self.coordinator.data_dir / "sessions" / session_id / "messages.jsonl")

    # =========================================================================
    # Archives (issue #2189 router-boundary PR A)
    # =========================================================================

    async def get_archives(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self.coordinator.get_archives(session_id, limit=limit, offset=offset)

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50
    ) -> dict:
        return await self.coordinator.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit
        )

    async def get_archive_state(self, session_id: str, archive_id: str) -> dict | None:
        return await self.coordinator.get_archive_state(session_id, archive_id)

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
        return await self.coordinator.get_archive_resources(
            session_id, archive_id, limit=limit, offset=offset,
            search=search, format_filter=format_filter, sort=sort,
        )

    async def get_archive_resource_by_id(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> dict | None:
        return await self.coordinator.get_archive_resource_by_id(session_id, archive_id, resource_id)

    async def get_archive_resource_file(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> bytes | None:
        return await self.coordinator.get_archive_resource_file(session_id, archive_id, resource_id)

    async def list_project_deleted_agents(
        self, project_id: str, limit: int = 50, offset: int = 0
    ) -> dict:
        return await self.coordinator.list_project_deleted_agents(project_id, limit=limit, offset=offset)

    async def erase_archives(self, session_id: str) -> bool:
        return await self.coordinator.erase_archives(session_id)

    async def check_history_archives(self, session_id: str) -> dict:
        return await self.coordinator.check_history_archives(session_id)

    # =========================================================================
    # Files / resources (issue #2189 router-boundary PR A)
    # =========================================================================

    async def register_uploaded_file(self, session_id: str, stored_path: str) -> None:
        await self.coordinator.register_uploaded_file(session_id, stored_path)

    async def register_uploaded_resource(
        self, session_id: str, file_path: str, title: str, description: str
    ) -> dict | None:
        return await self.coordinator.register_uploaded_resource(
            session_id=session_id, file_path=file_path, title=title, description=description,
        )

    async def unregister_uploaded_file(self, session_id: str, stored_path: str) -> None:
        await self.coordinator.unregister_uploaded_file(session_id, stored_path)

    async def get_session_resources(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ) -> dict:
        return await self.coordinator.get_session_resources(
            session_id, limit=limit, offset=offset,
            search=search, format_filter=format_filter, sort=sort,
        )

    async def get_session_resource_by_id(self, session_id: str, resource_id: str) -> dict | None:
        return await self.coordinator.get_session_resource_by_id(session_id, resource_id)

    async def get_session_resource_file(self, session_id: str, resource_id: str) -> bytes | None:
        return await self.coordinator.get_session_resource_file(session_id, resource_id)

    async def get_session_links(self, session_id: str) -> list:
        return await self.coordinator.get_session_links(session_id)

    async def remove_session_resource(self, session_id: str, resource_id: str) -> bool:
        return await self.coordinator.remove_session_resource(session_id, resource_id)

    # =========================================================================
    # Queues / poll (issue #2189 router-boundary PR A)
    # =========================================================================

    def get_ui_queue(self) -> EventQueue:
        return self.coordinator.ui_queue

    def get_session_queue(self, session_id: str) -> EventQueue | None:
        return self.coordinator.session_queues.get(session_id)

    def get_or_create_session_queue(self, session_id: str) -> EventQueue:
        queue = self.coordinator.session_queues.get(session_id)
        if queue is None:
            queue = EventQueue(on_append=self.coordinator.queue_append_hook(session_id))
            self.coordinator.session_queues[session_id] = queue
        return queue

    def drop_session_queue(self, session_id: str) -> None:
        self.coordinator.session_queues.pop(session_id, None)

    async def mark_session_viewed(self, session_id: str) -> None:
        await self.coordinator.session_manager.mark_viewed(session_id)

    # =========================================================================
    # Provider catalog (issue #2189 router-boundary PR A)
    # =========================================================================

    async def list_catalog_entries(self) -> list:
        return await self.coordinator.provider_catalog_manager.list_entries()

    def get_catalog_pending_changes(self) -> bool:
        return self.coordinator.provider_catalog_manager.pending_changes

    async def create_catalog_entry(self, data: dict) -> dict:
        return await self.coordinator.provider_catalog_manager.create_entry(data)

    async def update_catalog_entry(self, entry_id: str, body: dict) -> dict:
        return await self.coordinator.provider_catalog_manager.update_entry(entry_id, body)

    async def delete_catalog_entry(self, entry_id: str) -> None:
        await self.coordinator.provider_catalog_manager.delete_entry(entry_id)

    async def rebuild_litellm_proxy(self) -> None:
        await self.coordinator.litellm_proxy_manager.rebuild()

    async def clear_catalog_pending_changes(self) -> None:
        await self.coordinator.provider_catalog_manager.clear_pending_changes()

    def get_litellm_session_routing(self, session_id: str) -> dict | None:
        """Return LiteLLM routing config for a session's proxy sidecar, or None if
        no catalog is selected for that session."""
        mgr = self.coordinator.litellm_proxy_manager
        routing = mgr.get_session_routing(session_id)
        if routing is None:
            return None
        return {
            "hostname_rewrites": mgr.build_hostname_rewrites(),
            "virtual_key": routing["virtual_key"],
            "model_map": routing.get("model_map", {}),
            "default_model": routing.get("default_model"),
        }

    def get_litellm_proxy_runtime_status(self) -> dict:
        mgr = self.coordinator.litellm_proxy_manager
        return {
            "running": mgr.is_running,
            "port": mgr.port,
            "model_count": mgr.model_count,
            "last_restart": mgr.last_restart.isoformat() if mgr.last_restart else None,
            "last_error": mgr.last_error,
        }

    # =========================================================================
    # Skills (issue #2189 router-boundary PR A)
    # =========================================================================

    async def sync_skills(self) -> dict:
        return await self.coordinator.skill_manager.sync()

    def get_skill_last_sync_time(self):
        return self.coordinator.skill_manager.last_sync_time

    async def cleanup_skill_symlinks(self) -> None:
        await self.coordinator.skill_manager.cleanup_symlinks()

    # =========================================================================
    # Queue (issue #2189 router-boundary PR A)
    # =========================================================================

    async def enqueue_message(
        self, session_id: str, content: str, reset_session: bool | None, metadata: dict | None
    ) -> dict:
        return await self.coordinator.enqueue_message(
            session_id=session_id, content=content,
            reset_session=reset_session, metadata=metadata,
        )

    async def get_queue(self, session_id: str, limit: int = 100, offset: int = 0) -> dict:
        return await self.coordinator.get_queue(session_id, limit=limit, offset=offset)

    async def clear_queue_history(self, session_id: str) -> int:
        return await self.coordinator.clear_queue_history(session_id)

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        return await self.coordinator.cancel_queue_item(session_id, queue_id)

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        return await self.coordinator.requeue_item(session_id, queue_id)

    async def clear_queue(self, session_id: str) -> int:
        return await self.coordinator.clear_queue(session_id)

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        return await self.coordinator.pause_queue(session_id, paused)

    async def update_queue_config(self, session_id: str, data: dict) -> bool:
        return await self.coordinator.update_queue_config(session_id, data)

    async def broadcast_queue_update(self, session_id: str, action: str, item: dict) -> None:
        await self.coordinator.broadcast_queue_update(session_id, action, item)

    def broadcast_project_updated(self, project: dict) -> None:
        self.coordinator.broadcast_project_updated(project)

    def broadcast_project_deleted(self, project_id: str) -> None:
        self.coordinator.broadcast_project_deleted(project_id)

    def broadcast_state_change(
        self, session_id: str, session_dict: dict, timestamp: str | None = None
    ) -> None:
        self.coordinator.broadcast_state_change(session_id, session_dict, timestamp)

    # =========================================================================
    # Legion
    # =========================================================================

    async def get_legion_project(self, legion_id: str) -> dict | None:
        project = await self.coordinator.project_manager.get_project(legion_id)
        return project.to_dict() if project else None

    async def get_minion_session(self, minion_id: str) -> dict | None:
        session = await self.coordinator.session_manager.get_session_info(minion_id)
        return session.to_dict() if session else None

    async def get_minion_name(self, minion_id: str) -> str | None:
        """Return session name for a minion, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(minion_id)
        return session.name if session else None

    def get_data_dir(self):
        """Return the Backend's data_dir Path (issue #2189 router-boundary PR A)."""
        return self.coordinator.data_dir

    async def assemble_minion_hierarchy(self, legion_id: str) -> dict | None:
        legion_coord = self.coordinator.legion_system.legion_coordinator
        if not legion_coord:
            return None
        return await legion_coord.assemble_minion_hierarchy(legion_id)

    async def get_legion(self, legion_id: str):
        return await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)

    async def route_comm(self, comm) -> bool:
        return await self.coordinator.legion_system.comm_router.route_comm(comm)

    async def create_minion_for_user(
        self, legion_id: str, name: str, config, role: str, capabilities: list[str] | None
    ) -> str:
        return await self.coordinator.legion_system.overseer_controller.create_minion_for_user(
            legion_id=legion_id, name=name, config=config, role=role, capabilities=capabilities,
        )

    async def reparent_minion(self, subject_id: str, new_parent_id: str, caller_id: str | None = None) -> dict:
        return await self.coordinator.legion_system.overseer_controller.reparent_minion(
            subject_id=subject_id, new_parent_id=new_parent_id, caller_id=caller_id,
        )

    def get_scheduler_service(self):
        """Return the SchedulerService instance (issue #2189 router-boundary PR A) —
        routers need the object itself for its varied schedule CRUD/history API."""
        return self.coordinator.legion_system.scheduler_service

    async def create_ephemeral_session(
        self, session_config, schedule_name: str, project_id: str
    ) -> str:
        return await self.coordinator.create_ephemeral_session(
            session_config=session_config, schedule_name=schedule_name, project_id=project_id,
        )

    # =========================================================================
    # MCP Configs
    # =========================================================================

    async def list_mcp_configs(self, limit: int = 100, offset: int = 0) -> dict:
        configs = await self.coordinator.mcp_config_manager.list_configs()
        all_configs = [c.to_dict() for c in configs]
        total = len(all_configs)
        sliced = all_configs[offset : offset + limit]
        return {
            "configs": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def create_mcp_config(
        self,
        name: str,
        server_type: Any,
        command: str | None = None,
        args: list | None = None,
        env: dict | None = None,
        url: str | None = None,
        headers: dict | None = None,
        enabled: bool = True,
        oauth_enabled: bool = False,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_callback_port: int | None = None,
        shared_connection: bool = False,
        oauth_custom_callback_path: str | None = None,
        oauth_custom_callback_port: int | None = None,
    ) -> dict:
        config = await self.coordinator.mcp_config_manager.create_config(
            name=name,
            server_type=server_type,
            command=command,
            args=args or [],
            env=env or {},
            url=url,
            headers=headers or {},
            enabled=enabled,
            oauth_enabled=oauth_enabled,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_callback_port=oauth_callback_port,
            shared_connection=shared_connection,
            oauth_custom_callback_path=oauth_custom_callback_path,
            oauth_custom_callback_port=oauth_custom_callback_port,
            main_app_port=self.coordinator.port,
            litellm_port=self._litellm_port(),
        )
        return config.to_dict()

    def _litellm_port(self) -> int | None:
        """Return the LiteLLM proxy's port, if wired up (issue #1789 conflict checks)."""
        proxy = getattr(self.coordinator, "litellm_proxy_manager", None)
        return proxy.port if proxy is not None else None

    async def export_mcp_configs(self, ids: list[str] | None = None) -> list:
        configs = await self.coordinator.mcp_config_manager.list_configs()
        if ids is not None:
            id_set = set(ids)
            configs = [c for c in configs if c.id in id_set]
        return configs

    async def import_mcp_configs(self, servers: dict, dry_run: bool) -> dict:
        from backend.mcp_config_manager import McpServerType

        manager = self.coordinator.mcp_config_manager
        existing_configs = await manager.list_configs()  # FIX: was manager.configs.values()
        existing_by_name = {c.name: c for c in existing_configs}

        preview = []
        imported = []

        for name, server_data in servers.items():
            name = name.strip()
            if not name:
                preview.append({"name": "", "action": "skip", "reason": "Missing name"})
                continue

            server_type_raw = server_data.get("type", "stdio")
            try:
                server_type = McpServerType(server_type_raw)
            except ValueError:
                preview.append(
                    {"name": name, "action": "skip", "reason": f"Invalid type: {server_type_raw}"}
                )
                continue

            if server_type == McpServerType.STDIO and not server_data.get("command"):
                preview.append(
                    {"name": name, "action": "skip", "reason": "Missing command for stdio server"}
                )
                continue

            if server_type in (McpServerType.SSE, McpServerType.HTTP) and not server_data.get("url"):
                preview.append(
                    {
                        "name": name,
                        "action": "skip",
                        "reason": f"Missing url for {server_type} server",
                    }
                )
                continue

            existing = existing_by_name.get(name)
            action = "update" if existing else "create"
            entry: dict = {
                "name": name,
                "action": action,
                "config": dict(server_data),
            }
            if existing:
                entry["existing_id"] = existing.id

            if not dry_run:
                try:
                    oauth_data = server_data.get("oauth") or {}
                    oauth_client_id = oauth_data.get("clientId")
                    oauth_callback_port = oauth_data.get("callbackPort")
                    if action == "create":
                        config = await manager.create_config(
                            name=name,
                            server_type=server_type,
                            command=server_data.get("command"),
                            args=server_data.get("args") or [],
                            env=server_data.get("env") or {},
                            url=server_data.get("url"),
                            headers=server_data.get("headers") or {},
                            enabled=server_data.get("enabled", True),
                            oauth_client_id=oauth_client_id,
                            oauth_callback_port=oauth_callback_port,
                        )
                    else:
                        config = await manager.update_config(
                            config_id=existing.id,
                            name=name,
                            server_type=server_type,
                            command=server_data.get("command"),
                            args=server_data.get("args") or [],
                            env=server_data.get("env") or {},
                            url=server_data.get("url"),
                            headers=server_data.get("headers") or {},
                            enabled=server_data.get("enabled", True),
                            oauth_client_id=oauth_client_id,
                            oauth_callback_port=oauth_callback_port,
                        )
                    entry["result"] = config.to_dict()
                    imported.append(config.to_dict())
                except Exception as e:
                    entry["action"] = "skip"
                    entry["reason"] = str(e)

            preview.append(entry)

        create_count = sum(1 for p in preview if p["action"] == "create")
        update_count = sum(1 for p in preview if p["action"] == "update")
        skip_count = sum(1 for p in preview if p["action"] == "skip")

        return {
            "dry_run": dry_run,
            "preview": preview,
            "summary": {"create": create_count, "update": update_count, "skip": skip_count},
            "imported": imported,
        }

    async def get_mcp_config(self, config_id: str) -> dict | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        return config.to_dict() if config else None

    # ========== Hook configs (issue #1629) ==========

    async def list_hook_configs(self, limit: int = 100, offset: int = 0) -> dict:
        configs = await self.coordinator.hook_config_manager.list_configs()
        all_configs = [c.to_dict() for c in configs]
        total = len(all_configs)
        sliced = all_configs[offset : offset + limit]
        return {
            "configs": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def create_hook_config(
        self,
        name: str,
        enabled: bool = True,
        hooks: list[dict] | None = None,
    ) -> dict:
        config = await self.coordinator.hook_config_manager.create_config(
            name=name,
            enabled=enabled,
            hooks=hooks or [],
        )
        return config.to_dict()

    async def get_hook_config(self, config_id: str) -> dict | None:
        config = await self.coordinator.hook_config_manager.get_config(config_id)
        return config.to_dict() if config else None

    async def update_hook_config(self, config_id: str, **kwargs) -> dict | None:
        config = await self.coordinator.hook_config_manager.update_config(config_id, **kwargs)
        return config.to_dict() if config else None

    async def delete_hook_config(self, config_id: str) -> bool:
        return await self.coordinator.hook_config_manager.delete_config(config_id)

    async def update_mcp_config(self, config_id: str, **kwargs) -> dict | None:
        config = await self.coordinator.mcp_config_manager.update_config(
            config_id,
            shared_mcp_manager=self.coordinator.shared_mcp_manager,
            main_app_port=self.coordinator.port,
            litellm_port=self._litellm_port(),
            **kwargs,
        )
        return config.to_dict() if config else None

    async def delete_mcp_config(self, config_id: str) -> bool:
        return await self.coordinator.mcp_config_manager.delete_config(
            config_id,
            shared_mcp_manager=self.coordinator.shared_mcp_manager,
        )

    async def get_mcp_config_tools(self, config_id: str) -> dict | None:
        """Return live tool list for a shared-connection MCP config (issue #1799).

        Returns None if the config doesn't exist (router raises 404).
        Raises ValueError if the config isn't shared_connection (router raises 400).

        Status resolution order: disabled -> needs-auth (oauth enabled, no stored
        token) -> connected/failed via SharedMcpConnectionManager.list_tools(). The
        connection attempt can block up to ~35s on a broken shared server (inherited
        from SharedMcpConnectionManager._open_locked's own timeout), or up to ~70s if
        a concurrent OAuth token-refresh reconnect (_on_token_refreshed) is also
        contending for the same config's open-lock (issue #1806).
        """
        from backend.mcp_config_manager import McpServerType

        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if config is None:
            return None
        if not config.shared_connection:
            raise ValueError("Tool listing is only supported for shared_connection MCP servers")

        if not config.enabled:
            return {"status": "disabled", "tools": [], "error": None}

        if config.oauth_enabled and config.type in (McpServerType.HTTP, McpServerType.SSE):
            token = await self.coordinator.oauth_manager.get_stored_token(config_id)
            if token is None:
                return {"status": "needs-auth", "tools": [], "error": None}

        try:
            tools = await self.coordinator.shared_mcp_manager.list_tools(config)
            return {
                "status": "connected",
                "tools": [{"name": t.name, "description": t.description} for t in tools],
                "error": None,
            }
        except Exception as exc:
            return {"status": "failed", "tools": [], "error": str(exc)}

    async def test_connect_mcp_config(self, config_id: str) -> dict | None:
        """Open a throwaway connection to a non-shared-connection MCP config, list its
        tools, and close it immediately (issue #1800).

        Returns None if the config doesn't exist (router raises 404).
        Raises ValueError if the config IS shared_connection (router raises 400) — use
        get_mcp_config_tools()/the /tools endpoint for those instead.

        Status resolution order mirrors get_mcp_config_tools(): disabled -> needs-auth
        (oauth enabled, no stored token) -> connected/failed via
        McpOneshotConnector.test_connect(). Blocks until the outcome is known — up to
        ~30s on a hung server.
        """
        from backend.mcp_config_manager import McpServerType

        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if config is None:
            return None
        if config.shared_connection:
            raise ValueError(
                "test-connect is only supported for non-shared MCP servers; "
                "use the /tools endpoint for shared_connection servers"
            )

        if not config.enabled:
            return {"status": "disabled", "stage": None, "tools": [], "error": None}

        if config.oauth_enabled and config.type in (McpServerType.HTTP, McpServerType.SSE):
            token = await self.coordinator.oauth_manager.get_stored_token(config_id)
            if token is None:
                return {"status": "needs-auth", "stage": None, "tools": [], "error": None}

        result = await self.coordinator.mcp_oneshot_connector.test_connect(config)
        if result["status"] != "failed":
            result["tools"] = [{"name": t.name, "description": t.description} for t in result["tools"]]
        return result

    # =========================================================================
    # OAuth
    # =========================================================================

    async def oauth_complete_flow(self, state: str, code: str) -> str:
        return await self.coordinator.oauth_manager.complete_flow(state, code)

    async def oauth_initiate_flow(
        self,
        config_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str,
    ) -> str | None:
        from .mcp.secret_resolver import resolve_secret_refs_in_str

        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        client_secret = None
        if config.oauth_client_secret:
            client_secret = await resolve_secret_refs_in_str(
                config.oauth_client_secret, self.coordinator.credential_vault
            )
        return await self.coordinator.oauth_manager.start_flow(
            server_id=config_id,
            server_url=server_url,
            redirect_uri=redirect_uri,
            client_name=client_name,
            pre_registered_client_id=config.oauth_client_id or None,
            client_secret=client_secret,
        )

    async def oauth_disconnect(self, config_id: str) -> bool:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return False
        await self.coordinator.oauth_manager.disconnect(config_id)
        return True

    async def oauth_get_status(self, config_id: str) -> dict | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        token = await self.coordinator.oauth_manager.get_stored_token(config_id)
        if token is None:
            return {"status": "unauthenticated", "expires_at": None, "has_refresh_token": False}

        store = self.coordinator.oauth_manager.get_token_store(config_id)
        expiry = await store.get_token_expiry()
        now = time.time()
        # Issue #976: Distinguish expiring_soon (within 5 min) from fully authenticated
        if expiry is not None and expiry < now:
            status = "expired"
        elif expiry is not None and expiry < (now + 300):
            status = "expiring_soon"
        else:
            status = "authenticated"
        return {
            "status": status,
            "expires_at": expiry,
            "has_refresh_token": bool(token.refresh_token),
        }

    async def import_oauth_as_secret(
        self, config_id: str, base_name: str, replace: bool = False
    ) -> dict:
        """Import stored OAuth 2.1 tokens as proxy-injectable vault secrets.

        Creates up to 3 vault secrets (primary oauth2, refresh, client_secret) and
        updates the MCP config's Authorization header to ${secret:<base_name>}.

        When replace=True (Reconnect flow): updates existing records in-place after
        verifying sibling ownership. Skips the 409 collision pre-check.

        Raises:
            ValueError: 400 — invalid base_name, STDIO config, or missing token_url.
            LookupError: 404 — config not found or no stored tokens.
            KeyError: 409 — vault name collision (replace=False) or sibling ownership
                           guard failure (replace=True).
        """
        import re
        from datetime import UTC, datetime
        from urllib.parse import urlparse

        from .models.secret_record import RefreshSpec, ScrubSpec

        base_name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
        if not base_name_re.match(base_name):
            raise ValueError(
                "base_name must match ^[a-z0-9][a-z0-9_-]{0,62}$"
            )

        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            raise LookupError("404: MCP configuration not found")

        if not config.url:
            raise ValueError("Cannot import OAuth tokens from STDIO MCP server")

        store = self.coordinator.oauth_manager.get_token_store(config_id)
        tokens = await store.get_tokens()
        if tokens is None:
            raise LookupError("404: No OAuth tokens stored for this MCP server")

        expiry_ts = await store.get_token_expiry()
        client_info = await store.get_client_info()
        token_url = store.get_token_endpoint()
        if not token_url:
            raise ValueError("OAuth token endpoint not recorded; re-authenticate to refresh it")

        vault = self.coordinator.credential_vault

        # Determine sibling secret names
        refresh_token_name = f"{base_name}_refresh" if tokens.refresh_token else None
        client_secret_value = client_info.client_secret if client_info else None
        client_secret_name = f"{base_name}_client_secret" if client_secret_value else None
        client_secret_reused = False
        if client_secret_value:
            from .mcp.secret_resolver import find_secret_ref_names

            ref_names = find_secret_ref_names(config.oauth_client_secret)
            if ref_names and len(ref_names) == 1 and (
                config.oauth_client_secret.strip() == f"${{secret:{ref_names[0]}}}"
            ):
                existing_secret = await vault.get_secret(ref_names[0])
                if existing_secret is None:
                    raise LookupError(
                        f"404: Referenced client secret '{ref_names[0]}' no longer exists in vault"
                    )
                client_secret_name = ref_names[0]
                client_secret_reused = True

        now = datetime.now(UTC)
        # Issue #1871: .hostname (not .netloc) — target_hosts validation rejects a
        # port component, so an MCP server URL with an explicit non-standard port
        # would otherwise fail SecretRecord.validate() below with a confusing error.
        host = urlparse(config.url).hostname or urlparse(config.url).netloc
        token_path = urlparse(token_url).path
        client_id = (client_info.client_id if client_info else None) or config.oauth_client_id or ""
        expires_at_dt = datetime.fromtimestamp(expiry_ts, tz=UTC) if expiry_ts else None

        scrub = ScrubSpec(
            url_path=token_path,
            matcher_jsonpath="$.access_token",
            update_on_change=True,
        )

        refresh_spec = None
        if tokens.refresh_token and refresh_token_name:
            refresh_spec = RefreshSpec(
                token_url=token_url,
                client_id=client_id,
                refresh_token_secret_name=refresh_token_name,
                client_secret_secret_name=client_secret_name,
                expires_at=expires_at_dt,
                buffer_seconds=300,
            )

        expires_at_iso = expires_at_dt.isoformat() if expires_at_dt else None

        if replace:
            return await self._replace_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=client_secret_name,
                tokens=tokens,
                client_secret_value=client_secret_value,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
                expires_at_iso=expires_at_iso,
            )

        # Pre-check vault collisions (replace=False only). A reused reference's
        # existing presence in the vault is expected, not a collision.
        collision_candidates = [base_name, refresh_token_name]
        if not client_secret_reused:
            collision_candidates.append(client_secret_name)
        for name in filter(None, collision_candidates):
            existing = await vault.get_secret(name)
            if existing is not None:
                raise KeyError(f"409: Secret '{name}' already exists; choose a different base_name")

        created = await self._create_oauth_secret_bundle(
            vault=vault,
            base_name=base_name,
            refresh_token_name=refresh_token_name,
            client_secret_name=client_secret_name,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            client_secret_value=client_secret_value,
            refresh_spec=refresh_spec,
            scrub=scrub,
            host=host,
            now=now,
            client_secret_reused=client_secret_reused,
        )

        # Update MCP config headers — rollback all secrets if this fails
        try:
            new_headers = {**(config.headers or {}), "Authorization": f"${{secret:{base_name}}}"}
            await self.coordinator.mcp_config_manager.update_config(config_id, headers=new_headers)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise

        # Issue #1387: schedule background refresh for the new oauth2 secret
        self.coordinator.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_created": created,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    async def _create_oauth_secret_bundle(
        self,
        *,
        vault,
        base_name: str,
        refresh_token_name: str | None,
        client_secret_name: str | None,
        access_token: str,
        refresh_token: str | None,
        client_secret_value: str | None,
        refresh_spec,
        scrub,
        host: str,
        now,
        client_secret_reused: bool = False,
    ) -> list[str]:
        """Create up to 3 vault secrets (refresh-token sibling, client-secret sibling,
        primary oauth2 record) with rollback-on-failure. Returns the list of created
        secret names, siblings first then primary — the order the caller needs to roll
        back in if a later step (e.g. updating an MCP config header) also fails.

        Extracted from import_oauth_as_secret() (issue #1871) so the standalone
        vault-secret guided-authorization flow can create the same shape of bundle
        without going through any MCP config at all.

        When `client_secret_reused` is True, `client_secret_name` refers to a secret
        that already exists (a `${secret:NAME}` reference in `config.oauth_client_secret`,
        issue #2005) — its value is overwritten in place if it drifted from the freshly
        observed one, but it is never appended to `created`: this method's own rollback
        deletes everything in `created` on failure, and a secret this call didn't create
        must never be deleted by that rollback.
        """
        from .models.secret_record import SecretRecord, SecretType

        created: list[str] = []
        try:
            if refresh_token_name and refresh_token:
                refresh_record = SecretRecord(
                    name=refresh_token_name,
                    type=SecretType.GENERIC,
                    target_hosts=[host],
                    created_at=now,
                    updated_at=now,
                )
                await vault.create_secret(refresh_record, refresh_token)
                created.append(refresh_token_name)

            if client_secret_name and client_secret_value:
                if client_secret_reused:
                    resolved = await vault.resolve_secrets_for_assignment([client_secret_name])
                    current_value = resolved[0]["value"] if resolved else None
                    if current_value != client_secret_value:
                        existing_meta = await vault.get_secret(client_secret_name)
                        if existing_meta is None:
                            # Deleted concurrently between the earlier existence
                            # check and here — fail closed rather than crash on
                            # SecretRecord.from_dict(None) (Decision 1).
                            raise LookupError(
                                f"404: Referenced client secret '{client_secret_name}' "
                                "no longer exists in vault"
                            )
                        cs_record = SecretRecord.from_dict(
                            {**existing_meta, "updated_at": now.isoformat()}
                        )
                        await vault.update_secret(client_secret_name, cs_record, client_secret_value)
                else:
                    cs_record = SecretRecord(
                        name=client_secret_name,
                        type=SecretType.GENERIC,
                        target_hosts=[host],
                        created_at=now,
                        updated_at=now,
                    )
                    await vault.create_secret(cs_record, client_secret_value)
                    created.append(client_secret_name)

            primary_record = SecretRecord(
                name=base_name,
                type=SecretType.OAUTH2,
                target_hosts=[host],
                scrub=scrub,
                refresh=refresh_spec,
                created_at=now,
                updated_at=now,
            )
            await vault.create_secret(primary_record, access_token)
            created.append(base_name)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise
        return created

    async def _replace_oauth_secret_bundle(
        self,
        *,
        vault,
        base_name: str,
        refresh_token_name: str | None,
        client_secret_name: str | None,
        tokens,
        client_secret_value: str | None,
        refresh_spec,
        scrub,
        host: str,
        now,
        expires_at_iso: str | None,
    ) -> dict:
        """Replace existing vault oauth2 secret bundle in-place (Reconnect flow).

        Enforces sibling ownership guard: refuses to replace if the existing primary
        record's refresh metadata doesn't point to the expected sibling names.
        """
        from .models.secret_record import SecretRecord

        # Verify primary record exists
        primary_meta = await vault.get_secret(base_name)
        if primary_meta is None:
            raise LookupError(f"404: Primary secret '{base_name}' not found for replace")

        # Sibling ownership guard
        existing_refresh = primary_meta.get("refresh") or {}
        if refresh_token_name:
            expected = existing_refresh.get("refresh_token_secret_name")
            if expected and expected != refresh_token_name:
                raise KeyError(
                    f"409: Sibling ownership guard: refresh sibling '{expected}' "
                    f"doesn't match expected '{refresh_token_name}'"
                )
        if client_secret_name:
            expected_cs = existing_refresh.get("client_secret_secret_name")
            if expected_cs and expected_cs != client_secret_name:
                raise KeyError(
                    f"409: Sibling ownership guard: client_secret sibling '{expected_cs}' "
                    f"doesn't match expected '{client_secret_name}'"
                )

        updated: list[str] = []

        # Update refresh token sibling value
        if refresh_token_name and tokens.refresh_token:
            rt_meta = await vault.get_secret(refresh_token_name)
            if rt_meta:
                rt_record = SecretRecord.from_dict({**rt_meta, "updated_at": now.isoformat()})
                await vault.update_secret(refresh_token_name, rt_record, tokens.refresh_token)
                updated.append(refresh_token_name)

        # Update client_secret sibling value
        if client_secret_name and client_secret_value:
            cs_meta = await vault.get_secret(client_secret_name)
            if cs_meta:
                cs_record = SecretRecord.from_dict({**cs_meta, "updated_at": now.isoformat()})
                await vault.update_secret(client_secret_name, cs_record, client_secret_value)
                updated.append(client_secret_name)

        # Update primary record: new access_token value + reset refresh metadata
        primary_record = SecretRecord.from_dict({**primary_meta, "updated_at": now.isoformat()})
        if primary_record.refresh and refresh_spec:
            # Issue #1871: standalone Reconnect leaves token_url/client_id/
            # authorization_endpoint editable (not locked to their original values),
            # so refresh these from the fresh refresh_spec too, not just expires_at.
            # No-op for MCP's reconnect path, which always recomputes identical values.
            primary_record.refresh.token_url = refresh_spec.token_url
            primary_record.refresh.client_id = refresh_spec.client_id
            primary_record.refresh.client_secret_secret_name = refresh_spec.client_secret_secret_name
            primary_record.refresh.authorization_endpoint = refresh_spec.authorization_endpoint
            primary_record.refresh.expires_at = refresh_spec.expires_at
            primary_record.refresh.last_refresh_at = None
            primary_record.refresh.last_refresh_status = None
            primary_record.refresh.last_refresh_error = None
        elif refresh_spec:
            primary_record.refresh = refresh_spec
        await vault.update_secret(base_name, primary_record, tokens.access_token)
        updated.append(base_name)

        # Reschedule background refresh with fresh expiry
        self.coordinator.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_updated": updated,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    # =========================================================================
    # Standalone OAuth2 vault secrets — guided authorization flow (issue #1871)
    #
    # Unlike import_oauth_as_secret() above (which reads tokens already obtained via
    # an MCP server's OAuth connection), these methods run their own OAuth flow from
    # scratch against caller-supplied endpoints, with no MCP config involved at all.
    # Tokens are exchanged via OAuthFlowManager.start_flow_with_endpoints()/
    # complete_flow(persist=False) — persist=False is load-bearing: it means
    # FernetTokenStore (MCP-exclusive) is never touched, and tokens land only in the
    # vault/keyring via the same _create_oauth_secret_bundle()/_replace_oauth_secret_
    # bundle() helpers import_oauth_as_secret() uses.
    # =========================================================================

    async def standalone_oauth_initiate(
        self,
        base_name: str,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret_secret_name: str | None = None,
        scopes: list[str] | None = None,
        custom_callback_path: str | None = None,
        custom_callback_port: int | None = None,
    ) -> dict:
        """Start a guided-authorization flow for a brand-new standalone oauth2 secret.

        Nothing is written to the vault here — only on standalone_oauth_complete().
        Pre-checks base_name (+ its derived refresh-token sibling name) don't already
        collide with an existing secret, so the user gets an immediate error instead
        of completing a whole provider consent flow only to fail at the end.

        Raises:
            ValueError: 400 — invalid base_name.
            KeyError: 409 — base_name or its refresh sibling already exists.
            LookupError: 404 — client_secret_secret_name doesn't resolve to a secret.
        """
        import re

        base_name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
        if not base_name_re.match(base_name):
            raise ValueError("base_name must match ^[a-z0-9][a-z0-9_-]{0,62}$")

        vault = self.coordinator.credential_vault
        refresh_token_name = f"{base_name}_refresh"
        for name in (base_name, refresh_token_name):
            if await vault.get_secret(name) is not None:
                raise KeyError(f"409: Secret '{name}' already exists; choose a different base_name")

        client_secret_value = None
        if client_secret_secret_name:
            client_secret_value = await self._resolve_sibling_secret_value(client_secret_secret_name)

        context = {
            "flow_type": "standalone_secret",
            "base_name": base_name,
            "client_secret_secret_name": client_secret_secret_name,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "client_id": client_id,
        }
        auth_url, state = await self.coordinator.oauth_manager.start_flow_with_endpoints(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret_value,
            scopes=scopes,
            persist=False,
            context=context,
        )
        return {
            "auth_url": auth_url,
            "flow_id": state,
            "custom_callback_path": custom_callback_path,
            "custom_callback_port": custom_callback_port,
        }

    async def standalone_oauth_reconnect_initiate(
        self,
        secret_name: str,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret_secret_name: str | None = None,
        scopes: list[str] | None = None,
        custom_callback_path: str | None = None,
        custom_callback_port: int | None = None,
    ) -> dict:
        """Start Reconnect for an existing guided-flow oauth2 secret.

        Only secrets that were themselves created via the guided flow (i.e. have
        refresh.authorization_endpoint set) are eligible — a manually paste-in
        secret can never gain a Reconnect action; delete-and-recreate is the only
        route onto the guided flow (locked in during mockup review).

        Raises:
            LookupError: 404 — secret_name not found, or client_secret_secret_name
                doesn't resolve to a secret.
            ValueError: 400 — secret is not oauth2, or was not created via the
                guided flow.
        """
        vault = self.coordinator.credential_vault
        existing = await vault.get_secret(secret_name)
        if existing is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        if existing.get("type") != "oauth2":
            raise ValueError(f"Secret '{secret_name}' is not an oauth2 secret")
        if not (existing.get("refresh") or {}).get("authorization_endpoint"):
            raise ValueError(
                f"Secret '{secret_name}' was not created via the guided authorization "
                "flow and cannot be reconnected; delete and recreate it instead"
            )

        client_secret_value = None
        if client_secret_secret_name:
            client_secret_value = await self._resolve_sibling_secret_value(client_secret_secret_name)

        context = {
            "flow_type": "standalone_reconnect",
            "base_name": secret_name,
            "client_secret_secret_name": client_secret_secret_name,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "client_id": client_id,
        }
        auth_url, state = await self.coordinator.oauth_manager.start_flow_with_endpoints(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret_value,
            scopes=scopes,
            persist=False,
            context=context,
        )
        return {
            "auth_url": auth_url,
            "flow_id": state,
            "custom_callback_path": custom_callback_path,
            "custom_callback_port": custom_callback_port,
        }

    async def standalone_oauth_complete(self, state: str, code: str) -> dict:
        """Complete a standalone guided-authorization flow (new secret or Reconnect).

        Exchanges the code for tokens with persist=False (never touches
        FernetTokenStore), then builds/replaces the vault secret bundle via the same
        helpers import_oauth_as_secret() uses.

        Raises:
            NoRefreshTokenError: the provider didn't issue a refresh_token — no
                secret is created/modified in this case.
            KeyError: 409 — (new-secret path only) base_name or its refresh sibling
                was created by something else during the flow's lifetime.
            LookupError: 404 — (reconnect path only) the target secret vanished
                during the flow's lifetime.
        """
        from datetime import UTC, datetime
        from urllib.parse import urlparse

        from .models.secret_record import RefreshSpec, ScrubSpec

        token, context = await self.coordinator.oauth_manager.complete_flow(
            state, code, persist=False
        )

        if not token.refresh_token:
            raise NoRefreshTokenError(
                "Authorization succeeded, but the provider did not issue a refresh "
                "token. A secret without a refresh token cannot support proactive "
                "refresh, so nothing was saved. Some providers only issue a refresh "
                "token when offline access is explicitly requested — try adding an "
                "offline-access scope, or check the provider's settings, then retry."
            )

        flow_type = context.get("flow_type")
        base_name = context["base_name"]
        client_secret_secret_name = context.get("client_secret_secret_name")
        token_endpoint = context["token_endpoint"]
        authorization_endpoint = context.get("authorization_endpoint")
        client_id = context["client_id"]

        vault = self.coordinator.credential_vault
        now = datetime.now(UTC)
        # .hostname (not .netloc): target_hosts validation rejects a port component,
        # and guided-flow providers commonly run on a non-standard port (self-hosted,
        # enterprise, local dev) — found via manual E2E testing against a local stub.
        host = urlparse(token_endpoint).hostname or urlparse(token_endpoint).netloc
        token_path = urlparse(token_endpoint).path
        refresh_token_name = f"{base_name}_refresh"

        scrub = ScrubSpec(
            url_path=token_path,
            matcher_jsonpath="$.access_token",
            update_on_change=True,
        )

        expires_at_dt = None
        if token.expires_in:
            from mcp.shared.auth_utils import calculate_token_expiry
            expiry_ts = calculate_token_expiry(token.expires_in)
            if expiry_ts:
                expires_at_dt = datetime.fromtimestamp(expiry_ts, tz=UTC)
        expires_at_iso = expires_at_dt.isoformat() if expires_at_dt else None

        refresh_spec = RefreshSpec(
            token_url=token_endpoint,
            client_id=client_id,
            refresh_token_secret_name=refresh_token_name,
            client_secret_secret_name=client_secret_secret_name,
            authorization_endpoint=authorization_endpoint,
            expires_at=expires_at_dt,
            buffer_seconds=300,
        )

        # Note: client_secret_name/client_secret_value are always None below — unlike
        # import_oauth_as_secret()'s MCP path (which creates a *fresh* client-secret
        # sibling from FernetTokenStore's client_info), a standalone flow's client
        # secret is always a *reference* to an already-existing vault secret (created
        # separately via the frontend's inline "+ New secret" affordance, per the
        # mockup's locked-in decision #1). Neither creating nor overwriting someone
        # else's already-existing secret value is ever correct here — only
        # refresh_spec.client_secret_secret_name records the reference.
        if flow_type == "standalone_reconnect":
            result = await self._replace_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=None,
                tokens=token,
                client_secret_value=None,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
                expires_at_iso=expires_at_iso,
            )
        else:
            for name in (base_name, refresh_token_name):
                if await vault.get_secret(name) is not None:
                    raise KeyError(
                        f"409: Secret '{name}' already exists; choose a different base_name"
                    )
            created = await self._create_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=None,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                client_secret_value=None,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
            )
            self.coordinator.vault_refresh_manager.schedule_secret(base_name)
            result = {
                "secrets_created": created,
                "expires_at": expires_at_iso,
                "auto_refresh_enabled": True,
            }

        result["flow_id"] = state
        result["base_name"] = base_name
        return result

    async def standalone_oauth_cancel(self, flow_id: str) -> bool:
        """Cancel a pending standalone OAuth flow (user closed the panel before the
        popup completed). Pops the pending flow if present — the caller is
        responsible for tearing down any transient callback listener/route, since
        that's route-wiring state this service layer has no access to.

        Returns True if a pending flow was found and cancelled, False if it had
        already completed or already expired.
        """
        context = await self.coordinator.oauth_manager.cancel_pending(flow_id)
        return context is not None

    async def _resolve_sibling_secret_value(self, secret_name: str) -> str:
        """Resolve a plain (non-${secret:}-syntax) sibling secret reference to its
        plaintext value — the same lookup-by-name pattern client_secret_secret_name
        and refresh_token_secret_name already use elsewhere in this file.

        Raises LookupError if the name doesn't resolve to an existing secret.
        """
        from .secrets_keyring import get_secret_value

        vault = self.coordinator.credential_vault
        if await vault.get_secret(secret_name) is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        value = get_secret_value(secret_name)
        if value is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        return value

    # =========================================================================
    # Templates
    # =========================================================================

    async def list_templates(self, limit: int = 100, offset: int = 0) -> dict:
        templates = await self.coordinator.template_manager.list_templates()
        all_templates = [t.to_dict() for t in templates]
        total = len(all_templates)
        sliced = all_templates[offset : offset + limit]
        return {
            "templates": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_template(self, template_id: str) -> dict | None:
        template = await self.coordinator.template_manager.get_template(template_id)
        return template.to_dict() if template else None

    async def create_template(self, **kwargs) -> dict:
        template = await self.coordinator.template_manager.create_template(**kwargs)
        return template.to_dict()

    async def update_template(self, template_id: str, **kwargs) -> dict | None:
        template = await self.coordinator.template_manager.update_template(
            template_id=template_id, **kwargs
        )
        return template.to_dict() if template else None

    async def delete_template(self, template_id: str) -> bool:
        return await self.coordinator.template_manager.delete_template(
            template_id,
            session_manager=self.coordinator.session_manager,
        )

    async def import_template(self, **kwargs) -> dict:
        template = await self.coordinator.template_manager.import_template(**kwargs)
        return template.to_dict()

    # =========================================================================
    # Profiles (issue #1062)
    # =========================================================================

    async def list_profiles(self, area: str | None = None) -> dict:
        profiles = await self.coordinator.profile_manager.list_profiles(area=area)
        return {"profiles": [p.to_dict() for p in profiles]}

    async def get_profile(self, profile_id: str) -> dict | None:
        profile = await self.coordinator.profile_manager.get_profile(profile_id)
        return profile.to_dict() if profile else None

    async def create_profile(self, name: str, area: str, config: dict) -> dict:
        profile = await self.coordinator.profile_manager.create_profile(
            name=name, area=area, config=config
        )
        return profile.to_dict()

    async def update_profile(
        self,
        profile_id: str,
        name: str | None = None,
        config: dict | None = None,
    ) -> dict:
        profile = await self.coordinator.profile_manager.update_profile(
            profile_id=profile_id, name=name, config=config
        )
        return profile.to_dict()

    async def delete_profile(self, profile_id: str) -> bool:
        return await self.coordinator.profile_manager.delete_profile(
            profile_id,
            template_manager=self.coordinator.template_manager,
        )

    # =========================================================================
    # Secrets Vault (issue #827 — replaces issue #1053 CredentialVault)
    # =========================================================================

    async def list_secrets(self) -> dict:
        """List all secret metadata. Never includes secret values."""
        secrets = await self.coordinator.credential_vault.list_secrets()
        # Issue #1387: enrich OAUTH2 secrets with a computed health field
        for s in secrets:
            if s.get("type") == "oauth2":
                s["health"] = _compute_secret_health(s.get("refresh"))
        # Issue #1772: enrich each secret with a usage breakdown
        sessions, templates, profiles, mcp_configs = await asyncio.gather(
            self.coordinator.session_manager.list_sessions(),
            self.coordinator.template_manager.list_templates(),
            self.coordinator.profile_manager.list_profiles(area="isolation"),
            self.coordinator.mcp_config_manager.list_configs(),
        )
        usage_map = compute_secret_usage(secrets, sessions, templates, profiles, mcp_configs)
        for s in secrets:
            s["usage"] = usage_map.get(slugify_secret(s["name"]), empty_usage())
        return {"secrets": secrets}

    async def create_secret(
        self,
        name: str,
        secret_type: str,
        target_hosts: list[str],
        value: str,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict:
        """Create a new secret. Returns metadata only (value not in response)."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        now = datetime.now(UTC)
        inj_file = InjectFileSpec.from_dict(inject_file) if inject_file else None
        scrub_spec = ScrubSpec.from_dict(scrub) if scrub else None
        injection_spec = InjectionSpec.from_dict(injection) if injection else None
        refresh_spec = RefreshSpec.from_dict(refresh) if refresh else None
        record = SecretRecord(
            name=name,
            type=SecretType(secret_type),
            target_hosts=target_hosts,
            inject_env=inject_env,
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username,
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=now,
            updated_at=now,
        )
        return await self.coordinator.credential_vault.create_secret(record, value)

    async def update_secret(
        self,
        name: str,
        secret_type: str | None = None,
        target_hosts: list[str] | None = None,
        value: str | None = None,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict | None:
        """Update secret metadata and/or value. Returns updated metadata or None if not found."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        existing = await self.coordinator.credential_vault.get_secret(name)
        if existing is None:
            return None

        now = datetime.now(UTC)
        inj_file = None
        if inject_file is not None:
            inj_file = InjectFileSpec.from_dict(inject_file)
        elif existing.get("inject_file"):
            inj_file = InjectFileSpec.from_dict(existing["inject_file"])

        scrub_spec = None
        if scrub is not None:
            scrub_spec = ScrubSpec.from_dict(scrub)
        elif existing.get("scrub"):
            scrub_spec = ScrubSpec.from_dict(existing["scrub"])

        injection_spec = None
        if injection is not None:
            injection_spec = InjectionSpec.from_dict(injection)
        elif existing.get("injection"):
            injection_spec = InjectionSpec.from_dict(existing["injection"])

        refresh_spec = None
        if refresh is not None:
            refresh_spec = RefreshSpec.from_dict(refresh)
        elif existing.get("refresh"):
            refresh_spec = RefreshSpec.from_dict(existing["refresh"])

        record = SecretRecord(
            name=existing.get("name", name),
            type=SecretType(secret_type or existing.get("type", "generic")),
            target_hosts=target_hosts if target_hosts is not None else existing.get("target_hosts", []),
            inject_env=inject_env if inject_env is not None else existing.get("inject_env"),
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username if username is not None else existing.get("username"),
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=datetime.fromisoformat(existing["created_at"]),
            updated_at=now,
        )
        return await self.coordinator.credential_vault.update_secret(name, record, value)

    async def delete_secret(self, name: str) -> bool:
        """Delete a secret by name."""
        return await self.coordinator.credential_vault.delete_secret(name)

    async def refresh_secret(self, name: str) -> dict | None:
        """Manually trigger an OAuth2 token refresh for an oauth2-typed secret.

        Fetches the current refresh_token and client_secret sibling values from the
        keyring, POSTs to token_url, writes the new access_token (and rotated
        refresh_token if present) back to the keyring, and updates expires_at.
        Returns updated metadata, or None if the secret does not exist / is not oauth2.
        Raises RuntimeError on refresh failure.

        Issue #1387: Acquires the VaultRefreshManager per-secret lock to prevent
        concurrent refresh races with the background task.
        """
        # Issue #1387: acquire per-secret lock to deduplicate concurrent refreshes
        mgr = getattr(self.coordinator, "vault_refresh_manager", None)
        if mgr:
            lock = mgr.get_lock(name)
            async with lock:
                return await self._refresh_secret_impl(name)
        return await self._refresh_secret_impl(name)

    async def _refresh_secret_impl(self, name: str) -> dict | None:
        """Core refresh logic — caller must hold per-secret lock if dedup is needed."""
        from .models.secret_record import SecretType
        from .secret_types.oauth2 import OAuth2Handler
        from .secrets_keyring import get_secret_value, set_secret_value

        meta = await self.coordinator.credential_vault.get_secret(name)
        if meta is None or meta.get("type") != SecretType.OAUTH2.value:
            return None

        record = dict(meta)
        record["value"] = get_secret_value(name) or ""

        async def _get_sibling(sibling_name: str) -> str:
            return get_secret_value(sibling_name) or ""

        handler = OAuth2Handler()
        updates = await handler.refresh(record, _get_sibling)

        new_value = updates.get("value")
        if new_value:
            set_secret_value(name, new_value)

        new_refresh_token = updates.get("refresh", {}).get("_new_refresh_token")
        new_refresh_name = (record.get("refresh") or {}).get("refresh_token_secret_name")
        if new_refresh_token and new_refresh_name:
            set_secret_value(new_refresh_name, new_refresh_token)

        refresh_updates = updates.get("refresh") or {}
        refresh_updates.pop("_new_refresh_token", None)

        return await self.update_secret(
            name=name,
            refresh=refresh_updates or None,
            value=None,
        )

    def _resolve_writeable_secret_names(self, session) -> list[str]:
        """Names the proxy can both resolve and write back for this session.

        Source order (deduped, preserves first occurrence):
          1. session.config["assigned_secrets"] — direct session-level overrides
          2. session.secret_placeholders.values() — template/profile-derived names captured at start_session()
        """
        names: list[str] = list(session.config.get("assigned_secrets") or [])
        for name in (session.secret_placeholders or {}).values():
            if name not in names:
                names.append(name)
        return names

    async def resolve_secrets_for_session(self, session_id: str) -> dict:
        """Return resolved secrets (including values) for a session's assigned_secrets list.

        Issue #1134: attaches placeholder from session.secret_placeholders; also
        walks transitive references (refresh_token_secret_name, client_secret_secret_name)
        so the proxy has everything it needs for OAuth2 refresh without extra requests.
        """
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"secrets": []}

        assigned = self._resolve_writeable_secret_names(session)

        # Collect transitive names (sibling refresh-token and client-secret records).
        all_names: list[str] = list(assigned)
        for secret in await self.coordinator.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling and sibling not in all_names:
                        all_names.append(sibling)

        secrets = await self.coordinator.credential_vault.resolve_secrets_for_assignment(all_names)

        # Attach placeholder from session map (may be empty for sessions started before #1134).
        placeholder_map = session.secret_placeholders or {}
        reverse_map = {v: k for k, v in placeholder_map.items()}
        for s in secrets:
            name = s.get("name", "")
            s["placeholder"] = reverse_map.get(name)

        return {"secrets": secrets}

    async def update_secret_for_session(
        self, session_id: str, secret_name: str, **kwargs
    ) -> dict | None:
        """Update a secret, scoped to names in session.assigned_secrets (+ transitive siblings).

        Used by the proxy sidecar to write back refreshed token values.
        Returns None if session not found; raises PermissionError if name is out-of-scope.
        """
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return None
        assigned = self._resolve_writeable_secret_names(session)
        allowed: set[str] = set(assigned)
        # Also allow transitive sibling records referenced by assigned refresh specs.
        for secret in await self.coordinator.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling:
                        allowed.add(sibling)
        if secret_name not in allowed:
            raise PermissionError(
                f"Secret '{secret_name}' is not in session {session_id}'s assigned_secrets"
            )
        return await self.update_secret(name=secret_name, **kwargs)

    async def get_proxy_status(self, session_id: str) -> dict:
        """Return effective allowlist + active credential names + proxy state for a session."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}

        proxy_enabled = session.config.get("docker_proxy_enabled", False)
        if not proxy_enabled:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}

        # Build effective domains: static defaults + session config
        effective_domains: list[str] = []
        import json as _json
        from pathlib import Path as _Path
        static_allowlist_path = _Path("backend/docker/proxy/allowlist.json")
        if static_allowlist_path.exists():
            try:
                static = _json.loads(static_allowlist_path.read_text())
                effective_domains.extend(static.get("domains", []))
            except Exception:
                pass
        extra_domains = session.config.get("docker_proxy_allowlist_domains") or []
        all_domains = sorted(set(effective_domains + extra_domains))

        active_secrets = session.config.get("assigned_secrets") or []

        return {
            "proxy_enabled": True,
            "effective_domains": all_domains,
            "active_credentials": active_secrets,
            "sidecar_running": False,  # Sidecar runtime state not tracked server-side
        }

    def schedule_secret_refresh(self, name: str) -> None:
        self.coordinator.vault_refresh_manager.schedule_secret(name)

    def unschedule_secret_refresh(self, name: str) -> None:
        self.coordinator.vault_refresh_manager.unschedule_secret(name)

    # =========================================================================
    # OAuth-callback-route-sync (issue #2189 router-boundary PR A)
    # =========================================================================

    def oauth_callback_path_conflicts_with_app_route(self, path: str) -> bool:
        return self.coordinator.oauth_callback_path_conflicts_with_app_route(path)

    async def sync_standalone_oauth_callback(
        self, flow_id: str, custom_callback_path: str | None, custom_callback_port: int | None
    ) -> None:
        await self.coordinator.sync_standalone_oauth_callback(
            flow_id, custom_callback_path, custom_callback_port
        )

    async def remove_standalone_oauth_callback(self, flow_id: str) -> None:
        await self.coordinator.remove_standalone_oauth_callback(flow_id)

    async def get_mcp_config_object(self, config_id: str):
        """Return the raw McpConfig object (not the .to_dict() form) — needed by
        callers that must pass it to sync_oauth_callback_for_config()."""
        return await self.coordinator.mcp_config_manager.get_config(config_id)

    async def sync_oauth_callback_for_config(self, config) -> None:
        await self.coordinator._sync_oauth_callback_for_config(config)

    async def remove_oauth_callback_for_config(self, config_id: str) -> None:
        await self.coordinator._remove_oauth_callback_for_config(config_id)

    def get_oauth_callback_handlers(self):
        """Return (complete_and_broadcast, denied_and_broadcast) for the shared
        /oauth/callback route's render_oauth_callback() call."""
        mgr = self.coordinator.oauth_callback_listener_manager
        return mgr.complete_and_broadcast, mgr.denied_and_broadcast

    async def get_proxy_blocked_log(self, session_id: str, limit: int = 50) -> dict:
        """Return recent blocked connections from the sidecar access log."""
        import json as _json

        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"entries": []}

        # Access log is written to data/sessions/{id}/docker_claude_data/proxy/access.log
        session_dir = self.coordinator.data_dir / "sessions" / session_id
        log_path = session_dir / "docker_claude_data" / "proxy" / "access.log"
        if not log_path.exists():
            return {"entries": []}

        blocked = []
        try:
            lines = log_path.read_text().splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = _json.loads(line)
                    if not entry.get("allowed", True):
                        blocked.append(entry)
                        if len(blocked) >= limit:
                            break
                except Exception:
                    continue
        except Exception:
            pass

        return {"entries": blocked}
