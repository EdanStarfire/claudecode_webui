"""
FastAPI web server for Claude Code WebUI with HTTP long-polling support.
"""

import asyncio
import html
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from .application_service import ApplicationService
from .event_queue import EventQueue
from .exception_handlers import handle_exceptions
from .file_upload import FileUploadError, FileUploadManager
from .mcp_config_manager import McpServerType
from .message_parser import MessageParser, MessageProcessor
from .models.permission_mode import PermissionMode
from .permission_resolver import resolve_effective_permissions
from .permission_service import PermissionService
from .session_config import SessionConfigBase
from .session_coordinator import SessionCoordinator
from .session_manager import SessionState
from .skill_manager import SkillManager
from .task_utils import task_done_log_exception
from .template_manager import TemplateConflictError
from .timestamp_utils import normalize_timestamp

# Keep standard logger for errors
logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for HTTP requests (issue #728).

    Exempts static assets, root HTML, health check, and auth check endpoint.
    WebSocket auth is handled separately in each WS endpoint handler.
    """

    EXEMPT_PATHS = {'/', '/health', '/api/auth/check', '/oauth/callback'}
    EXEMPT_PREFIXES = ('/assets/',)

    def __init__(self, app, auth_token: str):
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt paths
        if path in self.EXEMPT_PATHS:
            return await call_next(request)
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Extract token from Authorization header or query param
        token = None
        auth_header = request.headers.get('authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        if not token:
            token = request.query_params.get('token')

        if token != self.auth_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required", "auth_required": True}
            )

        return await call_next(request)


def validate_and_normalize_working_directory(
    path: str | None,
    default_path: str
) -> Path:
    """
    Validate and normalize working directory path.

    Args:
        path: User-provided path (may be None, relative, or absolute)
        default_path: Default path if none provided

    Returns:
        Absolute Path object

    Raises:
        ValueError: If path doesn't exist or is network path
    """
    if not path:
        return Path(default_path).resolve()

    path_obj = Path(path)

    # Convert relative to absolute
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()

    # Check if it exists
    if not path_obj.exists():
        raise ValueError(f"Working directory does not exist: {path_obj}")

    # Check if it's a directory (not file)
    if not path_obj.is_dir():
        raise ValueError(f"Working directory path is not a directory: {path_obj}")

    # Reject network paths (Windows UNC or mapped drives that don't exist)
    path_str = str(path_obj)
    if path_str.startswith('//') or path_str.startswith('\\\\'):
        raise ValueError(f"Network paths are not supported: {path_obj}")

    return path_obj


class ProjectCreateRequest(BaseModel):
    name: str
    working_directory: str
    max_concurrent_minions: int = 20  # Max concurrent minions per project

    class Config:
        # Silently ignore unknown fields like is_multi_agent for backward compatibility
        extra = "ignore"


class PermissionPreviewRequest(BaseModel):
    """Request to preview effective permissions (issue #36)"""
    working_directory: str
    setting_sources: list[str] | None = None  # Default: ["user", "project", "local"]
    session_allowed_tools: list[str] | None = None  # Session-level allowed tools


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    is_expanded: bool | None = None


class ProjectReorderRequest(BaseModel):
    project_ids: list[str]


class SessionCreateRequest(SessionConfigBase):
    project_id: str
    name: str | None = None


class MessageRequest(BaseModel):
    message: str
    metadata: dict | None = None


class SessionNameUpdateRequest(BaseModel):
    name: str


class SessionUpdateRequest(BaseModel):
    """Generic session update request - all fields optional"""
    name: str | None = None
    model: str | None = None  # sonnet, opus, haiku, opusplan
    allowed_tools: list[str] | None = None  # List of tool names to allow
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    role: str | None = None
    system_prompt: str | None = None
    override_system_prompt: bool | None = None
    capabilities: list[str] | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load
    cli_path: str | None = None  # Issue #489: custom CLI executable path
    additional_directories: list[str] | None = None  # Issue #630: extra dirs for agent access
    # Docker sub-field updates (issue #787): docker_enabled is immutable; image/mounts/home are editable
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    docker_home_directory: str | None = None
    # Thinking and effort configuration (issue #540)
    thinking_mode: str | None = None  # "adaptive", "enabled", "disabled"
    thinking_budget_tokens: int | None = None  # Token budget (min 1024)
    effort: str | None = None  # "low", "medium", "high"
    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool | None = None
    # Auto-memory mode (issue #709)
    auto_memory_mode: str | None = None  # "claude" | "session" | "disabled"
    # Custom directory for auto-memory when mode is "claude" (issue #906)
    auto_memory_directory: str | None = None
    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool | None = None
    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None
    enable_claudeai_mcp_servers: bool | None = None
    strict_mcp_config: bool | None = None
    bare_mode: bool | None = None


class SessionReorderRequest(BaseModel):
    session_ids: list[str]


class PermissionModeRequest(BaseModel):
    mode: str


class McpToggleRequest(BaseModel):
    name: str
    enabled: bool


class McpReconnectRequest(BaseModel):
    name: str


class CommSendRequest(BaseModel):
    to_minion_id: str | None = None
    to_user: bool = False
    content: str
    comm_type: str = "task"


class MinionCreateRequest(SessionConfigBase):
    name: str
    role: str | None = ""
    system_prompt: str | None = ""
    capabilities: list[str] | None = None
    working_directory: str | None = None  # Optional custom working directory for this minion
    permission_mode: str = "default"  # Override default from SessionConfigBase


class ScheduleCreateRequest(BaseModel):
    """Request to create a cron schedule (issue #495, #578)."""
    minion_id: str | None = None  # Optional for ephemeral schedules (issue #578)
    name: str
    cron_expression: str
    prompt: str
    reset_session: bool = False
    max_retries: int = 3
    timeout_seconds: int = 3600
    session_config: dict | None = None  # Ephemeral session configuration (issue #578)


class ScheduleUpdateRequest(BaseModel):
    """Request to update a schedule (issue #495, #578)."""
    name: str | None = None
    cron_expression: str | None = None
    prompt: str | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None
    session_config: dict | None = None  # Ephemeral session configuration (issue #578)
    sandbox_enabled: bool = False  # Enable OS-level sandboxing (issue #319)
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load




class TemplateCreateRequest(SessionConfigBase):
    name: str
    role: str | None = None
    system_prompt: str | None = None
    description: str | None = None
    capabilities: list[str] | None = None


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    role: str | None = None
    system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    cli_path: str | None = None  # Issue #489: custom CLI path
    additional_directories: list[str] | None = None  # Issue #630
    # Docker session isolation (issue #496)
    docker_enabled: bool | None = None
    docker_image: str | None = None
    docker_extra_mounts: list[str] | None = None
    # Thinking and effort configuration (issue #580)
    thinking_mode: str | None = None
    thinking_budget_tokens: int | None = None
    effort: str | None = None
    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool | None = None
    # Auto-memory mode (issue #709)
    auto_memory_mode: str | None = None  # "claude" | "session" | "disabled"
    # Custom directory for auto-memory when mode is "claude" (issue #906)
    auto_memory_directory: str | None = None
    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool | None = None
    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None
    # MCP toggle configuration (issue #786)
    enable_claudeai_mcp_servers: bool | None = None
    strict_mcp_config: bool | None = None


# MCP config request models (issue #676)
class McpConfigCreateRequest(BaseModel):
    name: str
    type: McpServerType
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True
    oauth_enabled: bool = False


class McpConfigUpdateRequest(BaseModel):
    name: str | None = None
    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    oauth_enabled: bool | None = None


class McpOAuthInitiateRequest(BaseModel):
    redirect_uri: str


class McpConfigExportRequest(BaseModel):
    ids: list[str] | None = None  # If None, export all


class McpConfigImportRequest(BaseModel):
    servers: dict[str, dict]  # Named dict: {serverName: {type, command, ...}}
    dry_run: bool = True



def _validate_additional_directories(dirs: list[str] | None, working_directory: str | None) -> list[str]:
    """Validate additional directories: absolute paths, no duplicates, not same as working_dir."""
    if not dirs:
        return []
    validated = []
    seen = set()
    for d in dirs:
        d = d.strip()
        if not d:
            continue
        if not os.path.isabs(d):
            raise ValueError(f"Directory must be an absolute path: {d}")
        normalized = os.path.normpath(d)
        if normalized in seen:
            continue
        if working_directory and normalized == os.path.normpath(working_directory):
            continue
        seen.add(normalized)
        validated.append(normalized)
    return validated


class ClaudeWebUI:
    """Main WebUI application class"""

    def __init__(self, data_dir: Path = None, experimental: bool = False,
                 mock_sdk: bool = False, fixtures_dir: Path | None = None,
                 available_fixtures: list[str] | None = None,
                 config_file: Path | None = None,
                 auth_token: str | None = None, auth_enabled: bool = False):
        self.app = FastAPI(title="Claude Code WebUI", version="1.0.0")
        self.coordinator = SessionCoordinator(data_dir, experimental=experimental)
        self.service = ApplicationService(self.coordinator)
        self.config_file = config_file

        # Authentication (issue #728)
        self.auth_token = auth_token
        self.auth_enabled = auth_enabled

        # Wire mock SDK factory if mock mode active (issue #561)
        if mock_sdk and fixtures_dir:
            from src.mock_sdk import MockClaudeSDK
            self.coordinator.set_sdk_factory(
                _mock_factory_for_fixtures(
                    MockClaudeSDK, fixtures_dir, set(available_fixtures or [])
                )
            )
        self.skill_manager = SkillManager()
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {}

        # Inject ui_queue into LegionSystem so legion components can append events directly
        self.coordinator.legion_system.ui_queue = self.ui_queue

        # Initialize MessageProcessor for unified WebSocket message formatting
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Permission lifecycle management (session_queues must be initialized first)
        self.permission_service = PermissionService(self.coordinator, self.session_queues)

        # Rate limiting for restart endpoint (issue #434)
        self._last_restart_time: float = 0

        # Setup routes
        self._setup_routes()

        # Register auth middleware if enabled (issue #728)
        if self.auth_enabled and self.auth_token:
            self.app.add_middleware(AuthMiddleware, auth_token=self.auth_token)
            logger.info("Authentication middleware enabled")

        # Issue #699: Wire UI notification callback for comm sounds
        self.coordinator.legion_system.comm_router.set_ui_notification_callback(
            self._broadcast_comm_notification_to_ui
        )
        self.coordinator.legion_system.scheduler_service.set_schedule_broadcast_callback(
            self._broadcast_schedule_event
        )

        # Inject permission callback factory into SessionCoordinator
        # This allows legion components (overseer_controller, comm_router) to create
        # permission callbacks for spawned minions without direct access to web_server
        self.coordinator.set_permission_callback_factory(self._get_permission_callback_factory())
        logger.info("Permission callback factory injected into SessionCoordinator")

        # Inject message callback registrar into SessionCoordinator
        # This allows legion components to register WebSocket message callbacks
        self.coordinator.set_message_callback_registrar(self._get_message_callback_registrar())
        logger.info("Message callback registrar injected into SessionCoordinator")

        # Issue #404: Inject resource broadcast callback into SessionCoordinator
        # This allows ResourceMCPTools to broadcast resource_registered events
        self.coordinator.set_resource_broadcast_callback(self._broadcast_resource_registered)
        logger.info("Resource broadcast callback injected into SessionCoordinator")

        # Issue #976: Inject OAuth refresh broadcast callback into SessionCoordinator
        self.coordinator.set_oauth_refresh_broadcast_callback(self._broadcast_mcp_oauth_refreshed)
        logger.info("OAuth refresh broadcast callback injected into SessionCoordinator")

        # Setup static files (Vue 3 production build)
        static_dir = Path(__file__).parent.parent / "frontend" / "dist"
        if not static_dir.exists():
            raise RuntimeError(
                f"Frontend build not found at {static_dir}. "
                "Run 'cd frontend && npm run build' to create production build."
            )
        self.app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    def _get_permission_callback_factory(self):
        """
        Return a factory function that creates permission callbacks for any session.

        This allows components without direct access to web_server (like overseer_controller)
        to create permission callbacks that broadcast to WebSockets and handle async approval.

        Pattern: Same as user-created minions, but accessible from legion components.

        Returns:
            Callable[[str], Callable]: Factory function taking session_id, returning permission_callback
        """
        def factory(session_id: str):
            return self.permission_service.create_permission_callback(session_id)
        return factory

    def _get_message_callback_registrar(self):
        """
        Return a function that registers message callbacks for any session.

        This allows components without direct access to web_server (like overseer_controller)
        to register WebSocket message callbacks for spawned minions.

        Pattern: Same as user-created minions, but accessible from legion components.

        Returns:
            Callable[[str], None]: Function taking session_id, registers message callback
        """
        def registrar(session_id: str):
            # Clear any existing callbacks to prevent duplicates
            self.coordinator.clear_message_callbacks(session_id)

            # Register message callback for this session
            self.coordinator.add_message_callback(
                session_id,
                self._create_message_callback(session_id)
            )
            logger.info(f"Registered message callback for session {session_id}")

            # Create session poll queue if not already present
            if session_id not in self.session_queues:
                self.session_queues[session_id] = EventQueue()

            # Broadcast project_updated so UI shows new child session in real-time
            async def _broadcast_session_added():
                try:
                    session = await self.service._get_session_object(session_id)
                    if session and session.project_id:
                        project_dict = await self.service.get_project(session.project_id)
                        if project_dict:
                            self._broadcast_project_updated(
                                {k: v for k, v in project_dict.items() if k != "sessions"}
                            )
                            logger.debug(f"Appended project_updated for internally spawned session {session_id}")
                except Exception:
                    logger.exception(f"Error broadcasting project_updated for session {session_id}")

            task = asyncio.create_task(_broadcast_session_added(), name="broadcast_session_added")
            task.add_done_callback(task_done_log_exception)

        return registrar

    async def _broadcast_comm_notification_to_ui(self, comm):
        """Issue #699: Push comm notification event to UI poll queue for audio alerts."""
        try:
            self.ui_queue.append({
                "type": "notification",
                "data": {
                    "event_type": "minion_comm",
                    "comm_type": comm.comm_type.value if hasattr(comm.comm_type, 'value') else str(comm.comm_type),
                    "from_minion_name": comm.from_minion_name or "Minion",
                    "comm_id": comm.comm_id,
                }
            })
            logger.debug(f"Appended UI notification for comm {comm.comm_id}")
        except Exception:
            logger.exception("Error appending comm notification to UI queue")

    async def _broadcast_schedule_event(self, legion_id: str, event: dict):
        """Broadcast schedule event to UI poll queue."""
        try:
            self.ui_queue.append(event)
        except Exception:
            logger.exception("Error appending schedule event")

    def _broadcast_project_updated(self, project: dict) -> None:
        """Emit project_updated to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "project_updated", "data": {"project": project}})
            logger.debug("Appended project_updated for project %s", project.get("project_id"))
        except Exception:
            logger.exception("Error appending project_updated")

    def _broadcast_project_deleted(self, project_id: str) -> None:
        """Emit project_deleted to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "project_deleted", "data": {"project_id": project_id}})
            logger.debug("Appended project_deleted for project %s", project_id)
        except Exception:
            logger.exception("Error appending project_deleted")

    def _broadcast_state_change(self, session_id: str, session_dict: dict, timestamp: str | None = None) -> None:
        """Emit state_change to the global UI poll queue."""
        try:
            self.ui_queue.append({
                "type": "state_change",
                "data": {"session_id": session_id, "session": session_dict, "timestamp": timestamp}
            })
            logger.info("Appended state_change for session %s", session_id)
        except Exception:
            logger.exception("Error appending state_change")

    def _broadcast_server_restarting(self, pull_output: str, sync_output: str) -> None:
        """Emit server_restarting to the global UI poll queue."""
        try:
            self.ui_queue.append({
                "type": "server_restarting",
                "message": "Server is restarting...",
                "pull_output": pull_output,
                "sync_output": sync_output,
                "timestamp": datetime.now(UTC).isoformat(),
            })
        except Exception:
            logger.warning("Failed to append restart notice")

    def _broadcast_mcp_oauth_complete(self, server_id: str) -> None:
        """Emit mcp_oauth_complete to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "mcp_oauth_complete", "server_id": server_id})
        except Exception:
            logger.exception("Error appending mcp_oauth_complete")

    def _broadcast_mcp_oauth_refreshed(self, server_id: str) -> None:
        """Issue #976: Emit mcp_oauth_refreshed to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "mcp_oauth_refreshed", "server_id": server_id})
        except Exception:
            logger.exception("Error appending mcp_oauth_refreshed")

    def _broadcast_rate_limits_update(self, data: dict) -> None:
        """Issue #899: Emit rate_limits_update to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "rate_limits_update", "data": data})
        except Exception:
            logger.exception("Error appending rate_limits_update")

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict):
        """
        Append resource_registered event to session poll queue.

        Issue #404: Called by ResourceMCPTools when a resource is registered.

        Args:
            session_id: Session that registered the resource
            resource_metadata: Resource metadata dict (resource_id, title, is_image, etc.)
        """
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "resource_registered",
                    "resource": resource_metadata,
                    "timestamp": datetime.now(UTC).isoformat()
                })
                logger.debug(f"Appended resource_registered for {resource_metadata.get('resource_id')} to session {session_id}")
        except Exception:
            logger.exception("Error appending resource_registered")

    async def _broadcast_queue_update(self, session_id: str, action: str, item: dict):
        """
        Append queue update to session poll queue.

        Issue #500: Real-time queue status updates.

        Args:
            session_id: Session ID
            action: enqueued|sent|failed|cancelled|cleared|paused|resumed
            item: Queue item dict or action-specific data
        """
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "queue_update",
                    "action": action,
                    "item": item,
                    "pending_count": self.coordinator.queue_manager.get_pending_count(session_id),
                    "timestamp": datetime.now(UTC).isoformat()
                })
        except Exception:
            logger.exception("Error appending queue_update")

    def _cleanup_pending_permissions_for_session(self, session_id: str):
        """Clean up pending permissions for a specific session by auto-denying them"""
        self.permission_service.cleanup_pending_for_session(session_id)

    async def initialize(self):
        """Initialize the WebUI application"""
        from .config_manager import load_config
        await self.coordinator.initialize()
        config = load_config(self.config_file) if self.config_file else load_config()
        if config.features.skill_sync_enabled:
            await self.skill_manager.sync()
        else:
            logger.info("Skill syncing disabled by config")

        # Templates are now loaded in SessionCoordinator.initialize()

        # Create event queues for all existing sessions
        sessions_result = await self.coordinator.list_sessions()
        for s in sessions_result.get("sessions", []):
            sid = s.get('session_id') or (s.get('session') or {}).get('session_id')
            if sid:
                self.session_queues[sid] = EventQueue()

        # Register callbacks
        self.coordinator.add_state_change_callback(self._on_state_change)
        self.coordinator.add_session_reset_callback(self._on_session_reset)
        self.coordinator.add_tool_call_broadcast_callback(self._on_tool_call_broadcast)
        self.coordinator.set_rate_limit_broadcast_callback(self._broadcast_rate_limits_update)

        # Issue #500: Wire queue processor broadcast callback
        self.coordinator.queue_processor.set_broadcast_callback(self._broadcast_queue_update)

        logger.info("Claude Code WebUI initialized")

    def _setup_routes(self):
        """Setup FastAPI routes"""

        # ==================== POLL ENDPOINTS ====================

        @self.app.get("/api/poll/ui")
        @handle_exceptions("poll ui")
        async def poll_ui(since: int = 0, timeout: int = 30):
            """HTTP long-poll endpoint for global UI events."""
            effective_timeout = min(float(timeout), 30.0)
            await self.ui_queue.wait_for_events(since, timeout=effective_timeout)
            events, next_cursor = self.ui_queue.events_since(since)
            return {"events": events, "next_cursor": next_cursor}

        @self.app.get("/api/poll/cursor")
        @handle_exceptions("poll cursor")
        async def get_poll_cursor():
            """Return current UI event queue cursor position for client initialization."""
            return {"cursor": self.ui_queue.current_cursor}

        @self.app.get("/api/poll/session/{session_id}/cursor")
        @handle_exceptions("poll session cursor")
        async def get_session_poll_cursor(session_id: str):
            """Return current session event queue cursor position for client initialization."""
            if session_id not in self.session_queues:
                if not await self.service.get_session_exists(session_id):
                    raise HTTPException(status_code=404, detail="Session not found")
                return {"cursor": 0}  # session exists but queue not yet initialized
            return {"cursor": self.session_queues[session_id].current_cursor}

        @self.app.get("/api/poll/session/{session_id}")
        @handle_exceptions("poll session")
        async def poll_session(session_id: str, since: int = 0, timeout: int = 30):
            """HTTP long-poll endpoint for session-specific events."""
            if session_id not in self.session_queues:
                if not await self.service.get_session_exists(session_id):
                    raise HTTPException(status_code=404, detail="Session not found")
                self.session_queues[session_id] = EventQueue()
            queue = self.session_queues[session_id]
            effective_timeout = min(float(timeout), 30.0)
            await queue.wait_for_events(since, timeout=effective_timeout)
            events, next_cursor = queue.events_since(since)
            return {"events": events, "next_cursor": next_cursor}

        # ==================== REST INBOUND ENDPOINTS ====================

        class PermissionResponseRequest(BaseModel):
            decision: str
            apply_suggestions: bool = False
            clarification_message: str | None = None
            selected_suggestions: list | None = None
            updated_input: dict | None = None

        @self.app.post("/api/sessions/{session_id}/interrupt")
        @handle_exceptions("interrupt session")
        async def interrupt_session_rest(session_id: str):
            """Interrupt a session via REST (replaces WebSocket interrupt_session message)."""
            state = await self.service.get_session_state(session_id)
            if state is None:
                raise HTTPException(status_code=404, detail="Session not found")
            if state == SessionState.PAUSED:
                self.permission_service.deny_all_for_interrupt()
            result = await self.coordinator.interrupt_session(session_id)
            return {"success": bool(result)}

        @self.app.post("/api/sessions/{session_id}/permission/{request_id}")
        @handle_exceptions("respond to permission")
        async def respond_to_permission(
            session_id: str, request_id: str, request: PermissionResponseRequest
        ):
            """Respond to a pending permission request via REST."""
            if request_id not in self.permission_service.pending_permissions:
                raise HTTPException(status_code=404, detail="No pending permission with that ID")
            if request.decision == "allow":
                response = {"behavior": "allow"}
                if request.updated_input is not None:
                    response["updated_input"] = request.updated_input
                if request.apply_suggestions:
                    response["apply_suggestions"] = request.apply_suggestions
                if request.selected_suggestions is not None:
                    response["selected_suggestions"] = request.selected_suggestions
            else:
                if request.clarification_message:
                    response = {
                        "behavior": "deny",
                        "message": request.clarification_message,
                        "interrupt": False
                    }
                else:
                    response = {"behavior": "deny", "message": "User denied permission"}
            resolved = self.permission_service.resolve(request_id, response)
            if not resolved:
                raise HTTPException(status_code=409, detail="Permission already resolved")
            return {"success": True}

        @self.app.get("/", response_class=HTMLResponse)
        @handle_exceptions("serve root")
        async def read_root():
            """Serve the main HTML page"""
            html_file = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
            if html_file.exists():
                return HTMLResponse(
                    content=html_file.read_text(encoding='utf-8'),
                    status_code=200,
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                )
            return HTMLResponse(content=self._default_html(), status_code=200)

        @self.app.get("/health")
        @handle_exceptions("health check")
        async def health_check():
            """Health check endpoint"""
            return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

        @self.app.get("/api/auth/check")
        @handle_exceptions("check auth")
        async def auth_check(request: Request):
            """Check authentication status (issue #728). Exempt from auth middleware."""
            authenticated = False
            if self.auth_enabled and self.auth_token:
                # Check Authorization header
                auth_header = request.headers.get('authorization', '')
                if auth_header.startswith('Bearer ') and auth_header[7:] == self.auth_token:
                    authenticated = True
                # Check query param
                if not authenticated and request.query_params.get('token') == self.auth_token:
                    authenticated = True
            elif not self.auth_enabled:
                authenticated = True
            return {"auth_required": self.auth_enabled, "authenticated": authenticated}

        # ==================== PROJECT ENDPOINTS ====================

        @self.app.post("/api/projects")
        @handle_exceptions("create project")
        async def create_project(request: ProjectCreateRequest):
            """Create a new project."""
            project = await self.service.create_project(
                name=request.name,
                working_directory=request.working_directory,
                max_concurrent_minions=request.max_concurrent_minions,
            )
            self._broadcast_project_updated(project)
            return {"project": project}

        @self.app.get("/api/projects")
        @handle_exceptions("list projects")
        async def list_projects(limit: int = 200, offset: int = 0):
            """List all projects."""
            return await self.service.list_projects(limit=limit, offset=offset)

        @self.app.get("/api/projects/{project_id}")
        @handle_exceptions("get project")
        async def get_project(project_id: str):
            """Get project with sessions"""
            result = await self.service.get_project(project_id)
            if not result:
                raise HTTPException(status_code=404, detail="Project not found")
            return {
                "project": {k: v for k, v in result.items() if k != "sessions"},
                "sessions": result.get("sessions", []),
            }

        @self.app.put("/api/projects/reorder")
        @handle_exceptions("reorder projects")
        async def reorder_projects(request: ProjectReorderRequest):
            """Reorder projects"""
            success = await self.service.reorder_projects(request.project_ids)
            if not success:
                raise HTTPException(status_code=400, detail="Failed to reorder projects")
            return {"success": True}

        @self.app.put("/api/projects/{project_id}")
        @handle_exceptions("update project")
        async def update_project(project_id: str, request: ProjectUpdateRequest):
            """Update project metadata"""
            result = await self.service.update_project(
                project_id,
                name=request.name,
                is_expanded=request.is_expanded,
            )
            if not result:
                raise HTTPException(status_code=404, detail="Project not found")

            self._broadcast_project_updated(result)

            return {"success": True}

        @self.app.delete("/api/projects/{project_id}")
        @handle_exceptions("delete project")
        async def delete_project(project_id: str):
            """Delete project and all its sessions"""
            project_result = await self.service.get_project(project_id)
            if not project_result:
                raise HTTPException(status_code=404, detail="Project not found")

            # Delete all sessions in the project
            for session_id in project_result.get("sessions", []):
                sid = session_id if isinstance(session_id, str) else session_id.get("session_id")
                if sid:
                    await self.coordinator.delete_session(sid)

            # Delete the project
            del_result = await self.service.delete_project(project_id)
            if not del_result.get("success"):
                raise HTTPException(status_code=500, detail="Failed to delete project")

            self._broadcast_project_deleted(project_id)

            return {"success": True}

        @self.app.put("/api/projects/{project_id}/toggle-expansion")
        @handle_exceptions("toggle project expansion")
        async def toggle_project_expansion(project_id: str):
            """Toggle project expansion state"""
            result = await self.service.toggle_project_expansion(project_id)
            if not result:
                raise HTTPException(status_code=404, detail="Project not found")

            self._broadcast_project_updated(result)

            return {"success": True, "is_expanded": result.get("is_expanded")}

        @self.app.put("/api/projects/{project_id}/sessions/reorder")
        @handle_exceptions("reorder project sessions")
        async def reorder_project_sessions(project_id: str, request: SessionReorderRequest):
            """Reorder sessions within a project"""
            result = await self.service.reorder_project_sessions(project_id, request.session_ids)
            if not result:
                raise HTTPException(status_code=400, detail="Failed to reorder sessions")

            self._broadcast_project_updated(result)

            return {"success": True}

        # ==================== ARCHIVE ENDPOINTS ====================

        @self.app.get("/api/projects/{project_id}/archives/{session_id}")
        @handle_exceptions("list session archives")
        async def list_session_archives(project_id: str, session_id: str, limit: int = 50, offset: int = 0):
            """List archives for a session within a project, paginated."""
            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            return await self.coordinator.get_archives(session_id, limit=limit, offset=offset)

        @self.app.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/messages")
        @handle_exceptions("get archive messages")
        async def get_archive_messages(
            project_id: str, session_id: str, archive_id: str,
            limit: int | None = 50, offset: int = 0
        ):
            """Get paginated messages from an archive."""
            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            result = await self.coordinator.get_archive_messages(
                session_id, archive_id, offset=offset, limit=limit
            )
            return result

        @self.app.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/state")
        @handle_exceptions("get archive state")
        async def get_archive_state(project_id: str, session_id: str, archive_id: str):
            """Get archive state and disposal metadata."""
            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            result = await self.coordinator.get_archive_state(session_id, archive_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Archive not found")
            return result

        @self.app.get(
            "/api/projects/{project_id}/archives/{session_id}/{archive_id}/resources"
        )
        @handle_exceptions("get archive resources")
        async def get_archive_resources(
            project_id: str,
            session_id: str,
            archive_id: str,
            limit: int = 50,
            offset: int = 0,
            search: str | None = None,
            format_filter: str | None = None,
            sort: str = "newest",
        ):
            """List resource metadata from an archive, paginated with optional filter/sort."""
            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            return await self.coordinator.get_archive_resources(
                session_id,
                archive_id,
                limit=limit,
                offset=offset,
                search=search,
                format_filter=format_filter,
                sort=sort,
            )

        @self.app.get(
            "/api/projects/{project_id}/archives/{session_id}/{archive_id}"
            "/resources/{resource_id}"
        )
        @handle_exceptions("get archive resource file")
        async def get_archive_resource_file(
            project_id: str, session_id: str, archive_id: str, resource_id: str
        ):
            """Get raw file data for a resource in an archive."""
            from fastapi.responses import Response

            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")

            result = await self.coordinator.get_archive_resources(
                session_id, archive_id, limit=10000
            )
            resource_meta = next(
                (r for r in result["resources"] if r.get("resource_id") == resource_id), None
            )
            if not resource_meta:
                raise HTTPException(status_code=404, detail="Resource not found")

            resource_bytes = await self.coordinator.get_archive_resource_file(
                session_id, archive_id, resource_id
            )
            if not resource_bytes:
                raise HTTPException(
                    status_code=404, detail="Resource file not found"
                )

            content_type = resource_meta.get(
                "mime_type", "application/octet-stream"
            )
            original_name = resource_meta.get(
                "original_name", f"{resource_id}.bin"
            )

            return Response(
                content=resource_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{original_name}"'
                },
            )

        @self.app.get("/api/projects/{project_id}/deleted-agents")
        @handle_exceptions("list deleted agents")
        async def list_deleted_agents(project_id: str, limit: int = 50, offset: int = 0):
            """List deleted agents with archives for a project, paginated."""
            if not await self.service.validate_project_exists(project_id):
                raise HTTPException(status_code=404, detail="Project not found")
            return await self.coordinator.list_project_deleted_agents(project_id, limit=limit, offset=offset)

        # ==================== SESSION ENDPOINTS ====================

        @self.app.post("/api/sessions")
        @handle_exceptions("create session")
        async def create_session(request: SessionCreateRequest):
            """Create a new Claude Code session within a project"""
            # Pre-generate session ID so we can pass it to permission callback
            session_id = str(uuid.uuid4())

            if request.project_id:
                if not await self.service.validate_project_exists(request.project_id):
                    raise HTTPException(status_code=404, detail="Project not found")

            # Issue #630: Validate additional directories
            validated_dirs = _validate_additional_directories(
                request.additional_directories, None
            )

            config = request.to_session_config(additional_directories=validated_dirs)
            session_id = await self.coordinator.create_session(
                session_id=session_id,
                project_id=request.project_id,
                config=config,
                name=request.name,
                permission_callback=self.permission_service.create_permission_callback(session_id),
            )

            # Create event queue for this new session
            self.session_queues[session_id] = EventQueue()

            # Broadcast session creation to all UI clients
            session_info_dict = await self.coordinator.get_session_info(session_id)
            if session_info_dict:
                self._broadcast_state_change(
                    session_id,
                    session_info_dict.get("session", session_info_dict),
                    datetime.now().isoformat()
                )

            # Broadcast project update to all UI clients (session was added to project)
            project_dict = await self.service.get_project(request.project_id) if request.project_id else None
            if project_dict:
                self._broadcast_project_updated(
                    {k: v for k, v in project_dict.items() if k != "sessions"}
                )

            return {"session_id": session_id}

        @self.app.get("/api/sessions")
        @handle_exceptions("list sessions")
        async def list_sessions(limit: int = 500, offset: int = 0):
            """List all sessions"""
            return await self.coordinator.list_sessions(limit=limit, offset=offset)

        @self.app.get("/api/sessions/{session_id}")
        @handle_exceptions("get session info")
        async def get_session_info(session_id: str):
            """Get session information"""
            info = await self.coordinator.get_session_info(session_id)
            if not info:
                raise HTTPException(status_code=404, detail="Session not found")

            # Log session state for debugging
            session_state = info.get('session', {}).get('state', 'unknown')
            logger.info(f"API returning session {session_id} with state: {session_state}")

            return info

        @self.app.get("/api/sessions/{session_id}/descendants")
        @handle_exceptions("get session descendants")
        async def get_session_descendants(session_id: str, limit: int = 50, offset: int = 0):
            """Get all descendant sessions (children, grandchildren, etc.) of a session"""
            result = await self.coordinator.get_descendants(session_id, limit=limit, offset=offset)
            result["session_id"] = session_id
            return result

        @self.app.post("/api/sessions/{session_id}/start")
        @handle_exceptions("start session")
        async def start_session(session_id: str):
            """Start a session"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            # Clear any existing callbacks to prevent duplicates, then register fresh one
            self.coordinator.clear_message_callbacks(session_id)
            self.coordinator.add_message_callback(
                session_id,
                self._create_message_callback(session_id)
            )

            success = await self.coordinator.start_session(
                session_id,
                permission_callback=self.permission_service.create_permission_callback(session_id),
            )
            return {"success": success}

        @self.app.post("/api/sessions/{session_id}/terminate")
        @handle_exceptions("terminate session")
        async def terminate_session(session_id: str):
            """Terminate a session"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            # Clean up any pending permissions for this session
            self._cleanup_pending_permissions_for_session(session_id)

            success = await self.coordinator.terminate_session(session_id)
            return {"success": success}

        @self.app.put("/api/sessions/{session_id}/name")
        @handle_exceptions("update session name")
        async def update_session_name(session_id: str, request: SessionNameUpdateRequest):
            """Update session name"""
            success = await self.coordinator.update_session_name(session_id, request.name)
            if not success:
                raise HTTPException(status_code=404, detail="Session not found")
            return {"success": success}

        @self.app.patch("/api/sessions/{session_id}")
        @handle_exceptions("update session")
        async def update_session(session_id: str, request: SessionUpdateRequest):
            """Update session fields (generic endpoint)"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            updates = {}

            # Handle name update
            if request.name is not None:
                updates["name"] = request.name

            # Handle model update (takes effect on next restart if session is active)
            if request.model is not None:
                valid_models = ["sonnet", "opus", "haiku", "opusplan"]
                if request.model not in valid_models:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid model. Must be one of: {', '.join(valid_models)}"
                    )
                updates["model"] = request.model

            # Handle allowed_tools update (takes effect on next restart if session is active)
            if request.allowed_tools is not None:
                updates["allowed_tools"] = request.allowed_tools

            # Handle disallowed_tools update (takes effect on next reset if session is active)
            if request.disallowed_tools is not None:
                updates["disallowed_tools"] = request.disallowed_tools

            # Handle role update
            if request.role is not None:
                updates["role"] = request.role

            # Handle system_prompt update
            if request.system_prompt is not None:
                updates["system_prompt"] = request.system_prompt

            # Handle override_system_prompt update
            if request.override_system_prompt is not None:
                updates["override_system_prompt"] = request.override_system_prompt

            # Handle capabilities update
            if request.capabilities is not None:
                updates["capabilities"] = request.capabilities

            # Handle sandbox_enabled update
            if request.sandbox_enabled is not None:
                updates["sandbox_enabled"] = request.sandbox_enabled

            # Handle sandbox_config update (issue #458)
            if request.sandbox_config is not None:
                updates["sandbox_config"] = request.sandbox_config

            # Handle setting_sources update (issue #36)
            if request.setting_sources is not None:
                updates["setting_sources"] = request.setting_sources

            # Handle cli_path update (issue #489)
            # Empty string means clear the custom CLI path
            if request.cli_path is not None:
                updates["cli_path"] = request.cli_path if request.cli_path.strip() else None

            # Handle additional_directories update (issue #630)
            if request.additional_directories is not None:
                session_wd = await self.service.get_session_working_directory(session_id)
                validated_dirs = _validate_additional_directories(
                    request.additional_directories, session_wd
                )
                updates["additional_directories"] = validated_dirs

            # Handle Docker sub-field updates (docker_enabled is immutable; image/mounts/home are editable)
            # Takes effect on next restart if session is active.
            if request.docker_image is not None:
                updates["docker_image"] = request.docker_image if request.docker_image.strip() else None
            if request.docker_extra_mounts is not None:
                updates["docker_extra_mounts"] = request.docker_extra_mounts
            if request.docker_home_directory is not None:
                updates["docker_home_directory"] = request.docker_home_directory if request.docker_home_directory.strip() else None

            # Handle thinking and effort configuration (issue #540)
            if request.thinking_mode is not None:
                updates["thinking_mode"] = request.thinking_mode if request.thinking_mode else None
            if request.thinking_budget_tokens is not None:
                updates["thinking_budget_tokens"] = request.thinking_budget_tokens
            if request.effort is not None:
                eff = request.effort if request.effort else None
                if eff == 'max':
                    eff = 'high'
                updates["effort"] = eff

            if request.history_distillation_enabled is not None:
                updates["history_distillation_enabled"] = request.history_distillation_enabled

            if request.auto_memory_mode is not None:
                updates["auto_memory_mode"] = request.auto_memory_mode

            if request.auto_memory_directory is not None:
                updates["auto_memory_directory"] = request.auto_memory_directory

            if request.skill_creating_enabled is not None:
                updates["skill_creating_enabled"] = request.skill_creating_enabled

            # MCP server configuration (issue #676)
            if request.mcp_server_ids is not None:
                updates["mcp_server_ids"] = request.mcp_server_ids
            if request.enable_claudeai_mcp_servers is not None:
                updates["enable_claudeai_mcp_servers"] = request.enable_claudeai_mcp_servers
            if request.strict_mcp_config is not None:
                updates["strict_mcp_config"] = request.strict_mcp_config
            if request.bare_mode is not None:
                updates["bare_mode"] = request.bare_mode

            if not updates:
                return {"success": True, "message": "No fields to update"}

            success = await self.service.update_session(session_id, **updates)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to update session")

            return {"success": success}

        @self.app.delete("/api/sessions/{session_id}")
        @handle_exceptions("delete session")
        async def delete_session(session_id: str):
            """Delete a session and all its data (including cascaded child sessions)"""
            # Clean up any pending permissions for this session
            self._cleanup_pending_permissions_for_session(session_id)

            # Delete the session; service handles project state tracking
            result = await self.service.delete_session(session_id)
            if not result.get("success"):
                raise HTTPException(status_code=404, detail="Session not found")

            deleted_ids = result.get("deleted_session_ids", [])

            # Clean up event queues and pending permissions for cascaded child sessions
            self.session_queues.pop(session_id, None)
            for deleted_id in deleted_ids:
                if deleted_id != session_id:
                    self._cleanup_pending_permissions_for_session(deleted_id)
                    self.session_queues.pop(deleted_id, None)

            # Broadcast project state changes
            project_id = result.get("project_id")
            if project_id:
                if result.get("project_deleted"):
                    self._broadcast_project_deleted(project_id)
                    logger.info(f"Appended project_deleted for auto-deleted project {project_id}")
                else:
                    updated_project = result.get("updated_project")
                    if updated_project:
                        self._broadcast_project_updated(updated_project)
                        logger.debug(
                            f"Appended project_updated for project {project_id} after session deletion"
                        )

            return {
                "success": result.get("success"),
                "deleted_session_ids": deleted_ids
            }

        @self.app.post("/api/sessions/{session_id}/messages")
        @handle_exceptions("send message")
        async def send_message(session_id: str, request: MessageRequest):
            """Send a message to a session"""
            state = await self.service.get_session_state(session_id)
            if state is None:
                raise HTTPException(status_code=404, detail="Session not found")
            if state != SessionState.ACTIVE:
                raise HTTPException(status_code=409, detail="Session is not active")
            success = await self.coordinator.send_message(session_id, request.message, metadata=request.metadata)
            return {"success": success}

        @self.app.get("/api/sessions/{session_id}/messages")
        @handle_exceptions("get messages")
        async def get_messages(session_id: str, limit: int | None = 50, offset: int = 0):
            """Get messages from a session with pagination metadata"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            result = await self.coordinator.get_session_messages(
                session_id, limit=limit, offset=offset
            )
            return result

        # ==================== FILE UPLOAD ENDPOINTS ====================

        @self.app.post("/api/sessions/{session_id}/files")
        @handle_exceptions("upload file")
        async def upload_file(session_id: str, file: Annotated[UploadFile, File(...)]):
            """
            Upload a file for a session.

            Files are stored in data/sessions/{session_id}/attachments/
            and paths are passed to Claude for reading via the Read tool.
            """
            # Verify session exists
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            # Initialize file upload manager if not already done
            file_manager = FileUploadManager(self.coordinator.data_dir / "sessions")

            # Read file content
            file_content = await file.read()

            # Upload file
            try:
                file_info = await file_manager.upload_file(
                    session_id=session_id,
                    filename=file.filename,
                    file_data=file_content,
                    content_type=file.content_type
                )
            except FileUploadError as e:
                logger.warning(f"File upload validation failed: {e.message}")
                raise HTTPException(status_code=400, detail=e.message) from e

            # Register path for auto-approve (via session coordinator)
            await self.coordinator.register_uploaded_file(session_id, file_info.stored_path)

            # Issue #404: Auto-register all uploaded files to resource gallery
            resource_meta = None
            try:
                resource_meta = await self.coordinator.register_uploaded_resource(
                    session_id=session_id,
                    file_path=file_info.stored_path,
                    title=file_info.original_name,
                    description="Uploaded by user"
                )
                logger.info(
                    f"Auto-registered uploaded file to gallery: "
                    f"{file_info.original_name}"
                )
            except Exception as e:
                # Don't fail the upload if resource registration fails
                logger.warning(
                    f"Failed to register uploaded file to gallery: {e}"
                )

            response = {
                "success": True,
                "file": file_info.to_dict()
            }
            # Issue #774: Include resource metadata for attachment summaries
            if resource_meta:
                response["file"]["resource_id"] = resource_meta["resource_id"]
                response["file"]["markdown"] = resource_meta["markdown"]
            return response

        @self.app.get("/api/sessions/{session_id}/files")
        @handle_exceptions("list session files")
        async def list_session_files(session_id: str, limit: int = 100, offset: int = 0):
            """List uploaded files for a session, paginated"""
            # Verify session exists
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            file_manager = FileUploadManager(self.coordinator.data_dir / "sessions")
            all_files = await file_manager.list_files(session_id)
            all_dicts = [f.to_dict() for f in all_files]
            total = len(all_dicts)
            sliced = all_dicts[offset : offset + limit]
            return {
                "files": sliced,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(sliced) < total,
            }

        @self.app.delete("/api/sessions/{session_id}/files/{file_id}")
        @handle_exceptions("delete file")
        async def delete_file(session_id: str, file_id: str):
            """Delete an uploaded file"""
            # Verify session exists
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            file_manager = FileUploadManager(self.coordinator.data_dir / "sessions")

            # Get file info before deleting (to unregister path)
            file_info = await file_manager.get_file_info(session_id, file_id)
            if file_info:
                # Unregister from auto-approve
                await self.coordinator.unregister_uploaded_file(session_id, file_info.stored_path)

            success = await file_manager.delete_file(session_id, file_id)
            if not success:
                raise HTTPException(status_code=404, detail="File not found")

            return {"success": True}

        # Issue #404: Resource gallery endpoints
        @self.app.get("/api/sessions/{session_id}/resources")
        @handle_exceptions("get session resources")
        async def get_session_resources(
            session_id: str,
            limit: int = 50,
            offset: int = 0,
            search: str | None = None,
            format_filter: str | None = None,
            sort: str = "newest",
        ):
            """Get resource metadata for a session, paginated with optional filter/sort"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            return await self.coordinator.get_session_resources(
                session_id,
                limit=limit,
                offset=offset,
                search=search,
                format_filter=format_filter,
                sort=sort,
            )

        @self.app.get("/api/sessions/{session_id}/resources/{resource_id}")
        @handle_exceptions("get session resource")
        async def get_session_resource(session_id: str, resource_id: str):
            """Get raw file data for a specific resource"""
            from fastapi.responses import Response

            # Get resource metadata to determine content type
            result = await self.coordinator.get_session_resources(session_id, limit=10000)
            resource_meta = next((r for r in result["resources"] if r.get("resource_id") == resource_id), None)

            if not resource_meta:
                raise HTTPException(status_code=404, detail="Resource not found")

            # Get resource bytes
            resource_bytes = await self.coordinator.get_session_resource_file(session_id, resource_id)
            if not resource_bytes:
                raise HTTPException(status_code=404, detail="Resource file not found")

            # Use mime_type from metadata, fallback to octet-stream
            content_type = resource_meta.get("mime_type", "application/octet-stream")
            original_name = resource_meta.get("original_name", f"{resource_id}.bin")

            return Response(
                content=resource_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{original_name}"'
                }
            )

        @self.app.get("/api/sessions/{session_id}/resources/{resource_id}/download")
        @handle_exceptions("download session resource")
        async def download_session_resource(session_id: str, resource_id: str):
            """Download a resource file"""
            from fastapi.responses import Response

            # Get resource metadata
            result = await self.coordinator.get_session_resources(session_id, limit=10000)
            resource_meta = next((r for r in result["resources"] if r.get("resource_id") == resource_id), None)

            if not resource_meta:
                raise HTTPException(status_code=404, detail="Resource not found")

            # Get resource bytes
            resource_bytes = await self.coordinator.get_session_resource_file(session_id, resource_id)
            if not resource_bytes:
                raise HTTPException(status_code=404, detail="Resource file not found")

            content_type = resource_meta.get("mime_type", "application/octet-stream")
            original_name = resource_meta.get("original_name", f"{resource_id}.bin")

            return Response(
                content=resource_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{original_name}"'
                }
            )

        # Issue #820: Serve session /tmp files directly (for containerized agents)
        @self.app.get("/api/sessions/{session_id}/tmp/{path:path}")
        @handle_exceptions("get session tmp file")
        async def get_session_tmp_file(session_id: str, path: str):
            """Serve a file from the session's /tmp directory (for containerized agents)."""
            import mimetypes

            from fastapi.responses import FileResponse

            # Validate session exists
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            tmp_dir = os.path.realpath(self.coordinator.data_dir / "sessions" / session_id / "tmp")
            requested = os.path.realpath(os.path.join(tmp_dir, path))

            # Security: canonical path must remain within session tmp dir
            if not requested.startswith(tmp_dir + os.sep) and requested != tmp_dir:
                raise HTTPException(status_code=403, detail="Access denied")

            requested_path = Path(requested)
            if not requested_path.exists() or not requested_path.is_file():
                raise HTTPException(status_code=404, detail="File not found")

            media_type, _ = mimetypes.guess_type(str(requested_path))
            return FileResponse(
                path=str(requested_path),
                media_type=media_type or "application/octet-stream",
                filename=requested_path.name,
            )

        # Issue #423: Remove resource from session display (soft-remove)
        @self.app.delete("/api/sessions/{session_id}/resources/{resource_id}")
        @handle_exceptions("remove session resource")
        async def remove_session_resource(session_id: str, resource_id: str):
            """Soft-remove a resource from the session display (file is preserved)"""
            success = await self.coordinator.remove_session_resource(session_id, resource_id)
            if not success:
                raise HTTPException(status_code=404, detail="Resource not found or removal failed")

            # Append removal to session poll queue
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "resource_removed",
                    "resource_id": resource_id,
                })

            return {"status": "ok", "resource_id": resource_id}

        # Issue #404: Legacy image endpoints (backward compatibility)
        @self.app.get("/api/sessions/{session_id}/images")
        @handle_exceptions("get session images")
        async def get_session_images(session_id: str, limit: int = 100, offset: int = 0):
            """Get image metadata for a session, paginated (deprecated, use /resources)"""
            return await self.coordinator.get_session_images(session_id, limit=limit, offset=offset)

        @self.app.get("/api/sessions/{session_id}/images/{image_id}")
        @handle_exceptions("get session image")
        async def get_session_image(session_id: str, image_id: str):
            """Get raw image data for a specific image (deprecated, use /resources)"""
            from fastapi.responses import Response

            # Get image metadata to determine content type
            result = await self.coordinator.get_session_images(session_id, limit=10000)
            image_meta = next((img for img in result["images"] if img.get("image_id") == image_id), None)

            if not image_meta:
                raise HTTPException(status_code=404, detail="Image not found")

            # Get image bytes
            image_bytes = await self.coordinator.get_session_image_file(session_id, image_id)
            if not image_bytes:
                raise HTTPException(status_code=404, detail="Image file not found")

            # Determine content type from format
            format_to_mime = {
                "png": "image/png",
                "jpeg": "image/jpeg",
                "jpg": "image/jpeg",
                "webp": "image/webp",
                "gif": "image/gif"
            }
            content_type = format_to_mime.get(image_meta.get("format", "png"), "image/png")

            return Response(
                content=image_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{image_id}.{image_meta.get("format", "png")}"'
                }
            )

        # ==================== PERMISSION MODE ENDPOINT ====================

        @self.app.post("/api/sessions/{session_id}/permission-mode")
        @handle_exceptions("set permission mode")
        async def set_permission_mode(session_id: str, request: PermissionModeRequest):
            """Set the permission mode for a session"""
            # Validate mode
            if request.mode not in PermissionMode._value2member_map_:
                raise HTTPException(status_code=400, detail=f"Invalid permission mode: {request.mode}")

            success = await self.coordinator.set_permission_mode(session_id, request.mode)
            if not success:
                raise HTTPException(status_code=400, detail="Failed to set permission mode")
            return {"success": success, "mode": request.mode}

        @self.app.get("/api/sessions/{session_id}/mcp-status")
        @handle_exceptions("get mcp status")
        async def get_mcp_status(session_id: str):
            """Get MCP server status for a session"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            result = await self.coordinator.get_mcp_status(session_id)
            return result

        @self.app.post("/api/sessions/{session_id}/mcp-toggle")
        @handle_exceptions("toggle mcp server")
        async def toggle_mcp_server(session_id: str, request: McpToggleRequest):
            """Toggle an MCP server on or off"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            try:
                await self.coordinator.toggle_mcp_server(
                    session_id, request.name, request.enabled
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            return {"success": True, "name": request.name, "enabled": request.enabled}

        @self.app.post("/api/sessions/{session_id}/mcp-reconnect")
        @handle_exceptions("reconnect mcp server")
        async def reconnect_mcp_server(session_id: str, request: McpReconnectRequest):
            """Reconnect a failed MCP server"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            try:
                await self.coordinator.reconnect_mcp_server(session_id, request.name)
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            return {"success": True, "name": request.name}

        @self.app.post("/api/sessions/{session_id}/restart")
        @handle_exceptions("restart session")
        async def restart_session(session_id: str):
            """Restart a session (disconnect and resume)"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")

            # Clear any existing callbacks to prevent duplicates, then register fresh one
            self.coordinator.clear_message_callbacks(session_id)
            self.coordinator.add_message_callback(
                session_id,
                self._create_message_callback(session_id)
            )

            success = await self.coordinator.restart_session(
                session_id,
                permission_callback=self.permission_service.create_permission_callback(session_id),
            )
            return {"success": success}

        @self.app.post("/api/sessions/{session_id}/reset")
        @handle_exceptions("reset session")
        async def reset_session(session_id: str):
            """Reset a session (clear messages and start fresh)"""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            success = await self.coordinator.reset_session(
                session_id,
                permission_callback=self.permission_service.create_permission_callback(session_id),
            )
            return {"success": success}

        @self.app.delete("/api/sessions/{session_id}/history")
        @handle_exceptions("erase session history")
        async def erase_session_history(session_id: str):
            """Erase distilled history files for a session."""
            success = await self.coordinator.erase_history(session_id)
            return {"success": success}

        @self.app.delete("/api/sessions/{session_id}/archives")
        @handle_exceptions("erase session archives")
        async def erase_session_archives(session_id: str):
            """Erase archive data for a session."""
            success = await self.coordinator.erase_archives(session_id)
            return {"success": success}

        @self.app.get("/api/sessions/{session_id}/history-archives-status")
        @handle_exceptions("get history archives status")
        async def get_history_archives_status(session_id: str):
            """Check if history and/or archives exist for a session."""
            return await self.coordinator.check_history_archives(session_id)

        @self.app.post("/api/sessions/{session_id}/disconnect")
        @handle_exceptions("disconnect session")
        async def disconnect_session(session_id: str):
            """Disconnect SDK but keep session state (for end session)"""
            return await self.service.disconnect_session(session_id)

        # ==================== DIFF ENDPOINTS (Issue #435) ====================

        @self.app.get("/api/sessions/{session_id}/diff")
        @handle_exceptions("get session diff")
        async def get_session_diff(session_id: str):
            """Get diff summary for a session's working directory vs origin/main."""
            ctx = await self.service.get_session_diff_context(session_id)
            if not ctx.get("exists"):
                raise HTTPException(status_code=404, detail="Session not found")

            cwd = ctx.get("working_directory")
            if not cwd or not Path(cwd).exists():
                return {"is_git_repo": False}

            # Check if it's a git repo
            is_git = await self._run_git_command(
                ["git", "rev-parse", "--is-inside-work-tree"], cwd
            )
            if is_git is None:
                return {"is_git_repo": False}

            # Get current branch
            branch = await self._run_git_command(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd
            )

            # Find merge base with origin/main (fallback to origin/master)
            merge_base = await self._run_git_command(
                ["git", "merge-base", "HEAD", "origin/main"], cwd
            )
            if merge_base is None:
                merge_base = await self._run_git_command(
                    ["git", "merge-base", "HEAD", "origin/master"], cwd
                )
            # Track whether we're in local-only mode (no remote)
            is_local_only = merge_base is None
            if is_local_only:
                # No remote: use the empty tree as base so all commits/files are shown
                empty_tree = await self._run_git_command(
                    ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
                )
                if empty_tree:
                    merge_base = empty_tree.strip()
                else:
                    # Fallback to well-known empty tree hash
                    merge_base = "4b825dc642cb6eb9a060e54bf899d15f7f09f993"

            # Get commit log since merge base
            if is_local_only:
                # Local-only: show all commits (--root includes initial commit)
                log_output = await self._run_git_command(
                    ["git", "log", "--format=%H%n%h%n%s%n%an%n%aI%n---COMMIT_END---"],
                    cwd
                )
            else:
                log_output = await self._run_git_command(
                    ["git", "log", f"{merge_base}..HEAD",
                     "--format=%H%n%h%n%s%n%an%n%aI%n---COMMIT_END---"],
                    cwd
                )
            commits = []
            if log_output:
                raw_commits = log_output.strip().split("---COMMIT_END---")
                for raw in raw_commits:
                    lines = raw.strip().split("\n")
                    if len(lines) >= 5:
                        # Get files for this commit
                        commit_files_output = await self._run_git_command(
                            ["git", "diff-tree", "--no-commit-id", "--root",
                             "-r", "--name-only", lines[0]], cwd
                        )
                        commit_files = [
                            f for f in (commit_files_output or "").strip().split("\n")
                            if f
                        ]
                        commits.append({
                            "hash": lines[0],
                            "short_hash": lines[1],
                            "message": lines[2],
                            "author": lines[3],
                            "date": lines[4],
                            "files": commit_files
                        })

            # Detect uncommitted changes (staged + unstaged + untracked)
            status_output = await self._run_git_command(
                ["git", "status", "--porcelain"], cwd
            )
            uncommitted_files = []
            untracked_paths = []
            if status_output:
                for line in status_output.strip().split("\n"):
                    if not line or len(line) < 3:
                        continue
                    xy = line[:2]
                    path = line[3:].strip()
                    # Handle renames: "R  old -> new"
                    if " -> " in path:
                        path = path.split(" -> ", 1)[1]
                    if xy == "??":
                        untracked_paths.append(path)
                    else:
                        uncommitted_files.append(path)

            # Build synthetic uncommitted commit if dirty working tree
            if uncommitted_files or untracked_paths:
                # Tracked file stats: combined staged+unstaged vs HEAD
                wip_numstat = await self._run_git_command(
                    ["git", "diff", "--numstat", "HEAD"], cwd
                )
                wip_name_status = await self._run_git_command(
                    ["git", "diff", "--name-status", "HEAD"], cwd
                )
                # Also include staged new files (added but not in HEAD)
                staged_numstat = await self._run_git_command(
                    ["git", "diff", "--numstat", "--cached"], cwd
                )
                staged_name_status = await self._run_git_command(
                    ["git", "diff", "--name-status", "--cached"], cwd
                )

                wip_status_map = {}
                if wip_name_status:
                    for line in wip_name_status.strip().split("\n"):
                        if line:
                            parts = line.split("\t", 1)
                            if len(parts) == 2:
                                sc = parts[0].strip()
                                fp = parts[1].strip()
                                if sc.startswith("A"):
                                    wip_status_map[fp] = "added"
                                elif sc.startswith("D"):
                                    wip_status_map[fp] = "deleted"
                                elif sc.startswith("R"):
                                    wip_status_map[fp] = "renamed"
                                else:
                                    wip_status_map[fp] = "modified"
                # Merge staged-only entries (new files that only show in --cached)
                if staged_name_status:
                    for line in staged_name_status.strip().split("\n"):
                        if line:
                            parts = line.split("\t", 1)
                            if len(parts) == 2:
                                sc = parts[0].strip()
                                fp = parts[1].strip()
                                if fp not in wip_status_map:
                                    if sc.startswith("A"):
                                        wip_status_map[fp] = "added"
                                    elif sc.startswith("D"):
                                        wip_status_map[fp] = "deleted"
                                    else:
                                        wip_status_map[fp] = "modified"

                wip_files_list = []
                # Parse tracked file numstat
                all_numstat = (wip_numstat or "")
                if staged_numstat:
                    # Merge staged numstat for files not in wip_numstat
                    seen = set()
                    if all_numstat:
                        for line in all_numstat.strip().split("\n"):
                            if line:
                                p = line.split("\t")
                                if len(p) >= 3:
                                    seen.add(p[2].strip())
                    for line in staged_numstat.strip().split("\n"):
                        if line:
                            p = line.split("\t")
                            if len(p) >= 3 and p[2].strip() not in seen:
                                all_numstat += "\n" + line

                if all_numstat:
                    for line in all_numstat.strip().split("\n"):
                        if line:
                            parts = line.split("\t")
                            if len(parts) >= 3:
                                fp = parts[2].strip()
                                wip_files_list.append(fp)

                # Add untracked files
                for upath in untracked_paths:
                    wip_files_list.append(upath)
                    wip_status_map[upath] = "added"

                synthetic_commit = {
                    "hash": "uncommitted",
                    "short_hash": "wip",
                    "message": "Uncommitted changes",
                    "author": "",
                    "date": "",
                    "files": wip_files_list,
                    "is_uncommitted": True
                }
                commits.insert(0, synthetic_commit)

            # Total stats: two-dot notation includes uncommitted changes
            numstat_output = await self._run_git_command(
                ["git", "diff", "--numstat", merge_base], cwd
            )
            name_status_output = await self._run_git_command(
                ["git", "diff", "--name-status", merge_base], cwd
            )

            files = {}
            total_insertions = 0
            total_deletions = 0

            # Parse name-status for A/M/D
            status_map = {}
            if name_status_output:
                for line in name_status_output.strip().split("\n"):
                    if line:
                        parts = line.split("\t", 1)
                        if len(parts) == 2:
                            status_code = parts[0].strip()
                            filepath = parts[1].strip()
                            if status_code.startswith("A"):
                                status_map[filepath] = "added"
                            elif status_code.startswith("D"):
                                status_map[filepath] = "deleted"
                            elif status_code.startswith("R"):
                                status_map[filepath] = "renamed"
                            else:
                                status_map[filepath] = "modified"

            # Parse numstat for insertions/deletions
            if numstat_output:
                for line in numstat_output.strip().split("\n"):
                    if line:
                        parts = line.split("\t")
                        if len(parts) >= 3:
                            ins = parts[0].strip()
                            dels = parts[1].strip()
                            filepath = parts[2].strip()
                            ins_count = int(ins) if ins != "-" else 0
                            dels_count = int(dels) if dels != "-" else 0
                            is_binary = ins == "-" and dels == "-"
                            total_insertions += ins_count
                            total_deletions += dels_count
                            files[filepath] = {
                                "status": status_map.get(filepath, "modified"),
                                "insertions": ins_count,
                                "deletions": dels_count,
                                "is_binary": is_binary
                            }

            # Add untracked files to total stats (not covered by git diff)
            if untracked_paths:
                for upath in untracked_paths:
                    if upath not in files:
                        ustat = await self._run_git_command(
                            ["git", "diff", "--numstat", "--no-index",
                             "/dev/null", upath],
                            cwd, allow_nonzero=True
                        )
                        ins_count = 0
                        if ustat:
                            parts = ustat.split("\t")
                            if len(parts) >= 3:
                                ins_val = parts[0].strip()
                                ins_count = int(ins_val) if ins_val != "-" else 0
                        total_insertions += ins_count
                        files[upath] = {
                            "status": "added",
                            "insertions": ins_count,
                            "deletions": 0,
                            "is_binary": False
                        }

            return {
                "is_git_repo": True,
                "merge_base": merge_base,
                "branch": branch or "unknown",
                "commits": commits,
                "files": files,
                "total_insertions": total_insertions,
                "total_deletions": total_deletions
            }

        @self.app.get("/api/sessions/{session_id}/diff/file")
        @handle_exceptions("get session diff file")
        async def get_session_diff_file(
            session_id: str, path: str = None, ref: str = None
        ):
            """Get unified diff content for a specific file.

            Args:
                ref: Optional. ``uncommitted`` for working tree changes,
                    a commit hash for commit-specific changes, or null/empty
                    for cumulative branch diff (merge_base...HEAD).
            """
            if not path:
                raise HTTPException(status_code=400, detail="path query parameter required")

            ctx = await self.service.get_session_diff_context(session_id)
            if not ctx.get("exists"):
                raise HTTPException(status_code=404, detail="Session not found")

            cwd = ctx.get("working_directory")
            if not cwd or not Path(cwd).exists():
                raise HTTPException(status_code=400, detail="Invalid working directory")

            if ref and ref != "uncommitted":
                # Commit-specific diff: validate ref then diff against parent
                verified = await self._run_git_command(
                    ["git", "rev-parse", "--verify", ref], cwd
                )
                if verified is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid commit reference: {ref}"
                    )

                # Check if this commit has a parent
                parent = await self._run_git_command(
                    ["git", "rev-parse", "--verify", f"{ref}~1"], cwd
                )
                if parent:
                    # Normal commit: diff against parent
                    diff_output = await self._run_git_command(
                        ["git", "diff", f"{ref}~1", ref, "--", path], cwd
                    )
                else:
                    # Root commit: diff against empty tree
                    empty_tree = await self._run_git_command(
                        ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
                    )
                    base = (empty_tree.strip() if empty_tree
                            else "4b825dc642cb6eb9a060e54bf899d15f7f09f993")
                    diff_output = await self._run_git_command(
                        ["git", "diff", base, ref, "--", path], cwd
                    )

                return {
                    "path": path,
                    "ref": ref,
                    "diff": diff_output or ""
                }

            # Find merge base for uncommitted / total views
            merge_base = await self._run_git_command(
                ["git", "merge-base", "HEAD", "origin/main"], cwd
            )
            if merge_base is None:
                merge_base = await self._run_git_command(
                    ["git", "merge-base", "HEAD", "origin/master"], cwd
                )

            if ref == "uncommitted":
                # Check if file is untracked
                is_tracked = await self._run_git_command(
                    ["git", "ls-files", path], cwd
                )
                status_check = await self._run_git_command(
                    ["git", "status", "--porcelain", "--", path], cwd
                )
                is_untracked = (
                    status_check and status_check.startswith("??")
                )

                if is_untracked or not is_tracked:
                    # Untracked file: diff vs /dev/null
                    diff_output = await self._run_git_command(
                        ["git", "diff", "--no-index", "/dev/null", path],
                        cwd, allow_nonzero=True
                    )
                else:
                    # Tracked file: diff against merge base, or HEAD if no remote
                    base = merge_base or "HEAD"
                    diff_output = await self._run_git_command(
                        ["git", "diff", base, "--", path], cwd
                    )
            elif merge_base is not None:
                # Default: three-dot (committed changes only)
                diff_output = await self._run_git_command(
                    ["git", "diff", f"{merge_base}...HEAD", "--", path], cwd
                )
            else:
                # No remote: diff all changes from empty tree
                empty_tree = await self._run_git_command(
                    ["git", "hash-object", "-t", "tree", "/dev/null"], cwd
                )
                base = (empty_tree.strip() if empty_tree
                        else "4b825dc642cb6eb9a060e54bf899d15f7f09f993")
                diff_output = await self._run_git_command(
                    ["git", "diff", base, "HEAD", "--", path], cwd
                )

            return {
                "path": path,
                "merge_base": merge_base,
                "diff": diff_output or ""
            }

        # ==================== QUEUE ENDPOINTS (Issue #500) ====================

        @self.app.post("/api/sessions/{session_id}/queue-message")
        @handle_exceptions("enqueue message", value_error_status=400)
        async def enqueue_message(session_id: str, request: Request):
            """Enqueue a message for a session."""
            data = await request.json()
            content = data.get("content")
            if not content:
                raise HTTPException(status_code=400, detail="content is required")

            item = await self.coordinator.enqueue_message(
                session_id=session_id,
                content=content,
                reset_session=data.get("reset_session"),
                metadata=data.get("metadata"),
            )

            # Append queue update to session poll queue
            await self._broadcast_queue_update(session_id, "enqueued", item)

            return {
                "queue_id": item["queue_id"],
                "position": item["position"],
                "item": item,
            }

        @self.app.get("/api/sessions/{session_id}/queue")
        @handle_exceptions("get queue")
        async def get_queue(session_id: str, limit: int = 100, offset: int = 0):
            """List queue items for a session, paginated."""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            result = await self.coordinator.get_queue(session_id, limit=limit, offset=offset)
            return result

        @self.app.delete("/api/sessions/{session_id}/queue/{queue_id}")
        @handle_exceptions("cancel queue item")
        async def cancel_queue_item(session_id: str, queue_id: str):
            """Cancel a pending queue item."""
            item = await self.coordinator.cancel_queue_item(session_id, queue_id)
            if not item:
                raise HTTPException(status_code=404, detail="Queue item not found or not pending")

            await self._broadcast_queue_update(session_id, "cancelled", item)
            return {"item": item}

        @self.app.post("/api/sessions/{session_id}/queue/{queue_id}/requeue")
        @handle_exceptions("requeue item")
        async def requeue_item(session_id: str, queue_id: str):
            """Re-queue a sent or failed item at the front."""
            item = await self.coordinator.requeue_item(session_id, queue_id)
            if not item:
                raise HTTPException(status_code=404, detail="Queue item not found or not in sent/failed state")

            await self._broadcast_queue_update(session_id, "enqueued", item)
            return {"item": item}

        @self.app.delete("/api/sessions/{session_id}/queue")
        @handle_exceptions("clear queue")
        async def clear_queue(session_id: str):
            """Clear all pending queue items."""
            if not await self.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            count = await self.coordinator.clear_queue(session_id)
            await self._broadcast_queue_update(session_id, "cleared", {"count": count})
            return {"cancelled_count": count}

        @self.app.put("/api/sessions/{session_id}/queue/pause")
        @handle_exceptions("pause queue")
        async def pause_queue(session_id: str, request: Request):
            """Pause or resume the queue."""
            data = await request.json()
            paused = data.get("paused", True)
            success = await self.coordinator.pause_queue(session_id, paused)
            if not success:
                raise HTTPException(status_code=400, detail="Failed to update queue pause state")

            action = "paused" if paused else "resumed"
            await self._broadcast_queue_update(session_id, action, {"paused": paused})
            return {"paused": paused}

        @self.app.put("/api/sessions/{session_id}/queue/config")
        @handle_exceptions("update queue config")
        async def update_queue_config(session_id: str, request: Request):
            """Update queue configuration."""
            data = await request.json()
            success = await self.coordinator.update_queue_config(session_id, data)
            if not success:
                raise HTTPException(status_code=400, detail="Failed to update queue config")
            return {"config": data}

        # ==================== LEGION ENDPOINTS ====================
        # All projects support Legion capabilities (issue #313)

        @self.app.get("/api/legions/{legion_id}/timeline")
        @handle_exceptions("get legion timeline")
        async def get_legion_timeline(legion_id: str, limit: int = 100, offset: int = 0):
            """Get Comms for legion timeline (all communications in the legion)"""
            # Read all comms from the main legion timeline
            legion_dir = self.coordinator.data_dir / "legions" / legion_id
            if not legion_dir.exists():
                return {
                    "comms": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset
                }

            all_comms = []

            # Read from main timeline.jsonl (contains ALL comms)
            timeline_file = legion_dir / "timeline.jsonl"
            if timeline_file.exists():
                with open(timeline_file, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                comm_data = json.loads(line)
                                all_comms.append(comm_data)
                            except json.JSONDecodeError:
                                continue

            # Normalize timestamps to handle mixed string/float formats (backwards compatibility)
            for comm in all_comms:
                if 'timestamp' in comm:
                    try:
                        comm['timestamp'] = normalize_timestamp(comm['timestamp'])
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid timestamp in comm {comm.get('comm_id', 'unknown')}: {e}, using current time")
                        comm['timestamp'] = datetime.now(UTC).timestamp()

            # Sort by timestamp (newest first) and deduplicate
            all_comms.sort(key=lambda x: x.get('timestamp', 0.0), reverse=True)

            # Deduplicate by comm_id (since comms appear in both sender and receiver logs)
            seen_ids = set()
            unique_comms = []
            for comm in all_comms:
                comm_id = comm.get('comm_id')
                if comm_id and comm_id not in seen_ids:
                    seen_ids.add(comm_id)
                    unique_comms.append(comm)

            # Paginate
            total = len(unique_comms)
            paginated_comms = unique_comms[offset:offset + limit]

            return {
                "comms": paginated_comms,
                "total": total,
                "limit": limit,
                "offset": offset
            }

        @self.app.get("/api/legions/{legion_id}/hierarchy")
        @handle_exceptions("get legion hierarchy")
        async def get_legion_hierarchy(legion_id: str):
            """Get complete minion hierarchy with user at root (issue #313: universal Legion)"""
            # Issue #313: All projects support hierarchy - verify project exists
            if not await self.service.validate_project_exists(legion_id):
                raise HTTPException(status_code=404, detail="Project not found")

            # Get legion coordinator
            legion_coord = self.coordinator.legion_system.legion_coordinator
            if not legion_coord:
                raise HTTPException(status_code=500, detail="Legion coordinator not available")

            # Assemble hierarchy (returns empty children if no minions)
            hierarchy = await legion_coord.assemble_minion_hierarchy(legion_id)

            return hierarchy

        @self.app.post("/api/legions/{legion_id}/comms")
        @handle_exceptions("send comm to legion")
        async def send_comm_to_legion(legion_id: str, request: CommSendRequest):
            """Send a Comm in the legion"""
            import uuid

            from src.models.legion_models import Comm, CommType

            legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
            if not legion:
                raise HTTPException(status_code=404, detail="Legion not found")

            # Look up minion name if targeting a minion (for historical display)
            to_minion_name = None
            if request.to_minion_id:
                to_minion_name = await self.service.get_minion_name(request.to_minion_id)

            # Create Comm from user
            comm = Comm(
                comm_id=str(uuid.uuid4()),
                from_user=True,
                to_minion_id=request.to_minion_id,
                to_user=request.to_user,
                to_minion_name=to_minion_name,
                content=request.content,
                comm_type=CommType(request.comm_type)
            )

            # Route the comm
            success = await self.coordinator.legion_system.comm_router.route_comm(comm)

            if success:
                return {"comm": comm.to_dict(), "success": True}
            else:
                raise HTTPException(status_code=500, detail="Failed to route comm")

        @self.app.post("/api/legions/{legion_id}/minions")
        @handle_exceptions("create minion", value_error_status=400)
        async def create_minion(legion_id: str, request: MinionCreateRequest):
            """Create a new minion in the project (issue #313: universal Legion)"""
            # Verify project exists (all projects support minions - issue #313)
            project_dict = await self.service.get_legion_project(legion_id)
            if not project_dict:
                raise HTTPException(status_code=404, detail="Project not found")

            # Validate and normalize working directory
            working_dir = validate_and_normalize_working_directory(
                request.working_directory,
                str(project_dict.get("working_directory", ""))
            )

            # Create minion via OverseerController
            config = request.to_session_config(
                system_prompt=request.system_prompt,
                working_directory=str(working_dir),
            )
            minion_id = await self.coordinator.legion_system.overseer_controller.create_minion_for_user(
                legion_id=legion_id,
                name=request.name,
                config=config,
                role=request.role,
                capabilities=request.capabilities,
            )

            # Get the created minion info
            minion_info = await self.service.get_minion_session(minion_id)

            return {
                "success": True,
                "minion_id": minion_id,
                "minion": minion_info,
            }

        # ==================== FLEET CONTROL ENDPOINTS ====================

        @self.app.post("/api/legions/{legion_id}/halt-all")
        @handle_exceptions("emergency halt all")
        async def emergency_halt_all(legion_id: str):
            """Emergency halt all minions in the project (issue #313: universal Legion)"""
            # Issue #313: All projects support halt-all - verify project exists
            if not await self.service.validate_project_exists(legion_id):
                raise HTTPException(status_code=404, detail="Project not found")

            # Call LegionCoordinator.emergency_halt_all() (no-op if no minions)
            result = await self.coordinator.legion_system.legion_coordinator.emergency_halt_all(legion_id)

            return {
                "success": True,
                "halted_count": result["halted_count"],
                "failed_minions": result["failed_minions"],
                "total_minions": result["total_minions"]
            }

        @self.app.post("/api/legions/{legion_id}/resume-all")
        @handle_exceptions("resume all")
        async def resume_all(legion_id: str):
            """Resume all minions in the project (issue #313: universal Legion)"""
            # Issue #313: All projects support resume-all - verify project exists
            if not await self.service.validate_project_exists(legion_id):
                raise HTTPException(status_code=404, detail="Project not found")

            # Call LegionCoordinator.resume_all() (no-op if no minions)
            result = await self.coordinator.legion_system.legion_coordinator.resume_all(legion_id)

            return {
                "success": True,
                "resumed_count": result["resumed_count"],
                "failed_minions": result["failed_minions"],
                "total_minions": result["total_minions"]
            }

        # ==================== SCHEDULE ENDPOINTS (Issue #495) ====================

        @self.app.get("/api/legions/{legion_id}/schedules")
        @handle_exceptions("list schedules")
        async def list_schedules(
            legion_id: str, minion_id: str | None = None, status: str | None = None,
            limit: int = 100, offset: int = 0
        ):
            """List schedules for a legion with optional filters, paginated."""
            if not await self.service.validate_project_exists(legion_id):
                raise HTTPException(status_code=404, detail="Project not found")

            status_filter = None
            if status:
                from src.models.schedule_models import ScheduleStatus
                try:
                    status_filter = ScheduleStatus(status)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid status: {status}. Use active, paused, or cancelled",
                    ) from None

            all_schedules = await self.coordinator.legion_system.scheduler_service.list_schedules(
                legion_id=legion_id, minion_id=minion_id, status=status_filter
            )
            total = len(all_schedules)
            sliced = all_schedules[offset : offset + limit]
            return {
                "schedules": [s.to_dict() for s in sliced],
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(sliced) < total,
            }

        @self.app.post("/api/legions/{legion_id}/schedules")
        @handle_exceptions("create schedule", value_error_status=400)
        async def create_schedule(legion_id: str, request: ScheduleCreateRequest):
            """Create a new schedule (permanent or ephemeral)."""
            if not await self.service.validate_project_exists(legion_id):
                raise HTTPException(status_code=404, detail="Project not found")

            # Determine mode: permanent (minion_id) or ephemeral (session_config)
            minion_id = request.minion_id
            minion_name = None

            ephemeral_agent_id = None

            if minion_id:
                # Permanent mode: resolve minion name
                minion_name = await self.service.get_minion_name(minion_id)
                if minion_name is None and not await self.service.get_session_exists(minion_id):
                    raise HTTPException(status_code=404, detail="Minion not found")
                minion_name = (minion_name or minion_id[:8])
            elif request.session_config:
                # Ephemeral mode: create the persistent agent session up front
                ephemeral_agent_id = (
                    await self.coordinator.create_ephemeral_session(
                        session_config=request.session_config,
                        schedule_name=request.name,
                        project_id=legion_id,
                    )
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Either minion_id or session_config is required",
                )

            schedule = await self.coordinator.legion_system.scheduler_service.create_schedule(
                legion_id=legion_id,
                name=request.name,
                cron_expression=request.cron_expression,
                prompt=request.prompt,
                minion_id=minion_id,
                minion_name=minion_name,
                reset_session=request.reset_session,
                max_retries=request.max_retries,
                timeout_seconds=request.timeout_seconds,
                session_config=request.session_config,
                ephemeral_agent_id=ephemeral_agent_id,
            )
            return {"schedule": schedule.to_dict()}

        @self.app.get("/api/legions/{legion_id}/schedules/{schedule_id}")
        @handle_exceptions("get schedule")
        async def get_schedule(legion_id: str, schedule_id: str):
            """Get a single schedule."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")
            return {"schedule": schedule.to_dict()}

        @self.app.put("/api/legions/{legion_id}/schedules/{schedule_id}")
        @handle_exceptions("update schedule", value_error_status=400)
        async def update_schedule(
            legion_id: str, schedule_id: str, request: ScheduleUpdateRequest
        ):
            """Update schedule fields."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            fields = {k: v for k, v in request.model_dump().items() if v is not None}
            updated = await self.coordinator.legion_system.scheduler_service.update_schedule(
                schedule_id, **fields
            )
            return {"schedule": updated.to_dict()}

        @self.app.post("/api/legions/{legion_id}/schedules/{schedule_id}/pause")
        @handle_exceptions("pause schedule", value_error_status=400)
        async def pause_schedule(legion_id: str, schedule_id: str):
            """Pause an active schedule."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            updated = await self.coordinator.legion_system.scheduler_service.pause_schedule(
                schedule_id
            )
            return {"schedule": updated.to_dict()}

        @self.app.post("/api/legions/{legion_id}/schedules/{schedule_id}/resume")
        @handle_exceptions("resume schedule", value_error_status=400)
        async def resume_schedule(legion_id: str, schedule_id: str):
            """Resume a paused schedule."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            updated = await self.coordinator.legion_system.scheduler_service.resume_schedule(
                schedule_id
            )
            return {"schedule": updated.to_dict()}

        @self.app.post("/api/legions/{legion_id}/schedules/{schedule_id}/cancel")
        @handle_exceptions("cancel schedule", value_error_status=400)
        async def cancel_schedule(legion_id: str, schedule_id: str):
            """Cancel a schedule permanently."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            updated = await self.coordinator.legion_system.scheduler_service.cancel_schedule(
                schedule_id
            )
            return {"schedule": updated.to_dict()}

        @self.app.post("/api/legions/{legion_id}/schedules/{schedule_id}/run-now")
        @handle_exceptions("run schedule now", value_error_status=400)
        async def run_schedule_now(legion_id: str, schedule_id: str):
            """Manually trigger a schedule execution immediately."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            try:
                result = await self.coordinator.legion_system.scheduler_service.run_now(
                    schedule_id
                )
            except RuntimeError as e:
                raise HTTPException(status_code=409, detail=str(e)) from e
            return result

        @self.app.delete("/api/legions/{legion_id}/schedules/{schedule_id}")
        @handle_exceptions("delete schedule", value_error_status=400)
        async def delete_schedule(
            legion_id: str, schedule_id: str, delete_agent: bool = False
        ):
            """Delete a schedule entirely. Optionally delete its ephemeral agent."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            # Optionally delete the ephemeral agent session
            if delete_agent and schedule.ephemeral_agent_id:
                try:
                    await self.coordinator.delete_session(
                        schedule.ephemeral_agent_id,
                        archive_reason="schedule_deleted",
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to delete ephemeral agent "
                        f"{schedule.ephemeral_agent_id}: {e}"
                    )

            await self.coordinator.legion_system.scheduler_service.delete_schedule(
                schedule_id
            )
            return {"success": True}

        @self.app.get("/api/legions/{legion_id}/schedules/{schedule_id}/history")
        @handle_exceptions("get schedule history")
        async def get_schedule_history(
            legion_id: str, schedule_id: str, limit: int = 50, offset: int = 0
        ):
            """Get execution history for a schedule."""
            schedule = await self.coordinator.legion_system.scheduler_service.get_schedule(
                schedule_id
            )
            if not schedule or schedule.legion_id != legion_id:
                raise HTTPException(status_code=404, detail="Schedule not found")

            executions = (
                await self.coordinator.legion_system.scheduler_service.get_schedule_history(
                    legion_id=legion_id,
                    schedule_id=schedule_id,
                    limit=limit,
                    offset=offset,
                )
            )
            return {
                "executions": [e.to_dict() for e in executions],
                "limit": limit,
                "offset": offset,
            }

        # ==================== PERMISSION PREVIEW ENDPOINT (Issue #36) ====================

        @self.app.post("/api/permissions/preview")
        @handle_exceptions("preview permissions")
        async def preview_permissions(request: PermissionPreviewRequest):
            """
            Preview effective permissions from settings files.

            Returns a list of permissions with their source annotations.
            """
            permissions = resolve_effective_permissions(
                working_directory=request.working_directory,
                setting_sources=request.setting_sources,
                session_allowed_tools=request.session_allowed_tools
            )
            return {"permissions": permissions}

        # ==================== CONFIG ENDPOINTS ====================

        @self.app.get("/api/config")
        @handle_exceptions("get config")
        async def get_config():
            """Return full application config."""
            from .config_manager import load_config
            config = load_config(self.config_file) if self.config_file else load_config()
            return {"config": config.to_dict()}

        @self.app.put("/api/config")
        @handle_exceptions("update config")
        async def update_config(request: Request):
            """Update application config with side effects."""
            from .config_manager import load_config, save_config
            body = await request.json()
            config = load_config(self.config_file) if self.config_file else load_config()
            old_sync = config.features.skill_sync_enabled

            # Merge features section
            if "features" in body:
                features = body["features"]
                if "skill_sync_enabled" in features:
                    config.features.skill_sync_enabled = features["skill_sync_enabled"]

            # Merge networking section
            if "networking" in body:
                net = body["networking"]
                if "allow_network_binding" in net:
                    config.networking.allow_network_binding = net["allow_network_binding"]
                if "acknowledged_risk" in net:
                    config.networking.acknowledged_risk = net["acknowledged_risk"]

            if self.config_file:
                save_config(config, self.config_file)
            else:
                save_config(config)

            # Side effects for skill sync toggle
            new_sync = config.features.skill_sync_enabled
            if old_sync and not new_sync:
                await self.skill_manager.cleanup_symlinks()
            elif not old_sync and new_sync:
                await self.skill_manager.sync()

            return {"config": config.to_dict()}

        # ==================== SKILLS ENDPOINTS ====================

        @self.app.post("/api/skills/sync")
        @handle_exceptions("sync skills")
        async def sync_skills():
            """Manually trigger skill sync."""
            from .config_manager import load_config
            config = load_config(self.config_file) if self.config_file else load_config()
            if not config.features.skill_sync_enabled:
                raise HTTPException(status_code=409, detail="Skill syncing is disabled")
            result = await self.skill_manager.sync()
            return {"status": "synced", **result}

        @self.app.get("/api/skills/status")
        @handle_exceptions("get skills status")
        async def get_skills_status():
            """Get skill sync status."""
            from .config_manager import load_config
            config = load_config(self.config_file) if self.config_file else load_config()
            return {
                "sync_enabled": config.features.skill_sync_enabled,
                "last_sync_time": self.skill_manager.last_sync_time,
            }

        # ==================== SYSTEM ENDPOINTS (Issue #434) ====================

        @self.app.get("/api/system/docker-status")
        @handle_exceptions("check docker status")
        async def get_docker_status():
            """Check Docker availability and image status (issue #496)."""
            from src.docker_utils import check_docker_available
            status = await check_docker_available()
            return status

        @self.app.get("/api/system/git-status")
        @handle_exceptions("get git status")
        async def get_git_status():
            """Return current git branch, last commit, remote commit info, and dirty state."""
            project_root = str(Path(__file__).parent.parent)

            branch = await self._run_git_command(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root
            )
            commit_hash = await self._run_git_command(
                ["git", "log", "-1", "--format=%H"], project_root
            )
            commit_message = await self._run_git_command(
                ["git", "log", "-1", "--format=%s"], project_root
            )
            status = await self._run_git_command(
                ["git", "status", "--porcelain"], project_root
            )

            # Remote commit info
            remote_commit_hash = ""
            remote_commit_message = ""
            commits_behind = 0
            remote_fetch_failed = False

            # Detect remote tracking branch
            remote_branch = None
            if branch and branch != "HEAD":
                # Try the tracking branch for the current local branch
                candidate = f"origin/{branch}"
                ref_exists = await self._run_git_command(
                    ["git", "rev-parse", "--verify", candidate], project_root
                )
                if ref_exists:
                    remote_branch = candidate

            if not remote_branch:
                # Detached HEAD, no remote tracking, or unknown — try origin/HEAD
                origin_head = await self._run_git_command(
                    ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], project_root
                )
                if origin_head:
                    remote_branch = origin_head
                else:
                    # Fall back to origin/main, then origin/master
                    for fallback in ["origin/main", "origin/master"]:
                        ref_check = await self._run_git_command(
                            ["git", "rev-parse", "--verify", fallback], project_root
                        )
                        if ref_check:
                            remote_branch = fallback
                            break

            # Fetch from origin (15s timeout)
            if remote_branch:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "git", "fetch", "origin",
                        cwd=project_root,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=15)
                    if proc.returncode != 0:
                        remote_fetch_failed = True
                except (TimeoutError, OSError):
                    remote_fetch_failed = True

                # Read remote commit info (works even if fetch failed, using stale refs)
                r_hash = await self._run_git_command(
                    ["git", "log", "-1", "--format=%H", remote_branch], project_root
                )
                r_msg = await self._run_git_command(
                    ["git", "log", "-1", "--format=%s", remote_branch], project_root
                )
                if r_hash:
                    remote_commit_hash = r_hash
                    remote_commit_message = r_msg or ""
                    behind = await self._run_git_command(
                        ["git", "rev-list", "--count",
                         f"HEAD..{remote_branch}"], project_root
                    )
                    commits_behind = int(behind) if behind else 0
                else:
                    remote_fetch_failed = True
            else:
                remote_fetch_failed = True

            return {
                "branch": branch or "unknown",
                "last_commit_hash": commit_hash or "",
                "last_commit_message": commit_message or "",
                "has_uncommitted_changes": bool(status),
                "remote_commit_hash": remote_commit_hash,
                "remote_commit_message": remote_commit_message,
                "commits_behind": commits_behind,
                "remote_fetch_failed": remote_fetch_failed,
            }

        @self.app.post("/api/system/restart", status_code=202)
        @handle_exceptions("restart server")
        async def restart_server():
            """Pull latest code and restart the backend server via os.execv."""
            # Rate limiting: 1 restart per 30 seconds
            now = time.time()
            if now - self._last_restart_time < 30:
                remaining = int(30 - (now - self._last_restart_time))
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limited. Try again in {remaining} seconds."
                )
            self._last_restart_time = now

            project_root = Path(__file__).parent.parent

            # Git pull current branch
            try:
                result = subprocess.run(
                    ["git", "pull"],
                    cwd=project_root, capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"git pull failed: {result.stderr.strip()}"
                    )
                pull_output = result.stdout.strip()
            except subprocess.TimeoutExpired as e:
                raise HTTPException(status_code=504, detail="git pull timed out") from e
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("git pull failed")
                raise HTTPException(status_code=500, detail=str(e)) from e

            # Sync Python dependencies (after git pull, before restart)
            try:
                sync_result = subprocess.run(
                    ["uv", "sync"],
                    cwd=project_root, capture_output=True, text=True, timeout=120
                )
                if sync_result.returncode != 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"uv sync failed: {sync_result.stderr.strip()}"
                    )
                sync_output = sync_result.stdout.strip()
            except subprocess.TimeoutExpired as e:
                raise HTTPException(status_code=504, detail="uv sync timed out") from e
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("uv sync failed")
                raise HTTPException(status_code=500, detail=str(e)) from e

            # Append restart notice to UI poll queue
            self._broadcast_server_restarting(pull_output, sync_output)

            # Schedule the actual restart after response is sent
            async def _do_restart():
                await asyncio.sleep(0.5)
                logger.info("Executing os.execv restart...")
                try:
                    await self.coordinator.cleanup()
                except Exception as e:
                    logger.warning(f"Cleanup error during restart: {e}")
                os.execv(sys.executable, [sys.executable] + sys.argv)

            asyncio.get_event_loop().create_task(_do_restart())

            return {
                "status": "restarting",
                "message": "Server is pulling latest code and restarting...",
                "pull_output": pull_output,
                "sync_output": sync_output,
            }

        # ==================== FILESYSTEM ENDPOINTS ====================

        @self.app.get("/api/filesystem/browse")
        @handle_exceptions("browse filesystem")
        async def browse_filesystem(path: str = None):
            """Browse filesystem directories"""
            # Default to user home directory if no path provided
            if not path:
                path = str(Path.home())

            # Resolve and validate path
            browse_path = Path(path).resolve()

            # Check if path exists and is a directory
            if not browse_path.exists():
                raise HTTPException(status_code=404, detail="Path does not exist")
            if not browse_path.is_dir():
                raise HTTPException(status_code=400, detail="Path is not a directory")

            # Get parent path (None if at root)
            parent_path = str(browse_path.parent) if browse_path.parent != browse_path else None

            # List directories only
            directories = []
            try:
                for entry in sorted(browse_path.iterdir()):
                    if entry.is_dir():
                        # Skip hidden directories on Unix-like systems (optional)
                        if platform.system() != 'Windows' and entry.name.startswith('.'):
                            continue
                        directories.append({
                            "name": entry.name,
                            "path": str(entry.resolve())
                        })
            except PermissionError:
                # If we can't list the directory, return what we can
                pass

            return {
                "current_path": str(browse_path),
                "parent_path": parent_path,
                "directories": directories,
                "separator": os.sep
            }

        # ========== MCP Config Endpoints (issue #676) ==========

        @self.app.get("/api/mcp-configs")
        @handle_exceptions("list MCP configs")
        async def list_mcp_configs(limit: int = 100, offset: int = 0):
            """List global MCP server configurations, paginated"""
            return await self.service.list_mcp_configs(limit=limit, offset=offset)

        @self.app.post("/api/mcp-configs")
        @handle_exceptions("create MCP config", value_error_status=400)
        async def create_mcp_config(request: McpConfigCreateRequest):
            """Create a new global MCP server configuration"""
            return await self.service.create_mcp_config(
                name=request.name,
                server_type=request.type,
                command=request.command,
                args=request.args,
                env=request.env,
                url=request.url,
                headers=request.headers,
                enabled=request.enabled,
                oauth_enabled=request.oauth_enabled,
            )

        @self.app.post("/api/mcp-configs/export")
        @handle_exceptions("export MCP configs")
        async def export_mcp_configs(request: McpConfigExportRequest):
            """Export MCP server configurations as portable named dict (issue #788)"""
            all_configs = await self.service.export_mcp_configs(ids=request.ids)
            portable: dict = {}
            for c in all_configs:
                entry: dict = {"type": c.type.value, "enabled": c.enabled}
                if c.type == McpServerType.STDIO:
                    entry["command"] = c.command
                    if c.args:
                        entry["args"] = c.args
                    if c.env:
                        entry["env"] = c.env
                else:
                    entry["url"] = c.url
                    if c.headers:
                        entry["headers"] = c.headers
                portable[c.name] = entry
            return portable

        @self.app.post("/api/mcp-configs/import")
        @handle_exceptions("import MCP configs")
        async def import_mcp_configs(request: McpConfigImportRequest):
            """Import MCP server configurations with dry_run preview support (issue #788)"""
            return await self.service.import_mcp_configs(
                servers=request.servers, dry_run=request.dry_run
            )

        @self.app.get("/api/mcp-configs/{config_id}")
        @handle_exceptions("get MCP config")
        async def get_mcp_config(config_id: str):
            """Get a specific MCP server configuration"""
            config = await self.service.get_mcp_config(config_id)
            if not config:
                raise HTTPException(status_code=404, detail="MCP config not found")
            return config

        @self.app.put("/api/mcp-configs/{config_id}")
        @handle_exceptions("update MCP config", value_error_status=400)
        async def update_mcp_config(config_id: str, request: McpConfigUpdateRequest):
            """Update an existing MCP server configuration"""
            return await self.service.update_mcp_config(
                config_id,
                name=request.name,
                server_type=request.type,
                command=request.command,
                args=request.args,
                env=request.env,
                url=request.url,
                headers=request.headers,
                enabled=request.enabled,
                oauth_enabled=request.oauth_enabled,
            )

        @self.app.delete("/api/mcp-configs/{config_id}")
        @handle_exceptions("delete MCP config")
        async def delete_mcp_config(config_id: str):
            """Delete an MCP server configuration"""
            success = await self.service.delete_mcp_config(config_id)
            if not success:
                raise HTTPException(status_code=404, detail="MCP config not found")
            return {"deleted": True}

        # ========== MCP OAuth Endpoints (issue #813) ==========

        @self.app.get("/oauth/callback", response_class=HTMLResponse)
        @handle_exceptions("handle oauth callback")
        async def oauth_callback(request: Request):
            """Handle OAuth 2.1 authorization code callback.

            Exempt from auth middleware — this route is reached before any token exists.
            On success broadcasts mcp_oauth_complete to all UI WebSocket clients.
            """
            code = request.query_params.get("code")
            state = request.query_params.get("state")
            error = request.query_params.get("error")

            if error:
                error_desc = request.query_params.get("error_description", error)
                return HTMLResponse(
                    content=f"""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Authorization Failed</h2>
<p>{html.escape(error_desc)}</p>
<p>You may close this window.</p>
</body></html>""",
                    status_code=400,
                )

            if not code or not state:
                return HTMLResponse(
                    content="""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Missing Parameters</h2>
<p>Authorization code or state parameter missing.</p>
<p>You may close this window.</p>
</body></html>""",
                    status_code=400,
                )

            try:
                server_id = await self.service.oauth_complete_flow(state, code)
                # Append OAuth completion to UI poll queue
                self._broadcast_mcp_oauth_complete(server_id)
                return HTMLResponse(
                    content="""<!DOCTYPE html>
<html><head><title>Connected</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x2705; Connected Successfully</h2>
<p>MCP server authorized. You may close this window.</p>
<script>window.close();</script>
</body></html>"""
                )
            except Exception as e:
                logger.exception("OAuth callback error")
                return HTMLResponse(
                    content=f"""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Authorization Failed</h2>
<p>{html.escape(str(e))}</p>
<p>You may close this window.</p>
</body></html>""",
                    status_code=400,
                )

        @self.app.post("/api/mcp-configs/{config_id}/oauth/initiate")
        @handle_exceptions("initiate MCP OAuth")
        async def initiate_mcp_oauth(config_id: str, request: McpOAuthInitiateRequest):
            """Initiate OAuth 2.1 flow for an MCP server.

            Returns the authorization URL the frontend should open in a popup.
            """
            config = await self.service.get_mcp_config(config_id)
            if not config:
                raise HTTPException(status_code=404, detail="MCP config not found")
            if not config.get("url"):
                raise HTTPException(status_code=400, detail="OAuth requires a URL-based MCP server")
            auth_url = await self.service.oauth_initiate_flow(
                config_id=config_id,
                server_url=config["url"],
                redirect_uri=request.redirect_uri,
                client_name=f"Claude Code WebUI — {config['name']}",
            )
            return {"auth_url": auth_url}

        @self.app.post("/api/mcp-configs/{config_id}/oauth/disconnect")
        @handle_exceptions("disconnect MCP OAuth")
        async def disconnect_mcp_oauth(config_id: str):
            """Clear stored OAuth tokens for an MCP server."""
            success = await self.service.oauth_disconnect(config_id)
            if not success:
                raise HTTPException(status_code=404, detail="MCP config not found")
            return {"disconnected": True}

        @self.app.get("/api/mcp-configs/{config_id}/oauth/status")
        @handle_exceptions("get MCP OAuth status")
        async def get_mcp_oauth_status(config_id: str):
            """Return OAuth status for this MCP server.

            Returns {"status": "authenticated" | "expired" | "unauthenticated"}.
            Expiry is determined from the timestamp recorded at token storage time.
            """
            result = await self.service.oauth_get_status(config_id)
            if result is None:
                raise HTTPException(status_code=404, detail="MCP config not found")
            return result

        # ========== Template Endpoints ==========

        @self.app.get("/api/templates")
        @handle_exceptions("list templates")
        async def list_templates(limit: int = 100, offset: int = 0):
            """List minion templates, paginated"""
            return await self.service.list_templates(limit=limit, offset=offset)

        @self.app.get("/api/templates/{template_id}")
        @handle_exceptions("get template")
        async def get_template(template_id: str):
            """Get specific template"""
            template = await self.service.get_template(template_id)
            if not template:
                raise HTTPException(status_code=404, detail="Template not found")
            return template

        @self.app.post("/api/templates")
        @handle_exceptions("create template", value_error_status=400)
        async def create_template(request: TemplateCreateRequest):
            """Create new template"""
            config = request.to_session_config()
            return await self.service.create_template(
                name=request.name,
                config=config,
                role=request.role,
                system_prompt=request.system_prompt,
                description=request.description,
                capabilities=request.capabilities,
            )

        @self.app.put("/api/templates/{template_id}")
        @handle_exceptions("update template", value_error_status=400)
        async def update_template(template_id: str, request: TemplateUpdateRequest):
            """Update existing template"""
            return await self.service.update_template(
                template_id,
                name=request.name,
                permission_mode=request.permission_mode,
                allowed_tools=request.allowed_tools,
                disallowed_tools=request.disallowed_tools,
                role=request.role,
                system_prompt=request.system_prompt,
                description=request.description,
                model=request.model,
                capabilities=request.capabilities,
                override_system_prompt=request.override_system_prompt,
                sandbox_enabled=request.sandbox_enabled,
                sandbox_config=request.sandbox_config,
                cli_path=request.cli_path,
                additional_directories=request.additional_directories,
                # Docker session isolation (issue #496)
                docker_enabled=request.docker_enabled,
                docker_image=request.docker_image,
                docker_extra_mounts=request.docker_extra_mounts,
                # Thinking and effort configuration (issue #580)
                thinking_mode=request.thinking_mode,
                thinking_budget_tokens=request.thinking_budget_tokens,
                effort=request.effort,
                history_distillation_enabled=request.history_distillation_enabled,
                auto_memory_mode=request.auto_memory_mode,
                auto_memory_directory=request.auto_memory_directory,
                skill_creating_enabled=request.skill_creating_enabled,
                mcp_server_ids=request.mcp_server_ids,
                enable_claudeai_mcp_servers=request.enable_claudeai_mcp_servers,
                strict_mcp_config=request.strict_mcp_config,
            )

        @self.app.delete("/api/templates/{template_id}")
        @handle_exceptions("delete template")
        async def delete_template(template_id: str):
            """Delete template"""
            success = await self.service.delete_template(template_id)
            if not success:
                raise HTTPException(status_code=404, detail="Template not found")
            return {"deleted": True}

        @self.app.get("/api/templates/{template_id}/export")
        @handle_exceptions("export template")
        async def export_template(template_id: str):
            """Export template as a downloadable JSON envelope"""
            from fastapi.responses import Response as FastAPIResponse
            template = await self.service.get_template(template_id)
            if not template:
                raise HTTPException(status_code=404, detail="Template not found")
            envelope = {
                "version": 1,
                "exported_at": datetime.now(UTC).isoformat(),
                "template": template,
            }
            slug = re.sub(r'[^a-z0-9]+', '_', template["name"].strip().lower()).strip('_')
            filename = f"{slug}.template.json"
            return FastAPIResponse(
                content=json.dumps(envelope, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @self.app.post("/api/templates/import", status_code=201)
        @handle_exceptions("import template", value_error_status=400)
        async def import_template(request: Request):
            """Import a template from an export envelope"""
            body = await request.json()
            try:
                return await self.service.import_template(
                    data=body,
                    overwrite=bool(body.get("overwrite", False)),
                )
            except TemplateConflictError as e:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "name_conflict",
                        "existing_template_id": e.existing_id,
                        "name": e.name,
                    },
                ) from e




    def _create_message_callback(self, session_id: str):
        """Create message callback for poll queue broadcasting using unified MessageProcessor"""
        async def callback(session_id: str, message_data: Any):
            logger.info(f"Message callback triggered for session {session_id}, message type: {getattr(message_data, 'type', 'unknown')}")
            try:
                # Process message and prepare for poll queue using MessageProcessor
                if hasattr(message_data, '__dict__'):
                    # Handle ParsedMessage objects (from MessageProcessor)
                    websocket_data = self._message_processor.prepare_for_websocket(message_data)
                    parsed_message = message_data
                else:
                    # Handle raw dict messages - process them first
                    parsed_message = self._message_processor.process_message(message_data, source="websocket")
                    websocket_data = self._message_processor.prepare_for_websocket(parsed_message)

                # Issue #324: Emit tool_call messages for tool lifecycle events
                await self._emit_tool_call_updates(session_id, parsed_message)

                # Wrap in standard poll queue envelope
                serialized = {
                    "type": "message",
                    "session_id": session_id,
                    "data": websocket_data,
                    "timestamp": datetime.now(UTC).isoformat()
                }

                if session_id in self.session_queues:
                    self.session_queues[session_id].append(serialized)
                    logger.info(f"Appended message to session queue for {session_id}")

                # Issue #952: Emit context_update after result messages using SDK API
                msg_type = getattr(parsed_message, 'type', None)
                if msg_type is not None:
                    msg_type_str = msg_type.value if hasattr(msg_type, 'value') else str(msg_type)
                else:
                    msg_type_str = websocket_data.get("type", "")
                if msg_type_str == "result" and session_id in self.session_queues:
                    ctx = await self.coordinator.get_context_usage(session_id)
                    if ctx and ctx.get("totalTokens"):
                        self.session_queues[session_id].append({
                            "type": "context_update",
                            "session_id": session_id,
                            "input_tokens": ctx["totalTokens"],
                            "context_window": ctx["maxTokens"],
                            "context_pct": round(ctx["percentage"], 1),
                            "timestamp": datetime.now(UTC).isoformat(),
                        })

            except Exception:
                logger.exception("Error in message callback")

        return callback

    async def _emit_tool_call_updates(self, session_id: str, parsed_message: Any):
        """
        Issue #324: Emit unified tool_call messages for tool lifecycle events.

        Detects tool_use blocks in assistant messages and tool_results in user messages,
        creates/updates ToolCall objects, and broadcasts them to WebSocket.
        """
        try:
            msg_type = getattr(parsed_message, 'type', None)
            if msg_type:
                msg_type = msg_type.value if hasattr(msg_type, 'value') else str(msg_type)

            metadata = getattr(parsed_message, 'metadata', {}) or {}

            # Handle tool_use in assistant messages
            if msg_type == 'assistant':
                tool_uses = metadata.get('tool_uses', [])
                # Issue #195: Propagate parent_tool_use_id from message to child tool_calls
                parent_tool_use_id = metadata.get('parent_tool_use_id')
                for tool_use in tool_uses:
                    tool_id = tool_use.get('id')
                    tool_name = tool_use.get('name')
                    input_params = tool_use.get('input', {})

                    if tool_id and tool_name:
                        # Create new ToolCall with PENDING status
                        # Issue #195: Pass parent_tool_use_id so it's stored in the ToolCall object
                        tool_call = self.coordinator.create_tool_call(
                            session_id=session_id,
                            tool_use_id=tool_id,
                            name=tool_name,
                            input_params=input_params,
                            requires_permission=False,  # Will be updated if permission is requested
                            parent_tool_use_id=parent_tool_use_id,
                        )

                        # Emit tool_call message (parent_tool_use_id included via to_dict())
                        tool_call_data = tool_call.to_dict()
                        tool_call_data["type"] = "tool_call"

                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": tool_call_data,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                        if session_id in self.session_queues:
                            self.session_queues[session_id].append(websocket_message)
                        logger.debug(f"Emitted tool_call pending for {tool_name} ({tool_id}) in session {session_id}")

            # Handle tool_results in user messages
            elif msg_type == 'user':
                tool_results = metadata.get('tool_results', [])
                for tool_result in tool_results:
                    tool_use_id = tool_result.get('tool_use_id')
                    result_content = tool_result.get('content')
                    is_error = tool_result.get('is_error', False)

                    if tool_use_id:
                        # Update ToolCall with result
                        updated_tool_call = self.coordinator.update_tool_call_result(
                            session_id=session_id,
                            tool_use_id=tool_use_id,
                            result=result_content,
                            is_error=is_error,
                            triggering_message=tool_result,  # Issue #494: embed ToolResultBlock
                        )

                        if updated_tool_call:
                            # Emit tool_call message
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            if session_id in self.session_queues:
                                self.session_queues[session_id].append(websocket_message)
                            logger.debug(
                                f"Emitted tool_call {'failed' if is_error else 'completed'} "
                                f"for {tool_use_id} in session {session_id}"
                            )

        except Exception:
            logger.exception("Error emitting tool_call updates")

    async def _on_state_change(self, state_data: dict):
        """Handle session state changes"""
        try:
            session_id = state_data.get("session_id")
            if session_id:
                # Get full session info for real-time updates
                session_info_dict = await self.coordinator.get_session_info(session_id)
                if session_info_dict:
                    session_dict = session_info_dict.get("session", {})
                    # Issue #500: Include queue status in state changes
                    session_dict["queue_pending_count"] = (
                        self.coordinator.queue_manager.get_pending_count(session_id)
                    )
                    self._broadcast_state_change(session_id, session_dict, state_data.get("timestamp"))
        except Exception:
            logger.exception("Error handling state change")

    def _on_tool_call_broadcast(self, session_id: str, tool_call_data: dict):
        """Issue #520: Append tool_call message to session poll queue.

        Called synchronously from coordinator.
        """
        if session_id in self.session_queues:
            self.session_queues[session_id].append({
                "type": "tool_call",
                "session_id": session_id,
                "data": tool_call_data,
                "timestamp": datetime.now(UTC).isoformat(),
            })

    async def _on_session_reset(self, session_id: str):
        """Issue #500: Append session_reset to UI queue so frontend clears stale messages."""
        try:
            message = {
                "type": "session_reset",
                "data": {"session_id": session_id},
            }
            self.ui_queue.append(message)
            logger.info(f"Appended session_reset for {session_id} to UI queue")
        except Exception:
            logger.exception("Error appending session_reset")


# _serialize_message method removed - now using MessageProcessor.prepare_for_websocket() for unified formatting

    async def _run_git_command(
        self, args: list[str], cwd: str, allow_nonzero: bool = False
    ) -> str | None:
        """Run a git command via asyncio.create_subprocess_exec and return stdout, or None on error.

        Args:
            allow_nonzero: If True, return stdout even on non-zero exit codes.
                Required for commands like ``git diff --no-index`` which return
                exit code 1 when files differ (expected, not an error).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0 and not allow_nonzero:
                return None
            return stdout.decode().strip()
        except (TimeoutError, FileNotFoundError, OSError) as e:
            logger.debug(f"Git command failed: {args} - {e}")
            return None

    def _default_html(self) -> str:
        """Default HTML content when no index.html exists"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Claude Code WebUI</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body>
            <h1>Claude Code WebUI</h1>
            <p>Welcome to Claude Code WebUI. The frontend interface is being loaded.</p>
            <p>Please check that the static files are properly configured.</p>
        </body>
        </html>
        """

    async def cleanup(self):
        """Cleanup resources"""
        await self.coordinator.cleanup()
        logger.info("WebUI cleanup completed")


# Global application instance
webui_app = None

def _mock_factory_for_fixtures(mock_cls, fixtures_dir: Path, available_fixtures: set[str]):
    """Create a factory that maps session names to fixture directories (issue #561)."""
    def factory(session_id, working_directory, **kwargs):
        session_name = kwargs.pop("session_name", None)
        if session_name:
            candidate = fixtures_dir / session_name
            if candidate.is_dir():
                kwargs["session_dir"] = str(candidate)
            else:
                raise ValueError(
                    f"No fixture found for session name '{session_name}'. "
                    f"Available fixtures: {', '.join(sorted(available_fixtures))}"
                )
        return mock_cls(session_id=session_id, working_directory=working_directory, **kwargs)
    return factory


def create_app(
    data_dir: Path = None,
    experimental: bool = False,
    mock_sdk: bool = False,
    fixtures_dir: Path | None = None,
    available_fixtures: list[str] | None = None,
    auth_token: str | None = None,
    auth_enabled: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application"""
    global webui_app
    webui_app = ClaudeWebUI(
        data_dir, experimental=experimental,
        mock_sdk=mock_sdk, fixtures_dir=fixtures_dir,
        available_fixtures=available_fixtures,
        auth_token=auth_token, auth_enabled=auth_enabled,
    )
    return webui_app.app

async def startup_event():
    """Application startup event"""
    if webui_app:
        await webui_app.initialize()

async def shutdown_event():
    """Application shutdown event"""
    if webui_app:
        await webui_app.cleanup()
