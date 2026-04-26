"""
FastAPI web server for Claude Code WebUI with HTTP long-polling support.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .application_service import ApplicationService
from .event_queue import EventQueue
from .message_parser import MessageParser, MessageProcessor
from .permission_service import PermissionService
from .session_coordinator import SessionCoordinator
from .skill_manager import SkillManager
from .task_utils import task_done_log_exception

# Keep standard logger for errors
logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for HTTP requests (issue #728).

    Exempts static assets, root HTML, health check, and auth check endpoint.
    WebSocket auth is handled separately in each WS endpoint handler.
    """

    EXEMPT_PATHS = {'/', '/health', '/api/auth/check', '/oauth/callback'}
    EXEMPT_PREFIXES = ('/assets/',)
    # Issue #827: The per-session secrets resolve endpoint uses its own Bearer token auth,
    # not the global operator token. Exempt it from global AuthMiddleware.
    EXEMPT_SUFFIXES = ('/secrets/resolve',)

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
        for suffix in self.EXEMPT_SUFFIXES:
            if path.endswith(suffix):
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

        # Issue #1130/#1131: Session watchdog service (created here, started in initialize())
        from .config_manager import load_config as _load_cfg
        from .session_watchdog import SessionWatchdogService
        _cfg = _load_cfg(config_file) if config_file else _load_cfg()
        self._watchdog = SessionWatchdogService(
            session_manager=self.coordinator.session_manager,
            template_manager=self.coordinator.template_manager,
            app_config=_cfg,
            ui_queue=self.ui_queue,
        )
        self.coordinator._watchdog = self._watchdog

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

        # Issue #976/#989: Inject OAuth refresh broadcast callback into OAuthRefreshManager
        self.coordinator.oauth_refresh_manager.set_broadcast_callback(self._broadcast_mcp_oauth_refreshed)
        logger.info("OAuth refresh broadcast callback injected into OAuthRefreshManager")

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
        # Issue #1114: Wire enqueue broadcast callback so MCP queue_task (and other internal
        # callers of enqueue_message) emit real-time queue_update events to the WebUI.
        self.coordinator.set_enqueue_broadcast_callback(self._broadcast_queue_update)

        # Issue #1050: Best-effort proxy image check on startup (informational only)
        from .config_manager import load_config
        from .logging_config import get_logger as _get_logger
        _startup_logger = _get_logger('coordinator', category='PROXY')
        startup_config = load_config(self.config_file) if self.config_file else load_config()
        if startup_config.proxy.proxy_image:
            from .docker_utils import check_proxy_image_available
            image_ok = await check_proxy_image_available(startup_config.proxy.proxy_image)
            if image_ok:
                _startup_logger.info(f"Default proxy image '{startup_config.proxy.proxy_image}' available.")
            else:
                _startup_logger.info(
                    f"Default proxy image '{startup_config.proxy.proxy_image}' not found locally. "
                    f"It will be auto-built on first proxy-enabled session start."
                )

        # Issue #1130: Start session watchdog service
        await self._watchdog.start()

        logger.info("Claude Code WebUI initialized")

    def _setup_routes(self):
        """Setup FastAPI routes"""
        from .routers import register_all
        register_all(self.app, self)

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

                # Issue #1000: Propagate message_id from storage for frontend dedup
                if isinstance(message_data, dict) and 'message_id' in message_data:
                    websocket_data['message_id'] = message_data['message_id']

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
        # Issue #1130: Stop session watchdog service
        if hasattr(self, '_watchdog') and self._watchdog is not None:
            await self._watchdog.stop()
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
