"""Archive endpoints: /api/projects/{id}/archives/*, /api/projects/{id}/deleted-agents,
/api/sessions/{id}/archives, /api/sessions/{id}/history-archives-status"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== ARCHIVE ENDPOINTS ====================

    @router.get("/api/projects/{project_id}/archives/{session_id}")
    @handle_exceptions("list session archives")
    async def list_session_archives(project_id: str, session_id: str, limit: int = 50, offset: int = 0):
        """List archives for a session within a project, paginated."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return await webui.coordinator.get_archives(session_id, limit=limit, offset=offset)

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/messages")
    @handle_exceptions("get archive messages")
    async def get_archive_messages(
        project_id: str, session_id: str, archive_id: str,
        limit: int | None = 50, offset: int = 0
    ):
        """Get paginated messages from an archive."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        result = await webui.coordinator.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit
        )
        return result

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/state")
    @handle_exceptions("get archive state")
    async def get_archive_state(project_id: str, session_id: str, archive_id: str):
        """Get archive state and disposal metadata."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        result = await webui.coordinator.get_archive_state(session_id, archive_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Archive not found")
        return result

    @router.get(
        "/api/projects/{project_id}/archives/{session_id}/{archive_id}/resources"
    )
    @handle_exceptions("get archive resources")
    async def get_archive_resources(
        project_id: str,
        session_id: str,
        archive_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ):
        """List resource metadata from an archive, paginated with optional filter/sort."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return await webui.coordinator.get_archive_resources(
            session_id,
            archive_id,
            limit=limit,
            offset=offset,
            search=search,
            format_filter=format_filter,
            sort=sort,
        )

    @router.get(
        "/api/projects/{project_id}/archives/{session_id}/{archive_id}"
        "/resources/{resource_id}"
    )
    @handle_exceptions("get archive resource file")
    async def get_archive_resource_file(
        project_id: str, session_id: str, archive_id: str, resource_id: str
    ):
        """Get raw file data for a resource in an archive."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")

        result = await webui.coordinator.get_archive_resources(
            session_id, archive_id, limit=10000
        )
        resource_meta = next(
            (r for r in result["resources"] if r.get("resource_id") == resource_id), None
        )
        if not resource_meta:
            raise HTTPException(status_code=404, detail="Resource not found")

        resource_bytes = await webui.coordinator.get_archive_resource_file(
            session_id, archive_id, resource_id
        )
        if not resource_bytes:
            raise HTTPException(
                status_code=404, detail="Resource file not found"
            )

        content_type = resource_meta.get(
            "mime_type", "application/octet-stream"
        )
        original_name = resource_meta.get(
            "original_name", f"{resource_id}.bin"
        )

        return Response(
            content=resource_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{original_name}"'
            },
        )

    @router.get("/api/projects/{project_id}/deleted-agents")
    @handle_exceptions("list deleted agents")
    async def list_deleted_agents(project_id: str, limit: int = 50, offset: int = 0):
        """List deleted agents with archives for a project, paginated."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return await webui.coordinator.list_project_deleted_agents(project_id, limit=limit, offset=offset)

    @router.delete("/api/sessions/{session_id}/archives")
    @handle_exceptions("erase session archives")
    async def erase_session_archives(session_id: str):
        """Erase archive data for a session."""
        success = await webui.coordinator.erase_archives(session_id)
        return {"success": success}

    @router.get("/api/sessions/{session_id}/history-archives-status")
    @handle_exceptions("get history archives status")
    async def get_history_archives_status(session_id: str):
        """Check if history and/or archives exist for a session."""
        return await webui.coordinator.check_history_archives(session_id)

    return router
