"""Fleet control endpoints: halt-all, resume-all"""

from fastapi import APIRouter, HTTPException, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..session_backend import BackendMode


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # Issue #498: relay the whole call to REMOTE, which fans out to its own
    # local minions itself — no split-backend legion concern given the
    # global-switch scoping (there's only ever one active backend to route to).

    @router.post("/legions/{legion_id}/halt-all")
    @handle_exceptions("emergency halt all")
    async def emergency_halt_all(legion_id: str, request: Request):
        """Emergency halt all minions in the project (issue #313: universal Legion)"""
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, request)
        # Issue #313: All projects support halt-all - verify project exists
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Call LegionCoordinator.emergency_halt_all() (no-op if no minions)
        result = await webui.coordinator.legion_system.legion_coordinator.emergency_halt_all(legion_id)

        return {
            "success": True,
            "stopped_session_ids": result["stopped_session_ids"],
            "failed_sessions": result["failed_sessions"],
            "total_sessions": result["total_sessions"],
        }

    @router.post("/legions/{legion_id}/resume-all")
    @handle_exceptions("resume all")
    async def resume_all(legion_id: str, request: Request):
        """Resume all minions in the project (issue #313: universal Legion)"""
        if webui.coordinator.backend_mode == BackendMode.REMOTE:
            return await relay_client.forward(webui.coordinator, request)
        # Issue #313: All projects support resume-all - verify project exists
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Call LegionCoordinator.resume_all() (no-op if no minions)
        result = await webui.coordinator.legion_system.legion_coordinator.resume_all(legion_id)

        return {
            "success": True,
            "resumed_count": result["resumed_count"],
            "failed_minions": result["failed_minions"],
            "total_minions": result["total_minions"]
        }

    return router
