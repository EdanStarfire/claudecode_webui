"""Permission preview endpoint: /api/permissions/preview"""

from fastapi import APIRouter

from ..exception_handlers import handle_exceptions
from ..permission_resolver import resolve_effective_permissions
from ._models import PermissionPreviewRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/permissions/preview")
    @handle_exceptions("preview permissions")
    async def preview_permissions(request: PermissionPreviewRequest):
        """
        Preview effective permissions from settings files.

        Returns a list of permissions with their source annotations.
        """
        permissions = resolve_effective_permissions(
            working_directory=request.working_directory,
            setting_sources=request.setting_sources,
            session_allowed_tools=request.session_allowed_tools
        )
        return {"permissions": permissions}

    return router
