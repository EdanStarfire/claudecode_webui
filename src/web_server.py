"""
FastAPI web server for Claude Code WebUI with WebSocket support.
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import PermissionUpdate
from claude_agent_sdk.types import PermissionRuleValue
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .file_upload import FileUploadError, FileUploadManager
from .logging_config import get_logger
from .message_parser import MessageParser, MessageProcessor
from .models.messages import (
    PermissionInfo,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PermissionSuggestion,
    StoredMessage,
)
from .permission_resolver import resolve_effective_permissions
from .session_coordinator import SessionCoordinator
from .session_manager import SessionState
from .skill_manager import SkillManager
from .timestamp_utils import normalize_timestamp

# Get specialized logger for WebSocket lifecycle debugging
ws_logger = get_logger('websocket_debug', category='WS_LIFECYCLE')
# Get verbose logger for ping/pong (use only with --debug-ping-pong)
ws_verbose_logger = get_logger('websocket_verbose', category='WS_PING_PONG')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


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


class SessionCreateRequest(BaseModel):
    project_id: str
    permission_mode: str = "acceptEdits"
    system_prompt: str | None = None
    override_system_prompt: bool = False
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    model: str | None = None
    name: str | None = None
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load


class MessageRequest(BaseModel):
    message: str


class SessionNameUpdateRequest(BaseModel):
    name: str


class SessionUpdateRequest(BaseModel):
    """Generic session update request - all fields optional"""
    name: str | None = None
    model: str | None = None  # sonnet, opus, haiku, opusplan
    allowed_tools: list[str] | None = None  # List of tool names to allow
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    role: str | None = None
    system_prompt: str | None = None  # UI calls this "initialization_context"
    override_system_prompt: bool | None = None
    capabilities: list[str] | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load


class SessionReorderRequest(BaseModel):
    session_ids: list[str]


class PermissionModeRequest(BaseModel):
    mode: str


class CommSendRequest(BaseModel):
    to_minion_id: str | None = None
    to_user: bool = False
    content: str
    comm_type: str = "task"


class MinionCreateRequest(BaseModel):
    name: str
    model: str | None = None  # Model selection (sonnet, opus, haiku, opusplan)
    role: str | None = ""
    initialization_context: str | None = ""
    override_system_prompt: bool = False
    capabilities: list[str] | None = None
    permission_mode: str = "default"
    allowed_tools: list[str] | None = None  # None or empty list means no pre-authorized tools
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    working_directory: str | None = None  # Optional custom working directory for this minion
    sandbox_enabled: bool = False  # Enable OS-level sandboxing (issue #319)
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings
    setting_sources: list[str] | None = None  # Issue #36: which settings files to load




class TemplateCreateRequest(BaseModel):
    name: str
    permission_mode: str
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    default_role: str | None = None
    default_system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool = False
    sandbox_enabled: bool = False
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None  # Issue #461: tools to deny
    default_role: str | None = None
    default_system_prompt: str | None = None
    description: str | None = None
    model: str | None = None
    capabilities: list[str] | None = None
    override_system_prompt: bool | None = None
    sandbox_enabled: bool | None = None
    sandbox_config: dict | None = None  # Issue #458: sandbox configuration settings


class UIWebSocketManager:
    """Manages global UI WebSocket connections for session state updates"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"UI WebSocket connected. Total UI connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"UI WebSocket disconnected. Total UI connections: {len(self.active_connections)}")

    async def broadcast_to_all(self, message: dict):
        """Broadcast message to all connected UI clients"""
        if not self.active_connections:
            logger.debug("No UI WebSocket connections available for broadcasting")
            return

        dead_connections = []
        for connection in self.active_connections[:]:  # Create a copy to iterate safely
            try:
                # Check if connection is still open before attempting to send
                if connection.client_state.value != 1:  # 1 = OPEN state
                    logger.warning(f"UI WebSocket connection is not open (state: {connection.client_state.value})")
                    dead_connections.append(connection)
                    continue

                await connection.send_json(message)
                logger.debug(f"Broadcasted message to UI WebSocket: {message.get('type', 'unknown')}")

            except Exception as e:
                logger.error(f"Error sending to UI WebSocket connection: {e}")
                dead_connections.append(connection)

        # Clean up dead connections
        for dead_connection in dead_connections:
            self.disconnect(dead_connection)


class WebSocketManager:
    """Manages WebSocket connections for session-specific messaging"""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        logger.info(f"WebSocket connected for session {session_id}")

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        logger.info(f"WebSocket disconnected for session {session_id}")

    async def force_disconnect_session(self, session_id: str):
        """Force disconnect all WebSocket connections for a specific session"""
        if session_id in self.active_connections:
            connections = self.active_connections[session_id].copy()
            logger.info(f"Force disconnecting {len(connections)} WebSocket connections for session {session_id}")
            for websocket in connections:
                try:
                    await websocket.close(code=1012, reason="Session being deleted")
                except Exception as e:
                    logger.warning(f"Error closing WebSocket for session {session_id}: {e}")
            # Clear the connections list
            if session_id in self.active_connections:
                del self.active_connections[session_id]
            logger.info(f"All WebSocket connections for session {session_id} have been disconnected")

    async def send_message(self, session_id: str, message: dict):
        logger.info(f"WebSocketManager: Attempting to send message to session {session_id}, active connections: {len(self.active_connections.get(session_id, []))}")
        if session_id in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[session_id][:]:  # Create a copy to iterate safely
                try:
                    # Check if connection is still open before attempting to send
                    if connection.client_state.value != 1:  # 1 = OPEN state
                        logger.warning(f"WebSocket connection for session {session_id} is not open (state: {connection.client_state.value})")
                        dead_connections.append(connection)
                        continue

                    logger.info(f"WebSocketManager: Sending message to WebSocket connection for session {session_id}")
                    await connection.send_text(json.dumps(message))
                    logger.info(f"WebSocketManager: Message sent successfully to WebSocket connection for session {session_id}")
                except Exception as e:
                    logger.error(f"Error sending WebSocket message: {e}")
                    dead_connections.append(connection)

            # Remove dead connections
            for dead_conn in dead_connections:
                self.disconnect(dead_conn, session_id)


class LegionWebSocketManager:
    """Manages WebSocket connections for legion-specific real-time updates"""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, legion_id: str):
        await websocket.accept()
        if legion_id not in self.active_connections:
            self.active_connections[legion_id] = []
        self.active_connections[legion_id].append(websocket)
        logger.info(f"Legion WebSocket connected for legion {legion_id}. Total connections: {len(self.active_connections[legion_id])}")

    def disconnect(self, websocket: WebSocket, legion_id: str):
        if legion_id in self.active_connections:
            if websocket in self.active_connections[legion_id]:
                self.active_connections[legion_id].remove(websocket)
            if not self.active_connections[legion_id]:
                del self.active_connections[legion_id]
        logger.info(f"Legion WebSocket disconnected for legion {legion_id}")

    async def broadcast_to_legion(self, legion_id: str, message: dict):
        """Broadcast message to all clients watching this legion"""
        if legion_id not in self.active_connections:
            logger.debug(f"No Legion WebSocket connections for legion {legion_id}")
            return

        dead_connections = []
        for connection in self.active_connections[legion_id][:]:
            try:
                if connection.client_state.value != 1:  # 1 = OPEN state
                    logger.warning(f"Legion WebSocket connection for legion {legion_id} is not open")
                    dead_connections.append(connection)
                    continue

                await connection.send_text(json.dumps(message))
                logger.debug(f"Broadcast message to legion {legion_id} WebSocket")
            except Exception as e:
                logger.error(f"Error broadcasting to legion {legion_id} WebSocket: {e}")
                dead_connections.append(connection)

        # Clean up dead connections
        for dead_connection in dead_connections:
            self.disconnect(dead_connection, legion_id)


class ClaudeWebUI:
    """Main WebUI application class"""

    def __init__(self, data_dir: Path = None, experimental: bool = False):
        self.app = FastAPI(title="Claude Code WebUI", version="1.0.0")
        self.coordinator = SessionCoordinator(data_dir, experimental=experimental)
        self.skill_manager = SkillManager()
        self.websocket_manager = WebSocketManager()
        self.ui_websocket_manager = UIWebSocketManager()
        self.legion_websocket_manager = LegionWebSocketManager()

        # Inject UI WebSocket manager into Legion system for project update broadcasts
        self.coordinator.legion_system.ui_websocket_manager = self.ui_websocket_manager

        # Initialize MessageProcessor for unified WebSocket message formatting
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Track pending permission requests with asyncio.Future objects
        self.pending_permissions: dict[str, asyncio.Future] = {}

        # Rate limiting for restart endpoint (issue #434)
        self._last_restart_time: float = 0

        # Setup routes
        self._setup_routes()

        # Setup Legion WebSocket broadcast callbacks
        self.coordinator.legion_system.comm_router.set_comm_broadcast_callback(
            self._broadcast_comm_to_legion_websocket
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
            return self._create_permission_callback(session_id)
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
            if session_id in self.coordinator._message_callbacks:
                self.coordinator._message_callbacks[session_id] = []

            # Register WebSocket callback for this session
            self.coordinator.add_message_callback(
                session_id,
                self._create_message_callback(session_id)
            )
            logger.info(f"Registered WebSocket message callback for session {session_id}")
        return registrar

    async def _broadcast_comm_to_legion_websocket(self, legion_id: str, comm):
        """Broadcast new comm to WebSocket clients watching this legion"""
        try:
            # Convert comm to dict for JSON serialization
            comm_dict = comm.to_dict()

            # Broadcast to all clients watching this legion
            await self.legion_websocket_manager.broadcast_to_legion(legion_id, {
                "type": "comm",
                "comm": comm_dict,
                "timestamp": datetime.now(UTC).isoformat()
            })
            logger.debug(f"Broadcast comm {comm.comm_id} to legion {legion_id} WebSocket clients")
        except Exception as e:
            logger.error(f"Error broadcasting comm to legion WebSocket: {e}")

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict):
        """
        Broadcast resource_registered event to WebSocket clients watching this session.

        Issue #404: Called by ResourceMCPTools when a resource is registered.

        Args:
            session_id: Session that registered the resource
            resource_metadata: Resource metadata dict (resource_id, title, is_image, etc.)
        """
        try:
            await self.websocket_manager.send_message(session_id, {
                "type": "resource_registered",
                "resource": resource_metadata,
                "timestamp": datetime.now(UTC).isoformat()
            })
            logger.debug(f"Broadcast resource_registered for {resource_metadata.get('resource_id')} to session {session_id}")
        except Exception as e:
            logger.error(f"Error broadcasting resource_registered: {e}")

    def _cleanup_pending_permissions_for_session(self, session_id: str):
        """Clean up pending permissions for a specific session by auto-denying them"""
        try:
            # Find all pending permissions for this session and auto-deny them
            permissions_to_cleanup = []
            for request_id, future in self.pending_permissions.items():
                if not future.done():
                    # We need to identify which permissions belong to this session
                    # Since the permission callback stores the session_id in its closure,
                    # we'll auto-deny all pending permissions when a session terminates
                    # This is safe because if a session is terminated, no tools should execute
                    permissions_to_cleanup.append(request_id)

            for request_id in permissions_to_cleanup:
                future = self.pending_permissions.pop(request_id, None)
                if future and not future.done():
                    # Auto-deny the permission
                    response = {
                        "behavior": "deny",
                        "message": f"Session {session_id} terminated - auto-denying pending permission"
                    }
                    future.set_result(response)
                    logger.info(f"Auto-denied pending permission {request_id} due to session {session_id} termination")

            if permissions_to_cleanup:
                logger.info(f"Cleaned up {len(permissions_to_cleanup)} pending permissions for session {session_id}")

        except Exception as e:
            logger.error(f"Error cleaning up pending permissions for session {session_id}: {e}")

    async def initialize(self):
        """Initialize the WebUI application"""
        await self.coordinator.initialize()
        await self.skill_manager.sync()

        # Templates are now loaded in SessionCoordinator.initialize()

        # Register callbacks
        self.coordinator.add_state_change_callback(self._on_state_change)

        logger.info("Claude Code WebUI initialized")

    def _setup_routes(self):
        """Setup FastAPI routes"""

        @self.app.get("/", response_class=HTMLResponse)
        async def read_root():
            """Serve the main HTML page"""
            html_file = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
            if html_file.exists():
                return HTMLResponse(content=html_file.read_text(encoding='utf-8'), status_code=200)
            return HTMLResponse(content=self._default_html(), status_code=200)

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint"""
            return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

        # ==================== PROJECT ENDPOINTS ====================

        @self.app.post("/api/projects")
        async def create_project(request: ProjectCreateRequest):
            """Create a new project."""
            try:
                project = await self.coordinator.project_manager.create_project(
                    name=request.name,
                    working_directory=request.working_directory,
                    max_concurrent_minions=request.max_concurrent_minions
                )
                return {"project": project.to_dict()}
            except Exception as e:
                logger.error(f"Failed to create project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/projects")
        async def list_projects():
            """List all projects."""
            try:
                projects = await self.coordinator.project_manager.list_projects()
                return {"projects": [p.to_dict() for p in projects]}
            except Exception as e:
                logger.error(f"Failed to list projects: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/projects/{project_id}")
        async def get_project(project_id: str):
            """Get project with sessions"""
            try:
                project = await self.coordinator.project_manager.get_project(project_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Get sessions for this project
                sessions = await self.coordinator.session_manager.get_sessions_by_ids(project.session_ids)

                return {
                    "project": project.to_dict(),
                    "sessions": [s.to_dict() for s in sessions]
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/projects/reorder")
        async def reorder_projects(request: ProjectReorderRequest):
            """Reorder projects"""
            try:
                success = await self.coordinator.project_manager.reorder_projects(request.project_ids)
                if not success:
                    raise HTTPException(status_code=400, detail="Failed to reorder projects")
                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to reorder projects: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/projects/{project_id}")
        async def update_project(project_id: str, request: ProjectUpdateRequest):
            """Update project metadata"""
            try:
                success = await self.coordinator.project_manager.update_project(
                    project_id=project_id,
                    name=request.name,
                    is_expanded=request.is_expanded
                )
                if not success:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Broadcast update to UI
                project = await self.coordinator.project_manager.get_project(project_id)
                await self.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })

                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to update project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/projects/{project_id}")
        async def delete_project(project_id: str):
            """Delete project and all its sessions"""
            try:
                project = await self.coordinator.project_manager.get_project(project_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Delete all sessions in the project
                for session_id in project.session_ids:
                    await self.coordinator.delete_session(session_id)

                # Delete the project
                success = await self.coordinator.project_manager.delete_project(project_id)
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to delete project")

                # Broadcast deletion to UI
                await self.ui_websocket_manager.broadcast_to_all({
                    "type": "project_deleted",
                    "data": {"project_id": project_id}
                })

                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to delete project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/projects/{project_id}/toggle-expansion")
        async def toggle_project_expansion(project_id: str):
            """Toggle project expansion state"""
            try:
                success = await self.coordinator.project_manager.toggle_expansion(project_id)
                if not success:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Broadcast update to UI
                project = await self.coordinator.project_manager.get_project(project_id)
                await self.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })

                return {"success": True, "is_expanded": project.is_expanded}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to toggle expansion: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/projects/{project_id}/sessions/reorder")
        async def reorder_project_sessions(project_id: str, request: SessionReorderRequest):
            """Reorder sessions within a project"""
            try:
                success = await self.coordinator.project_manager.reorder_project_sessions(
                    project_id=project_id,
                    session_ids=request.session_ids
                )
                if not success:
                    raise HTTPException(status_code=400, detail="Failed to reorder sessions")

                # Also update session order in session manager
                await self.coordinator.session_manager.reorder_sessions(request.session_ids)

                # Broadcast project update to all UI clients
                project = await self.coordinator.project_manager.get_project(project_id)
                if project:
                    await self.ui_websocket_manager.broadcast_to_all({
                        "type": "project_updated",
                        "data": {"project": project.to_dict()}
                    })

                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to reorder project sessions: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== SESSION ENDPOINTS ====================

        @self.app.post("/api/sessions")
        async def create_session(request: SessionCreateRequest):
            """Create a new Claude Code session within a project"""
            try:
                # Pre-generate session ID so we can pass it to permission callback
                session_id = str(uuid.uuid4())

                session_id = await self.coordinator.create_session(
                    session_id=session_id,
                    project_id=request.project_id,
                    permission_mode=request.permission_mode,
                    system_prompt=request.system_prompt,
                    override_system_prompt=request.override_system_prompt,
                    allowed_tools=request.allowed_tools,
                    disallowed_tools=request.disallowed_tools,
                    model=request.model,
                    name=request.name,
                    permission_callback=self._create_permission_callback(session_id),
                    setting_sources=request.setting_sources  # Issue #36
                )

                # Broadcast session creation to all UI clients
                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                if session_info:
                    await self.ui_websocket_manager.broadcast_to_all({
                        "type": "state_change",
                        "data": {
                            "session_id": session_id,
                            "session": session_info.to_dict(),
                            "timestamp": datetime.now().isoformat()
                        }
                    })
                    logger.debug(f"Broadcasted state_change for newly created session {session_id}")

                # Broadcast project update to all UI clients (session was added to project)
                project = await self.coordinator.project_manager.get_project(request.project_id)
                if project:
                    await self.ui_websocket_manager.broadcast_to_all({
                        "type": "project_updated",
                        "data": {"project": project.to_dict()}
                    })
                    logger.debug(f"Broadcasted project_updated for project {request.project_id} after session creation")

                return {"session_id": session_id}

            except Exception as e:
                logger.error(f"Failed to create session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions")
        async def list_sessions():
            """List all sessions"""
            try:
                sessions = await self.coordinator.list_sessions()
                return {"sessions": sessions}
            except Exception as e:
                logger.error(f"Failed to list sessions: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}")
        async def get_session_info(session_id: str):
            """Get session information"""
            try:
                info = await self.coordinator.get_session_info(session_id)
                if not info:
                    raise HTTPException(status_code=404, detail="Session not found")

                # Log session state for debugging
                session_state = info.get('session', {}).get('state', 'unknown')
                logger.info(f"API returning session {session_id} with state: {session_state}")

                return info
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get session info: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/descendants")
        async def get_session_descendants(session_id: str):
            """Get all descendant sessions (children, grandchildren, etc.) of a session"""
            try:
                descendants = await self.coordinator.get_descendants(session_id)
                return {
                    "session_id": session_id,
                    "descendants": descendants,
                    "count": len(descendants)
                }
            except Exception as e:
                logger.error(f"Failed to get descendants for session {session_id}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/start")
        async def start_session(session_id: str):
            """Start a session"""
            try:
                # Clear any existing callbacks to prevent duplicates (in case session is restarted)
                if session_id in self.coordinator._message_callbacks:
                    self.coordinator._message_callbacks[session_id] = []

                # Register WebSocket callback for this session (works for both new and resumed sessions)
                self.coordinator.add_message_callback(
                    session_id,
                    self._create_message_callback(session_id)
                )

                success = await self.coordinator.start_session(session_id, permission_callback=self._create_permission_callback(session_id))
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to start session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/pause")
        async def pause_session(session_id: str):
            """Pause a session"""
            try:
                success = await self.coordinator.pause_session(session_id)
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to pause session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/terminate")
        async def terminate_session(session_id: str):
            """Terminate a session"""
            try:
                # Clean up any pending permissions for this session
                self._cleanup_pending_permissions_for_session(session_id)

                success = await self.coordinator.terminate_session(session_id)
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to terminate session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/sessions/{session_id}/name")
        async def update_session_name(session_id: str, request: SessionNameUpdateRequest):
            """Update session name"""
            try:
                success = await self.coordinator.update_session_name(session_id, request.name)
                if not success:
                    raise HTTPException(status_code=404, detail="Session not found")
                return {"success": success}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to update session name: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.patch("/api/sessions/{session_id}")
        async def update_session(session_id: str, request: SessionUpdateRequest):
            """Update session fields (generic endpoint)"""
            try:
                session = await self.coordinator.session_manager.get_session_info(session_id)
                if not session:
                    raise HTTPException(status_code=404, detail="Session not found")

                is_active = session.state.value in ["active", "starting"]
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

                # Handle system_prompt update (UI calls this "initialization_context")
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

                if not updates:
                    return {"success": True, "message": "No fields to update"}

                success = await self.coordinator.session_manager.update_session(session_id, **updates)
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to update session")

                return {"success": success}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to update session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/sessions/{session_id}")
        async def delete_session(session_id: str):
            """Delete a session and all its data (including cascaded child sessions)"""
            try:
                # Find the project before deletion (so we can check if it gets auto-deleted)
                project = await self.coordinator._find_project_for_session(session_id)
                project_id = project.project_id if project else None

                # First force disconnect any WebSocket connections for this session
                await self.websocket_manager.force_disconnect_session(session_id)

                # Clean up any pending permissions for this session
                self._cleanup_pending_permissions_for_session(session_id)

                # Delete the session (may also delete the project if it was the last session)
                # Returns dict with success and deleted_session_ids (for cascading deletes)
                result = await self.coordinator.delete_session(session_id)
                if not result.get("success"):
                    raise HTTPException(status_code=404, detail="Session not found")

                deleted_ids = result.get("deleted_session_ids", [])

                # Force disconnect WebSocket connections for any cascaded child sessions
                for deleted_id in deleted_ids:
                    if deleted_id != session_id:  # Already disconnected the primary session
                        await self.websocket_manager.force_disconnect_session(deleted_id)
                        self._cleanup_pending_permissions_for_session(deleted_id)

                # Check if the project still exists - if not, it was auto-deleted
                if project_id:
                    updated_project = await self.coordinator.project_manager.get_project(project_id)
                    if updated_project is None:
                        # Project was deleted because it became empty
                        await self.ui_websocket_manager.broadcast_to_all({
                            "type": "project_deleted",
                            "data": {"project_id": project_id}
                        })
                        logger.info(f"Broadcasted project_deleted for auto-deleted project {project_id}")
                    else:
                        # Project still exists - broadcast update with reduced session count
                        await self.ui_websocket_manager.broadcast_to_all({
                            "type": "project_updated",
                            "data": {"project": updated_project.to_dict()}
                        })
                        logger.debug(f"Broadcasted project_updated for project {project_id} after session deletion")

                return {
                    "success": result.get("success"),
                    "deleted_session_ids": deleted_ids
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to delete session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/messages")
        async def send_message(session_id: str, request: MessageRequest):
            """Send a message to a session"""
            try:
                success = await self.coordinator.send_message(session_id, request.message)
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/messages")
        async def get_messages(session_id: str, limit: int | None = 50, offset: int = 0):
            """Get messages from a session with pagination metadata"""
            try:
                result = await self.coordinator.get_session_messages(
                    session_id, limit=limit, offset=offset
                )
                return result
            except Exception as e:
                logger.error(f"Failed to get messages: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== FILE UPLOAD ENDPOINTS ====================

        @self.app.post("/api/sessions/{session_id}/files")
        async def upload_file(session_id: str, file: UploadFile = File(...)):
            """
            Upload a file for a session.

            Files are stored in data/sessions/{session_id}/attachments/
            and paths are passed to Claude for reading via the Read tool.
            """
            try:
                # Verify session exists
                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                if not session_info:
                    raise HTTPException(status_code=404, detail="Session not found")

                # Initialize file upload manager if not already done
                file_manager = FileUploadManager(self.coordinator.data_dir / "sessions")

                # Read file content
                file_content = await file.read()

                # Upload file
                file_info = await file_manager.upload_file(
                    session_id=session_id,
                    filename=file.filename,
                    file_data=file_content,
                    content_type=file.content_type
                )

                # Register path for auto-approve (via session coordinator)
                await self.coordinator.register_uploaded_file(session_id, file_info.stored_path)

                # Issue #404: Auto-register all uploaded files to resource gallery
                try:
                    await self.coordinator.register_uploaded_resource(
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

                return {
                    "success": True,
                    "file": file_info.to_dict()
                }

            except FileUploadError as e:
                logger.warning(f"File upload validation failed: {e.message}")
                raise HTTPException(status_code=400, detail=e.message)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to upload file: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/files")
        async def list_session_files(session_id: str):
            """List all uploaded files for a session"""
            try:
                # Verify session exists
                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                if not session_info:
                    raise HTTPException(status_code=404, detail="Session not found")

                file_manager = FileUploadManager(self.coordinator.data_dir / "sessions")
                files = await file_manager.list_files(session_id)

                return {
                    "files": [f.to_dict() for f in files]
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to list files: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/sessions/{session_id}/files/{file_id}")
        async def delete_file(session_id: str, file_id: str):
            """Delete an uploaded file"""
            try:
                # Verify session exists
                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                if not session_info:
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

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to delete file: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # Issue #404: Resource gallery endpoints
        @self.app.get("/api/sessions/{session_id}/resources")
        async def get_session_resources(session_id: str):
            """Get all resource metadata for a session"""
            try:
                resources = await self.coordinator.get_session_resources(session_id)
                return {"resources": resources, "count": len(resources)}
            except Exception as e:
                logger.error(f"Failed to get resources: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/resources/{resource_id}")
        async def get_session_resource(session_id: str, resource_id: str):
            """Get raw file data for a specific resource"""
            from fastapi.responses import Response

            try:
                # Get resource metadata to determine content type
                resources = await self.coordinator.get_session_resources(session_id)
                resource_meta = next((r for r in resources if r.get("resource_id") == resource_id), None)

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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/resources/{resource_id}/download")
        async def download_session_resource(session_id: str, resource_id: str):
            """Download a resource file"""
            from fastapi.responses import Response

            try:
                # Get resource metadata
                resources = await self.coordinator.get_session_resources(session_id)
                resource_meta = next((r for r in resources if r.get("resource_id") == resource_id), None)

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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to download resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # Issue #423: Remove resource from session display (soft-remove)
        @self.app.delete("/api/sessions/{session_id}/resources/{resource_id}")
        async def remove_session_resource(session_id: str, resource_id: str):
            """Soft-remove a resource from the session display (file is preserved)"""
            try:
                success = await self.coordinator.remove_session_resource(session_id, resource_id)
                if not success:
                    raise HTTPException(status_code=404, detail="Resource not found or removal failed")

                # Broadcast removal to WebSocket clients
                await self.websocket_manager.send_message(session_id, {
                    "type": "resource_removed",
                    "resource_id": resource_id,
                })

                return {"status": "ok", "resource_id": resource_id}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to remove resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # Issue #404: Legacy image endpoints (backward compatibility)
        @self.app.get("/api/sessions/{session_id}/images")
        async def get_session_images(session_id: str):
            """Get all image metadata for a session (deprecated, use /resources)"""
            try:
                images = await self.coordinator.get_session_images(session_id)
                return {"images": images, "count": len(images)}
            except Exception as e:
                logger.error(f"Failed to get images: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/images/{image_id}")
        async def get_session_image(session_id: str, image_id: str):
            """Get raw image data for a specific image (deprecated, use /resources)"""
            from fastapi.responses import Response

            try:
                # Get image metadata to determine content type
                images = await self.coordinator.get_session_images(session_id)
                image_meta = next((img for img in images if img.get("image_id") == image_id), None)

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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get image: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== PERMISSION MODE ENDPOINT ====================

        @self.app.post("/api/sessions/{session_id}/permission-mode")
        async def set_permission_mode(session_id: str, request: PermissionModeRequest):
            """Set the permission mode for a session"""
            try:
                # Validate mode
                valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
                if request.mode not in valid_modes:
                    raise HTTPException(status_code=400, detail=f"Invalid permission mode: {request.mode}")

                success = await self.coordinator.set_permission_mode(session_id, request.mode)
                if not success:
                    raise HTTPException(status_code=400, detail="Failed to set permission mode")
                return {"success": success, "mode": request.mode}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to set permission mode: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/restart")
        async def restart_session(session_id: str):
            """Restart a session (disconnect and resume)"""
            try:
                # Get permission callback for this session
                permission_callback = self._create_permission_callback(session_id)

                success = await self.coordinator.restart_session(
                    session_id,
                    permission_callback=permission_callback
                )
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to restart session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/reset")
        async def reset_session(session_id: str):
            """Reset a session (clear messages and start fresh)"""
            try:
                # Get permission callback for this session
                permission_callback = self._create_permission_callback(session_id)

                success = await self.coordinator.reset_session(
                    session_id,
                    permission_callback=permission_callback
                )
                return {"success": success}
            except Exception as e:
                logger.error(f"Failed to reset session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/sessions/{session_id}/disconnect")
        async def disconnect_session(session_id: str):
            """Disconnect SDK but keep session state (for end session)"""
            try:
                sdk = self.coordinator._active_sdks.get(session_id)
                if sdk:
                    success = await sdk.disconnect()
                    if success:
                        del self.coordinator._active_sdks[session_id]
                    return {"success": success}
                return {"success": True}  # Already disconnected
            except Exception as e:
                logger.error(f"Failed to disconnect session: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== DIFF ENDPOINTS (Issue #435) ====================

        @self.app.get("/api/sessions/{session_id}/diff")
        async def get_session_diff(session_id: str):
            """Get diff summary for a session's working directory vs origin/main."""
            try:
                session = await self.coordinator.session_manager.get_session_info(session_id)
                if not session:
                    raise HTTPException(status_code=404, detail="Session not found")

                cwd = session.working_directory
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
                if merge_base is None:
                    return {
                        "is_git_repo": True,
                        "branch": branch or "unknown",
                        "error": "No remote tracking branch found (origin/main or origin/master)"
                    }

                # Get commit log since merge base
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
                                ["git", "diff-tree", "--no-commit-id", "-r",
                                 "--name-only", lines[0]], cwd
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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get diff for session {session_id}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/sessions/{session_id}/diff/file")
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

            try:
                session = await self.coordinator.session_manager.get_session_info(session_id)
                if not session:
                    raise HTTPException(status_code=404, detail="Session not found")

                cwd = session.working_directory
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

                    # Try diff against parent commit
                    diff_output = await self._run_git_command(
                        ["git", "diff", f"{ref}~1", ref, "--", path], cwd
                    )
                    if diff_output is None:
                        # First commit or no parent — fall back to git show
                        diff_output = await self._run_git_command(
                            ["git", "show", ref, "--", path], cwd
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
                    # No remote: cannot compute cumulative branch diff
                    diff_output = ""

                return {
                    "path": path,
                    "merge_base": merge_base,
                    "diff": diff_output or ""
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get file diff for {path}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== LEGION ENDPOINTS ====================
        # All projects support Legion capabilities (issue #313)

        @self.app.get("/api/legions/{legion_id}/timeline")
        async def get_legion_timeline(legion_id: str, limit: int = 100, offset: int = 0):
            """Get Comms for legion timeline (all communications in the legion)"""
            try:
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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get timeline: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/legions/{legion_id}/hierarchy")
        async def get_legion_hierarchy(legion_id: str):
            """Get complete minion hierarchy with user at root (issue #313: universal Legion)"""
            try:
                # Issue #313: All projects support hierarchy - verify project exists
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Get legion coordinator
                legion_coord = self.coordinator.legion_system.legion_coordinator
                if not legion_coord:
                    raise HTTPException(status_code=500, detail="Legion coordinator not available")

                # Assemble hierarchy (returns empty children if no minions)
                hierarchy = await legion_coord.assemble_minion_hierarchy(legion_id)

                return hierarchy

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get minion hierarchy: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/legions/{legion_id}/comms")
        async def send_comm_to_legion(legion_id: str, request: CommSendRequest):
            """Send a Comm in the legion"""
            try:
                import uuid

                from src.models.legion_models import Comm, CommType

                legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
                if not legion:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Look up minion name if targeting a minion (for historical display)
                to_minion_name = None
                if request.to_minion_id:
                    minion_session = await self.coordinator.session_manager.get_session_info(request.to_minion_id)
                    if minion_session:
                        to_minion_name = minion_session.name

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

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to send comm: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/legions/{legion_id}/minions")
        async def create_minion(legion_id: str, request: MinionCreateRequest):
            """Create a new minion in the project (issue #313: universal Legion)"""
            try:
                # Verify project exists (all projects support minions - issue #313)
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Validate and normalize working directory
                try:
                    working_dir = validate_and_normalize_working_directory(
                        request.working_directory,
                        str(project.working_directory)
                    )
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))

                # Create minion via OverseerController
                # Map initialization_context to system_prompt (initialization_context is just UI semantics)
                minion_id = await self.coordinator.legion_system.overseer_controller.create_minion_for_user(
                    legion_id=legion_id,
                    name=request.name,
                    role=request.role,
                    system_prompt=request.initialization_context,
                    override_system_prompt=request.override_system_prompt,
                    capabilities=request.capabilities,
                    permission_mode=request.permission_mode,
                    allowed_tools=request.allowed_tools,
                    disallowed_tools=request.disallowed_tools,
                    working_directory=str(working_dir),
                    model=request.model,
                    sandbox_enabled=request.sandbox_enabled,
                    sandbox_config=request.sandbox_config,
                    setting_sources=request.setting_sources  # Issue #36
                )

                # Get the created minion info
                minion_info = await self.coordinator.session_manager.get_session_info(minion_id)

                return {
                    "success": True,
                    "minion_id": minion_id,
                    "minion": minion_info.to_dict() if minion_info else None
                }

            except ValueError as e:
                # OverseerController raises ValueError for validation errors
                logger.error(f"Validation error creating minion: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to create minion: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== FLEET CONTROL ENDPOINTS ====================

        @self.app.post("/api/legions/{legion_id}/halt-all")
        async def emergency_halt_all(legion_id: str):
            """Emergency halt all minions in the project (issue #313: universal Legion)"""
            try:
                # Issue #313: All projects support halt-all - verify project exists
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Call LegionCoordinator.emergency_halt_all() (no-op if no minions)
                result = await self.coordinator.legion_system.legion_coordinator.emergency_halt_all(legion_id)

                return {
                    "success": True,
                    "halted_count": result["halted_count"],
                    "failed_minions": result["failed_minions"],
                    "total_minions": result["total_minions"]
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to halt all minions: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/legions/{legion_id}/resume-all")
        async def resume_all(legion_id: str):
            """Resume all minions in the project (issue #313: universal Legion)"""
            try:
                # Issue #313: All projects support resume-all - verify project exists
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")

                # Call LegionCoordinator.resume_all() (no-op if no minions)
                result = await self.coordinator.legion_system.legion_coordinator.resume_all(legion_id)

                return {
                    "success": True,
                    "resumed_count": result["resumed_count"],
                    "failed_minions": result["failed_minions"],
                    "total_minions": result["total_minions"]
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to resume all minions: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== PERMISSION PREVIEW ENDPOINT (Issue #36) ====================

        @self.app.post("/api/permissions/preview")
        async def preview_permissions(request: PermissionPreviewRequest):
            """
            Preview effective permissions from settings files.

            Returns a list of permissions with their source annotations.
            """
            try:
                permissions = resolve_effective_permissions(
                    working_directory=request.working_directory,
                    setting_sources=request.setting_sources,
                    session_allowed_tools=request.session_allowed_tools
                )
                return {"permissions": permissions}
            except Exception as e:
                logger.error(f"Failed to preview permissions: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ==================== SYSTEM ENDPOINTS (Issue #434) ====================

        @self.app.get("/api/system/git-status")
        async def get_git_status():
            """Return current git branch, last commit, and dirty state."""
            try:
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

                return {
                    "branch": branch or "unknown",
                    "last_commit_hash": commit_hash or "",
                    "last_commit_message": commit_message or "",
                    "has_uncommitted_changes": bool(status),
                }
            except Exception as e:
                logger.error(f"Failed to get git status: {e}")
                raise HTTPException(status_code=500, detail=str(e)) from e

        @self.app.post("/api/system/restart", status_code=202)
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
                logger.error(f"git pull failed: {e}")
                raise HTTPException(status_code=500, detail=str(e)) from e

            # Broadcast restart notice to all WebSocket connections
            try:
                await self.ui_websocket_manager.broadcast({
                    "type": "server_restarting",
                    "message": "Server is restarting...",
                    "pull_output": pull_output,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to broadcast restart notice: {e}")

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
            }

        # ==================== FILESYSTEM ENDPOINTS ====================

        @self.app.get("/api/filesystem/browse")
        async def browse_filesystem(path: str = None):
            """Browse filesystem directories"""
            try:
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
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to browse filesystem: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # ========== Template Endpoints ==========

        @self.app.get("/api/templates")
        async def list_templates():
            """List all minion templates"""
            try:
                templates = await self.coordinator.template_manager.list_templates()
                return [t.to_dict() for t in templates]
            except Exception as e:
                logger.error(f"Failed to list templates: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/templates/{template_id}")
        async def get_template(template_id: str):
            """Get specific template"""
            try:
                template = await self.coordinator.template_manager.get_template(template_id)
                if not template:
                    raise HTTPException(status_code=404, detail="Template not found")
                return template.to_dict()
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get template: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/templates")
        async def create_template(request: TemplateCreateRequest):
            """Create new template"""
            try:
                template = await self.coordinator.template_manager.create_template(
                    name=request.name,
                    permission_mode=request.permission_mode,
                    allowed_tools=request.allowed_tools,
                    disallowed_tools=request.disallowed_tools,
                    default_role=request.default_role,
                    default_system_prompt=request.default_system_prompt,
                    description=request.description,
                    model=request.model,
                    capabilities=request.capabilities,
                    override_system_prompt=request.override_system_prompt,
                    sandbox_enabled=request.sandbox_enabled,
                    sandbox_config=request.sandbox_config,
                )
                return template.to_dict()
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                logger.error(f"Failed to create template: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/templates/{template_id}")
        async def update_template(template_id: str, request: TemplateUpdateRequest):
            """Update existing template"""
            try:
                template = await self.coordinator.template_manager.update_template(
                    template_id=template_id,
                    name=request.name,
                    permission_mode=request.permission_mode,
                    allowed_tools=request.allowed_tools,
                    disallowed_tools=request.disallowed_tools,
                    default_role=request.default_role,
                    default_system_prompt=request.default_system_prompt,
                    description=request.description,
                    model=request.model,
                    capabilities=request.capabilities,
                    override_system_prompt=request.override_system_prompt,
                    sandbox_enabled=request.sandbox_enabled,
                    sandbox_config=request.sandbox_config,
                )
                return template.to_dict()
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                logger.error(f"Failed to update template: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/templates/{template_id}")
        async def delete_template(template_id: str):
            """Delete template"""
            try:
                success = await self.coordinator.template_manager.delete_template(template_id)
                if not success:
                    raise HTTPException(status_code=404, detail="Template not found")
                return {"deleted": True}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to delete template: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.websocket("/ws/ui")
        async def ui_websocket_endpoint(websocket: WebSocket):
            """Global UI WebSocket endpoint for session state updates"""
            logger.info("UI WebSocket connection request received")
            await self.ui_websocket_manager.connect(websocket)

            try:
                # Send initial session list on connection
                sessions_data = await self.coordinator.list_sessions()
                initial_message = {
                    "type": "sessions_list",
                    "data": {
                        "sessions": sessions_data  # Already converted to dicts by coordinator.list_sessions()
                    }
                }
                await websocket.send_json(initial_message)
                logger.info("Sent initial sessions list to UI WebSocket")

                # Keep connection alive and handle incoming messages
                while True:
                    try:
                        # Wait for any incoming messages (ping, etc.)
                        message = await asyncio.wait_for(websocket.receive_text(), timeout=3.0)

                        # Handle ping/pong for keepalive
                        try:
                            message_data = json.loads(message)
                            if message_data.get("type") == "ping":
                                ws_verbose_logger.debug("UI WebSocket received ping, sending pong")
                                await websocket.send_json({"type": "pong", "timestamp": datetime.now(UTC).isoformat()})
                            else:
                                # Log non-ping/pong messages
                                logger.debug(f"UI WebSocket received: {message}")
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in UI WebSocket message: {message}")

                    except TimeoutError:
                        # Send periodic ping to keep connection alive
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": datetime.now(UTC).isoformat()
                        })

            except WebSocketDisconnect:
                logger.info("UI WebSocket disconnected")
            except Exception as e:
                logger.error(f"Error in UI WebSocket: {e}")
            finally:
                self.ui_websocket_manager.disconnect(websocket)

        @self.app.websocket("/ws/legion/{legion_id}")
        async def legion_websocket_endpoint(websocket: WebSocket, legion_id: str):
            """WebSocket endpoint for project real-time updates (issue #313: universal Legion)"""
            logger.info(f"Legion WebSocket connection request for project {legion_id}")

            # Issue #313: All projects support Legion WebSocket - verify project exists
            try:
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    logger.warning(f"Legion WebSocket connection rejected - project not found: {legion_id}")
                    await websocket.close(code=4404)
                    return
            except Exception as e:
                logger.error(f"Error validating project {legion_id}: {e}")
                await websocket.close(code=4500)
                return

            await self.legion_websocket_manager.connect(websocket, legion_id)

            # Send initial connection confirmation
            try:
                await websocket.send_json({
                    "type": "connection_established",
                    "legion_id": legion_id,
                    "timestamp": datetime.now(UTC).isoformat()
                })
            except Exception as e:
                logger.error(f"Failed to send initial message to legion WebSocket {legion_id}: {e}")
                self.legion_websocket_manager.disconnect(websocket, legion_id)
                return

            try:
                while True:
                    try:
                        # Wait for ping messages from client (keepalive)
                        message = await asyncio.wait_for(websocket.receive_text(), timeout=3.0)
                        try:
                            message_data = json.loads(message)
                            if message_data.get("type") == "ping":
                                await websocket.send_json({"type": "pong", "timestamp": datetime.now(UTC).isoformat()})
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in legion WebSocket message: {message}")
                    except TimeoutError:
                        # Send periodic ping to keep connection alive
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": datetime.now(UTC).isoformat()
                        })
            except WebSocketDisconnect:
                logger.info(f"Legion WebSocket disconnected for legion {legion_id}")
            except Exception as e:
                logger.error(f"Error in legion WebSocket for {legion_id}: {e}")
            finally:
                self.legion_websocket_manager.disconnect(websocket, legion_id)

        @self.app.websocket("/ws/session/{session_id}")
        async def websocket_endpoint(websocket: WebSocket, session_id: str):
            """WebSocket endpoint for session-specific messaging"""
            ws_connection_start_time = time.time()
            ws_logger.debug(f"WebSocket connection attempt for session {session_id} at {ws_connection_start_time}")

            # Validate session exists and is active before accepting connection
            try:
                session_validation_time = time.time()
                ws_logger.debug(f"Validating session {session_id} at {session_validation_time}")

                session_info = await self.coordinator.get_session_info(session_id)
                if not session_info:
                    rejection_time = time.time()
                    ws_logger.debug(f"WebSocket connection REJECTED for non-existent session: {session_id} at {rejection_time}")
                    await websocket.close(code=4404)
                    return

                session_state = session_info.get('session', {}).get('state')
                ws_logger.debug(f"Session {session_id} state: {session_state}")

                # Allow connections to active, error, and paused (waiting for permission) states
                if session_state not in ['active', 'error', 'paused']:
                    rejection_time = time.time()
                    ws_logger.debug(f"WebSocket connection REJECTED for session: {session_id} (state: {session_state}) at {rejection_time}")
                    ws_logger.debug("WebSocket will only connect to sessions in 'active', 'error', or 'paused' state")
                    await websocket.close(code=4003)
                    return

                validation_success_time = time.time()
                ws_logger.debug(f"Session validation successful for {session_id} at {validation_success_time}")

            except Exception as e:
                validation_error_time = time.time()
                logger.error(f"Error validating session {session_id} for WebSocket at {validation_error_time}: {e}")
                await websocket.close(code=4500)
                return

            # Accept WebSocket connection
            connection_accept_time = time.time()
            ws_logger.debug(f"Accepting WebSocket connection for session {session_id} at {connection_accept_time}")

            await self.websocket_manager.connect(websocket, session_id)

            connection_established_time = time.time()
            ws_logger.info(f"WebSocket connection ESTABLISHED for session {session_id} at {connection_established_time}")
            ws_logger.debug(f"Connection establishment took {connection_established_time - ws_connection_start_time:.3f}s")
            ws_logger.debug(f"WebSocket loop starting for session {session_id}")

            # Send initial ping to establish connection
            try:
                initial_message_time = time.time()
                ws_logger.debug(f"Sending initial connection_established message at {initial_message_time}")

                await websocket.send_text(json.dumps({
                    "type": "connection_established",
                    "session_id": session_id,
                    "timestamp": datetime.now(UTC).isoformat()
                }))

                initial_message_sent_time = time.time()
                ws_logger.debug(f"Initial message sent successfully at {initial_message_sent_time}")

            except Exception as e:
                initial_message_error_time = time.time()
                logger.error(f"Failed to send initial message to WebSocket {session_id} at {initial_message_error_time}: {e}")
                # Clean up the connection that was already registered
                self.websocket_manager.disconnect(websocket, session_id)
                ws_logger.debug(f"WebSocket disconnected due to initial message failure for session {session_id}")
                return

            message_loop_iteration = 0
            try:
                ws_verbose_logger.debug(f"Starting message loop for session {session_id}")

                while True:
                    message_loop_iteration += 1
                    loop_iteration_start_time = time.time()
                    ws_verbose_logger.debug(f"Message loop iteration {message_loop_iteration} started at {loop_iteration_start_time}")

                    # Wait for incoming messages with proper error handling
                    try:
                        message_wait_start_time = time.time()
                        ws_verbose_logger.debug(f"WebSocket waiting for message from session {session_id} (timeout=3s)")

                        message = await asyncio.wait_for(websocket.receive_text(), timeout=3.0)

                        message_received_time = time.time()
                        message_data = json.loads(message)

                        # Filter ping/pong messages from general debug logging (use ws_verbose_logger for those)
                        if message_data.get("type") in ["ping", "pong"]:
                            ws_verbose_logger.debug(f"WebSocket received {message_data.get('type')} from session {session_id}")
                        else:
                            ws_logger.debug(f"WebSocket received message from session {session_id} at {message_received_time}: {message[:100]}...")
                            ws_logger.debug(f"Message wait took {message_received_time - message_wait_start_time:.3f}s")
                            ws_logger.debug(f"DEBUG: Received message type: '{message_data.get('type', 'unknown')}' from session {session_id}")
                            ws_logger.debug(f"DEBUG: Full message data: {message_data}")

                        if message_data.get("type") == "send_message":
                            # Handle message sending through WebSocket
                            content = message_data.get("content", "")
                            if content:
                                message_processing_start_time = time.time()
                                ws_logger.debug(f"Forwarding message to coordinator for session {session_id} at {message_processing_start_time}")
                                ws_logger.debug(f"Message content preview: {content[:100]}...")

                                await self.coordinator.send_message(session_id, content)

                                message_processing_end_time = time.time()
                                ws_logger.debug(f"Message forwarded to coordinator at {message_processing_end_time}")
                                ws_logger.debug(f"Message forwarding took {message_processing_end_time - message_processing_start_time:.3f}s")

                        elif message_data.get("type") == "interrupt_session":
                            # Handle session interrupt through WebSocket
                            interrupt_start_time = time.time()
                            ws_logger.debug(f"DEBUG: INTERRUPT REQUEST RECEIVED for session {session_id} at {interrupt_start_time}")
                            ws_logger.debug(f"DEBUG: Full interrupt message data: {message_data}")

                            try:
                                # Check if session is in PAUSED state (waiting for permission)
                                # If so, deny all pending permissions for this session
                                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                                if session_info and session_info.state == SessionState.PAUSED:
                                    # Find and resolve any pending permission requests for this session
                                    permissions_to_resolve = []
                                    for request_id, future in list(self.pending_permissions.items()):
                                        # We need to check if this request belongs to this session
                                        # Since we don't store session_id with the future, we'll resolve all pending
                                        # This is safe because interrupting a session should clear its permissions
                                        if not future.done():
                                            permissions_to_resolve.append(request_id)

                                    for request_id in permissions_to_resolve:
                                        future = self.pending_permissions.get(request_id)
                                        if future and not future.done():
                                            future.set_result({
                                                "behavior": "deny",
                                                "message": "Permission denied due to session interrupt",
                                                "interrupt": True
                                            })
                                            del self.pending_permissions[request_id]
                                            logger.info(f"Resolved pending permission {request_id} with deny due to interrupt")

                                # Forward interrupt request to coordinator
                                result = await self.coordinator.interrupt_session(session_id)
                                interrupt_end_time = time.time()

                                if result:
                                    ws_logger.debug(f"Interrupt successfully initiated for session {session_id} at {interrupt_end_time}")
                                    ws_logger.debug(f"Interrupt processing took {interrupt_end_time - interrupt_start_time:.3f}s")

                                    # Send success response back to client
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": True,
                                        "message": "Interrupt initiated successfully",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(UTC).isoformat()
                                    }))
                                else:
                                    ws_logger.debug(f"Interrupt failed for session {session_id} at {interrupt_end_time}")

                                    # Send failure response back to client
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": False,
                                        "message": "Failed to initiate interrupt",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(UTC).isoformat()
                                    }))

                            except Exception as interrupt_error:
                                logger.error(f"Interrupt error for session {session_id}: {interrupt_error}")

                                # Send error response back to client
                                try:
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": False,
                                        "message": f"Interrupt error: {str(interrupt_error)}",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(UTC).isoformat()
                                    }))
                                except Exception as response_error:
                                    logger.error(f"Failed to send interrupt error response: {response_error}")

                        elif message_data.get("type") == "permission_response":
                            # Handle permission response from user
                            request_id = message_data.get("request_id")
                            decision = message_data.get("decision")

                            ws_logger.debug(f"Permission response received: request_id={request_id}, decision={decision}")

                            if not request_id or decision not in ["allow", "deny"]:
                                ws_logger.debug(f"Invalid permission response: {message_data}")
                                continue

                            # Find and resolve the pending permission Future
                            if request_id in self.pending_permissions:
                                pending_future = self.pending_permissions.pop(request_id)

                                if not pending_future.done():
                                    # Resolve the Future with the user's decision
                                    if decision == "allow":
                                        response = {"behavior": "allow"}
                                        # Only include updated_input if it was actually provided
                                        # This is used by AskUserQuestion to pass user answers back to SDK
                                        if "updated_input" in message_data:
                                            response["updated_input"] = message_data["updated_input"]
                                            ws_logger.info(f"Permission response includes updated_input for AskUserQuestion: {message_data['updated_input']}")
                                        # Include apply_suggestions flag if provided
                                        if "apply_suggestions" in message_data:
                                            response["apply_suggestions"] = message_data["apply_suggestions"]
                                            ws_logger.debug(f"Permission response includes apply_suggestions: {message_data['apply_suggestions']}")
                                        # Include selected_suggestions for granular permission selection
                                        if "selected_suggestions" in message_data:
                                            response["selected_suggestions"] = message_data["selected_suggestions"]
                                            ws_logger.debug(f"Permission response includes selected_suggestions: {len(message_data['selected_suggestions'])} items")
                                    else:
                                        # Check if this is a deny with clarification
                                        clarification_message = message_data.get("clarification_message")
                                        if clarification_message:
                                            # Deny with clarification - let SDK continue with user guidance
                                            response = {
                                                "behavior": "deny",
                                                "message": clarification_message,
                                                "interrupt": False  # CRITICAL: Allow SDK to continue
                                            }
                                            ws_logger.info(f"Permission denied with clarification: {clarification_message[:100]}...")
                                        else:
                                            # Standard deny - use default behavior
                                            response = {
                                                "behavior": "deny",
                                                "message": message_data.get("reason", "User denied permission")
                                            }

                                    pending_future.set_result(response)
                                    ws_logger.debug(f"Permission request {request_id} resolved with decision: {decision}")
                                else:
                                    ws_logger.debug(f"Permission request {request_id} Future was already resolved")
                            else:
                                ws_logger.debug(f"No pending permission found for request_id: {request_id}")

                    except TimeoutError:
                        # Send ping to keep connection alive
                        timeout_time = time.time()
                        ws_verbose_logger.debug(f"WebSocket timeout for session {session_id} at {timeout_time} - sending ping")

                        try:
                            ping_start_time = time.time()
                            await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now(UTC).isoformat()}))

                            ping_sent_time = time.time()
                            ws_verbose_logger.debug(f"Ping sent successfully at {ping_sent_time}")

                        except Exception as ping_error:
                            # Connection is dead, break the loop
                            connection_death_time = time.time()
                            ws_logger.debug(f"WebSocket connection DEAD for session {session_id} at {connection_death_time}: {ping_error}")
                            ws_logger.debug("Breaking message loop due to dead connection")
                            break

                    except json.JSONDecodeError as e:
                        json_error_time = time.time()
                        ws_logger.debug(f"Invalid JSON received on WebSocket for session {session_id} at {json_error_time}: {e}")
                        continue

                    loop_iteration_end_time = time.time()
                    ws_verbose_logger.debug(f"Message loop iteration {message_loop_iteration} completed at {loop_iteration_end_time}")

            except WebSocketDisconnect as disconnect_error:
                disconnect_time = time.time()
                ws_logger.info(f"WebSocket DISCONNECTED gracefully for session {session_id} at {disconnect_time}")
                ws_logger.debug(f"Disconnect details: {disconnect_error}")
                ws_logger.debug(f"Total message loop iterations: {message_loop_iteration}")
                self.websocket_manager.disconnect(websocket, session_id)

            except Exception as ws_error:
                error_time = time.time()
                logger.error(f"WebSocket ERROR for session {session_id} at {error_time}: {ws_error}")
                logger.error(f"Error type: {type(ws_error)}")
                logger.error(f"Total message loop iterations before error: {message_loop_iteration}")
                self.websocket_manager.disconnect(websocket, session_id)

            finally:
                cleanup_time = time.time()
                total_connection_time = cleanup_time - ws_connection_start_time
                ws_logger.debug(f"WebSocket cleanup for session {session_id} at {cleanup_time}")
                ws_logger.debug(f"Total WebSocket connection duration: {total_connection_time:.3f}s")
                ws_logger.debug(f"Total message loop iterations: {message_loop_iteration}")
                ws_logger.info(f"WebSocket loop ENDED for session {session_id}")

    def _create_message_callback(self, session_id: str):
        """Create message callback for WebSocket broadcasting using unified MessageProcessor"""
        async def callback(session_id: str, message_data: Any):
            logger.info(f"WebSocket callback triggered for session {session_id}, message type: {getattr(message_data, 'type', 'unknown')}")
            try:
                # Process message and prepare for WebSocket using MessageProcessor
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

                # Wrap in standard WebSocket envelope
                serialized = {
                    "type": "message",
                    "session_id": session_id,
                    "data": websocket_data,
                    "timestamp": datetime.now(UTC).isoformat()
                }

                logger.info(f"Attempting to send WebSocket message for session {session_id}: {serialized['type']}")
                await self.websocket_manager.send_message(session_id, serialized)
                logger.info(f"WebSocket message sent successfully for session {session_id}")

            except Exception as e:
                logger.error(f"Error in message callback: {e}")

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
                for tool_use in tool_uses:
                    tool_id = tool_use.get('id')
                    tool_name = tool_use.get('name')
                    input_params = tool_use.get('input', {})

                    if tool_id and tool_name:
                        # Create new ToolCall with PENDING status
                        tool_call = self.coordinator.create_tool_call(
                            session_id=session_id,
                            tool_use_id=tool_id,
                            name=tool_name,
                            input_params=input_params,
                            requires_permission=False,  # Will be updated if permission is requested
                        )

                        # Emit tool_call message
                        tool_call_data = tool_call.to_dict()
                        tool_call_data["type"] = "tool_call"

                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": tool_call_data,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                        await self.websocket_manager.send_message(session_id, websocket_message)
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
                            await self.websocket_manager.send_message(session_id, websocket_message)
                            logger.debug(
                                f"Emitted tool_call {'failed' if is_error else 'completed'} "
                                f"for {tool_use_id} in session {session_id}"
                            )

        except Exception as e:
            logger.error(f"Error emitting tool_call updates: {e}")

    async def _on_state_change(self, state_data: dict):
        """Handle session state changes"""
        try:
            session_id = state_data.get("session_id")
            if session_id:
                # Get full session info for real-time updates
                session_info = await self.coordinator.session_manager.get_session_info(session_id)
                if session_info:
                    message = {
                        "type": "state_change",
                        "data": {
                            "session_id": session_id,
                            "session": session_info.to_dict(),
                            "timestamp": state_data.get("timestamp")
                        }
                    }
                    # Broadcast to all UI WebSocket connections instead of session-specific
                    await self.ui_websocket_manager.broadcast_to_all(message)
                    logger.info(f"Broadcasted state change for session {session_id} to all UI clients")
        except Exception as e:
            logger.error(f"Error handling state change: {e}")

    def _create_permission_callback(self, session_id: str):
        """Create permission callback for tool usage"""
        async def permission_callback(tool_name: str, input_params: dict, context) -> dict:

            # Generate unique request ID to correlate request/response
            request_id = str(uuid.uuid4())
            request_time = time.time()

            logger.info(f"PERMISSION CALLBACK TRIGGERED: tool={tool_name}, session={session_id}, request_id={request_id}")
            logger.info(f"Permission requested for tool: {tool_name} (request_id: {request_id})")

            # Issue #403: Auto-approve Read tool for user-uploaded files
            if tool_name == 'Read':
                file_path = input_params.get('file_path', '')
                if file_path and self.coordinator.is_uploaded_file(session_id, file_path):
                    logger.info(f"Auto-approving Read for uploaded file: {file_path}")
                    return {"behavior": "allow"}

            # Extract suggestions from context
            suggestions = []
            if context and hasattr(context, 'suggestions'):
                for s in context.suggestions:
                    if hasattr(s, 'to_dict'):
                        suggestions.append(s.to_dict())
                    else:
                        suggestions.append(s)
                logger.info(f"Permission context has {len(suggestions)} suggestions")

            # INJECT: Add setMode suggestion for ExitPlanMode when in plan mode
            if tool_name == 'ExitPlanMode':
                try:
                    session_info = await self.coordinator.session_manager.get_session_info(session_id)
                    if session_info and session_info.current_permission_mode == 'plan':
                        # Create setMode suggestion to allow transition to acceptEdits
                        setmode_suggestion = {
                            'type': 'setMode',
                            'mode': 'acceptEdits',
                            'destination': 'session'
                        }

                        # Prepend so it appears first in UI
                        suggestions.insert(0, setmode_suggestion)
                        logger.info(f"Injected setMode suggestion for ExitPlanMode in session {session_id}")
                except Exception as inject_error:
                    logger.error(f"Failed to inject setMode suggestion for ExitPlanMode: {inject_error}")

            # Store permission request message using dataclass (Phase 0, Issue #310)
            try:
                # Convert suggestions to dataclass format
                suggestion_objects = [
                    PermissionSuggestion.from_dict(s) if isinstance(s, dict) else s
                    for s in suggestions
                ]

                # Create permission request dataclass
                permission_request = PermissionRequestMessage(
                    request_id=request_id,
                    tool_name=tool_name,
                    input_params=input_params,
                    suggestions=suggestion_objects,
                    timestamp=request_time,
                    session_id=session_id,
                )

                # Wrap in StoredMessage for unified storage
                stored_msg = StoredMessage.from_permission_request(permission_request)
                storage_data = stored_msg.to_dict()

                storage_manager = await self.coordinator.get_session_storage(session_id)
                if storage_manager:
                    await storage_manager.append_message(storage_data)
                    logger.debug(f"Stored permission request message for session {session_id}")

                # Issue #324: Update ToolCall to awaiting_permission and emit unified tool_call message
                try:
                    # Find the matching tool by signature
                    tool_call = self.coordinator.find_tool_call_by_signature(
                        session_id, tool_name, input_params
                    )

                    if tool_call:
                        # Create PermissionInfo from suggestions
                        permission_info = PermissionInfo(
                            message=f"Allow {tool_name}?",
                            suggestions=[s.to_dict() for s in suggestion_objects],
                            risk_level="medium",
                        )

                        # Update tool call status
                        updated_tool_call = self.coordinator.update_tool_call_permission_request(
                            session_id,
                            tool_call.tool_use_id,
                            permission_info,
                        )

                        if updated_tool_call:
                            # Emit unified tool_call message
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"
                            tool_call_data["request_id"] = request_id  # For permission response correlation

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            await self.websocket_manager.send_message(session_id, websocket_message)
                            logger.info(f"Broadcasted tool_call awaiting_permission for {tool_name} in session {session_id}")
                    else:
                        logger.warning(f"Could not find matching ToolCall for permission request: {tool_name}")

                    # Also broadcast legacy permission_request for backward compatibility
                    websocket_data = {
                        "type": "permission_request",
                        "content": permission_request.content,
                        "session_id": session_id,
                        "timestamp": request_time,
                        "tool_name": tool_name,
                        "input_params": input_params,
                        "request_id": request_id,
                        "suggestions": [s.to_dict() for s in suggestion_objects]
                    }
                    websocket_message = {
                        "type": "message",
                        "session_id": session_id,
                        "data": websocket_data,
                        "timestamp": datetime.now(UTC).isoformat()
                    }
                    await self.websocket_manager.send_message(session_id, websocket_message)
                    logger.info(f"Broadcasted permission request to WebSocket for session {session_id}")
                except Exception as ws_error:
                    logger.error(f"Failed to broadcast permission request to WebSocket: {ws_error}")

            except Exception as e:
                logger.error(f"Failed to store permission request message: {e}")

            # Set session state to PAUSED while waiting for permission response
            # This provides visual feedback that the session is blocked on user input
            try:
                await self.coordinator.session_manager.pause_session(session_id)
                logger.info(f"Set session {session_id} to PAUSED state while waiting for permission")
            except Exception as pause_error:
                logger.error(f"Failed to pause session {session_id} for permission wait: {pause_error}")

            # Wait for user permission decision via WebSocket
            logger.info(f"PERMISSION CALLBACK: Creating Future for request_id {request_id}")

            # Create a Future to wait for user response
            permission_future = asyncio.Future()
            self.pending_permissions[request_id] = permission_future

            try:
                # Wait for user decision (no timeout - wait indefinitely)
                logger.info(f"PERMISSION CALLBACK: Waiting for user decision on request_id {request_id}")
                response = await permission_future
                logger.info(f"PERMISSION CALLBACK: Received user decision for request_id {request_id}: {response}")

                # Restore session to ACTIVE state after permission decision
                # The session will continue processing, which will show as ACTIVE+processing (purple)
                try:
                    session_info = await self.coordinator.session_manager.get_session_info(session_id)
                    if session_info and session_info.state == SessionState.PAUSED:
                        # Use update_session_state instead of start_session to avoid re-initializing SDK
                        await self.coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)
                        logger.info(f"Restored session {session_id} to ACTIVE state after permission decision")
                except Exception as restore_error:
                    logger.error(f"Failed to restore session {session_id} to ACTIVE after permission: {restore_error}")

                decision = response.get("behavior")
                reasoning = f"User {decision}ed permission"

                # Handle permission updates if user chose to apply suggestions
                if decision == "allow" and response.get("apply_suggestions") and suggestions:
                    updated_permissions = []
                    applied_updates_for_storage = []

                    # Use selected_suggestions if provided (granular selection),
                    # otherwise fall back to full suggestions list (backward compatibility)
                    suggestions_to_apply = response.get("selected_suggestions", suggestions)

                    for suggestion in suggestions_to_apply:
                        # Force destination to 'session' as per requirements
                        suggestion_dict = dict(suggestion)
                        suggestion_dict['destination'] = 'session'

                        # Convert rules from dicts to PermissionRuleValue objects if present
                        rules_param = None
                        if suggestion_dict.get('rules'):
                            rules_param = []
                            for rule_dict in suggestion_dict['rules']:
                                # SDK uses snake_case (tool_name, rule_content) but suggestions come in camelCase
                                rule_obj = PermissionRuleValue(
                                    tool_name=rule_dict.get('toolName', ''),
                                    rule_content=rule_dict.get('ruleContent') or ""
                                )
                                rules_param.append(rule_obj)

                        update = PermissionUpdate(
                            type=suggestion_dict['type'],
                            mode=suggestion_dict.get('mode'),
                            rules=rules_param,
                            behavior=suggestion_dict.get('behavior'),
                            directories=suggestion_dict.get('directories'),
                            destination='session'
                        )
                        updated_permissions.append(update)
                        applied_updates_for_storage.append(suggestion_dict)

                        # Immediately update our session state to reflect the SDK's internal mode change
                        if suggestion_dict['type'] == 'setMode' and suggestion_dict.get('mode'):
                            new_mode = suggestion_dict['mode']
                            try:
                                await self.coordinator.session_manager.update_permission_mode(session_id, new_mode)
                                logger.info(f"Updated session {session_id} permission mode to {new_mode}")
                            except Exception as mode_error:
                                logger.error(f"Failed to update session mode: {mode_error}")

                    response['updated_permissions'] = updated_permissions
                    response['applied_updates_for_storage'] = applied_updates_for_storage
                    logger.info(f"Built {len(updated_permissions)} permission updates from suggestions")

                    # Issue #433: Persist approved tool names to session allowed_tools
                    # SDK suggestions split tool spec into toolName + ruleContent,
                    # e.g. toolName="Bash", ruleContent="gh issue view:*"
                    # Reconstruct full format: "Bash(gh issue view:*)"
                    tools_to_persist = set()
                    for suggestion_dict in applied_updates_for_storage:
                        if (
                            suggestion_dict.get('type') == 'addRules'
                            and suggestion_dict.get('behavior') == 'allow'
                        ):
                            for rule in suggestion_dict.get('rules') or []:
                                tool_name = rule.get('toolName', '')
                                rule_content = rule.get('ruleContent', '')
                                if tool_name:
                                    if rule_content:
                                        tools_to_persist.add(f"{tool_name}({rule_content})")
                                    else:
                                        tools_to_persist.add(tool_name)

                    if tools_to_persist:
                        try:
                            await self.coordinator.session_manager.update_allowed_tools(
                                session_id, list(tools_to_persist)
                            )
                            logger.info(
                                f"Persisted {len(tools_to_persist)} approved tools to session "
                                f"{session_id} allowed_tools: {tools_to_persist}"
                            )
                        except Exception as persist_error:
                            logger.error(f"Failed to persist approved tools: {persist_error}")

            except Exception as e:
                # Handle any errors (e.g., session termination)
                logger.error(f"PERMISSION CALLBACK: Error waiting for permission decision: {e}")
                decision = "deny"
                reasoning = f"Permission request failed: {str(e)}"
                response = {
                    "behavior": "deny",
                    "message": reasoning
                }

                # Clean up the pending permission
                if request_id in self.pending_permissions:
                    del self.pending_permissions[request_id]

            # Store permission response message using dataclass (Phase 0, Issue #310)
            decision_time = time.time()
            try:
                # Extract clarification message if it was provided
                clarification_msg = response.get("message") if decision == "deny" and not response.get("interrupt", True) else None
                interrupt_flag = response.get("interrupt", True)

                # Convert applied updates to PermissionSuggestion objects
                applied_update_objects = []
                if decision == "allow" and response.get("applied_updates_for_storage"):
                    applied_update_objects = [
                        PermissionSuggestion.from_dict(u) if isinstance(u, dict) else u
                        for u in response["applied_updates_for_storage"]
                    ]

                # Extract updated_input for AskUserQuestion (contains answers)
                updated_input_data = response.get("updated_input")

                # Create permission response dataclass
                permission_response_msg = PermissionResponseMessage(
                    request_id=request_id,
                    decision=decision,
                    tool_name=tool_name,
                    reasoning=reasoning,
                    response_time_ms=int((decision_time - request_time) * 1000),
                    applied_updates=applied_update_objects,
                    clarification_message=clarification_msg,
                    interrupt=interrupt_flag,
                    timestamp=decision_time,
                    session_id=session_id,
                    updated_input=updated_input_data,
                )

                # Wrap in StoredMessage for unified storage
                stored_msg = StoredMessage.from_permission_response(permission_response_msg)
                storage_data = stored_msg.to_dict()

                if storage_manager:
                    await storage_manager.append_message(storage_data)
                    logger.debug(f"Stored permission response message for session {session_id}")

                if clarification_msg:
                    logger.info(f"Stored permission denial with clarification for session {session_id}")
                if applied_update_objects:
                    logger.info(f"Permission response includes {len(applied_update_objects)} applied updates")

                # Issue #324: Update ToolCall after permission response and emit unified tool_call message
                try:
                    # Find the tool by signature and update status
                    tool_call = self.coordinator.find_tool_call_by_signature(
                        session_id, tool_name, input_params
                    )

                    if tool_call:
                        # Update tool call with permission decision
                        granted = decision == "allow"
                        updated_tool_call = self.coordinator.update_tool_call_permission_response(
                            session_id,
                            tool_call.tool_use_id,
                            granted,
                        )

                        if updated_tool_call:
                            # Emit unified tool_call message
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            await self.websocket_manager.send_message(session_id, websocket_message)
                            logger.info(
                                f"Broadcasted tool_call {'running' if granted else 'denied'} "
                                f"for {tool_name} in session {session_id}"
                            )
                except Exception as tc_error:
                    logger.error(f"Failed to update ToolCall for permission response: {tc_error}")

                # Broadcast legacy permission response for backward compatibility
                try:
                    websocket_data = {
                        "type": "permission_response",
                        "content": permission_response_msg.content,
                        "session_id": session_id,
                        "timestamp": decision_time,
                        "request_id": request_id,
                        "decision": decision,
                        "reasoning": reasoning,
                        "tool_name": tool_name,
                        "response_time_ms": int((decision_time - request_time) * 1000),
                    }
                    if clarification_msg:
                        websocket_data["clarification_message"] = clarification_msg
                        websocket_data["interrupt"] = False
                    if applied_update_objects:
                        websocket_data["applied_updates"] = [u.to_dict() for u in applied_update_objects]
                    if updated_input_data:
                        websocket_data["updated_input"] = updated_input_data

                    websocket_message = {
                        "type": "message",
                        "session_id": session_id,
                        "data": websocket_data,
                        "timestamp": datetime.now(UTC).isoformat()
                    }
                    await self.websocket_manager.send_message(session_id, websocket_message)
                    logger.info(f"Broadcasted permission response to WebSocket for session {session_id}")
                except Exception as ws_error:
                    logger.error(f"Failed to broadcast permission response to WebSocket: {ws_error}")

            except Exception as e:
                logger.error(f"Failed to store permission response message: {e}")

            logger.info(f"Permission {decision} for tool: {tool_name} (request_id: {request_id})")
            return response

        return permission_callback

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

def create_app(data_dir: Path = None, experimental: bool = False) -> FastAPI:
    """Create and configure the FastAPI application"""
    global webui_app
    webui_app = ClaudeWebUI(data_dir, experimental=experimental)
    return webui_app.app

async def startup_event():
    """Application startup event"""
    if webui_app:
        await webui_app.initialize()

async def shutdown_event():
    """Application shutdown event"""
    if webui_app:
        await webui_app.cleanup()
