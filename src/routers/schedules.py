"""Schedule endpoints: /api/legions/{legion_id}/schedules"""

import logging

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions
from ..legion.scheduler_service import _ScheduleAutoDeletedError
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
                    detail=f"Invalid status: {status}. Use active or paused",
                ) from None

        svc = webui.coordinator.legion_system.scheduler_service
        all_schedules = await svc.list_schedules(
            legion_id=legion_id, minion_id=minion_id, status=status_filter
        )
        total = len(all_schedules)
        sliced = all_schedules[offset : offset + limit]
        return {
            "schedules": [await svc.schedule_to_api_dict(s) for s in sliced],
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

        # Validate schedule_type-specific required fields
        schedule_type = request.schedule_type
        if schedule_type not in ("prompt", "script"):
            raise HTTPException(status_code=400, detail="schedule_type must be 'prompt' or 'script'")
        if schedule_type == "script":
            if not (request.script_command or "").strip():
                raise HTTPException(status_code=400, detail="script_command is required for script schedules")
        else:
            if not (request.prompt or "").strip():
                raise HTTPException(status_code=400, detail="prompt is required for prompt schedules")

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

        svc = webui.coordinator.legion_system.scheduler_service
        schedule = await svc.create_schedule(
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
            schedule_type=schedule_type,
            script_command=request.script_command,
            script_timeout_seconds=request.script_timeout_seconds,
            repeat_count=request.repeat_count,
        )
        return {"schedule": await svc.schedule_to_api_dict(schedule)}

    @router.get("/api/legions/{legion_id}/schedules/{schedule_id}")
    @handle_exceptions("get schedule")
    async def get_schedule(legion_id: str, schedule_id: str):
        """Get a single schedule."""
        svc = webui.coordinator.legion_system.scheduler_service
        schedule = await svc.get_schedule(schedule_id)
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"schedule": await svc.schedule_to_api_dict(schedule)}

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

        # Reject schedule_type changes (immutable post-create)
        raw = request.model_dump()
        if "schedule_type" in raw and raw["schedule_type"] is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot change schedule type after creation. Delete and recreate.",
            )

        # Build field set, special-casing repeat_count so explicit null ("set to unlimited")
        # propagates even though the default null-filter would drop it.
        repeat_count_set = raw.pop("repeat_count_set", False)
        fields = {k: v for k, v in raw.items() if v is not None}
        if repeat_count_set or "repeat_count" in {k for k, v in raw.items() if v is not None}:
            # Caller explicitly set repeat_count (possibly to null); include it
            fields["repeat_count"] = raw.get("repeat_count")  # may be None (unlimited)

        svc = webui.coordinator.legion_system.scheduler_service
        try:
            updated = await svc.update_schedule(schedule_id, **fields)
        except _ScheduleAutoDeletedError as exc:
            # Edit-time recompute triggered auto-delete; return snapshot with deleted=True
            return {"schedule": await svc.schedule_to_api_dict(exc.schedule), "deleted": True}
        return {"schedule": await svc.schedule_to_api_dict(updated)}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/pause")
    @handle_exceptions("pause schedule", value_error_status=400)
    async def pause_schedule(legion_id: str, schedule_id: str):
        """Pause an active schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        svc = webui.coordinator.legion_system.scheduler_service
        updated = await svc.pause_schedule(schedule_id)
        return {"schedule": await svc.schedule_to_api_dict(updated)}

    @router.post("/api/legions/{legion_id}/schedules/{schedule_id}/resume")
    @handle_exceptions("resume schedule", value_error_status=400)
    async def resume_schedule(legion_id: str, schedule_id: str):
        """Resume a paused schedule."""
        schedule = await webui.coordinator.legion_system.scheduler_service.get_schedule(
            schedule_id
        )
        if not schedule or schedule.legion_id != legion_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        svc = webui.coordinator.legion_system.scheduler_service
        updated = await svc.resume_schedule(schedule_id)
        return {"schedule": await svc.schedule_to_api_dict(updated)}

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
