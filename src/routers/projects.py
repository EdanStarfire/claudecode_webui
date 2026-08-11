"""Project endpoints: /api/projects*"""

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions
from ._models import (
    KanbanGroupCreateRequest,
    KanbanGroupReorderRequest,
    KanbanGroupUpdateRequest,
    ProjectCreateRequest,
    ProjectReorderRequest,
    ProjectUpdateRequest,
    SessionKanbanGroupAssignRequest,
    SessionReorderRequest,
)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== PROJECT ENDPOINTS ====================

    @router.post("/api/projects")
    @handle_exceptions("create project")
    async def create_project(request: ProjectCreateRequest):
        """Create a new project."""
        from ..config_manager import load_config
        cfg = load_config(webui.config_file) if webui.config_file else load_config()
        max_minions = request.max_concurrent_minions
        if max_minions is None:
            max_minions = cfg.legion.max_concurrent_minions
        project = await webui.service.create_project(
            name=request.name,
            working_directory=request.working_directory,
            max_concurrent_minions=max_minions,
        )
        webui._broadcast_project_updated(project)
        return {"project": project}

    @router.get("/api/projects")
    @handle_exceptions("list projects")
    async def list_projects(limit: int = 200, offset: int = 0):
        """List all projects."""
        return await webui.service.list_projects(limit=limit, offset=offset)

    @router.get("/api/projects/{project_id}")
    @handle_exceptions("get project")
    async def get_project(project_id: str):
        """Get project with sessions"""
        result = await webui.service.get_project(project_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            "project": {k: v for k, v in result.items() if k != "sessions"},
            "sessions": result.get("sessions", []),
        }

    @router.put("/api/projects/reorder")
    @handle_exceptions("reorder projects")
    async def reorder_projects(request: ProjectReorderRequest):
        """Reorder projects"""
        success = await webui.service.reorder_projects(request.project_ids)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to reorder projects")
        return {"success": True}

    @router.put("/api/projects/{project_id}")
    @handle_exceptions("update project")
    async def update_project(project_id: str, request: ProjectUpdateRequest):
        """Update project metadata"""
        result = await webui.service.update_project(
            project_id,
            name=request.name,
            is_expanded=request.is_expanded,
            max_concurrent_minions=request.max_concurrent_minions,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.delete("/api/projects/{project_id}")
    @handle_exceptions("delete project")
    async def delete_project(project_id: str):
        """Delete project and all its sessions"""
        project_result = await webui.service.get_project(project_id)
        if not project_result:
            raise HTTPException(status_code=404, detail="Project not found")

        # Delete all sessions in the project
        for session_id in project_result.get("sessions", []):
            sid = session_id if isinstance(session_id, str) else session_id.get("session_id")
            if sid:
                await webui.coordinator.delete_session(sid)

        # Delete the project
        del_result = await webui.service.delete_project(project_id)
        if not del_result.get("success"):
            raise HTTPException(status_code=500, detail="Failed to delete project")

        webui._broadcast_project_deleted(project_id)

        return {"success": True}

    @router.put("/api/projects/{project_id}/toggle-expansion")
    @handle_exceptions("toggle project expansion")
    async def toggle_project_expansion(project_id: str):
        """Toggle project expansion state"""
        result = await webui.service.toggle_project_expansion(project_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")

        webui._broadcast_project_updated(result)

        return {"success": True, "is_expanded": result.get("is_expanded")}

    @router.put("/api/projects/{project_id}/sessions/reorder")
    @handle_exceptions("reorder project sessions")
    async def reorder_project_sessions(project_id: str, request: SessionReorderRequest):
        """Reorder sessions within a project"""
        result = await webui.service.reorder_project_sessions(project_id, request.session_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to reorder sessions")

        webui._broadcast_project_updated(result)

        return {"success": True}

    # ==================== KANBAN GROUP ENDPOINTS (issue #1722) ====================

    @router.post("/api/projects/{project_id}/kanban-groups")
    @handle_exceptions("create kanban group")
    async def create_kanban_group(project_id: str, request: KanbanGroupCreateRequest):
        """Create a new kanban priority group in a project.

        Returns the full project (unlike the other kanban-group endpoints) so the
        caller has the server-generated group_id immediately, without waiting on the
        broadcast poll event, for use by any immediate follow-up action (e.g. assign).
        """
        result = await webui.service.create_kanban_group(project_id, request.name)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")

        webui._broadcast_project_updated(result)

        return {"success": True, "project": result}

    # Registered before the /{group_id} routes below: FastAPI matches path routes in
    # registration order, and "/kanban-groups/reorder" would otherwise be swallowed by
    # the "/kanban-groups/{group_id}" pattern (treating "reorder" as a group_id).
    @router.put("/api/projects/{project_id}/kanban-groups/reorder")
    @handle_exceptions("reorder kanban groups")
    async def reorder_kanban_groups(project_id: str, request: KanbanGroupReorderRequest):
        """Reorder kanban priority groups"""
        result = await webui.service.reorder_kanban_groups(project_id, request.group_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to reorder kanban groups")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.put("/api/projects/{project_id}/kanban-groups/{group_id}")
    @handle_exceptions("rename kanban group")
    async def rename_kanban_group(project_id: str, group_id: str, request: KanbanGroupUpdateRequest):
        """Rename a kanban priority group"""
        result = await webui.service.rename_kanban_group(project_id, group_id, request.name)
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.delete("/api/projects/{project_id}/kanban-groups/{group_id}")
    @handle_exceptions("delete kanban group")
    async def delete_kanban_group(project_id: str, group_id: str):
        """Delete a kanban priority group; assigned sessions fall back to Unassigned"""
        result = await webui.service.delete_kanban_group(project_id, group_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.put("/api/projects/{project_id}/sessions/{session_id}/kanban-group")
    @handle_exceptions("assign session kanban group")
    async def assign_session_kanban_group(
        project_id: str, session_id: str, request: SessionKanbanGroupAssignRequest
    ):
        """Move a session into a kanban group, or clear its assignment (Unassigned)"""
        result = await webui.service.assign_session_kanban_group(
            project_id, session_id, request.group_id
        )
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    return router
