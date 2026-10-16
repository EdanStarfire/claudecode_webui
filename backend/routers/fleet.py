"""Fleet control endpoints: halt-all, resume-all, fleet snapshot (issue #2159 stage 6c)"""

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ._models import FleetSnapshotClearRequest, FleetSnapshotRecordRequest


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
        result = await webui.service.emergency_halt_all(legion_id)

        return {
            "success": True,
            "stopped_session_ids": result["stopped_session_ids"],
            "failed_sessions": result["failed_sessions"],
            "total_sessions": result["total_sessions"],
        }

    @router.post("/api/legions/{legion_id}/resume-all")
    @handle_exceptions("resume all")
    async def resume_all(legion_id: str):
        """Resume all minions in the project (issue #313: universal Legion)"""
        # Issue #313: All projects support resume-all - verify project exists
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Call LegionCoordinator.resume_all() (no-op if no minions)
        result = await webui.service.resume_all_minions(legion_id)

        return {
            "success": True,
            "resumed_count": result["resumed_count"],
            "failed_minions": result["failed_minions"],
            "total_minions": result["total_minions"]
        }

    # Issue #2159 (stage 6c): persisted fleet halt/resume snapshot, replacing
    # frontend/src/utils/stoppedSet.js's sessionStorage-only bookkeeping — this
    # survives a browser reload and a Backend restart (T3), not just the current
    # tab's session.

    @router.get("/api/legions/{legion_id}/fleet-snapshot")
    @handle_exceptions("get fleet snapshot")
    async def get_fleet_snapshot(legion_id: str):
        """Current halted-session snapshot for this project, pruned against live
        session_ids. Replaces ProjectOverview.vue's sessionStorage
        getStoppedSet()/getProcessingSet() reads."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        snapshot = await webui.service.get_fleet_snapshot(legion_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Project not found")

        return snapshot

    @router.post("/api/legions/{legion_id}/fleet-snapshot/record")
    @handle_exceptions("record fleet snapshot")
    async def record_fleet_snapshot(legion_id: str, request: FleetSnapshotRecordRequest):
        """Merge newly-halted session ids into the persisted snapshot — used by
        Stop Selected, which halts sessions individually rather than through
        halt-all. Replaces sessionStorage addToStoppedSet()/setProcessingSet()."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        project = await webui.service.record_fleet_stopped(
            legion_id, request.session_ids, request.processing_session_ids
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        return {
            "stopped_session_ids": project.fleet_stopped_session_ids,
            "processing_session_ids": project.fleet_stopped_processing_ids,
        }

    @router.post("/api/legions/{legion_id}/fleet-snapshot/clear")
    @handle_exceptions("clear fleet snapshot")
    async def clear_fleet_snapshot(legion_id: str, request: FleetSnapshotClearRequest):
        """Remove specific session ids from the persisted snapshot — used once
        each has been successfully resumed/started outside of resume-all.
        Replaces sessionStorage removeFromStoppedSet()/setStoppedSet()."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        project = await webui.service.clear_fleet_stopped(
            legion_id, request.session_ids
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        return {
            "stopped_session_ids": project.fleet_stopped_session_ids,
            "processing_session_ids": project.fleet_stopped_processing_ids,
        }

    return router
