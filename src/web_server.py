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
from .logging_config import get_logger

logger = get_logger(__name__)


class SessionCreateRequest(BaseModel):
    working_directory: str
    permissions: str = "acceptEdits"
    system_prompt: Optional[str] = None
    tools: List[str] = []
    model: Optional[str] = None
    name: Optional[str] = None


class MessageRequest(BaseModel):
    message: str


class SessionNameUpdateRequest(BaseModel):
    name: str


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

        # Setup routes
        self._setup_routes()

        # Setup static files
        static_dir = Path(__file__).parent.parent / "static"
        static_dir.mkdir(exist_ok=True)
        self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

        @self.app.post("/api/sessions")
        async def create_session(request: SessionCreateRequest):
            """Create a new Claude Code session"""
            try:
                # Pre-generate session ID so we can pass it to permission callback
                import uuid
                session_id = str(uuid.uuid4())

                session_id = await self.coordinator.create_session(
                    session_id=session_id,
                    working_directory=request.working_directory,
                    permissions=request.permissions,
                    system_prompt=request.system_prompt,
                    tools=request.tools,
                    model=request.model,
                    name=request.name,
                    permission_callback=self._create_permission_callback(session_id)
                )


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
            logger.info(f"[WS_LIFECYCLE] WebSocket connection attempt for session {session_id} at {ws_connection_start_time}")

            # Validate session exists and is active before accepting connection
            try:
                session_validation_time = time.time()
                logger.info(f"[WS_LIFECYCLE] Validating session {session_id} at {session_validation_time}")

                session_info = await self.coordinator.get_session_info(session_id)
                if not session_info:
                    rejection_time = time.time()
                    logger.warning(f"[WS_LIFECYCLE] WebSocket connection REJECTED for non-existent session: {session_id} at {rejection_time}")
                    await websocket.close(code=4404)
                    return

                session_state = session_info.get('session', {}).get('state')
                logger.info(f"[WS_LIFECYCLE] Session {session_id} state: {session_state}")

                if session_state not in ['active', 'error']:
                    rejection_time = time.time()
                    logger.warning(f"[WS_LIFECYCLE] WebSocket connection REJECTED for session: {session_id} (state: {session_state}) at {rejection_time}")
                    logger.info(f"[WS_LIFECYCLE] WebSocket will only connect to sessions in 'active' or 'error' state")
                    await websocket.close(code=4003)
                    return

                validation_success_time = time.time()
                logger.info(f"[WS_LIFECYCLE] Session validation successful for {session_id} at {validation_success_time}")

            except Exception as e:
                validation_error_time = time.time()
                logger.error(f"[WS_LIFECYCLE] Error validating session {session_id} for WebSocket at {validation_error_time}: {e}")
                await websocket.close(code=4500)
                return

            # Accept WebSocket connection
            connection_accept_time = time.time()
            logger.info(f"[WS_LIFECYCLE] Accepting WebSocket connection for session {session_id} at {connection_accept_time}")

            await self.websocket_manager.connect(websocket, session_id)

            connection_established_time = time.time()
            logger.info(f"[WS_LIFECYCLE] WebSocket connection ESTABLISHED for session {session_id} at {connection_established_time}")
            logger.info(f"[WS_LIFECYCLE] Connection establishment took {connection_established_time - ws_connection_start_time:.3f}s")
            logger.info(f"[WS_LIFECYCLE] WebSocket loop starting for session {session_id}")

            # Send initial ping to establish connection
            try:
                initial_message_time = time.time()
                logger.info(f"[WS_LIFECYCLE] Sending initial connection_established message at {initial_message_time}")

                await websocket.send_text(json.dumps({
                    "type": "connection_established",
                    "session_id": session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }))

                initial_message_sent_time = time.time()
                logger.info(f"[WS_LIFECYCLE] Initial message sent successfully at {initial_message_sent_time}")

            except Exception as e:
                initial_message_error_time = time.time()
                logger.error(f"[WS_LIFECYCLE] Failed to send initial message to WebSocket {session_id} at {initial_message_error_time}: {e}")
                # Clean up the connection that was already registered
                self.websocket_manager.disconnect(websocket, session_id)
                logger.info(f"[WS_LIFECYCLE] WebSocket disconnected due to initial message failure for session {session_id}")
                return

            message_loop_iteration = 0
            try:
                logger.info(f"[WS_LIFECYCLE] Starting message loop for session {session_id}")

                while True:
                    message_loop_iteration += 1
                    loop_iteration_start_time = time.time()
                    logger.debug(f"[WS_LIFECYCLE] Message loop iteration {message_loop_iteration} started at {loop_iteration_start_time}")

                    # Wait for incoming messages with proper error handling
                    try:
                        message_wait_start_time = time.time()
                        logger.debug(f"[WS_LIFECYCLE] WebSocket waiting for message from session {session_id} (timeout=30s)")

                        message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                        message_received_time = time.time()
                        logger.debug(f"[WS_LIFECYCLE] WebSocket received message from session {session_id} at {message_received_time}: {message[:100]}...")
                        logger.debug(f"[WS_LIFECYCLE] Message wait took {message_received_time - message_wait_start_time:.3f}s")

                        message_data = json.loads(message)
                        logger.info(f"[WS_LIFECYCLE] DEBUG: Received message type: '{message_data.get('type', 'unknown')}' from session {session_id}")
                        logger.info(f"[WS_LIFECYCLE] DEBUG: Full message data: {message_data}")

                        if message_data.get("type") == "send_message":
                            # Handle message sending through WebSocket
                            content = message_data.get("content", "")
                            if content:
                                message_processing_start_time = time.time()
                                logger.info(f"[WS_LIFECYCLE] Forwarding message to coordinator for session {session_id} at {message_processing_start_time}")
                                logger.info(f"[WS_LIFECYCLE] Message content preview: {content[:100]}...")

                                await self.coordinator.send_message(session_id, content)

                                message_processing_end_time = time.time()
                                logger.info(f"[WS_LIFECYCLE] Message forwarded to coordinator at {message_processing_end_time}")
                                logger.info(f"[WS_LIFECYCLE] Message forwarding took {message_processing_end_time - message_processing_start_time:.3f}s")

                        elif message_data.get("type") == "interrupt_session":
                            # Handle session interrupt through WebSocket
                            interrupt_start_time = time.time()
                            logger.info(f"[WS_LIFECYCLE] DEBUG: INTERRUPT REQUEST RECEIVED for session {session_id} at {interrupt_start_time}")
                            logger.info(f"[WS_LIFECYCLE] DEBUG: Full interrupt message data: {message_data}")

                            try:
                                # Forward interrupt request to coordinator
                                result = await self.coordinator.interrupt_session(session_id)
                                interrupt_end_time = time.time()

                                if result:
                                    logger.info(f"[WS_LIFECYCLE] Interrupt successfully initiated for session {session_id} at {interrupt_end_time}")
                                    logger.info(f"[WS_LIFECYCLE] Interrupt processing took {interrupt_end_time - interrupt_start_time:.3f}s")

                                    # Send success response back to client
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": True,
                                        "message": "Interrupt initiated successfully",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(timezone.utc).isoformat()
                                    }))
                                else:
                                    logger.warning(f"[WS_LIFECYCLE] Interrupt failed for session {session_id} at {interrupt_end_time}")

                                    # Send failure response back to client
                                    await websocket.send_text(json.dumps({
                                        "type": "interrupt_response",
                                        "success": False,
                                        "message": "Failed to initiate interrupt",
                                        "session_id": session_id,
                                        "timestamp": datetime.now(timezone.utc).isoformat()
                                    }))

                            except Exception as interrupt_error:
                                logger.error(f"[WS_LIFECYCLE] Interrupt error for session {session_id}: {interrupt_error}")

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
                                    logger.error(f"[WS_LIFECYCLE] Failed to send interrupt error response: {response_error}")

                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        timeout_time = time.time()
                        logger.debug(f"[WS_LIFECYCLE] WebSocket timeout for session {session_id} at {timeout_time} - sending ping")

                        try:
                            ping_start_time = time.time()
                            await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}))

                            ping_sent_time = time.time()
                            logger.debug(f"[WS_LIFECYCLE] Ping sent successfully at {ping_sent_time}")

                        except Exception as ping_error:
                            # Connection is dead, break the loop
                            connection_death_time = time.time()
                            logger.warning(f"[WS_LIFECYCLE] WebSocket connection DEAD for session {session_id} at {connection_death_time}: {ping_error}")
                            logger.info(f"[WS_LIFECYCLE] Breaking message loop due to dead connection")
                            break

                    except json.JSONDecodeError as e:
                        json_error_time = time.time()
                        logger.warning(f"[WS_LIFECYCLE] Invalid JSON received on WebSocket for session {session_id} at {json_error_time}: {e}")
                        continue

                    loop_iteration_end_time = time.time()
                    logger.debug(f"[WS_LIFECYCLE] Message loop iteration {message_loop_iteration} completed at {loop_iteration_end_time}")

            except WebSocketDisconnect as disconnect_error:
                disconnect_time = time.time()
                logger.info(f"[WS_LIFECYCLE] WebSocket DISCONNECTED gracefully for session {session_id} at {disconnect_time}")
                logger.info(f"[WS_LIFECYCLE] Disconnect details: {disconnect_error}")
                logger.info(f"[WS_LIFECYCLE] Total message loop iterations: {message_loop_iteration}")
                self.websocket_manager.disconnect(websocket, session_id)

            except Exception as ws_error:
                error_time = time.time()
                logger.error(f"[WS_LIFECYCLE] WebSocket ERROR for session {session_id} at {error_time}: {ws_error}")
                logger.error(f"[WS_LIFECYCLE] Error type: {type(ws_error)}")
                logger.error(f"[WS_LIFECYCLE] Total message loop iterations before error: {message_loop_iteration}")
                self.websocket_manager.disconnect(websocket, session_id)

            finally:
                cleanup_time = time.time()
                total_connection_time = cleanup_time - ws_connection_start_time
                logger.info(f"[WS_LIFECYCLE] WebSocket cleanup for session {session_id} at {cleanup_time}")
                logger.info(f"[WS_LIFECYCLE] Total WebSocket connection duration: {total_connection_time:.3f}s")
                logger.info(f"[WS_LIFECYCLE] Total message loop iterations: {message_loop_iteration}")
                logger.info(f"[WS_LIFECYCLE] WebSocket loop ENDED for session {session_id}")

    def _create_message_callback(self, session_id: str):
        """Create message callback for WebSocket broadcasting"""
        async def callback(session_id: str, message_data: Any):
            logger.info(f"WebSocket callback triggered for session {session_id}, message type: {getattr(message_data, 'type', 'unknown')}")
            try:
                # Convert message to JSON-serializable format
                if hasattr(message_data, '__dict__'):
                    # Handle dataclass/object messages
                    serialized = {
                        "type": "message",
                        "session_id": session_id,
                        "data": self._serialize_message(message_data),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    # Handle dict messages
                    serialized = {
                        "type": "message",
                        "session_id": session_id,
                        "data": message_data,
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

                # Get storage manager for this session
                storage_manager = await self.coordinator.get_session_storage(session_id)
                if storage_manager:
                    await storage_manager.append_message(permission_request)
                    logger.debug(f"Stored permission request message for session {session_id}")

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

            # Process permission decision (current auto-approval logic)
            decision_time = time.time()
            safe_tools = ["read", "write", "glob", "grep"]

            if tool_name.lower() in safe_tools:
                decision = "allow"
                reasoning = f"Tool '{tool_name}' is in safe tools list"
                response = {
                    "behavior": "allow",
                    "updated_input": input_params
                }
            else:
                decision = "deny"
                reasoning = f"Tool '{tool_name}' not in safe tools list"
                response = {
                    "behavior": "deny",
                    "message": reasoning
                }

            # Store permission response message
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

                if storage_manager:
                    await storage_manager.append_message(permission_response)
                    logger.debug(f"Stored permission response message for session {session_id}")

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

    def _serialize_message(self, message_data: Any) -> dict:
        """Serialize message data for JSON transmission"""
        try:
            if hasattr(message_data, 'type'):
                msg_type = message_data.type.value if hasattr(message_data.type, 'value') else str(message_data.type)
            else:
                msg_type = "unknown"

            content = getattr(message_data, 'content', str(message_data)) if hasattr(message_data, 'content') else ""

            # Include metadata fields, especially subtype for init message detection
            result = {
                "type": msg_type,
                "content": content,
                "timestamp": getattr(message_data, 'timestamp', datetime.now(timezone.utc).isoformat())
            }

            # Add full metadata if available
            if hasattr(message_data, 'metadata') and message_data.metadata:
                # Create a serializable copy of metadata (exclude non-serializable objects)
                metadata_copy = {}
                for key, value in message_data.metadata.items():
                    try:
                        # Test if value is JSON serializable by trying to serialize it
                        import json
                        json.dumps(value)
                        metadata_copy[key] = value
                    except (TypeError, ValueError):
                        # Skip non-serializable values (like SDK objects)
                        if key == 'sdk_message':
                            continue
                        # Convert other objects to string representation
                        metadata_copy[key] = str(value)

                if metadata_copy:
                    result['metadata'] = metadata_copy

                # Maintain backward compatibility - still add subtype to root level
                subtype = message_data.metadata.get('subtype')
                if subtype:
                    result['subtype'] = subtype

            return result
        except Exception as e:
            logger.error(f"Error serializing message: {e}")
            return {
                "type": "unknown",
                "content": str(message_data),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

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