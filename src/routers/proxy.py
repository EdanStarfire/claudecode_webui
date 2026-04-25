"""Proxy credential and session proxy endpoints: /api/proxy/*, /api/sessions/{id}/proxy/*"""

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions
from ._models import CredentialCreateRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== PROXY ENDPOINTS (issue #1053) ====================

    @router.get("/api/proxy/credentials")
    @handle_exceptions("list proxy credentials")
    async def list_proxy_credentials():
        """List all proxy credentials (metadata only, no values)."""
        return await webui.service.list_credentials()

    @router.post("/api/proxy/credentials", status_code=201)
    @handle_exceptions("create proxy credential", value_error_status=400)
    async def create_proxy_credential(request: CredentialCreateRequest):
        """Create a named proxy credential. Returns metadata only (value not in response)."""
        return await webui.service.create_credential(
            name=request.name,
            host_pattern=request.host_pattern,
            header_name=request.header_name,
            value_format=request.value_format,
            real_value=request.real_value,
            delivery=request.delivery,
        )

    @router.delete("/api/proxy/credentials/{name}")
    @handle_exceptions("delete proxy credential")
    async def delete_proxy_credential(name: str):
        """Delete a proxy credential by name."""
        deleted = await webui.service.delete_credential(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Credential not found")
        return {"deleted": True}

    @router.get("/api/sessions/{session_id}/proxy/status")
    @handle_exceptions("get proxy status")
    async def get_proxy_status(session_id: str):
        """Return effective allowlist + active credential names + proxy state for a session."""
        return await webui.service.get_proxy_status(session_id)

    @router.get("/api/sessions/{session_id}/proxy/blocked")
    @handle_exceptions("get proxy blocked log")
    async def get_proxy_blocked_log(session_id: str, limit: int = 50):
        """Return recent blocked connections from the sidecar access log."""
        return await webui.service.get_proxy_blocked_log(session_id, limit=limit)

    return router
