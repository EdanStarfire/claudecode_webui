"""
SchedulerService - Background asyncio service for cron-based schedule execution.

Ticks every 30 seconds, evaluates cron expressions via croniter, and delivers
due prompts to owning minions through SessionCoordinator.enqueue_message().
"""

import asyncio
import json
import os
import shlex
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.config_resolution import resolve_effective_config
from src.docker_utils import (
    find_session_container,
    find_session_container_any_state,
    run_command_in_container,
    run_command_on_host,
)
from src.logging_config import get_logger
from src.models.schedule_models import (
    Schedule,
    ScheduleExecution,
    ScheduleMetrics,
    ScheduleStatus,
    cap_stream,
    get_next_run,
    is_error_status,
    validate_cron_expression,
)
from src.session_manager import SessionState
from src.task_utils import task_done_log_exception

if TYPE_CHECKING:
    from src.legion_system import LegionSystem

legion_logger = get_logger("legion", "SCHEDULER")


class _ScheduleAutoDeletedError(Exception):
    """Sentinel raised by update_schedule when edit-time recompute triggers auto-delete."""

    def __init__(self, schedule: "Schedule"):
        super().__init__("schedule auto-deleted")
        self.schedule = schedule

TICK_INTERVAL = 30  # seconds between scheduler evaluations


class SchedulerService:
    """Background service that evaluates cron schedules and fires due prompts."""

    def __init__(self, system: "LegionSystem"):
        self.system = system
        self._schedules: dict[str, Schedule] = {}  # schedule_id -> Schedule
        self._task: asyncio.Task | None = None
        self._running = False
        self._schedule_broadcast_callback = None
        # Per-session inflight script tracking (issue #1356)
        self._inflight_scripts_by_session: dict[str, set[str]] = {}  # session_id -> {schedule_id}
        # Issue #1372: Per-legion metrics cache and rotation trigger counter
        self._metrics_cache: dict[str, dict[str, ScheduleMetrics]] = {}  # legion_id -> {schedule_id -> ScheduleMetrics}
        self._appends_since_rotation: dict[str, int] = {}  # legion_id -> count

    def set_schedule_broadcast_callback(self, callback):
        """Set callback for broadcasting schedule events to WebSocket clients.

        Args:
            callback: Async function(legion_id, event_data) to broadcast schedule events
        """
        self._schedule_broadcast_callback = callback

    async def start(self):
        """Start the scheduler background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        legion_logger.info("SchedulerService started")

    async def stop(self):
        """Stop the scheduler background loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        legion_logger.info("SchedulerService stopped")

    async def _scheduler_loop(self):
        """Main loop: tick every TICK_INTERVAL seconds, fire due schedules."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                legion_logger.error(f"Scheduler tick error: {e}")
            try:
                await asyncio.sleep(TICK_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _tick(self):
        """Evaluate all active schedules and fire any that are due."""
        now = datetime.now(UTC).timestamp()
        for schedule in list(self._schedules.values()):
            if schedule.status != ScheduleStatus.ACTIVE:
                continue
            if schedule.next_run is None:
                continue
            if now < schedule.next_run:
                continue

            if schedule.schedule_type == "script":
                target_id = schedule.minion_id or schedule.ephemeral_agent_id
                # Same-schedule overlap guard: skip if previous fire still in-flight
                if schedule.schedule_id in self._inflight_scripts_by_session.get(target_id, set()):
                    legion_logger.info(
                        f"Skipping script schedule {schedule.schedule_id} — "
                        "previous run still in progress"
                    )
                    schedule.next_run = get_next_run(schedule.cron_expression)
                    continue
                self._inflight_scripts_by_session.setdefault(target_id, set()).add(schedule.schedule_id)
                t = asyncio.create_task(self._fire_script_with_cleanup(schedule, target_id, now))
                t.add_done_callback(task_done_log_exception)
            else:
                await self._fire_schedule(schedule, now)

    # ── CRUD Operations ──

    async def create_schedule(
        self,
        legion_id: str,
        name: str,
        cron_expression: str,
        prompt: str = "",
        minion_id: str | None = None,
        minion_name: str | None = None,
        reset_session: bool = False,
        max_retries: int = 3,
        timeout_seconds: int = 3600,
        session_config: dict | None = None,
        ephemeral_agent_id: str | None = None,
        schedule_type: str = "prompt",
        script_command: str | None = None,
        script_timeout_seconds: int = 60,
        repeat_count: int | None = None,
    ) -> Schedule:
        """Create a new schedule.

        Either minion_id (permanent) or session_config (ephemeral) must be provided.
        For ephemeral schedules, ephemeral_agent_id should be the pre-created agent session.

        Raises:
            ValueError: If cron expression is invalid or neither mode specified.
        """
        if not validate_cron_expression(cron_expression):
            raise ValueError(f"Invalid cron expression: {cron_expression}")

        if not minion_id and not session_config:
            raise ValueError("Either minion_id (permanent) or session_config (ephemeral) is required")

        schedule = Schedule(
            schedule_id=str(uuid.uuid4()),
            legion_id=legion_id,
            minion_id=minion_id,
            minion_name=minion_name,
            name=name,
            cron_expression=cron_expression,
            prompt=prompt,
            reset_session=reset_session,
            status=ScheduleStatus.ACTIVE,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            next_run=get_next_run(cron_expression),
            session_config=session_config,
            ephemeral_agent_id=ephemeral_agent_id,
            schedule_type=schedule_type,
            script_command=script_command,
            script_timeout_seconds=script_timeout_seconds,
            repeat_count=repeat_count,
        )

        self._schedules[schedule.schedule_id] = schedule
        await self._persist_schedules(legion_id)
        await self._broadcast_schedule_event(legion_id, schedule)

        mode = "ephemeral" if session_config else "permanent"
        target = minion_id or ephemeral_agent_id or "ephemeral"
        legion_logger.info(
            f"Schedule created: {schedule.schedule_id} '{name}' "
            f"({mode}) for {target} in legion {legion_id}"
        )
        return schedule

    async def list_schedules(
        self,
        legion_id: str,
        minion_id: str | None = None,
        status: ScheduleStatus | None = None,
    ) -> list[Schedule]:
        """List schedules with optional filters."""
        results = []
        for s in self._schedules.values():
            if s.legion_id != legion_id:
                continue
            if minion_id and s.minion_id != minion_id:
                continue
            if status and s.status != status:
                continue
            results.append(s)
        return sorted(results, key=lambda s: s.created_at, reverse=True)

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        """Get a single schedule by ID."""
        return self._schedules.get(schedule_id)

    async def update_schedule(self, schedule_id: str, **fields) -> Schedule:
        """Update mutable schedule fields (name, cron_expression, prompt, max_retries, timeout_seconds).

        Raises:
            ValueError: If schedule not found or invalid cron expression.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        if "cron_expression" in fields:
            if not validate_cron_expression(fields["cron_expression"]):
                raise ValueError(f"Invalid cron expression: {fields['cron_expression']}")

        allowed = {
            "name", "cron_expression", "prompt", "max_retries", "timeout_seconds",
            "session_config", "script_command", "script_timeout_seconds", "repeat_count",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(schedule, key, value)

        # Recalculate next_run if cron changed
        if "cron_expression" in fields and schedule.status == ScheduleStatus.ACTIVE:
            schedule.next_run = get_next_run(schedule.cron_expression)

        schedule.updated_at = datetime.now(UTC).timestamp()

        # Edit-time recompute: if the new repeat_count has already been reached, auto-delete
        if "repeat_count" in fields and schedule.repeat_count is not None:
            if schedule.fire_count >= schedule.repeat_count:
                await self.delete_schedule(schedule_id)
                raise _ScheduleAutoDeletedError(schedule)

        await self._persist_schedules(schedule.legion_id)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)
        return schedule

    async def pause_schedule(self, schedule_id: str) -> Schedule:
        """Pause an active schedule.

        Raises:
            ValueError: If schedule not found or not active.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")
        if schedule.status != ScheduleStatus.ACTIVE:
            raise ValueError(f"Schedule {schedule_id} is not active (status: {schedule.status.value})")

        schedule.status = ScheduleStatus.PAUSED
        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)
        legion_logger.info(f"Schedule paused: {schedule_id}")
        return schedule

    async def resume_schedule(self, schedule_id: str) -> Schedule:
        """Resume a paused schedule.

        Raises:
            ValueError: If schedule not found or not paused.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")
        if schedule.status != ScheduleStatus.PAUSED:
            raise ValueError(
                f"Schedule {schedule_id} is not paused (status: {schedule.status.value})"
            )

        schedule.status = ScheduleStatus.ACTIVE
        schedule.next_run = get_next_run(schedule.cron_expression)
        schedule.failure_count = 0
        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)
        legion_logger.info(f"Schedule resumed: {schedule_id}")
        return schedule

    async def delete_schedule(self, schedule_id: str) -> bool:
        """Remove a schedule entirely.

        Raises:
            ValueError: If schedule not found.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        legion_id = schedule.legion_id
        del self._schedules[schedule_id]
        await self._persist_schedules(legion_id)
        await self._broadcast_schedule_event(legion_id, schedule, deleted=True)
        legion_logger.info(f"Schedule deleted: {schedule_id}")
        return True

    async def delete_schedules_for_minion(self, minion_id: str) -> int:
        """Delete all schedules owned by a minion (used on disposal / session delete).

        Returns:
            Number of schedules deleted.
        """
        deleted = 0
        affected_legions = set()
        for schedule_id, schedule in list(self._schedules.items()):
            if schedule.minion_id != minion_id:
                continue
            affected_legions.add(schedule.legion_id)
            del self._schedules[schedule_id]
            await self._broadcast_schedule_event(schedule.legion_id, schedule, deleted=True)
            deleted += 1

        for legion_id in affected_legions:
            await self._persist_schedules(legion_id)

        if deleted > 0:
            legion_logger.info(
                f"Deleted {deleted} schedules for disposed minion {minion_id}"
            )
        return deleted

    async def get_schedule_history(
        self,
        legion_id: str,
        schedule_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScheduleExecution]:
        """Read execution history from schedule_history.jsonl."""
        data_dir = self.system.session_coordinator.data_dir
        history_file = data_dir / "legions" / legion_id / "schedule_history.jsonl"
        if not history_file.exists():
            return []

        executions = []
        try:
            with open(history_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if schedule_id and data.get("schedule_id") != schedule_id:
                        continue
                    executions.append(ScheduleExecution.from_dict(data))
        except Exception as e:
            legion_logger.error(f"Error reading schedule history for legion {legion_id}: {e}")
            return []

        # Return newest first, with pagination
        executions.reverse()
        return executions[offset : offset + limit]

    # ── Manual Trigger ──

    async def run_now(self, schedule_id: str) -> dict:
        """Manually trigger a schedule execution immediately.

        Reuses existing fire logic with trigger="manual".
        Does NOT recalculate next_run — preserves cron schedule.

        Raises:
            ValueError: If schedule not found or cancelled.
            RuntimeError: If ephemeral agent is currently active.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        # For ephemeral schedules, check if agent is currently active
        if schedule.session_config is not None and schedule.ephemeral_agent_id:
            session_info = (
                await self.system.session_coordinator.session_manager.get_session_info(
                    schedule.ephemeral_agent_id
                )
            )
            if session_info and session_info.state.value in ("active", "starting"):
                raise RuntimeError(
                    f"Ephemeral agent {schedule.ephemeral_agent_id} is currently active"
                )

        now = datetime.now(UTC).timestamp()

        # Save next_run before firing (fire methods recalculate it for cron)
        saved_next_run = schedule.next_run

        if schedule.schedule_type == "script":
            target_id = schedule.minion_id or schedule.ephemeral_agent_id
            self._inflight_scripts_by_session.setdefault(target_id, set()).add(schedule.schedule_id)
            t = asyncio.create_task(
                self._fire_script_with_cleanup(schedule, target_id, now, trigger="manual")
            )
            t.add_done_callback(task_done_log_exception)
        else:
            await self._fire_schedule(schedule, now, trigger="manual")

        # Restore next_run to preserve cron schedule
        schedule.next_run = saved_next_run
        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)

        legion_logger.info(f"Schedule {schedule_id} manually triggered")
        return {"status": "queued", "schedule_id": schedule_id}

    # ── Execution ──

    async def _fire_schedule(self, schedule: Schedule, now: float, trigger: str = "cron"):
        """Fire a due schedule — delegates to permanent or ephemeral path."""
        if schedule.session_config is not None:
            await self._fire_ephemeral_schedule(schedule, now, trigger=trigger)
        else:
            await self._fire_permanent_schedule(schedule, now, trigger=trigger)

    async def _fire_permanent_schedule(self, schedule: Schedule, now: float, trigger: str = "cron"):
        """Fire a permanent schedule by enqueuing the prompt to an existing minion."""
        legion_logger.info(
            f"Firing schedule {schedule.schedule_id} '{schedule.name}' "
            f"for minion {schedule.minion_id}"
        )

        # Increment fire_count before work so crashes still consume the count (issue #1538)
        schedule.fire_count += 1
        await self._persist_schedules(schedule.legion_id)

        # Get minion state for execution record
        minion_state = "unknown"
        try:
            session_info = (
                await self.system.session_coordinator.session_manager.get_session_info(
                    schedule.minion_id
                )
            )
            if session_info:
                minion_state = session_info.state.value
        except Exception:
            pass

        formatted_prompt = (
            f"**[Scheduled Task: {schedule.name}]**\n\n"
            f"{schedule.prompt}"
        )

        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_time=schedule.next_run or now,
            actual_time=now,
            status="queued",
            minion_state=minion_state,
            trigger=trigger,
        )

        try:
            result = await self.system.session_coordinator.enqueue_message(
                session_id=schedule.minion_id,
                content=formatted_prompt,
                reset_session=schedule.reset_session,
                metadata={
                    "source": trigger,
                    "schedule_id": schedule.schedule_id,
                    "schedule_name": schedule.name,
                    "trigger_time": now,
                },
            )

            # Capture queue_id from response
            execution.queue_id = result.get("queue_id")

            # Success
            schedule.last_run = now
            schedule.last_status = "queued"
            schedule.execution_count += 1
            schedule.failure_count = 0
            execution.status = "queued"
            legion_logger.info(f"Schedule {schedule.schedule_id} fired successfully")

        except Exception as e:
            legion_logger.error(f"Schedule {schedule.schedule_id} fire failed: {e}")
            execution.status = "failed"
            execution.error_message = str(e)
            schedule.last_status = "failed"
            schedule.failure_count += 1

            # Retry logic
            if schedule.failure_count <= schedule.max_retries:
                await self._handle_retry(schedule)
                execution.status = "retry"
            else:
                legion_logger.warning(
                    f"Schedule {schedule.schedule_id} exceeded max retries "
                    f"({schedule.max_retries}), pausing"
                )
                schedule.status = ScheduleStatus.PAUSED
                schedule.next_run = None

        # Calculate next run (if still active and not retrying)
        if schedule.status == ScheduleStatus.ACTIVE and schedule.failure_count == 0:
            schedule.next_run = get_next_run(schedule.cron_expression)

        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)
        await self._record_execution(schedule, execution)
        await self._broadcast_execution_event(schedule.legion_id, execution)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)

        # Auto-delete when repeat_count reached (issue #1538)
        if schedule.repeat_count is not None and schedule.fire_count >= schedule.repeat_count:
            try:
                await self.delete_schedule(schedule.schedule_id)
            except ValueError:
                pass  # already deleted

    async def _fire_ephemeral_schedule(self, schedule: Schedule, now: float, trigger: str = "cron"):
        """Fire an ephemeral schedule using its static agent session.

        The agent is created once at schedule creation. On each fire:
        1. Start the terminated agent session
        2. Enqueue the prompt
        3. Monitor for completion — archive + clear + terminate on idle
        """
        agent_id = schedule.ephemeral_agent_id

        # Migration: old-format schedule without agent_id — create one now
        if not agent_id:
            agent_id = await self._migrate_ephemeral_schedule(schedule)
            if not agent_id:
                return

        # Check agent session state
        session_info = (
            await self.system.session_coordinator.session_manager.get_session_info(
                agent_id
            )
        )

        # Agent was deleted — recreate from session_config
        if not session_info:
            legion_logger.warning(
                f"Ephemeral agent {agent_id} for schedule {schedule.schedule_id} not found — recreating"
            )
            try:
                agent_id = await self.system.session_coordinator.create_ephemeral_session(
                    session_config=schedule.session_config,
                    schedule_name=schedule.name,
                    project_id=schedule.legion_id,
                )
                schedule.ephemeral_agent_id = agent_id
                await self._persist_schedules(schedule.legion_id)
            except Exception as e:
                legion_logger.error(f"Failed to recreate ephemeral agent: {e}")
                return
            session_info = (
                await self.system.session_coordinator.session_manager.get_session_info(
                    agent_id
                )
            )

        # Guard: skip if agent is currently active/processing
        if session_info and session_info.state.value in ("active", "starting"):
            legion_logger.info(
                f"Skipping ephemeral schedule {schedule.schedule_id} '{schedule.name}' — "
                f"agent {agent_id} still active"
            )
            schedule.next_run = get_next_run(schedule.cron_expression)
            schedule.updated_at = datetime.now(UTC).timestamp()
            await self._persist_schedules(schedule.legion_id)
            return

        legion_logger.info(
            f"Firing ephemeral schedule {schedule.schedule_id} '{schedule.name}' "
            f"with agent {agent_id}"
        )

        # Increment fire_count before work so crashes still consume the count (issue #1538)
        schedule.fire_count += 1
        await self._persist_schedules(schedule.legion_id)

        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_time=schedule.next_run or now,
            actual_time=now,
            status="queued",
            minion_state=session_info.state.value if session_info else "unknown",
            trigger=trigger,
        )

        try:
            # Start the terminated/created agent session
            await self.system.session_coordinator.start_session(agent_id)

            # Enqueue the scheduled prompt
            formatted_prompt = (
                f"**[Scheduled Task: {schedule.name}]**\n\n"
                f"{schedule.prompt}"
            )
            result = await self.system.session_coordinator.enqueue_message(
                session_id=agent_id,
                content=formatted_prompt,
                reset_session=False,
                metadata={
                    "source": trigger,
                    "schedule_id": schedule.schedule_id,
                    "schedule_name": schedule.name,
                    "trigger_time": now,
                    "ephemeral": True,
                },
            )

            execution.queue_id = result.get("queue_id")
            execution.minion_state = "active"
            execution.status = "queued"

            schedule.last_run = now
            schedule.last_status = "queued"
            schedule.execution_count += 1
            schedule.failure_count = 0

            # Launch monitoring task
            t = asyncio.create_task(
                self._monitor_ephemeral_session(schedule.schedule_id, agent_id, schedule.legion_id)
            )
            t.add_done_callback(task_done_log_exception)

            legion_logger.info(
                f"Ephemeral schedule {schedule.schedule_id} fired — agent {agent_id} started"
            )

        except Exception as e:
            legion_logger.error(
                f"Ephemeral schedule {schedule.schedule_id} fire failed: {e}"
            )
            execution.status = "failed"
            execution.error_message = str(e)
            schedule.last_status = "failed"
            schedule.failure_count += 1

            if schedule.failure_count <= schedule.max_retries:
                await self._handle_retry(schedule)
                execution.status = "retry"
            else:
                legion_logger.warning(
                    f"Ephemeral schedule {schedule.schedule_id} exceeded max retries, pausing"
                )
                schedule.status = ScheduleStatus.PAUSED
                schedule.next_run = None

        # Calculate next run
        if schedule.status == ScheduleStatus.ACTIVE and schedule.failure_count == 0:
            schedule.next_run = get_next_run(schedule.cron_expression)

        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)
        await self._record_execution(schedule, execution)
        await self._broadcast_execution_event(schedule.legion_id, execution)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)

        # Auto-delete when repeat_count reached (issue #1538)
        if schedule.repeat_count is not None and schedule.fire_count >= schedule.repeat_count:
            try:
                await self.delete_schedule(schedule.schedule_id)
            except ValueError:
                pass  # already deleted

    async def _migrate_ephemeral_schedule(self, schedule: Schedule) -> str | None:
        """Migrate old-format ephemeral schedule by creating a persistent agent."""
        try:
            agent_id = await self.system.session_coordinator.create_ephemeral_session(
                session_config=schedule.session_config,
                schedule_name=schedule.name,
                project_id=schedule.legion_id,
            )
            schedule.ephemeral_agent_id = agent_id
            await self._persist_schedules(schedule.legion_id)
            legion_logger.info(
                f"Migrated ephemeral schedule {schedule.schedule_id} — created agent {agent_id}"
            )
            return agent_id
        except Exception as e:
            legion_logger.error(
                f"Failed to migrate ephemeral schedule {schedule.schedule_id}: {e}"
            )
            return None

    async def _monitor_ephemeral_session(self, schedule_id: str, session_id: str, legion_id: str):
        """Monitor an ephemeral session and archive+terminate when it finishes.

        Polls every 10 seconds. Once idle for a grace period, archives session
        data (with completion timestamp), clears messages, and terminates — leaving
        the agent ready for its next scheduled fire.
        """
        poll_interval = 10  # seconds
        idle_grace = 10  # seconds of idle before cleanup
        idle_since = None

        legion_logger.info(
            f"Monitoring ephemeral session {session_id} for schedule {schedule_id}"
        )

        try:
            while True:
                await asyncio.sleep(poll_interval)

                schedule = self._schedules.get(schedule_id)
                if not schedule:
                    legion_logger.warning(
                        f"Schedule {schedule_id} no longer exists — stopping monitor"
                    )
                    break

                # Check session state
                session_info = (
                    await self.system.session_coordinator.session_manager.get_session_info(
                        session_id
                    )
                )
                if not session_info:
                    legion_logger.warning(
                        f"Ephemeral session {session_id} no longer exists"
                    )
                    break

                # If session already terminated or errored externally, just update state
                if session_info.state.value in ("terminated", "error"):
                    legion_logger.info(
                        f"Ephemeral session {session_id} is {session_info.state.value}"
                    )
                    break

                # Check if session is idle (not processing and queue empty)
                is_idle = not session_info.is_processing

                # Also check queue for pending items
                try:
                    session_dir = await self.system.session_coordinator.session_manager.get_session_directory(session_id)
                    if session_dir:
                        queue_items = await self.system.session_coordinator.queue_manager.get_queue(
                            session_id, session_dir
                        )
                        pending_items = [
                            q for q in queue_items if q.status == "pending"
                        ]
                        if pending_items:
                            is_idle = False
                except Exception:
                    pass  # If we can't check queue, use is_processing only

                if is_idle:
                    if idle_since is None:
                        idle_since = asyncio.get_event_loop().time()
                    elif asyncio.get_event_loop().time() - idle_since >= idle_grace:
                        legion_logger.info(
                            f"Ephemeral session {session_id} idle for {idle_grace}s — archiving"
                        )
                        break
                else:
                    idle_since = None

        except asyncio.CancelledError:
            legion_logger.info(f"Ephemeral monitor for {session_id} cancelled")
            return
        except Exception as e:
            legion_logger.error(
                f"Ephemeral monitor failed for schedule {schedule_id}: {e}", exc_info=True
            )
            if self._schedule_broadcast_callback:
                try:
                    await self._schedule_broadcast_callback(legion_id, {
                        "type": "schedule_monitor_error",
                        "legion_id": legion_id,
                        "schedule_id": schedule_id,
                        "error": str(e),
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception:
                    legion_logger.exception("Failed to broadcast schedule_monitor_error")

        # Archive on completion: archive data, clear messages, terminate
        try:
            await self.system.session_coordinator.archive_and_clear_session(session_id)
        except Exception as e:
            legion_logger.error(
                f"Failed to archive ephemeral session {session_id}: {e}"
            )

        # Update schedule state
        schedule = self._schedules.get(schedule_id)
        if schedule:
            schedule.updated_at = datetime.now(UTC).timestamp()
            await self._persist_schedules(schedule.legion_id)
            await self._broadcast_schedule_event(schedule.legion_id, schedule)

    # ── Script Schedule Helpers (issue #1356) ──

    async def _ensure_session_active(self, session_id: str, timeout: float = 30.0) -> bool:
        """Ensure the target session is ACTIVE before script execution.

        Mirrors queue_processor.py auto-start pattern. start_session() is non-blocking;
        polls session state until ACTIVE or timeout.
        """
        session_info = await self.system.session_coordinator.session_manager.get_session_info(session_id)
        if not session_info:
            return False
        if session_info.state == SessionState.ERROR:
            return False
        if session_info.state == SessionState.ACTIVE:
            return True

        started = await self.system.session_coordinator.start_session(session_id)
        if not started:
            return False

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            info = await self.system.session_coordinator.session_manager.get_session_info(session_id)
            if info and info.state == SessionState.ACTIVE:
                return True
            if info and info.state == SessionState.ERROR:
                return False
            await asyncio.sleep(0.5)
        return False

    def has_inflight_scripts(self, session_id: str) -> bool:
        """Return True if any script schedule is currently running against this session.

        Used by the queue processor to gate its post-item idle wait — the queue
        must not advance to the next item while a script that may be about to
        enqueue is still running.
        """
        return bool(self._inflight_scripts_by_session.get(session_id))

    def _build_command_argv(
        self,
        schedule: "Schedule",
        session_info,
        docker_enabled: bool,
    ) -> list[str]:
        """Expand template variables in script_command and return shlex.split argv."""
        import json as _json
        import tempfile
        from pathlib import Path

        class _SafeFormatDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        session_data_payload = {
            "session_id": session_info.session_id,
            "name": session_info.name,
            "state": session_info.state.value,
            "working_directory": session_info.working_directory,
            "config": session_info.config if hasattr(session_info, "config") else {},
        }

        # Build session_data_path on host for writing
        data_dir = self.system.session_coordinator.data_dir
        session_dir = data_dir / "sessions" / session_info.session_id
        if session_dir.exists():
            tmp_dir = session_dir / "tmp"
            tmp_dir.mkdir(exist_ok=True, parents=True)
            host_data_path = tmp_dir / f"schedule_{schedule.schedule_id}_data.json"
        else:
            # Fallback for defensive case
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
            host_data_path = Path(tf.name)
            tf.close()

        host_data_path.write_text(_json.dumps(session_data_payload))

        # Path as seen from inside the command
        if docker_enabled:
            cmd_data_path = f"/tmp/schedule_{schedule.schedule_id}_data.json"
        else:
            cmd_data_path = str(host_data_path)

        expanded = (schedule.script_command or "").format_map(
            _SafeFormatDict(
                session_id=session_info.session_id,
                working_dir=session_info.working_directory or "",
                session_data=cmd_data_path,
            )
        )
        return shlex.split(expanded)

    async def _fire_script_with_cleanup(
        self,
        schedule: "Schedule",
        target_id: str,
        now: float,
        trigger: str = "cron",
    ):
        """Wrapper that fires a script schedule and always removes from inflight set."""
        try:
            await self._fire_script_schedule(schedule, now, trigger=trigger)
        finally:
            session_set = self._inflight_scripts_by_session.get(target_id, set())
            session_set.discard(schedule.schedule_id)
            if not session_set:
                self._inflight_scripts_by_session.pop(target_id, None)

    async def _fire_script_schedule(self, schedule: "Schedule", now: float, trigger: str = "cron"):
        """Fire a script schedule: auto-start session, run command, route outcome.

        **Security:** For Docker sessions, vault secrets are passed as proxy placeholders
        (CC_SECRET_*) so the sidecar substitutes them on outbound HTTP. For host sessions
        (docker_enabled=False), the proxy is unavailable; vault secrets are resolved to
        plaintext and visible via process listing. Use Docker sessions for sensitive scripts.
        """
        target_id = schedule.minion_id or schedule.ephemeral_agent_id
        started_clock = time.monotonic()

        # Increment fire_count before work so crashes still consume the count (issue #1538)
        schedule.fire_count += 1
        await self._persist_schedules(schedule.legion_id)

        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_time=schedule.next_run or now,
            actual_time=now,
            status="error",
            minion_state="unknown",
            trigger=trigger,
            schedule_type="script",
        )

        stdout = stderr = ""
        exit_code: int | None = None

        try:
            # 1. Auto-start (self-healing)
            ok = await self._ensure_session_active(target_id)
            if not ok:
                raise RuntimeError(f"Could not start session {target_id}")

            session_info = await self.system.session_coordinator.session_manager.get_session_info(target_id)
            execution.minion_state = session_info.state.value

            sc = self.system.session_coordinator
            effective_config = await resolve_effective_config(
                session_info, sc.template_manager, sc.profile_manager,
            )
            docker_enabled = bool(effective_config.docker_enabled)
            workdir = session_info.working_directory or None

            # 2. Render template variables
            try:
                argv = self._build_command_argv(schedule, session_info, docker_enabled)
            except Exception as e:
                raise RuntimeError(f"Failed to parse script_command: {e}") from e

            # 3. Execute
            if docker_enabled:
                container_id = None
                for _ in range(3):
                    container_id = await find_session_container(target_id)
                    if container_id:
                        break
                    await asyncio.sleep(0.5)
                if not container_id:
                    stopped_id = await find_session_container_any_state(target_id)
                    if stopped_id:
                        raise RuntimeError(
                            f"Container for session {target_id} has exited; cannot run script via docker exec"
                        )
                    raise RuntimeError(
                        f"Container did not appear after session start for {target_id}"
                    )

                # Build env dict: extra_env first, then vault secrets as placeholders (secrets win)
                script_env: dict[str, str] = {}
                if effective_config.extra_env:
                    script_env.update(effective_config.extra_env)
                # Issue #1425: invert placeholder→name map to get name→placeholder.
                placeholders_by_name = {
                    name: ph for ph, name in (session_info.secret_placeholders or {}).items()
                }
                if effective_config.assigned_secrets:
                    resolved = await sc.credential_vault.resolve_secrets_for_assignment(
                        effective_config.assigned_secrets
                    )
                    missing: list[str] = []
                    for secret in resolved:
                        inject_env = secret.get("inject_env")
                        name = secret.get("name")
                        if not inject_env:
                            continue
                        placeholder = placeholders_by_name.get(name)
                        if placeholder is None:
                            missing.append(name)
                            continue
                        script_env[inject_env] = placeholder
                    if missing:
                        raise RuntimeError(
                            f"Scheduled script for session {target_id}: secrets {missing} "
                            "are assigned but no proxy placeholder exists. Restart the session "
                            "to regenerate placeholders, or remove the assignment."
                        )
                    legion_logger.debug(
                        f"Script env for {target_id}: {len(script_env)} vars "
                        f"(keys: {sorted(script_env)})"
                    )

                exit_code, stdout, stderr, timed_out = await run_command_in_container(
                    container_id, argv, schedule.script_timeout_seconds, workdir,
                    env=script_env or None,
                )
            else:
                exit_code, stdout, stderr, timed_out = await run_command_on_host(
                    argv, schedule.script_timeout_seconds, workdir,
                )

            # 4. Outcome routing
            if timed_out:
                execution.status = "error"
                execution.error_message = (
                    f"Script exceeded timeout ({schedule.script_timeout_seconds}s)"
                )
            elif exit_code != 0:
                execution.status = "error"
                execution.error_message = f"Script exited with code {exit_code}"
            elif stdout.strip() == "":
                execution.status = "discarded"
            else:
                formatted = f"**[Scheduled Task: {schedule.name}]**\n\n{stdout.rstrip()}"

                # Always enqueue immediately — no pre-enqueue wait.
                # Cross-schedule serialization is on the dequeue side via
                # queue_processor._wait_for_idle + has_inflight_scripts (§6.3).
                result = await self.system.session_coordinator.enqueue_message(
                    session_id=target_id,
                    content=formatted,
                    reset_session=schedule.reset_session,
                    metadata={
                        "source": trigger,
                        "schedule_id": schedule.schedule_id,
                        "schedule_name": schedule.name,
                        "trigger_time": now,
                        "script_filtered": True,
                    },
                )
                execution.queue_id = result.get("queue_id")
                execution.status = "delivered"

        except Exception as e:
            execution.status = "error"
            execution.error_message = str(e)
            if not stderr:
                stderr = str(e)

        # 5. Persist execution + schedule state
        execution.exit_code = exit_code
        execution.stdout = cap_stream(stdout)
        execution.stderr = cap_stream(stderr)
        execution.duration_ms = int((time.monotonic() - started_clock) * 1000)

        schedule.last_run = now
        schedule.last_status = execution.status
        schedule.last_exit_code = exit_code
        schedule.last_stdout = cap_stream(stdout)
        schedule.last_stderr = cap_stream(stderr)
        schedule.execution_count += 1

        if execution.status == "error":
            schedule.failure_count += 1
            if schedule.failure_count > schedule.max_retries:
                schedule.status = ScheduleStatus.PAUSED
                schedule.next_run = None
        else:
            schedule.failure_count = 0

        if schedule.status == ScheduleStatus.ACTIVE:
            schedule.next_run = get_next_run(schedule.cron_expression)
        schedule.updated_at = datetime.now(UTC).timestamp()

        await self._persist_schedules(schedule.legion_id)
        await self._record_execution(schedule, execution)
        await self._broadcast_execution_event(schedule.legion_id, execution)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)

        # Auto-delete when repeat_count reached (issue #1538)
        if schedule.repeat_count is not None and schedule.fire_count >= schedule.repeat_count:
            try:
                await self.delete_schedule(schedule.schedule_id)
            except ValueError:
                pass  # already deleted

    async def _handle_retry(self, schedule: Schedule):
        """Apply exponential backoff for retry: 60s, 120s, 240s, ..."""
        backoff = 60 * (2 ** (schedule.failure_count - 1))
        schedule.next_run = datetime.now(UTC).timestamp() + backoff
        legion_logger.info(
            f"Schedule {schedule.schedule_id} retry #{schedule.failure_count} "
            f"in {backoff}s"
        )

    # ── Metrics (issue #1372) ──

    def _metrics_file(self, legion_id: str):
        data_dir = self.system.session_coordinator.data_dir
        return data_dir / "legions" / legion_id / "schedule_metrics.json"

    async def _load_metrics(self, legion_id: str) -> dict[str, ScheduleMetrics]:
        """Load (or return cached) metrics for a legion. Creates empty cache on miss."""
        if legion_id in self._metrics_cache:
            return self._metrics_cache[legion_id]

        metrics_file = self._metrics_file(legion_id)
        cache: dict[str, ScheduleMetrics] = {}
        if metrics_file.exists():
            try:
                raw = json.loads(metrics_file.read_text())
                for sid, entry in raw.items():
                    cache[sid] = ScheduleMetrics.from_dict(entry)
            except Exception as e:
                legion_logger.warning(f"Failed to read metrics for legion {legion_id}: {e}")
        self._metrics_cache[legion_id] = cache
        return cache

    async def _persist_metrics(self, legion_id: str):
        """Write metrics cache for a legion atomically (tmp + os.replace)."""
        cache = self._metrics_cache.get(legion_id, {})
        metrics_file = self._metrics_file(legion_id)
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = metrics_file.with_suffix(".tmp")
        try:
            payload = {sid: m.to_dict() for sid, m in cache.items()}
            tmp_file.write_text(json.dumps(payload, indent=2))
            os.replace(tmp_file, metrics_file)
        except Exception as e:
            legion_logger.error(f"Failed to persist metrics for legion {legion_id}: {e}")
            tmp_file.unlink(missing_ok=True)

    async def _update_metrics(self, legion_id: str, execution: ScheduleExecution):
        """Update in-memory ScheduleMetrics for one execution and persist."""
        if execution.status == "retry":
            return  # intermediate state — the retried fire generates its own record

        cache = await self._load_metrics(legion_id)
        schedule_id = execution.schedule_id
        if schedule_id not in cache:
            cache[schedule_id] = ScheduleMetrics(schedule_id=schedule_id)
        m = cache[schedule_id]

        now_ts = execution.actual_time
        m.last_run = now_ts
        m.last_status = execution.status
        m.updated_at = datetime.now(UTC).timestamp()

        if is_error_status(execution.status):
            m.total_runs += 1
            m.total_errors += 1
            m.consecutive_errors += 1
            m.last_error_time = now_ts
            m.last_error_message = execution.error_message
        else:
            m.total_runs += 1
            m.consecutive_errors = 0
            m.last_success_time = now_ts

        await self._persist_metrics(legion_id)

    async def _seed_metrics_from_history(self, legion_id: str):
        """Seed schedule_metrics.json from schedule_history.jsonl if metrics file is absent."""
        metrics_file = self._metrics_file(legion_id)
        if metrics_file.exists():
            await self._load_metrics(legion_id)
            return

        data_dir = self.system.session_coordinator.data_dir
        history_file = data_dir / "legions" / legion_id / "schedule_history.jsonl"
        if not history_file.exists():
            self._metrics_cache.setdefault(legion_id, {})
            return

        cache: dict[str, ScheduleMetrics] = {}
        try:
            with open(history_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        ex = ScheduleExecution.from_dict(data)
                    except Exception:
                        continue
                    if ex.status == "retry":
                        continue
                    sid = ex.schedule_id
                    if sid not in cache:
                        cache[sid] = ScheduleMetrics(schedule_id=sid)
                    m = cache[sid]
                    m.last_run = ex.actual_time
                    m.last_status = ex.status
                    if is_error_status(ex.status):
                        m.total_runs += 1
                        m.total_errors += 1
                        m.consecutive_errors += 1
                        m.last_error_time = ex.actual_time
                        m.last_error_message = ex.error_message
                    else:
                        m.total_runs += 1
                        m.consecutive_errors = 0
                        m.last_success_time = ex.actual_time
        except Exception as e:
            legion_logger.error(f"Failed to seed metrics from history for legion {legion_id}: {e}")

        self._metrics_cache[legion_id] = cache
        await self._persist_metrics(legion_id)
        legion_logger.info(
            f"Seeded metrics for {len(cache)} schedules from history for legion {legion_id}"
        )

        # Queue an immediate rotation to prune the file to the retention window
        rotator = getattr(self.system, "history_rotator", None)
        if rotator is not None:
            asyncio.ensure_future(rotator.request_rotation(legion_id))

    async def get_schedule_metrics(self, legion_id: str, schedule_id: str) -> ScheduleMetrics | None:
        """Return cached ScheduleMetrics for one schedule (or None if never fired)."""
        cache = await self._load_metrics(legion_id)
        return cache.get(schedule_id)

    async def schedule_to_api_dict(self, schedule: Schedule) -> dict:
        """Return schedule.to_dict() enriched with aggregate metrics for API responses."""
        metrics = await self.get_schedule_metrics(schedule.legion_id, schedule.schedule_id)
        return schedule.to_dict(metrics=metrics)

    def _maybe_trigger_rotation(self, legion_id: str):
        """Increment append counter and queue rotation when threshold crossed."""
        from src.legion.history_rotator import HistoryRotator

        rotator = getattr(self.system, "history_rotator", None)
        if not isinstance(rotator, HistoryRotator) or not rotator.config.enabled:
            return
        count = self._appends_since_rotation.get(legion_id, 0) + 1
        self._appends_since_rotation[legion_id] = count
        if count >= rotator.config.rotation_trigger_count:
            self._appends_since_rotation[legion_id] = 0
            asyncio.ensure_future(rotator.request_rotation(legion_id))

    async def _record_execution(self, schedule: Schedule, execution: ScheduleExecution):
        """Update metrics, append execution record, and trigger rotation if needed."""
        await self._update_metrics(schedule.legion_id, execution)
        await self._append_execution(schedule.legion_id, execution)
        self._maybe_trigger_rotation(schedule.legion_id)

    # ── Persistence ──

    async def _persist_schedules(self, legion_id: str):
        """Write all schedules for a legion to schedules.json."""
        data_dir = self.system.session_coordinator.data_dir
        legion_dir = data_dir / "legions" / legion_id
        legion_dir.mkdir(parents=True, exist_ok=True)
        schedules_file = legion_dir / "schedules.json"

        schedules = [
            s.to_dict() for s in self._schedules.values() if s.legion_id == legion_id
        ]

        try:
            with open(schedules_file, "w") as f:
                json.dump(schedules, f, indent=2)
        except Exception as e:
            legion_logger.error(f"Failed to persist schedules for legion {legion_id}: {e}")

    async def _load_schedules(self, legion_id: str):
        """Load schedules for a specific legion from disk."""
        data_dir = self.system.session_coordinator.data_dir
        schedules_file = data_dir / "legions" / legion_id / "schedules.json"

        if not schedules_file.exists():
            return

        try:
            with open(schedules_file) as f:
                data = json.load(f)
            migrated = 0
            for item in data:
                had_cancelled = item.get("status") == "cancelled"
                schedule = Schedule.from_dict(item)
                self._schedules[schedule.schedule_id] = schedule

                if had_cancelled:
                    migrated += 1

                # Recalculate next_run for active schedules (skip missed windows)
                if schedule.status == ScheduleStatus.ACTIVE:
                    schedule.next_run = get_next_run(schedule.cron_expression)

            if migrated:
                legion_logger.info(
                    f"Migrated {migrated} cancelled schedules to paused for legion {legion_id}"
                )
                await self._persist_schedules(legion_id)

            legion_logger.info(
                f"Loaded {len(data)} schedules for legion {legion_id}"
            )
        except Exception as e:
            legion_logger.error(f"Failed to load schedules for legion {legion_id}: {e}")

    async def load_all_schedules(self):
        """Load schedules from all legions on startup."""
        data_dir = self.system.session_coordinator.data_dir
        legions_dir = data_dir / "legions"
        if not legions_dir.exists():
            return

        legion_ids = []
        for legion_dir in legions_dir.iterdir():
            if legion_dir.is_dir():
                await self._load_schedules(legion_dir.name)
                legion_ids.append(legion_dir.name)

        total = len(self._schedules)
        active = sum(
            1 for s in self._schedules.values() if s.status == ScheduleStatus.ACTIVE
        )
        legion_logger.info(f"Loaded {total} schedules ({active} active) from all legions")

        # Issue #1372: Seed metrics from history for legions that don't have metrics yet
        for legion_id in legion_ids:
            await self._seed_metrics_from_history(legion_id)

        # Issue #578: Recover orphaned ephemeral sessions on startup
        await self._recover_orphaned_ephemeral_sessions()

    async def _recover_orphaned_ephemeral_sessions(self):
        """On startup, recover ephemeral agents left over from a previous run.

        For schedules with an ephemeral_agent_id:
        - If agent is active (crash leftover): terminate it so next fire starts clean
        - If agent doesn't exist: clear ephemeral_agent_id (will recreate on next fire)
        """
        recovered = 0
        for schedule in list(self._schedules.values()):
            if not schedule.ephemeral_agent_id:
                continue

            agent_id = schedule.ephemeral_agent_id
            session_info = (
                await self.system.session_coordinator.session_manager.get_session_info(
                    agent_id
                )
            )

            if not session_info:
                # Agent was deleted — clear reference, will recreate on next fire
                legion_logger.info(
                    f"Ephemeral agent {agent_id} for schedule {schedule.schedule_id} "
                    f"no longer exists — clearing reference"
                )
                schedule.ephemeral_agent_id = None
                schedule.updated_at = datetime.now(UTC).timestamp()
                recovered += 1

            elif session_info.state.value in ("active", "starting"):
                # Agent still running (crash leftover) — terminate it
                legion_logger.info(
                    f"Terminating orphaned active agent {agent_id} "
                    f"for schedule {schedule.schedule_id}"
                )
                try:
                    await self.system.session_coordinator.archive_and_clear_session(agent_id)
                except Exception as e:
                    legion_logger.error(
                        f"Failed to clean up orphaned agent {agent_id}: {e}"
                    )
                recovered += 1

            # terminated/error state is fine — agent is ready for next fire

        if recovered > 0:
            affected_legions = {
                s.legion_id for s in self._schedules.values()
                if s.ephemeral_agent_id is None or True  # persist all affected
            }
            for legion_id in affected_legions:
                await self._persist_schedules(legion_id)
            legion_logger.info(f"Recovered {recovered} orphaned ephemeral agents")

    async def _append_execution(self, legion_id: str, execution: ScheduleExecution):
        """Append execution record to schedule_history.jsonl.

        Acquires the per-legion HistoryRotator lock to prevent write-during-rotation races.
        """
        data_dir = self.system.session_coordinator.data_dir
        legion_dir = data_dir / "legions" / legion_id
        legion_dir.mkdir(parents=True, exist_ok=True)
        history_file = legion_dir / "schedule_history.jsonl"

        from src.legion.history_rotator import HistoryRotator

        rotator = getattr(self.system, "history_rotator", None)
        lock = rotator.appender_lock(legion_id) if isinstance(rotator, HistoryRotator) else None

        try:
            if lock is not None:
                async with lock:
                    with open(history_file, "a") as f:
                        f.write(json.dumps(execution.to_dict()) + "\n")
            else:
                with open(history_file, "a") as f:
                    f.write(json.dumps(execution.to_dict()) + "\n")
        except Exception as e:
            legion_logger.error(f"Failed to append execution history: {e}")

    # ── WebSocket Broadcasting ──

    async def _broadcast_schedule_event(
        self, legion_id: str, schedule: Schedule, deleted: bool = False
    ):
        """Broadcast schedule change to WebSocket clients."""
        if not self._schedule_broadcast_callback:
            return

        event = {
            "type": "schedule_updated",
            "schedule": await self.schedule_to_api_dict(schedule),
            "deleted": deleted,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            await self._schedule_broadcast_callback(legion_id, event)
        except Exception as e:
            legion_logger.error(f"Failed to broadcast schedule event: {e}")

    async def _broadcast_execution_event(
        self, legion_id: str, execution: ScheduleExecution
    ):
        """Broadcast a schedule execution record to WebSocket clients."""
        if not self._schedule_broadcast_callback:
            return

        event = {
            "type": "schedule_execution",
            "execution": execution.to_dict(),
            "schedule_id": execution.schedule_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            await self._schedule_broadcast_callback(legion_id, event)
        except Exception as e:
            legion_logger.error(f"Failed to broadcast execution event: {e}")
