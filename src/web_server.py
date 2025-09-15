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


class MessageRequest(BaseModel):
    message: str


class WebSocketManager:
    """Manages WebSocket connections for real-time communication"""

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

    async def send_message(self, session_id: str, message: dict):
        if session_id in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[session_id]:
                try:
                    await connection.send_text(json.dumps(message))
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
                session_id = await self.coordinator.create_session(
                    working_directory=request.working_directory,
                    permissions=request.permissions,
                    system_prompt=request.system_prompt,
                    tools=request.tools,
                    model=request.model,
                    permission_callback=self._create_permission_callback()
                )

                # Register message callback for this session
                self.coordinator.add_message_callback(
                    session_id,
                    self._create_message_callback(session_id)
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
                success = await self.coordinator.start_session(session_id)
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
            """Get messages from a session"""
            try:
                messages = await self.coordinator.get_session_messages(
                    session_id, limit=limit, offset=offset
                )
                return {"messages": messages}
            except Exception as e:
                logger.error(f"Failed to get messages: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.websocket("/ws/{session_id}")
        async def websocket_endpoint(websocket: WebSocket, session_id: str):
            """WebSocket endpoint for real-time communication"""
            await self.websocket_manager.connect(websocket, session_id)
            try:
                while True:
                    # Wait for incoming messages with proper error handling
                    try:
                        message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                        message_data = json.loads(message)

                        if message_data.get("type") == "send_message":
                            # Handle message sending through WebSocket
                            content = message_data.get("content", "")
                            if content:
                                await self.coordinator.send_message(session_id, content)

                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        try:
                            await websocket.send_text(json.dumps({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}))
                        except Exception:
                            # Connection is dead, break the loop
                            break
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON received on WebSocket: {e}")
                        continue

            except WebSocketDisconnect:
                self.websocket_manager.disconnect(websocket, session_id)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.websocket_manager.disconnect(websocket, session_id)

    def _create_message_callback(self, session_id: str):
        """Create message callback for WebSocket broadcasting"""
        async def callback(session_id: str, message_data: Any):
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

                await self.websocket_manager.send_message(session_id, serialized)

            except Exception as e:
                logger.error(f"Error in message callback: {e}")

        return callback

    async def _on_state_change(self, state_data: dict):
        """Handle session state changes"""
        try:
            session_id = state_data.get("session_id")
            if session_id:
                message = {
                    "type": "state_change",
                    "data": state_data
                }
                await self.websocket_manager.send_message(session_id, message)
        except Exception as e:
            logger.error(f"Error handling state change: {e}")

    def _create_permission_callback(self):
        """Create permission callback for tool usage"""
        async def permission_callback(tool_name: str, input_params: dict) -> dict:
            logger.info(f"Permission requested for tool: {tool_name}")

            # For now, auto-approve common tools
            safe_tools = ["read", "write", "edit", "bash", "glob", "grep"]
            if tool_name.lower() in safe_tools:
                return {
                    "behavior": "allow",
                    "updated_input": input_params
                }
            else:
                return {
                    "behavior": "deny",
                    "message": f"Tool '{tool_name}' not in safe tools list"
                }

        return permission_callback

    def _serialize_message(self, message_data: Any) -> dict:
        """Serialize message data for JSON transmission"""
        try:
            if hasattr(message_data, 'type'):
                msg_type = message_data.type.value if hasattr(message_data.type, 'value') else str(message_data.type)
            else:
                msg_type = "unknown"

            content = getattr(message_data, 'content', str(message_data)) if hasattr(message_data, 'content') else ""

            return {
                "type": msg_type,
                "content": content,
                "timestamp": getattr(message_data, 'timestamp', datetime.now(timezone.utc).isoformat())
            }
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