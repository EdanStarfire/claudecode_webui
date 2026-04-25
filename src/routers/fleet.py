"""Fleet control endpoints: halt-all, resume-all"""

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/legions/{legion_id}/halt-all")
    @handle_exceptions("emergency halt all")
    async def emergency_halt_all(legion_id: str):
        """Emergency halt all minions in the project (issue #313: universal Legion)"""
        # Issue #313: All projects support halt-all - verify project exists
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Call LegionCoordinator.emergency_halt_all() (no-op if no minions)
        result = await webui.coordinator.legion_system.legion_coordinator.emergency_halt_all(legion_id)

        return {
            "success": True,
            "halted_count": result["halted_count"],
            "failed_minions": result["failed_minions"],
            "total_minions": result["total_minions"]
        }

    @router.post("/api/legions/{legion_id}/resume-all")
    @handle_exceptions("resume all")
    async def resume_all(legion_id: str):
        """Resume all minions in the project (issue #313: universal Legion)"""
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
