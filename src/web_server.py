"""
FastAPI web server for Claude Code WebUI with WebSocket support.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .session_coordinator import SessionCoordinator
from .message_parser import MessageProcessor, MessageParser
from .logging_config import get_logger

# Get specialized logger for WebSocket lifecycle debugging
ws_logger = get_logger('websocket_debug', category='WS_LIFECYCLE')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


class ProjectCreateRequest(BaseModel):
    name: str
    working_directory: str


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    is_expanded: Optional[bool] = None


class ProjectReorderRequest(BaseModel):
    project_ids: List[str]


class SessionCreateRequest(BaseModel):
    project_id: str
    permission_mode: str = "acceptEdits"
    system_prompt: Optional[str] = None
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


class ClaudeWebUI:
    """Main WebUI application class"""

    def __init__(self, data_dir: Path = None):
        self.app = FastAPI(title="Claude Code WebUI", version="1.0.0")
        self.coordinator = SessionCoordinator(data_dir)
        self.websocket_manager = WebSocketManager()
        self.ui_websocket_manager = UIWebSocketManager()

        # Initialize MessageProcessor for unified WebSocket message formatting
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Track pending permission requests with asyncio.Future objects
        self.pending_permissions: Dict[str, asyncio.Future] = {}

        # Setup routes
        self._setup_routes()

        # Setup static files
        static_dir = Path(__file__).parent.parent / "static"
        static_dir.mkdir(exist_ok=True)
        self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

        # Register callbacks
        self.coordinator.add_state_change_callback(self._on_state_change)

        logger.info("Claude Code WebUI initialized")

    def _setup_routes(self):
        """Setup FastAPI routes"""

        @self.app.get("/", response_class=HTMLResponse)
        async def read_root():
            """Serve the main HTML page"""
            html_file = Path(__file__).parent.parent / "static" / "index.html"
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
            """Create a new project"""
            try:
                project = await self.coordinator.project_manager.create_project(
                    name=request.name,
                    working_directory=request.working_directory
                )
                return {"project": project.to_dict()}
            except Exception as e:
                logger.error(f"Failed to create project: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/projects")
        async def list_projects():
            """List all projects"""
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
                import uuid
                session_id = str(uuid.uuid4())

                session_id = await self.coordinator.create_session(
                    session_id=session_id,
                    project_id=request.project_id,
                    permission_mode=request.permission_mode,
                    system_prompt=request.system_prompt,
                    tools=request.tools,
                    model=request.model,
                    name=request.name,
                    permission_callback=self._create_permission_callback(session_id)
                )

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
                # First force disconnect any WebSocket connections for this session
                await self.websocket_manager.force_disconnect_session(session_id)

                # Clean up any pending permissions for this session
                self._cleanup_pending_permissions_for_session(session_id)

                success = await self.coordinator.delete_session(session_id)
                if not success:
                    raise HTTPException(status_code=404, detail="Session not found")
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

        @self.app.get("/api/filesystem/browse")
        async def browse_filesystem(path: str = None):
            """Browse filesystem directories"""
            import os
            import platform

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
                        message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
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

        @self.app.websocket("/ws/session/{session_id}")
        async def websocket_endpoint(websocket: WebSocket, session_id: str):
            """WebSocket endpoint for session-specific messaging"""
            import time
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

                if session_state not in ['active', 'error']:
                    rejection_time = time.time()
                    ws_logger.debug(f"WebSocket connection REJECTED for session: {session_id} (state: {session_state}) at {rejection_time}")
                    ws_logger.debug(f"WebSocket will only connect to sessions in 'active' or 'error' state")
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
                        ws_logger.debug(f"WebSocket waiting for message from session {session_id} (timeout=30s)")

                        message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

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
                                    else:
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
                        ws_logger.debug(f"WebSocket timeout for session {session_id} at {timeout_time} - sending ping")

                        try:
                            ping_start_time = time.time()
                            await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}))

                            ping_sent_time = time.time()
                            ws_logger.debug(f"Ping sent successfully at {ping_sent_time}")

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
        async def permission_callback(tool_name: str, input_params: dict) -> dict:
            import uuid
            import time

            # Generate unique request ID to correlate request/response
            request_id = str(uuid.uuid4())
            request_time = time.time()

            logger.info(f"PERMISSION CALLBACK TRIGGERED: tool={tool_name}, session={session_id}, request_id={request_id}")
            logger.info(f"Permission requested for tool: {tool_name} (request_id: {request_id})")

            # Store permission request message
            try:
                permission_request = {
                    "type": "permission_request",
                    "content": f"Permission requested for tool: {tool_name}",
                    "session_id": session_id,
                    "timestamp": request_time,
                    "tool_name": tool_name,
                    "input_params": input_params,
                    "request_id": request_id
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

                decision = response.get("behavior")
                reasoning = f"User {decision}ed permission"

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