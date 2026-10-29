"""ApplicationService: service layer between HTTP routes and SessionCoordinator.

Routes call only this class. This class calls coordinator public API
and coordinator sub-managers. No route handler should access
coordinator attributes directly.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from backend.mcp.shared_connection_manager import _describe_open_failure
from shared.event_queue import EventQueue
from shared.git_restart import run_git_command as _run_git_command_impl

from . import docker_utils
from .fixture_export import export_fixture
from .mcp_config_manager import McpServerType, validate_mcp_server_url
from .principal import Principal, get_current_principal

if TYPE_CHECKING:
    from backend.session_coordinator import SessionCoordinator


class NoRefreshTokenError(Exception):
    """Issue #1871: raised when a standalone guided-authorization flow completes but
    the provider didn't issue a refresh_token. No secret is created in this case —
    an oauth2 secret without a refresh token can't support proactive background
    refresh, so surfacing this distinctly (instead of a generic failure) lets the UI
    explain why and what to try (e.g. requesting an offline-access scope)."""


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

    def current_principal(self) -> Principal | None:
        """The authenticated identity behind the current request (issue #2268 AC3).

        Set once per request by AuthMiddleware.dispatch() after the bearer token
        validates — always the same implicit singleton in single-user mode. A seam
        for #1818, not consumed by any existing logic in this stage."""
        return get_current_principal()

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

    async def get_session_messages(
        self, session_id: str, limit: int | None = 50, offset: int = 0, **window_params: Any
    ) -> dict:
        return await self.coordinator.get_session_messages(
            session_id, limit=limit, offset=offset, **window_params
        )

    async def get_background_agents(self, session_id: str) -> dict:
        return await self.coordinator.get_background_agents(session_id)

    async def get_background_tasks(self, session_id: str) -> dict:
        return await self.coordinator.get_background_tasks(session_id)

    async def get_session_tasks(self, session_id: str) -> dict:
        return await self.coordinator.get_session_tasks(session_id)

    async def get_history_index(self, session_id: str) -> dict:
        return await self.coordinator.get_history_index(session_id)

    async def get_archive_history_index(self, session_id: str, archive_id: str) -> dict:
        return await self.coordinator.get_archive_history_index(session_id, archive_id)

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
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50,
        **window_params: Any,
    ) -> dict:
        return await self.coordinator.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit, **window_params
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

    async def archive_exists(self, session_id: str, archive_id: str) -> bool:
        return await self.coordinator.archive_exists(session_id, archive_id)

    async def get_archive_queue(self, session_id: str, archive_id: str) -> list[dict]:
        return await self.coordinator.get_archive_queue(session_id, archive_id)

    async def get_archive_proxy_logs(self, session_id: str, archive_id: str) -> list[dict]:
        return await self.coordinator.get_archive_proxy_logs(session_id, archive_id)

    async def get_archive_proxy_log_content(
        self,
        session_id: str,
        archive_id: str,
        log_name: str,
        offset: int = 0,
        limit: int = 100,
        from_end: bool = False,
    ) -> dict:
        return await self.coordinator.get_archive_proxy_log_content(
            session_id, archive_id, log_name, offset=offset, limit=limit, from_end=from_end
        )

    async def get_archive_history(self, session_id: str, archive_id: str) -> list[dict]:
        return await self.coordinator.get_archive_history(session_id, archive_id)

    async def get_archive_history_content(
        self, session_id: str, archive_id: str, filename: str
    ) -> str | None:
        return await self.coordinator.get_archive_history_content(session_id, archive_id, filename)

    async def get_archive_schedules(self, session_id: str, archive_id: str) -> dict:
        return await self.coordinator.get_archive_schedules(session_id, archive_id)

    async def get_archive_attachments(self, session_id: str, archive_id: str) -> list[dict]:
        return await self.coordinator.get_archive_attachments(session_id, archive_id)

    async def get_archive_attachment_file(
        self, session_id: str, archive_id: str, filename: str
    ) -> tuple[bytes, str] | None:
        return await self.coordinator.get_archive_attachment_file(session_id, archive_id, filename)

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
            "passthrough_models": routing.get("passthrough_models", []),
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
        all_configs = [self._annotate_url_validity(c.to_dict()) for c in configs]
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
        return await self.coordinator.mcp_config_manager.export_configs(ids)

    async def import_mcp_configs(self, servers: dict, dry_run: bool) -> dict:
        return await self.coordinator.mcp_config_manager.import_configs(
            servers, dry_run, oauth_manager=self.coordinator.oauth_manager
        )

    async def get_mcp_config(self, config_id: str) -> dict | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        return self._annotate_url_validity(config.to_dict()) if config else None

    @staticmethod
    def _annotate_url_validity(config_dict: dict) -> dict:
        """Issue #2248 (AC4): flag already-stored invalid-URL configs in list/get
        responses, computed fresh each call (never persisted/cached)."""
        if config_dict.get("type") not in ("sse", "http"):
            config_dict["url_valid"] = None
            config_dict["url_error"] = None
            return config_dict
        try:
            validate_mcp_server_url(config_dict.get("url") or "")
        except ValueError as exc:
            config_dict["url_valid"] = False
            config_dict["url_error"] = str(exc)
        else:
            config_dict["url_valid"] = True
            config_dict["url_error"] = None
        return config_dict

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
            oauth_manager=self.coordinator.oauth_manager,
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

    async def _resolve_oauth_needs_auth(self, config_id: str) -> dict | None:
        """Check whether config_id has a usable stored OAuth token, refreshing an
        expired one. Returns None if a usable token is available (caller should
        proceed with the connection attempt), or a needs_auth_extra dict
        ({"expires_at": float | None}) to merge into a needs-auth response otherwise.

        Neither caller uses the token itself — SharedMcpConnectionManager.list_tools()
        and McpOneshotConnector.test_connect() each resolve their own token when
        building connection headers — so only the needs-auth verdict is returned.

        Uses hard expiry (expiry <= now), not OAuthRefreshManager.REFRESH_BUFFER_SECONDS
        — that 300s proactive buffer exists to keep an unattended background session
        warm before it needs the token, a different concern from a user-triggered
        action that should still try a technically-valid token rather than preemptively
        declaring needs-auth up to 5 minutes early.

        Fetches the token store once and reuses it for both the token and expiry reads
        (rather than going through get_stored_token() separately) since FernetTokenStore
        construction does blocking directory/key-file I/O.
        """
        store = self.coordinator.oauth_manager.get_token_store(config_id)
        token = await store.get_tokens()
        if token is None:
            return {"expires_at": None}
        expiry = await store.get_token_expiry()
        if expiry is not None and expiry <= time.time():
            refreshed = await self.coordinator.oauth_refresh_manager.refresh_token(config_id)
            if refreshed is None:
                return {"expires_at": expiry}
        return None

    async def get_mcp_config_tools(self, config_id: str) -> dict | None:
        """Return live tool list for a shared-connection MCP config (issue #1799).

        Returns None if the config doesn't exist (router raises 404).
        Raises ValueError if the config isn't shared_connection (router raises 400).

        Status resolution order: disabled -> needs-auth (oauth enabled, no stored or
        unrefreshable token) -> connected/failed via SharedMcpConnectionManager.
        list_tools(). The connection attempt can block up to ~35s on a broken shared
        server (inherited from SharedMcpConnectionManager._open_locked's own timeout),
        or up to ~70s if a concurrent OAuth token-refresh reconnect
        (_on_token_refreshed) is also contending for the same config's open-lock
        (issue #1806).
        """
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if config is None:
            return None
        if not config.shared_connection:
            raise ValueError("Tool listing is only supported for shared_connection MCP servers")

        if not config.enabled:
            return {"status": "disabled", "tools": [], "error": None}

        if config.oauth_enabled and config.type in (McpServerType.HTTP, McpServerType.SSE):
            needs_auth_extra = await self._resolve_oauth_needs_auth(config_id)
            if needs_auth_extra is not None:
                return {"status": "needs-auth", "tools": [], "error": None, **needs_auth_extra}

        try:
            tools = await self.coordinator.shared_mcp_manager.list_tools(config)
            return {
                "status": "connected",
                "tools": [{"name": t.name, "description": t.description} for t in tools],
                "error": None,
            }
        except Exception as exc:
            return {"status": "failed", "tools": [], "error": _describe_open_failure(exc)}

    async def test_connect_mcp_config(self, config_id: str) -> dict | None:
        """Open a throwaway connection to a non-shared-connection MCP config, list its
        tools, and close it immediately (issue #1800).

        Returns None if the config doesn't exist (router raises 404).
        Raises ValueError if the config IS shared_connection (router raises 400) — use
        get_mcp_config_tools()/the /tools endpoint for those instead.

        Status resolution order mirrors get_mcp_config_tools(): disabled -> needs-auth
        (oauth enabled, no stored or unrefreshable token) -> connected/failed via
        McpOneshotConnector.test_connect(). Blocks until the outcome is known — up to
        ~30s on a hung server.
        """
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
            needs_auth_extra = await self._resolve_oauth_needs_auth(config_id)
            if needs_auth_extra is not None:
                return {
                    "status": "needs-auth",
                    "stage": None,
                    "tools": [],
                    "error": None,
                    **needs_auth_extra,
                }

        result = await self.coordinator.mcp_oneshot_connector.test_connect(config)
        if result["status"] != "failed":
            result["tools"] = [{"name": t.name, "description": t.description} for t in result["tools"]]
        return result

    # =========================================================================
    # OAuth
    # =========================================================================

    async def oauth_complete_flow(self, state: str, code: str) -> str:
        return await self.coordinator.oauth_secret_bundle_manager.oauth_complete_flow(state, code)

    async def oauth_initiate_flow(
        self,
        config_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str,
        client_metadata_url: str | None = None,
    ) -> str | None:
        return await self.coordinator.oauth_secret_bundle_manager.oauth_initiate_flow(
            config_id, server_url, redirect_uri, client_name,
            client_metadata_url=client_metadata_url,
        )

    def get_oauth_discovery_summary(self, config_id: str) -> dict | None:
        return self.coordinator.oauth_secret_bundle_manager.oauth_manager.get_discovery_summary(
            config_id
        )

    async def oauth_disconnect(self, config_id: str) -> bool:
        return await self.coordinator.oauth_secret_bundle_manager.oauth_disconnect(config_id)

    async def oauth_get_status(self, config_id: str) -> dict | None:
        return await self.coordinator.oauth_secret_bundle_manager.oauth_get_status(config_id)

    async def import_oauth_as_secret(
        self, config_id: str, base_name: str, replace: bool = False
    ) -> dict:
        return await self.coordinator.oauth_secret_bundle_manager.import_oauth_as_secret(
            config_id, base_name, replace
        )

    # =========================================================================
    # Standalone OAuth2 vault secrets — guided authorization flow (issue #1871)
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
        return await self.coordinator.oauth_secret_bundle_manager.standalone_oauth_initiate(
            base_name,
            authorization_endpoint,
            token_endpoint,
            client_id,
            redirect_uri,
            client_secret_secret_name=client_secret_secret_name,
            scopes=scopes,
            custom_callback_path=custom_callback_path,
            custom_callback_port=custom_callback_port,
        )

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
        return await self.coordinator.oauth_secret_bundle_manager.standalone_oauth_reconnect_initiate(
            secret_name,
            authorization_endpoint,
            token_endpoint,
            client_id,
            redirect_uri,
            client_secret_secret_name=client_secret_secret_name,
            scopes=scopes,
            custom_callback_path=custom_callback_path,
            custom_callback_port=custom_callback_port,
        )

    async def standalone_oauth_complete(self, state: str, code: str) -> dict:
        return await self.coordinator.oauth_secret_bundle_manager.standalone_oauth_complete(
            state, code
        )

    async def standalone_oauth_cancel(self, flow_id: str) -> bool:
        return await self.coordinator.oauth_secret_bundle_manager.standalone_oauth_cancel(flow_id)

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
        return await self.coordinator.secrets_service.list_secrets()

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
        return await self.coordinator.secrets_service.create_secret(
            name=name,
            secret_type=secret_type,
            target_hosts=target_hosts,
            value=value,
            inject_env=inject_env,
            inject_file=inject_file,
            scrub=scrub,
            username=username,
            injection=injection,
            refresh=refresh,
        )

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
        return await self.coordinator.secrets_service.update_secret(
            name=name,
            secret_type=secret_type,
            target_hosts=target_hosts,
            value=value,
            inject_env=inject_env,
            inject_file=inject_file,
            scrub=scrub,
            username=username,
            injection=injection,
            refresh=refresh,
        )

    async def delete_secret(self, name: str) -> bool:
        return await self.coordinator.secrets_service.delete_secret(name)

    async def refresh_secret(self, name: str) -> dict | None:
        return await self.coordinator.secrets_service.refresh_secret(name)

    async def resolve_secrets_for_session(self, session_id: str) -> dict:
        return await self.coordinator.secrets_service.resolve_secrets_for_session(session_id)

    async def update_secret_for_session(
        self, session_id: str, secret_name: str, **kwargs
    ) -> dict | None:
        return await self.coordinator.secrets_service.update_secret_for_session(
            session_id, secret_name, **kwargs
        )

    async def get_proxy_status(self, session_id: str) -> dict:
        """Return effective allowlist + active credential names + proxy state for a session."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}
        return docker_utils.get_proxy_status(session, self.coordinator.data_dir)

    def schedule_secret_refresh(self, name: str) -> None:
        self.coordinator.secrets_service.schedule_secret_refresh(name)

    def unschedule_secret_refresh(self, name: str) -> None:
        self.coordinator.secrets_service.unschedule_secret_refresh(name)

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
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"entries": []}
        return docker_utils.get_proxy_blocked_log(session_id, self.coordinator.data_dir, limit)
