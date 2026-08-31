"""Docker status endpoint: /api/system/docker-status

Issue #498: split out of system.py — this is the only system.py route that's
relay-eligible (docker availability is host-local; every other system.py route,
e.g. git-status/restart-server, operates on the Hub's own git checkout/process
and has no REMOTE equivalent).
"""

from fastapi import APIRouter, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..session_backend import BackendMode


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/system/docker-status")
    @handle_exceptions("check docker status")
    async def get_docker_status(request: Request):
        """Check Docker availability and image status (issue #496)."""
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, request)
        from src.docker_utils import check_docker_available
        status = await check_docker_available()
        return status

    return router
