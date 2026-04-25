"""Schedule endpoints: /api/legions/{legion_id}/schedules"""

import logging

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions
from ._models import ScheduleCreateRequest, ScheduleUpdateRequest

logger = logging.getLogger(__name__)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/legions/{legion_id}/schedules")
    @handle_exceptions("list schedules")
    async def list_schedules(
        legion_id: str, minion_id: str | None = None, status: str | None = None,
        limit: int = 100, offset: int = 0
    ):
        """List schedules for a legion with optional filters, paginated."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        status_filter = None
        if status:
            from src.models.schedule_models import ScheduleStatus
            try:
                status_filter = ScheduleStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}. Use active, paused, or cancelled",
                ) from None

        all_schedules = await webui.coordinator.legion_system.scheduler_service.list_schedules(
            legion_id=legion_id, minion_id=minion_id, status=status_filter
        )
        total = len(all_schedules)
        sliced = all_schedules[offset : offset + limit]
        return {
            "schedules": [s.to_dict() for s in sliced],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    @router.post("/api/legions/{legion_id}/schedules")
    @handle_exceptions("create schedule", value_error_status=400)
    async def create_schedule(legion_id: str, request: ScheduleCreateRequest):
        """Create a new schedule (permanent or ephemeral)."""
        if not await webui.service.validate_project_exists(legion_id):
            raise HTTPException(status_code=404, detail="Project not found")

        # Determine mode: permanent (minion_id) or ephemeral (session_config)
        minion_id = request.minion_id
        minion_name = None

        ephemeral_agent_id = None

        if minion_id:
            # Permanent mode: resolve minion name
            minion_name = await webui.service.get_minion_name(minion_id)
            if minion_name is None and not await webui.service.get_session_exists(minion_id):
                raise HTTPException(status_code=404, detail="Minion not found")
            minion_name = (minion_name or minion_id[:8])
        elif request.session_config:
            # Ephemeral mode: create the persistent agent session up front
            ephemeral_agent_id = (
                await webui.coordinator.create_ephemeral_session(
                    session_config=request.session_config,
                    schedule_name=request.name,
                    project_id=legion_id,
                )
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Either minion_id or session_config is required",
            )

        schedule = await webui.coordinator.legion_system.scheduler_service.create_schedule(
            legion_id=legion_id,
            name=request.name,
            cron_expression=request.cron_expression,
            prompt=request.prompt,
            minion_id=minion_id,
            minion_name=minion_name,
            reset_session=request.reset_session,
            max_retries=request.max_retries,
            timeout_seconds=request.timeout_seconds,
            session_config=request.session_config,
            ephemeral_agent_id=ephemeral_agent_id,
        )
        return {"schedule": schedule.to_dict()}

    @router.get("/api/legions/{legion_id}/schedules/{schedule_id}")
    @handle_exceptions("get schedule")
    async def get_schedule(legion_id: str, schedule_id: str):
        """Get a single schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"schedule": schedule.to_dict()}

    @router.put("/api/legions/{legion_id}/schedules/{schedule_id}")
    @handle_exceptions("update schedule", value_error_status=400)
    async def update_schedule(
        legion_id: str, schedule_id: str, request: ScheduleUpdateRequest
    ):
        """Update schedule fields."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        fields = {k: v for k, v in request.model_dump().items() if v is not None}
        updated = await webui.coordinator.legion_system.scheduler_service.update_schedule(
            schedule_id, **fields
        )
        return {"schedule": updated.to_dict()}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/pause")
    @handle_exceptions("pause schedule", value_error_status=400)
    async def pause_schedule(legion_id: str, schedule_id: str):
        """Pause an active schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        updated = await webui.coordinator.legion_system.scheduler_service.pause_schedule(
            schedule_id
        )
        return {"schedule": updated.to_dict()}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/resume")
    @handle_exceptions("resume schedule", value_error_status=400)
    async def resume_schedule(legion_id: str, schedule_id: str):
        """Resume a paused schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        updated = await webui.coordinator.legion_system.scheduler_service.resume_schedule(
            schedule_id
        )
        return {"schedule": updated.to_dict()}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/cancel")
    @handle_exceptions("cancel schedule", value_error_status=400)
    async def cancel_schedule(legion_id: str, schedule_id: str):
        """Cancel a schedule permanently."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        updated = await webui.coordinator.legion_system.scheduler_service.cancel_schedule(
            schedule_id
        )
        return {"schedule": updated.to_dict()}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/run-now")
    @handle_exceptions("run schedule now", value_error_status=400)
    async def run_schedule_now(legion_id: str, schedule_id: str):
        """Manually trigger a schedule execution immediately."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        try:
            result = await webui.coordinator.legion_system.scheduler_service.run_now(
                schedule_id
            )
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return result

    @router.delete("/api/legions/{legion_id}/schedules/{schedule_id}")
    @handle_exceptions("delete schedule", value_error_status=400)
    async def delete_schedule(
        legion_id: str, schedule_id: str, delete_agent: bool = False
    ):
        """Delete a schedule entirely. Optionally delete its ephemeral agent."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        # Optionally delete the ephemeral agent session
        if delete_agent and schedule.ephemeral_agent_id:
            try:
                await webui.coordinator.delete_session(
                    schedule.ephemeral_agent_id,
                    archive_reason="schedule_deleted",
                )
            except Exception as e:
                logger.warning(
                    f"Failed to delete ephemeral agent "
                    f"{schedule.ephemeral_agent_id}: {e}"
                )

        await webui.coordinator.legion_system.scheduler_service.delete_schedule(
            schedule_id
        )
        return {"success": True}

    @router.get("/api/legions/{legion_id}/schedules/{schedule_id}/history")
    @handle_exceptions("get schedule history")
    async def get_schedule_history(
        legion_id: str, schedule_id: str, limit: int = 50, offset: int = 0
    ):
        """Get execution history for a schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        executions = (
            await webui.coordinator.legion_system.scheduler_service.get_schedule_history(
                legion_id=legion_id,
                schedule_id=schedule_id,
                limit=limit,
                offset=offset,
            )
        )
        return {
            "executions": [e.to_dict() for e in executions],
            "limit": limit,
            "offset": offset,
        }

    return router
