"""Permission preview endpoint: /api/permissions/preview"""

from fastapi import APIRouter, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..permission_resolver import resolve_effective_permissions
from ..session_backend import BackendMode
from ._models import PermissionPreviewRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/permissions/preview")
    @handle_exceptions("preview permissions")
    async def preview_permissions(request: PermissionPreviewRequest, http_request: Request):
        """
        Preview effective permissions from settings files.

        Returns a list of permissions with their source annotations.
        """
        # Issue #498: settings files live on whichever host runs the session —
        # relay unconditionally when REMOTE is configured, no ID needed.
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, http_request)
        permissions = resolve_effective_permissions(
            working_directory=request.working_directory,
            setting_sources=request.setting_sources,
            session_allowed_tools=request.session_allowed_tools
        )
        return {"permissions": permissions}

    return router
