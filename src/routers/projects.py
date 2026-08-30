"""Project endpoints: /api/projects*"""

from fastapi import APIRouter, HTTPException, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..session_backend import BackendMode
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

    def _is_remote() -> bool:
        return webui.coordinator.backend_mode == BackendMode.REMOTE

    # ==================== PROJECT ENDPOINTS ====================
    # Issue #498: project/session hierarchy lives wherever the session actually
    # runs — Pattern A wholesale relay when REMOTE is configured. The Hub's own
    # UI-broadcast side effects (_broadcast_project_updated/_deleted) are skipped
    # on the relay path — same accepted gap as the rest of the global UI-poll
    # stream not being relayed (see Batch 1 completion notes).

    @router.post("/projects")
    @handle_exceptions("create project")
    async def create_project(request: ProjectCreateRequest, http_request: Request):
        """Create a new project."""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
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

    @router.get("/projects")
    @handle_exceptions("list projects")
    async def list_projects(request: Request, limit: int = 200, offset: int = 0):
        """List all projects."""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, request)
        return await webui.service.list_projects(limit=limit, offset=offset)

    @router.get("/projects/{project_id}")
    @handle_exceptions("get project")
    async def get_project(project_id: str, request: Request):
        """Get project with sessions"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, request)
        result = await webui.service.get_project(project_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            "project": {k: v for k, v in result.items() if k != "sessions"},
            "sessions": result.get("sessions", []),
        }

    @router.put("/projects/reorder")
    @handle_exceptions("reorder projects")
    async def reorder_projects(request: ProjectReorderRequest, http_request: Request):
        """Reorder projects"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        success = await webui.service.reorder_projects(request.project_ids)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to reorder projects")
        return {"success": True}

    @router.put("/projects/{project_id}")
    @handle_exceptions("update project")
    async def update_project(project_id: str, request: ProjectUpdateRequest, http_request: Request):
        """Update project metadata"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
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

    @router.delete("/projects/{project_id}")
    @handle_exceptions("delete project")
    async def delete_project(project_id: str, request: Request):
        """Delete project and all its sessions"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, request)
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

    @router.put("/projects/{project_id}/toggle-expansion")
    @handle_exceptions("toggle project expansion")
    async def toggle_project_expansion(project_id: str, request: Request):
        """Toggle project expansion state"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, request)
        result = await webui.service.toggle_project_expansion(project_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")

        webui._broadcast_project_updated(result)

        return {"success": True, "is_expanded": result.get("is_expanded")}

    @router.put("/projects/{project_id}/sessions/reorder")
    @handle_exceptions("reorder project sessions")
    async def reorder_project_sessions(
        project_id: str, request: SessionReorderRequest, http_request: Request
    ):
        """Reorder sessions within a project"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        result = await webui.service.reorder_project_sessions(project_id, request.session_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to reorder sessions")

        webui._broadcast_project_updated(result)

        return {"success": True}

    # ==================== KANBAN GROUP ENDPOINTS (issue #1722) ====================

    @router.post("/projects/{project_id}/kanban-groups")
    @handle_exceptions("create kanban group")
    async def create_kanban_group(
        project_id: str, request: KanbanGroupCreateRequest, http_request: Request
    ):
        """Create a new kanban priority group in a project.

        Returns the full project (unlike the other kanban-group endpoints) so the
        caller has the server-generated group_id immediately, without waiting on the
        broadcast poll event, for use by any immediate follow-up action (e.g. assign).
        """
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        result = await webui.service.create_kanban_group(project_id, request.name)
        if not result:
            raise HTTPException(status_code=404, detail="Project not found")

        webui._broadcast_project_updated(result)

        return {"success": True, "project": result}

    # Registered before the /{group_id} routes below: FastAPI matches path routes in
    # registration order, and "/kanban-groups/reorder" would otherwise be swallowed by
    # the "/kanban-groups/{group_id}" pattern (treating "reorder" as a group_id).
    @router.put("/projects/{project_id}/kanban-groups/reorder")
    @handle_exceptions("reorder kanban groups")
    async def reorder_kanban_groups(
        project_id: str, request: KanbanGroupReorderRequest, http_request: Request
    ):
        """Reorder kanban priority groups"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        result = await webui.service.reorder_kanban_groups(project_id, request.group_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to reorder kanban groups")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.put("/projects/{project_id}/kanban-groups/{group_id}")
    @handle_exceptions("rename kanban group")
    async def rename_kanban_group(
        project_id: str, group_id: str, request: KanbanGroupUpdateRequest, http_request: Request
    ):
        """Rename a kanban priority group"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        result = await webui.service.rename_kanban_group(project_id, group_id, request.name)
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.delete("/projects/{project_id}/kanban-groups/{group_id}")
    @handle_exceptions("delete kanban group")
    async def delete_kanban_group(project_id: str, group_id: str, request: Request):
        """Delete a kanban priority group; assigned sessions fall back to Unassigned"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, request)
        result = await webui.service.delete_kanban_group(project_id, group_id)
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    @router.put("/projects/{project_id}/sessions/{session_id}/kanban-group")
    @handle_exceptions("assign session kanban group")
    async def assign_session_kanban_group(
        project_id: str,
        session_id: str,
        request: SessionKanbanGroupAssignRequest,
        http_request: Request,
    ):
        """Move a session into a kanban group, or clear its assignment (Unassigned)"""
        if _is_remote():
            return await relay_client.forward(webui.coordinator, http_request)
        result = await webui.service.assign_session_kanban_group(
            project_id, session_id, request.group_id
        )
        if not result:
            raise HTTPException(status_code=404, detail="Project or kanban group not found")

        webui._broadcast_project_updated(result)

        return {"success": True}

    return router
