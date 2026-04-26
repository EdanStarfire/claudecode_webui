"""Proxy session status endpoints: /api/sessions/{id}/proxy/*

Credential CRUD has moved to /api/secrets/* (see secrets.py — issue #827).
This router retains the per-session proxy status and blocked-log endpoints,
which describe proxy sidecar state rather than credential management.
"""

from fastapi import APIRouter

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

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
