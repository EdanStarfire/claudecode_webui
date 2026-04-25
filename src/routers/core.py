"""Core cross-cutting endpoints: /, /health, /api/auth/check, interrupt, permission response"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..exception_handlers import handle_exceptions
from ..session_manager import SessionState
from ._models import PermissionResponseRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

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

    @router.get("/", response_class=HTMLResponse)
    @handle_exceptions("serve root")
    async def read_root():
        """Serve the main HTML page"""
        html_file = Path(__file__).parent.parent.parent / "frontend" / "dist" / "index.html"
        if html_file.exists():
            return HTMLResponse(
                content=html_file.read_text(encoding='utf-8'),
                status_code=200,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
        return HTMLResponse(content=webui._default_html(), status_code=200)

    @router.get("/health")
    @handle_exceptions("health check")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

    @router.get("/api/auth/check")
    @handle_exceptions("check auth")
    async def auth_check(request: Request):
        """Check authentication status (issue #728). Exempt from auth middleware."""
        authenticated = False
        if webui.auth_enabled and webui.auth_token:
            # Check Authorization header
            auth_header = request.headers.get('authorization', '')
            if auth_header.startswith('Bearer ') and auth_header[7:] == webui.auth_token:
                authenticated = True
            # Check query param
            if not authenticated and request.query_params.get('token') == webui.auth_token:
                authenticated = True
        elif not webui.auth_enabled:
            authenticated = True
        return {"auth_required": webui.auth_enabled, "authenticated": authenticated}

    return router
