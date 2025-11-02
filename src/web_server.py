"""
FastAPI web server for Claude Code WebUI with WebSocket support.
"""

import asyncio
import gc
import json
import logging
import os
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from claude_agent_sdk import PermissionUpdate
from claude_agent_sdk.types import PermissionRuleValue

from .logging_config import get_logger
from .message_parser import MessageParser, MessageProcessor
from .session_coordinator import SessionCoordinator
from .session_manager import SessionState
from .template_manager import TemplateManager
from .timestamp_utils import normalize_timestamp

# Get specialized logger for WebSocket lifecycle debugging
ws_logger = get_logger('websocket_debug', category='WS_LIFECYCLE')
# Get verbose logger for ping/pong (use only with --debug-websocket-verbose)
ws_verbose_logger = get_logger('websocket_verbose', category='WS_PING_PONG')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


class ProjectCreateRequest(BaseModel):
    name: str
    working_directory: str
    is_multi_agent: bool = False  # True to create as legion
    max_concurrent_minions: int = 20  # Only used if is_multi_agent=True


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    is_expanded: Optional[bool] = None


class ProjectReorderRequest(BaseModel):
    project_ids: List[str]


class SessionCreateRequest(BaseModel):
    project_id: str
    permission_mode: str = "acceptEdits"
    system_prompt: Optional[str] = None
    override_system_prompt: bool = False
    tools: List[str] = []
    model: Optional[str] = None
    name: Optional[str] = None


class MessageRequest(BaseModel):
    message: str


class SessionNameUpdateRequest(BaseModel):
    name: str


class SessionReorderRequest(BaseModel):
    session_ids: List[str]


class PermissionModeRequest(BaseModel):
    mode: str


class CommSendRequest(BaseModel):
    to_minion_id: Optional[str] = None
    to_channel_id: Optional[str] = None
    to_user: bool = False
    content: str
    comm_type: str = "task"


class MinionCreateRequest(BaseModel):
    name: str
    role: Optional[str] = ""
    initialization_context: Optional[str] = ""
    override_system_prompt: bool = False
    capabilities: List[str] = []
    permission_mode: str = "default"
    allowed_tools: List[str] = []  # Empty list means no pre-authorized tools (prompts for everything)


class ChannelCreateRequest(BaseModel):
    name: str
    description: str = ""
    purpose: str = ""
    member_minion_ids: List[str] = []


class ChannelMemberRequest(BaseModel):
    action: str  # "add" or "remove"
    self_id: str  # Actor performing the action
    member_id: str  # Target minion to add/remove


class ChannelBroadcastRequest(BaseModel):
    from_user: bool = False
    from_minion_id: Optional[str] = None
    from_minion_name: Optional[str] = None  # Capture name for history
    content: str
    summary: str = ""
    comm_type: str = "info"


class TemplateCreateRequest(BaseModel):
    name: str
    permission_mode: str
    allowed_tools: Optional[List[str]] = None
    default_role: Optional[str] = None
    default_system_prompt: Optional[str] = None
    description: Optional[str] = None


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    permission_mode: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    default_role: Optional[str] = None
    default_system_prompt: Optional[str] = None
    description: Optional[str] = None


class UIWebSocketManager:
    """Manages global UI WebSocket connections for session state updates"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

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
        self.active_connections: Dict[str, List[WebSocket]] = {}

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
        self.active_connections: Dict[str, List[WebSocket]] = {}

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

    def __init__(self, data_dir: Path = None):
        self.app = FastAPI(title="Claude Code WebUI", version="1.0.0")
        self.coordinator = SessionCoordinator(data_dir)
        self.websocket_manager = WebSocketManager()
        self.ui_websocket_manager = UIWebSocketManager()
        self.legion_websocket_manager = LegionWebSocketManager()

        # Inject UI WebSocket manager into Legion system for project update broadcasts
        self.coordinator.legion_system.ui_websocket_manager = self.ui_websocket_manager

        # Initialize MessageProcessor for unified WebSocket message formatting
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Track pending permission requests with asyncio.Future objects
        self.pending_permissions: Dict[str, asyncio.Future] = {}

        # Setup routes
        self._setup_routes()

        # Setup Legion WebSocket broadcast callback
        self.coordinator.legion_system.comm_router.set_comm_broadcast_callback(
            self._broadcast_comm_to_legion_websocket
        )

        # Setup static files (Vue 3 production build)
        static_dir = Path(__file__).parent.parent / "frontend" / "dist"
        if not static_dir.exists():
            raise RuntimeError(
                f"Frontend build not found at {static_dir}. "
                "Run 'cd frontend && npm run build' to create production build."
            )
        self.app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    async def _broadcast_comm_to_legion_websocket(self, legion_id: str, comm):
        """Broadcast new comm to WebSocket clients watching this legion"""
        try:
            # Convert comm to dict for JSON serialization
            comm_dict = comm.to_dict()

            # Broadcast to all clients watching this legion
            await self.legion_websocket_manager.broadcast_to_legion(legion_id, {
                "type": "comm",
                "comm": comm_dict,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            logger.debug(f"Broadcast comm {comm.comm_id} to legion {legion_id} WebSocket clients")
        except Exception as e:
            logger.error(f"Error broadcasting comm to legion WebSocket: {e}")

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
            return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

        # ==================== PROJECT ENDPOINTS ====================

        @self.app.post("/api/projects")
        async def create_project(request: ProjectCreateRequest):
            """Create a new project (or legion if is_multi_agent=True)"""
            try:
                project = await self.coordinator.project_manager.create_project(
                    name=request.name,
                    working_directory=request.working_directory,
                    is_multi_agent=request.is_multi_agent,
                    max_concurrent_minions=request.max_concurrent_minions
                )
                return {"project": project.to_dict()}
            except Exception as e:
                logger.error(f"Failed to create project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/projects")
        async def list_projects():
            """List all projects (including legions with is_multi_agent=true)"""
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
                    tools=request.tools,
                    model=request.model,
                    name=request.name,
                    permission_callback=self._create_permission_callback(session_id)
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

        @self.app.post("/api/sessions/{session_id}/start")
        async def start_session(session_id: str):
            """Start a session"""
            try:
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

        @self.app.delete("/api/sessions/{session_id}")
        async def delete_session(session_id: str):
            """Delete a session and all its data"""
            try:
                # Find the project before deletion (so we can check if it gets auto-deleted)
                project = await self.coordinator._find_project_for_session(session_id)
                project_id = project.project_id if project else None

                # First force disconnect any WebSocket connections for this session
                await self.websocket_manager.force_disconnect_session(session_id)

                # Clean up any pending permissions for this session
                self._cleanup_pending_permissions_for_session(session_id)

                # Delete the session (may also delete the project if it was the last session)
                success = await self.coordinator.delete_session(session_id)
                if not success:
                    raise HTTPException(status_code=404, detail="Session not found")

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

                return {"success": success}
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
        async def get_messages(session_id: str, limit: Optional[int] = 50, offset: int = 0):
            """Get messages from a session with pagination metadata"""
            try:
                result = await self.coordinator.get_session_messages(
                    session_id, limit=limit, offset=offset
                )
                return result
            except Exception as e:
                logger.error(f"Failed to get messages: {e}")
                raise HTTPException(status_code=500, detail=str(e))

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

        # ==================== LEGION ENDPOINTS ====================
        # NOTE: Legion creation now uses POST /api/projects with is_multi_agent=true
        # Timeline and comms endpoints remain for future multi-agent communication features

        @self.app.get("/api/legions/{legion_id}/timeline")
        async def get_legion_timeline(legion_id: str, limit: int = 100, offset: int = 0):
            """Get Comms for legion timeline (all minions in the legion)"""
            try:
                # Read all comms from all minions in the legion
                legion_dir = self.coordinator.data_dir / "legions" / legion_id
                if not legion_dir.exists():
                    return {
                        "comms": [],
                        "total": 0,
                        "limit": limit,
                        "offset": offset
                    }

                all_comms = []
                minions_dir = legion_dir / "minions"

                if minions_dir.exists():
                    # Read comms from each minion's directory
                    for minion_dir in minions_dir.iterdir():
                        if minion_dir.is_dir():
                            comms_file = minion_dir / "comms.jsonl"
                            if comms_file.exists():
                                with open(comms_file, 'r', encoding='utf-8') as f:
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
                            comm['timestamp'] = datetime.now(timezone.utc).timestamp()

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

        @self.app.get("/api/legions/{legion_id}/hordes")
        async def get_legion_horde(legion_id: str):
            """Get complete horde hierarchy with user at root"""
            try:
                # Verify legion exists
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project or not project.is_multi_agent:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Get legion coordinator
                legion_coord = self.coordinator.legion_system.legion_coordinator
                if not legion_coord:
                    raise HTTPException(status_code=500, detail="Legion coordinator not available")

                # Assemble hierarchy
                hierarchy = await legion_coord.assemble_horde_hierarchy(legion_id)

                return hierarchy

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get horde hierarchy: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/legions/{legion_id}/comms")
        async def send_comm_to_legion(legion_id: str, request: CommSendRequest):
            """Send a Comm in the legion"""
            try:
                import uuid
                from src.models.legion_models import Comm, CommType, InterruptPriority

                legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
                if not legion:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Look up minion name if targeting a minion (for historical display)
                to_minion_name = None
                if request.to_minion_id:
                    minion_session = await self.coordinator.session_manager.get_session_info(request.to_minion_id)
                    if minion_session:
                        to_minion_name = minion_session.name

                # TODO: Look up channel name when channels are implemented
                to_channel_name = None

                # Create Comm from user
                comm = Comm(
                    comm_id=str(uuid.uuid4()),
                    from_user=True,
                    to_minion_id=request.to_minion_id,
                    to_channel_id=request.to_channel_id,
                    to_user=request.to_user,
                    to_minion_name=to_minion_name,
                    to_channel_name=to_channel_name,
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
            """Create a new minion in the legion"""
            try:
                # Validate legion exists
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project:
                    raise HTTPException(status_code=404, detail="Legion not found")
                if not project.is_multi_agent:
                    raise HTTPException(status_code=400, detail="Project is not a legion")

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
                    allowed_tools=request.allowed_tools
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

        # ==================== CHANNEL ENDPOINTS ====================

        @self.app.post("/api/legions/{legion_id}/channels", status_code=201)
        async def create_channel(legion_id: str, request: ChannelCreateRequest):
            """Create a new channel in the legion"""
            try:
                # Validate legion exists
                legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
                if not legion:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Create channel via ChannelManager
                channel_id = await self.coordinator.legion_system.channel_manager.create_channel(
                    legion_id=legion_id,
                    name=request.name,
                    description=request.description,
                    purpose=request.purpose,
                    member_minion_ids=request.member_minion_ids,
                    created_by_minion_id=None  # User-created channel
                )

                # Get the created channel
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)

                return {
                    "success": True,
                    "channel_id": channel_id,
                    "channel": channel.to_dict() if channel else None
                }

            except ValueError as e:
                # ChannelManager raises ValueError for validation errors
                logger.error(f"Validation error creating channel: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to create channel: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/legions/{legion_id}/channels")
        async def list_channels(legion_id: str):
            """List all channels in a legion"""
            try:
                # Validate legion exists
                legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
                if not legion:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Get all channels for this legion
                channels = await self.coordinator.legion_system.channel_manager.list_channels(legion_id)

                return {
                    "channels": [channel.to_dict() for channel in channels],
                    "total": len(channels)
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to list channels: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/channels/{channel_id}")
        async def get_channel_details(channel_id: str):
            """Get details for a specific channel"""
            try:
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)
                if not channel:
                    raise HTTPException(status_code=404, detail="Channel not found")

                return {"channel": channel.to_dict()}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get channel details: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/channels/{channel_id}/members")
        async def manage_channel_member(channel_id: str, request: ChannelMemberRequest):
            """Add or remove a member from a channel (with authorization)"""
            try:
                from src.models.legion_models import USER_MINION_ID

                # Get channel
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)
                if not channel:
                    raise HTTPException(status_code=404, detail="Channel not found")

                # Validate action
                if request.action not in ["add", "remove"]:
                    raise HTTPException(status_code=400, detail="Action must be 'add' or 'remove'")

                # Authorization logic
                is_user = request.self_id == USER_MINION_ID
                is_self_action = request.self_id == request.member_id

                if not is_user and not is_self_action:
                    # Check if self_id is overseer of member_id
                    overseer = await self.coordinator.session_manager.get_session_info(request.self_id)
                    if not overseer or not overseer.is_minion:
                        raise HTTPException(status_code=403, detail="Unauthorized: Actor is not a valid minion")

                    # Check if member_id is child of overseer
                    if request.member_id not in (overseer.child_minion_ids or []):
                        raise HTTPException(
                            status_code=403,
                            detail="Unauthorized: Only user, the minion itself, or its overseer can manage membership"
                        )

                # Perform action
                if request.action == "add":
                    await self.coordinator.legion_system.channel_manager.add_member(channel_id, request.member_id)
                    return {"success": True, "action": "added", "member_id": request.member_id}
                else:  # remove
                    await self.coordinator.legion_system.channel_manager.remove_member(channel_id, request.member_id)
                    return {"success": True, "action": "removed", "member_id": request.member_id}

            except ValueError as e:
                # ChannelManager raises ValueError for validation errors
                logger.error(f"Validation error managing channel member: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to manage channel member: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/channels/{channel_id}/broadcast")
        async def broadcast_to_channel(channel_id: str, request: ChannelBroadcastRequest):
            """Broadcast a comm to all members of a channel"""
            try:
                from src.models.legion_models import Comm, CommType, InterruptPriority, USER_MINION_ID

                # Get channel
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)
                if not channel:
                    raise HTTPException(status_code=404, detail="Channel not found")

                # Validate sender
                if request.from_user:
                    # User can always broadcast
                    from_minion_id = None
                    from_minion_name = None
                elif request.from_minion_id:
                    # Minion must be a member of the channel
                    if request.from_minion_id not in channel.member_minion_ids:
                        raise HTTPException(
                            status_code=403,
                            detail="Unauthorized: Minion must be a member to broadcast to this channel"
                        )
                    from_minion_id = request.from_minion_id
                    from_minion_name = request.from_minion_name or "Unknown"
                else:
                    raise HTTPException(status_code=400, detail="Must specify from_user=true or from_minion_id")

                # Map comm_type string to CommType enum
                try:
                    comm_type = CommType(request.comm_type.lower())
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid comm_type. Valid values: {[t.value for t in CommType]}"
                    )

                # Create Comm for channel broadcast
                comm = Comm(
                    comm_id=str(uuid.uuid4()),
                    from_user=request.from_user,
                    from_minion_id=from_minion_id,
                    from_minion_name=from_minion_name,
                    to_channel_id=channel_id,
                    to_channel_name=channel.name,
                    to_user=False,
                    summary=request.summary,
                    content=request.content,
                    comm_type=comm_type,
                    interrupt_priority=InterruptPriority.ROUTINE,
                    visible_to_user=True
                )

                # Route the comm via CommRouter
                success = await self.coordinator.legion_system.comm_router.route_comm(comm)

                if success:
                    # Calculate recipient count (all members except sender if sender is a minion)
                    if request.from_minion_id:
                        recipient_count = len([m for m in channel.member_minion_ids if m != request.from_minion_id])
                    else:
                        recipient_count = len(channel.member_minion_ids)

                    return {
                        "success": True,
                        "comm_id": comm.comm_id,
                        "recipient_count": recipient_count
                    }
                else:
                    raise HTTPException(status_code=500, detail="Failed to broadcast comm to channel")

            except ValueError as e:
                logger.error(f"Validation error broadcasting to channel: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to broadcast to channel: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/channels/{channel_id}/comms")
        async def get_channel_history(channel_id: str, limit: int = 1000, offset: int = 0):
            """Get communication history for a channel (paginated)"""
            try:
                # Get channel to verify it exists
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)
                if not channel:
                    raise HTTPException(status_code=404, detail="Channel not found")

                # Read comms from channel log
                channel_log_path = channel.get_comm_log_path(self.coordinator.data_dir)
                comms = []
                total = 0

                if channel_log_path.exists():
                    # Count total comms
                    with open(channel_log_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        total = len(lines)

                    # Parse paginated comms
                    for line in lines[offset:offset + limit]:
                        if line.strip():
                            try:
                                comm_data = json.loads(line)
                                comms.append(comm_data)
                            except json.JSONDecodeError:
                                continue

                return {
                    "comms": comms,
                    "total": total,
                    "limit": limit,
                    "offset": offset
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get channel history: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/legions/{legion_id}/channels/{channel_id}")
        async def delete_channel(legion_id: str, channel_id: str):
            """Delete a channel and all its communications"""
            try:
                # Validate legion exists
                legion = await self.coordinator.legion_system.legion_coordinator.get_legion(legion_id)
                if not legion:
                    raise HTTPException(status_code=404, detail="Legion not found")

                # Get channel to validate and capture name for response
                channel = await self.coordinator.legion_system.channel_manager.get_channel(channel_id)
                if not channel:
                    raise HTTPException(status_code=404, detail="Channel not found")

                # Verify channel belongs to this legion
                if channel.legion_id != legion_id:
                    raise HTTPException(status_code=400, detail="Channel does not belong to this legion")

                channel_name = channel.name

                # Delete channel via ChannelManager (may raise KeyError if not in cache)
                try:
                    await self.coordinator.legion_system.channel_manager.delete_channel(channel_id)
                except KeyError:
                    raise HTTPException(status_code=404, detail="Channel not found")

                # TODO: Broadcast channel_deleted via WebSocket to all connected clients
                # await self.ui_websocket_manager.broadcast({
                #     "type": "channel_deleted",
                #     "legion_id": legion_id,
                #     "channel_id": channel_id
                # })

                return {
                    "success": True,
                    "message": f"Channel {channel_name} deleted successfully"
                }

            except ValueError as e:
                logger.error(f"Validation error deleting channel: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to delete channel: {e}")
                raise HTTPException(status_code=500, detail=str(e))

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
                    default_role=request.default_role,
                    default_system_prompt=request.default_system_prompt,
                    description=request.description
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
                    default_role=request.default_role,
                    default_system_prompt=request.default_system_prompt,
                    description=request.description
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
                        logger.debug(f"UI WebSocket received: {message}")

                        # Handle ping/pong for keepalive
                        try:
                            message_data = json.loads(message)
                            if message_data.get("type") == "ping":
                                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in UI WebSocket message: {message}")

                    except asyncio.TimeoutError:
                        # Send periodic ping to keep connection alive
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })

            except WebSocketDisconnect:
                logger.info("UI WebSocket disconnected")
            except Exception as e:
                logger.error(f"Error in UI WebSocket: {e}")
            finally:
                self.ui_websocket_manager.disconnect(websocket)

        @self.app.websocket("/ws/legion/{legion_id}")
        async def legion_websocket_endpoint(websocket: WebSocket, legion_id: str):
            """WebSocket endpoint for legion-specific real-time updates"""
            logger.info(f"Legion WebSocket connection request for legion {legion_id}")

            # Validate legion exists
            try:
                project = await self.coordinator.project_manager.get_project(legion_id)
                if not project or not project.is_multi_agent:
                    logger.warning(f"Legion WebSocket connection rejected for non-legion project: {legion_id}")
                    await websocket.close(code=4404)
                    return
            except Exception as e:
                logger.error(f"Error validating legion {legion_id}: {e}")
                await websocket.close(code=4500)
                return

            await self.legion_websocket_manager.connect(websocket, legion_id)

            # Send initial connection confirmation
            try:
                await websocket.send_json({
                    "type": "connection_established",
                    "legion_id": legion_id,
                    "timestamp": datetime.now(timezone.utc).isoformat()
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
                                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in legion WebSocket message: {message}")
                    except asyncio.TimeoutError:
                        # Send periodic ping to keep connection alive
                        await websocket.send_json({
                            "type": "ping",
                            "timestamp": datetime.now(timezone.utc).isoformat()
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
                    ws_logger.debug(f"WebSocket will only connect to sessions in 'active', 'error', or 'paused' state")
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
                    "timestamp": datetime.now(timezone.utc).isoformat()
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
                ws_logger.debug(f"Starting message loop for session {session_id}")

                while True:
                    message_loop_iteration += 1
                    loop_iteration_start_time = time.time()
                    ws_logger.debug(f"Message loop iteration {message_loop_iteration} started at {loop_iteration_start_time}")

                    # Wait for incoming messages with proper error handling
                    try:
                        message_wait_start_time = time.time()
                        ws_logger.debug(f"WebSocket waiting for message from session {session_id} (timeout=3s)")

                        message = await asyncio.wait_for(websocket.receive_text(), timeout=3.0)

                        message_received_time = time.time()
                        ws_logger.debug(f"WebSocket received message from session {session_id} at {message_received_time}: {message[:100]}...")
                        ws_logger.debug(f"Message wait took {message_received_time - message_wait_start_time:.3f}s")

                        message_data = json.loads(message)
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
                                        "timestamp": datetime.now(timezone.utc).isoformat()
                                    }))
                                else:
                                    ws_logger.debug(f"Interrupt failed for session {session_id} at {interrupt_end_time}")

                                    # Send failure response back to client
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": False,
                                        "message": "Failed to initiate interrupt",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(timezone.utc).isoformat()
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
                                        "timestamp": datetime.now(timezone.utc).isoformat()
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
                                        if "updated_input" in message_data:
                                            response["updated_input"] = message_data["updated_input"]
                                        # Include apply_suggestions flag if provided
                                        if "apply_suggestions" in message_data:
                                            response["apply_suggestions"] = message_data["apply_suggestions"]
                                            ws_logger.debug(f"Permission response includes apply_suggestions: {message_data['apply_suggestions']}")
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

                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        timeout_time = time.time()
                        ws_verbose_logger.debug(f"WebSocket timeout for session {session_id} at {timeout_time} - sending ping")

                        try:
                            ping_start_time = time.time()
                            await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}))

                            ping_sent_time = time.time()
                            ws_verbose_logger.debug(f"Ping sent successfully at {ping_sent_time}")

                        except Exception as ping_error:
                            # Connection is dead, break the loop
                            connection_death_time = time.time()
                            ws_logger.debug(f"WebSocket connection DEAD for session {session_id} at {connection_death_time}: {ping_error}")
                            ws_logger.debug(f"Breaking message loop due to dead connection")
                            break

                    except json.JSONDecodeError as e:
                        json_error_time = time.time()
                        ws_logger.debug(f"Invalid JSON received on WebSocket for session {session_id} at {json_error_time}: {e}")
                        continue

                    loop_iteration_end_time = time.time()
                    ws_logger.debug(f"Message loop iteration {message_loop_iteration} completed at {loop_iteration_end_time}")

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
                else:
                    # Handle raw dict messages - process them first
                    processed_message = self._message_processor.process_message(message_data, source="websocket")
                    websocket_data = self._message_processor.prepare_for_websocket(processed_message)

                # Wrap in standard WebSocket envelope
                serialized = {
                    "type": "message",
                    "session_id": session_id,
                    "data": websocket_data,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

                logger.info(f"Attempting to send WebSocket message for session {session_id}: {serialized['type']}")
                await self.websocket_manager.send_message(session_id, serialized)
                logger.info(f"WebSocket message sent successfully for session {session_id}")

            except Exception as e:
                logger.error(f"Error in message callback: {e}")

        return callback

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

            # Store permission request message
            try:
                permission_request = {
                    "type": "permission_request",
                    "content": f"Permission requested for tool: {tool_name}",
                    "session_id": session_id,
                    "timestamp": request_time,
                    "tool_name": tool_name,
                    "input_params": input_params,
                    "request_id": request_id,
                    "suggestions": suggestions
                }

                # Store using unified MessageProcessor for consistency
                try:
                    processed_message = self._message_processor.process_message(permission_request, source="permission")
                    storage_data = self._message_processor.prepare_for_storage(processed_message)

                    storage_manager = await self.coordinator.get_session_storage(session_id)
                    if storage_manager:
                        await storage_manager.append_message(storage_data)
                        logger.debug(f"Stored processed permission request message for session {session_id}")

                    # Broadcast permission request to WebSocket clients
                    try:
                        websocket_data = self._message_processor.prepare_for_websocket(processed_message)
                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": websocket_data,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        await self.websocket_manager.send_message(session_id, websocket_message)
                        logger.info(f"Broadcasted permission request to WebSocket for session {session_id}")
                    except Exception as ws_error:
                        logger.error(f"Failed to broadcast permission request to WebSocket: {ws_error}")
                except Exception as storage_error:
                    logger.error(f"Failed to store permission request with MessageProcessor: {storage_error}")
                    # Fallback to direct storage
                    storage_manager = await self.coordinator.get_session_storage(session_id)
                    if storage_manager:
                        await storage_manager.append_message(permission_request)

                    # Broadcast permission request to WebSocket clients
                    try:
                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": permission_request,
                            "timestamp": datetime.now(timezone.utc).isoformat()
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

                    for suggestion in suggestions:
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
                                    rule_content=rule_dict.get('ruleContent')
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

            # Store permission response message
            decision_time = time.time()
            try:
                # Extract clarification message if it was provided
                clarification_msg = response.get("message") if decision == "deny" and not response.get("interrupt", True) else None

                permission_response = {
                    "type": "permission_response",
                    "content": f"Permission {decision} for tool: {tool_name} - {reasoning}",
                    "session_id": session_id,
                    "timestamp": decision_time,
                    "request_id": request_id,
                    "decision": decision,
                    "reasoning": reasoning,
                    "tool_name": tool_name,
                    "response_time_ms": int((decision_time - request_time) * 1000)
                }

                # Include clarification message if provided (for deny with clarification)
                if clarification_msg:
                    permission_response["clarification_message"] = clarification_msg
                    permission_response["interrupt"] = False
                    logger.info(f"Stored permission denial with clarification for session {session_id}")

                # Include applied updates if any were applied (use the dict version we saved)
                if decision == "allow" and response.get("applied_updates_for_storage"):
                    permission_response["applied_updates"] = response["applied_updates_for_storage"]
                    logger.info(f"Permission response includes {len(response['applied_updates_for_storage'])} applied updates")

                # Store using unified MessageProcessor for consistency
                try:
                    processed_message = self._message_processor.process_message(permission_response, source="permission")
                    storage_data = self._message_processor.prepare_for_storage(processed_message)

                    if storage_manager:
                        await storage_manager.append_message(storage_data)
                        logger.debug(f"Stored processed permission response message for session {session_id}")
                except Exception as storage_error:
                    logger.error(f"Failed to store permission response with MessageProcessor: {storage_error}")
                    # Fallback to direct storage
                    if storage_manager:
                        await storage_manager.append_message(permission_response)

                    # Broadcast permission response to WebSocket clients
                    try:
                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": permission_response,
                            "timestamp": datetime.now(timezone.utc).isoformat()
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

def create_app(data_dir: Path = None) -> FastAPI:
    """Create and configure the FastAPI application"""
    global webui_app
    webui_app = ClaudeWebUI(data_dir)
    return webui_app.app

async def startup_event():
    """Application startup event"""
    if webui_app:
        await webui_app.initialize()

async def shutdown_event():
    """Application shutdown event"""
    if webui_app:
        await webui_app.cleanup()