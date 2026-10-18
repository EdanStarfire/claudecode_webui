"""Archive endpoints: /api/projects/{id}/archives/*, /api/projects/{id}/deleted-agents,
/api/sessions/{id}/archives, /api/sessions/{id}/history-archives-status"""

from fastapi import APIRouter, HTTPException, Request

from shared.exception_handlers import handle_exceptions

from ..http_range import build_resource_response


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== ARCHIVE ENDPOINTS ====================

    @router.get("/api/projects/{project_id}/archives/{session_id}")
    @handle_exceptions("list session archives")
    async def list_session_archives(project_id: str, session_id: str, limit: int = 50, offset: int = 0):
        """List archives for a session within a project, paginated."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return await webui.service.get_archives(session_id, limit=limit, offset=offset)

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/messages")
    @handle_exceptions("get archive messages")
    async def get_archive_messages(
        project_id: str, session_id: str, archive_id: str,
        limit: int | None = 50, offset: int = 0
    ):
        """Get paginated messages from an archive."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        result = await webui.service.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit
        )
        return result

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/state")
    @handle_exceptions("get archive state")
    async def get_archive_state(project_id: str, session_id: str, archive_id: str):
        """Get archive state and disposal metadata."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        result = await webui.service.get_archive_state(session_id, archive_id)
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
        return await webui.service.get_archive_resources(
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
        project_id: str, session_id: str, archive_id: str, resource_id: str, request: Request
    ):
        """Get raw file data for a resource in an archive."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")

        resource_meta = await webui.service.get_archive_resource_by_id(
            session_id, archive_id, resource_id
        )
        if not resource_meta:
            raise HTTPException(status_code=404, detail="Resource not found")

        resource_bytes = await webui.service.get_archive_resource_file(
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

        return build_resource_response(
            request,
            content=resource_bytes,
            media_type=content_type,
            filename=original_name,
            disposition="inline",
        )

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/proxy-logs")
    @handle_exceptions("get archive proxy logs")
    async def get_archive_proxy_logs(project_id: str, session_id: str, archive_id: str):
        """List archived proxy log files with size and line count."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_proxy_logs(session_id, archive_id)

    @router.get(
        "/api/projects/{project_id}/archives/{session_id}/{archive_id}/proxy-logs/{log_name}"
    )
    @handle_exceptions("get archive proxy log content", value_error_status=400)
    async def get_archive_proxy_log_content(
        project_id: str,
        session_id: str,
        archive_id: str,
        log_name: str,
        offset: int = 0,
        limit: int = 100,
        from_end: bool = False,
    ):
        """Get a chunk of lines from an archived proxy log file."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_proxy_log_content(
            session_id, archive_id, log_name, offset=offset, limit=limit, from_end=from_end
        )

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/queue")
    @handle_exceptions("get archive queue")
    async def get_archive_queue(project_id: str, session_id: str, archive_id: str):
        """Get archived message queue entries."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_queue(session_id, archive_id)

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/attachments")
    @handle_exceptions("get archive attachments")
    async def get_archive_attachments(project_id: str, session_id: str, archive_id: str):
        """List files in an archive's attachments/ directory."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_attachments(session_id, archive_id)

    @router.get(
        "/api/projects/{project_id}/archives/{session_id}/{archive_id}/attachments/{filename}"
    )
    @handle_exceptions("get archive attachment file")
    async def get_archive_attachment_file(
        project_id: str, session_id: str, archive_id: str, filename: str, request: Request
    ):
        """Get raw file bytes for an archived attachment."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")

        result = await webui.service.get_archive_attachment_file(session_id, archive_id, filename)
        if result is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        content, content_type = result

        return build_resource_response(
            request,
            content=content,
            media_type=content_type,
            filename=filename,
            disposition="attachment",
        )

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/history")
    @handle_exceptions("get archive history listing")
    async def get_archive_history(project_id: str, session_id: str, archive_id: str):
        """List distilled history .md files in an archive."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_history(session_id, archive_id)

    @router.get(
        "/api/projects/{project_id}/archives/{session_id}/{archive_id}/history/{filename}"
    )
    @handle_exceptions("get archive history content")
    async def get_archive_history_content(
        project_id: str, session_id: str, archive_id: str, filename: str
    ):
        """Get raw markdown text for an archived history file."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")

        content = await webui.service.get_archive_history_content(session_id, archive_id, filename)
        if content is None:
            raise HTTPException(status_code=404, detail="History file not found")
        return {"content": content}

    @router.get("/api/projects/{project_id}/archives/{session_id}/{archive_id}/schedules")
    @handle_exceptions("get archive schedules")
    async def get_archive_schedules(project_id: str, session_id: str, archive_id: str):
        """Get archived schedule config and execution history."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        if not await webui.service.archive_exists(session_id, archive_id):
            raise HTTPException(status_code=404, detail="Archive not found")
        return await webui.service.get_archive_schedules(session_id, archive_id)

    @router.get("/api/projects/{project_id}/deleted-agents")
    @handle_exceptions("list deleted agents")
    async def list_deleted_agents(project_id: str, limit: int = 50, offset: int = 0):
        """List deleted agents with archives for a project, paginated."""
        if not await webui.service.validate_project_exists(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return await webui.service.list_project_deleted_agents(project_id, limit=limit, offset=offset)

    @router.delete("/api/sessions/{session_id}/archives")
    @handle_exceptions("erase session archives")
    async def erase_session_archives(session_id: str):
        """Erase archive data for a session."""
        success = await webui.service.erase_archives(session_id)
        return {"success": success}

    @router.get("/api/sessions/{session_id}/history-archives-status")
    @handle_exceptions("get history archives status")
    async def get_history_archives_status(session_id: str):
        """Check if history and/or archives exist for a session."""
        return await webui.service.check_history_archives(session_id)

    return router
