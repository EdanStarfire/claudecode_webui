"""Proxy session status endpoints: /api/sessions/{id}/proxy/*

Credential CRUD has moved to /api/secrets/* (see secrets.py — issue #827).
This router retains the per-session proxy status and blocked-log endpoints,
which describe proxy sidecar state rather than credential management.
"""

from fastapi import APIRouter, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..session_backend import BackendMode


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions/{session_id}/proxy/status")
    @handle_exceptions("get proxy status")
    async def get_proxy_status(session_id: str, request: Request):
        """Return effective allowlist + active credential names + proxy state for a session."""
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, request)
        return await webui.service.get_proxy_status(session_id)

    @router.get("/sessions/{session_id}/proxy/blocked")
    @handle_exceptions("get proxy blocked log")
    async def get_proxy_blocked_log(session_id: str, request: Request, limit: int = 50):
        """Return recent blocked connections from the sidecar access log."""
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, request)
        return await webui.service.get_proxy_blocked_log(session_id, limit=limit)

    return router
