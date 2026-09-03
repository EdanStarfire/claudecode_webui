"""Session runtime endpoints: permission-mode, MCP, restart, reset, history, disconnect, proxy-logs."""

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ..models.permission_mode import PermissionMode
from ..session_manager import VALID_MODELS, SessionState
from ._models import (
    AddDirectoryRequest,
    McpReconnectRequest,
    McpToggleRequest,
    ModelRequest,
    PermissionModeRequest,
    PermissionResponseRequest,
)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== INTERRUPT / PERMISSION-RESPONSE ENDPOINTS ====================
    # Relocated from src/routers/core.py (issue #498): these act on backend-owned
    # state (SessionCoordinator, PermissionService.pending_permissions) and no longer
    # belong on the Frontend API side of the relay boundary.

    @router.post("/api/sessions/{session_id}/interrupt")
    @handle_exceptions("interrupt session")
    async def interrupt_session_rest(session_id: str):
        """Interrupt a session via REST (replaces WebSocket interrupt_session message)."""
        state = await webui.service.get_session_state(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if state == SessionState.PAUSED:
            webui.permission_service.deny_all_for_interrupt()
        result = await webui.coordinator.interrupt_session(session_id)
        return {"success": bool(result)}

    @router.post("/api/sessions/{session_id}/permission/{request_id}")
    @handle_exceptions("respond to permission")
    async def respond_to_permission(
        session_id: str, request_id: str, request: PermissionResponseRequest
    ):
        """Respond to a pending permission request via REST."""
        if request_id not in webui.permission_service.pending_permissions:
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
        resolved = webui.permission_service.resolve(request_id, response)
        if not resolved:
            raise HTTPException(status_code=409, detail="Permission already resolved")
        return {"success": True}

    # ==================== PROXY LOG ENDPOINTS (Issue #1102) ====================

    @router.get("/api/sessions/{session_id}/proxy-logs")
    @handle_exceptions("get proxy logs")
    async def get_proxy_logs(
        session_id: str,
        log_type: str = "http",
        limit: int = 200,
    ):
        """Get proxy log entries for a session (HTTP access log, DNS query log, or SOCKS5 connection log)"""
        if log_type not in ("http", "dns", "socks5"):
            raise HTTPException(status_code=400, detail="log_type must be 'http', 'dns', or 'socks5'")
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await webui.coordinator.get_proxy_logs(session_id, log_type=log_type, limit=limit)

    # ==================== PERMISSION MODE ENDPOINT ====================

    @router.post("/api/sessions/{session_id}/permission-mode")
    @handle_exceptions("set permission mode", value_error_status=400)
    async def set_permission_mode(session_id: str, request: PermissionModeRequest):
        """Set the permission mode for a session"""
        if request.mode not in PermissionMode._value2member_map_:
            raise HTTPException(status_code=400, detail=f"Invalid permission mode: {request.mode}")

        success = await webui.coordinator.set_permission_mode(session_id, request.mode)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to set permission mode")
        return {"success": success, "mode": request.mode}

    # ==================== MODEL ENDPOINT ====================

    @router.post("/api/sessions/{session_id}/model")
    @handle_exceptions("set model", value_error_status=400)
    async def set_model(session_id: str, request: ModelRequest):
        """Set the model for a session, live-switching if active (no restart required)"""
        if request.model not in VALID_MODELS:
            raise HTTPException(status_code=400, detail=f"Invalid model: {request.model}")

        success = await webui.coordinator.set_model(session_id, request.model)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to set model")
        return {"success": success, "model": request.model}

    @router.post("/api/sessions/{session_id}/add-directory")
    @handle_exceptions("add directory", value_error_status=400)
    async def add_directory(session_id: str, request: AddDirectoryRequest):
        """Register a new working directory on an active session, live (issue #1675)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        result = await webui.coordinator.add_directory(session_id, request.directory)
        return {"success": True, **result}

    @router.get("/api/sessions/{session_id}/mcp-status")
    @handle_exceptions("get mcp status")
    async def get_mcp_status(session_id: str):
        """Get MCP server status for a session"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        result = await webui.coordinator.get_mcp_status(session_id)
        return result

    @router.post("/api/sessions/{session_id}/mcp-toggle")
    @handle_exceptions("toggle mcp server")
    async def toggle_mcp_server(session_id: str, request: McpToggleRequest):
        """Toggle an MCP server on or off"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            await webui.coordinator.toggle_mcp_server(
                session_id, request.name, request.enabled
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"success": True, "name": request.name, "enabled": request.enabled}

    @router.post("/api/sessions/{session_id}/mcp-reconnect")
    @handle_exceptions("reconnect mcp server")
    async def reconnect_mcp_server(session_id: str, request: McpReconnectRequest):
        """Reconnect a failed MCP server"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            await webui.coordinator.reconnect_mcp_server(session_id, request.name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"success": True, "name": request.name}

    @router.post("/api/sessions/{session_id}/restart")
    @handle_exceptions("restart session")
    async def restart_session(session_id: str):
        """Restart a session (disconnect and resume)"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Clear any existing callbacks to prevent duplicates, then register fresh one
        webui.coordinator.clear_message_callbacks(session_id)
        webui.coordinator.add_message_callback(
            session_id,
            webui._create_message_callback(session_id)
        )

        success = await webui.coordinator.restart_session(
            session_id,
            permission_callback=webui.permission_service.create_permission_callback(session_id),
        )
        return {"success": success}

    @router.post("/api/sessions/{session_id}/reset")
    @handle_exceptions("reset session")
    async def reset_session(session_id: str):
        """Reset a session (clear messages and start fresh)"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Fix 6: parity with /restart — clear and re-register message callback
        webui.coordinator.clear_message_callbacks(session_id)
        webui.coordinator.add_message_callback(
            session_id,
            webui._create_message_callback(session_id)
        )

        success = await webui.coordinator.reset_session(
            session_id,
            permission_callback=webui.permission_service.create_permission_callback(session_id),
        )
        return {"success": success}

    @router.delete("/api/sessions/{session_id}/history")
    @handle_exceptions("erase session history")
    async def erase_session_history(session_id: str):
        """Erase distilled history files for a session."""
        success = await webui.coordinator.erase_history(session_id)
        return {"success": success}

    @router.post("/api/sessions/{session_id}/disconnect")
    @handle_exceptions("disconnect session")
    async def disconnect_session(session_id: str):
        """Disconnect SDK but keep session state (for end session)"""
        return await webui.service.disconnect_session(session_id)

    return router
