"""File upload and resource endpoints for sessions."""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..exception_handlers import handle_exceptions
from ..file_upload import FileUploadError, FileUploadManager

logger = logging.getLogger(__name__)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== FILE UPLOAD ENDPOINTS ====================

    @router.post("/api/sessions/{session_id}/files")
    @handle_exceptions("upload file")
    async def upload_file(session_id: str, file: Annotated[UploadFile, File(...)]):
        """
        Upload a file for a session.

        Files are stored in data/sessions/{session_id}/attachments/
        and paths are passed to Claude for reading via the Read tool.
        """
        # Verify session exists
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Initialize file upload manager if not already done
        file_manager = FileUploadManager(webui.coordinator.data_dir / "sessions")

        # Read file content
        file_content = await file.read()

        # Upload file
        try:
            file_info = await file_manager.upload_file(
                session_id=session_id,
                filename=file.filename,
                file_data=file_content,
                content_type=file.content_type
            )
        except FileUploadError as e:
            logger.warning(f"File upload validation failed: {e.message}")
            raise HTTPException(status_code=400, detail=e.message) from e

        # Register path for auto-approve (via session coordinator)
        await webui.coordinator.register_uploaded_file(session_id, file_info.stored_path)

        # Issue #404: Auto-register all uploaded files to resource gallery
        resource_meta = None
        try:
            resource_meta = await webui.coordinator.register_uploaded_resource(
                session_id=session_id,
                file_path=file_info.stored_path,
                title=file_info.original_name,
                description="Uploaded by user"
            )
            logger.info(
                f"Auto-registered uploaded file to gallery: "
                f"{file_info.original_name}"
            )
        except Exception as e:
            # Don't fail the upload if resource registration fails
            logger.warning(
                f"Failed to register uploaded file to gallery: {e}"
            )

        response = {
            "success": True,
            "file": file_info.to_dict()
        }
        # Issue #774: Include resource metadata for attachment summaries
        if resource_meta:
            response["file"]["resource_id"] = resource_meta["resource_id"]
            response["file"]["markdown"] = resource_meta["markdown"]
        return response

    @router.get("/api/sessions/{session_id}/files")
    @handle_exceptions("list session files")
    async def list_session_files(session_id: str, limit: int = 100, offset: int = 0):
        """List uploaded files for a session, paginated"""
        # Verify session exists
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        file_manager = FileUploadManager(webui.coordinator.data_dir / "sessions")
        all_files = await file_manager.list_files(session_id)
        all_dicts = [f.to_dict() for f in all_files]
        total = len(all_dicts)
        sliced = all_dicts[offset : offset + limit]
        return {
            "files": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    @router.delete("/api/sessions/{session_id}/files/{file_id}")
    @handle_exceptions("delete file")
    async def delete_file(session_id: str, file_id: str):
        """Delete an uploaded file"""
        # Verify session exists
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        file_manager = FileUploadManager(webui.coordinator.data_dir / "sessions")

        # Get file info before deleting (to unregister path)
        file_info = await file_manager.get_file_info(session_id, file_id)
        if file_info:
            # Unregister from auto-approve
            await webui.coordinator.unregister_uploaded_file(session_id, file_info.stored_path)

        success = await file_manager.delete_file(session_id, file_id)
        if not success:
            raise HTTPException(status_code=404, detail="File not found")

        return {"success": True}

    # Issue #404: Resource gallery endpoints
    @router.get("/api/sessions/{session_id}/resources")
    @handle_exceptions("get session resources")
    async def get_session_resources(
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ):
        """Get resource metadata for a session, paginated with optional filter/sort"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await webui.coordinator.get_session_resources(
            session_id,
            limit=limit,
            offset=offset,
            search=search,
            format_filter=format_filter,
            sort=sort,
        )

    @router.get("/api/sessions/{session_id}/resources/{resource_id}")
    @handle_exceptions("get session resource")
    async def get_session_resource(session_id: str, resource_id: str):
        """Get raw file data for a specific resource"""
        from fastapi.responses import Response

        # Get resource metadata to determine content type
        result = await webui.coordinator.get_session_resources(session_id, limit=10000)
        resource_meta = next(
            (r for r in result["resources"] if r.get("resource_id") == resource_id), None
        )

        if not resource_meta:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Get resource bytes
        resource_bytes = await webui.coordinator.get_session_resource_file(session_id, resource_id)
        if not resource_bytes:
            raise HTTPException(status_code=404, detail="Resource file not found")

        # Use mime_type from metadata, fallback to octet-stream
        content_type = resource_meta.get("mime_type", "application/octet-stream")
        original_name = resource_meta.get("original_name", f"{resource_id}.bin")

        return Response(
            content=resource_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{original_name}"'
            }
        )

    @router.get("/api/sessions/{session_id}/resources/{resource_id}/download")
    @handle_exceptions("download session resource")
    async def download_session_resource(session_id: str, resource_id: str):
        """Download a resource file"""
        from fastapi.responses import Response

        # Get resource metadata
        result = await webui.coordinator.get_session_resources(session_id, limit=10000)
        resource_meta = next(
            (r for r in result["resources"] if r.get("resource_id") == resource_id), None
        )

        if not resource_meta:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Get resource bytes
        resource_bytes = await webui.coordinator.get_session_resource_file(session_id, resource_id)
        if not resource_bytes:
            raise HTTPException(status_code=404, detail="Resource file not found")

        content_type = resource_meta.get("mime_type", "application/octet-stream")
        original_name = resource_meta.get("original_name", f"{resource_id}.bin")

        return Response(
            content=resource_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{original_name}"'
            }
        )

    # Issue #820: Serve session /tmp files directly (for containerized agents)
    @router.get("/api/sessions/{session_id}/tmp/{path:path}")
    @handle_exceptions("get session tmp file")
    async def get_session_tmp_file(session_id: str, path: str):
        """Serve a file from the session's /tmp directory (for containerized agents)."""
        import mimetypes

        from fastapi.responses import FileResponse

        # Validate session exists
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        tmp_dir = os.path.realpath(webui.coordinator.data_dir / "sessions" / session_id / "tmp")
        requested = os.path.realpath(os.path.join(tmp_dir, path))

        # Security: canonical path must remain within session tmp dir
        if not requested.startswith(tmp_dir + os.sep) and requested != tmp_dir:
            raise HTTPException(status_code=403, detail="Access denied")

        requested_path = Path(requested)
        if not requested_path.exists() or not requested_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        media_type, _ = mimetypes.guess_type(str(requested_path))
        return FileResponse(
            path=str(requested_path),
            media_type=media_type or "application/octet-stream",
            filename=requested_path.name,
        )

    # Issue #423: Remove resource from session display (soft-remove)
    @router.delete("/api/sessions/{session_id}/resources/{resource_id}")
    @handle_exceptions("remove session resource")
    async def remove_session_resource(session_id: str, resource_id: str):
        """Soft-remove a resource from the session display (file is preserved)"""
        success = await webui.coordinator.remove_session_resource(session_id, resource_id)
        if not success:
            raise HTTPException(status_code=404, detail="Resource not found or removal failed")

        # Append removal to session poll queue
        if session_id in webui.session_queues:
            webui.session_queues[session_id].append({
                "type": "resource_removed",
                "resource_id": resource_id,
            })

        return {"status": "ok", "resource_id": resource_id}

    # Issue #404: Legacy image endpoints (backward compatibility)
    @router.get("/api/sessions/{session_id}/images")
    @handle_exceptions("get session images")
    async def get_session_images(session_id: str, limit: int = 100, offset: int = 0):
        """Get image metadata for a session, paginated (deprecated, use /resources)"""
        return await webui.coordinator.get_session_images(session_id, limit=limit, offset=offset)

    @router.get("/api/sessions/{session_id}/images/{image_id}")
    @handle_exceptions("get session image")
    async def get_session_image(session_id: str, image_id: str):
        """Get raw image data for a specific image (deprecated, use /resources)"""
        from fastapi.responses import Response

        # Get image metadata to determine content type
        result = await webui.coordinator.get_session_images(session_id, limit=10000)
        image_meta = next(
            (img for img in result["images"] if img.get("image_id") == image_id), None
        )

        if not image_meta:
            raise HTTPException(status_code=404, detail="Image not found")

        # Get image bytes
        image_bytes = await webui.coordinator.get_session_image_file(session_id, image_id)
        if not image_bytes:
            raise HTTPException(status_code=404, detail="Image file not found")

        # Determine content type from format
        format_to_mime = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif"
        }
        content_type = format_to_mime.get(image_meta.get("format", "png"), "image/png")

        return Response(
            content=image_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": (
                    f'inline; filename="{image_id}.{image_meta.get("format", "png")}"'
                )
            }
        )

    return router
