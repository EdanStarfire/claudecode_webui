"""
SchedulerService - Background asyncio service for cron-based schedule execution.

Ticks every 30 seconds, evaluates cron expressions via croniter, and delivers
due prompts to owning minions through SessionCoordinator.enqueue_message().
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.logging_config import get_logger
from src.models.schedule_models import (
    Schedule,
    ScheduleExecution,
    ScheduleStatus,
    get_next_run,
    validate_cron_expression,
)

if TYPE_CHECKING:
    from src.legion_system import LegionSystem

legion_logger = get_logger("legion", "SCHEDULER")

TICK_INTERVAL = 30  # seconds between scheduler evaluations


class SchedulerService:
    """Background service that evaluates cron schedules and fires due prompts."""

    def __init__(self, system: "LegionSystem"):
        self.system = system
        self._schedules: dict[str, Schedule] = {}  # schedule_id -> Schedule
        self._task: asyncio.Task | None = None
        self._running = False
        self._schedule_broadcast_callback = None

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
            if now >= schedule.next_run:
                await self._fire_schedule(schedule, now)

    # ── CRUD Operations ──

    async def create_schedule(
        self,
        legion_id: str,
        minion_id: str,
        minion_name: str,
        name: str,
        cron_expression: str,
        prompt: str,
        reset_session: bool = False,
        max_retries: int = 3,
        timeout_seconds: int = 3600,
    ) -> Schedule:
        """Create a new schedule.

        Raises:
            ValueError: If cron expression is invalid.
        """
        if not validate_cron_expression(cron_expression):
            raise ValueError(f"Invalid cron expression: {cron_expression}")

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
        )

        self._schedules[schedule.schedule_id] = schedule
        await self._persist_schedules(legion_id)
        await self._broadcast_schedule_event(legion_id, schedule)
        legion_logger.info(
            f"Schedule created: {schedule.schedule_id} '{name}' "
            f"for minion {minion_id} in legion {legion_id}"
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

        allowed = {"name", "cron_expression", "prompt", "max_retries", "timeout_seconds"}
        for key, value in fields.items():
            if key in allowed:
                setattr(schedule, key, value)

        # Recalculate next_run if cron changed
        if "cron_expression" in fields and schedule.status == ScheduleStatus.ACTIVE:
            schedule.next_run = get_next_run(schedule.cron_expression)

        schedule.updated_at = datetime.now(UTC).timestamp()
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

    async def cancel_schedule(self, schedule_id: str) -> Schedule:
        """Cancel a schedule permanently.

        Raises:
            ValueError: If schedule not found or already cancelled.
        """
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")
        if schedule.status == ScheduleStatus.CANCELLED:
            raise ValueError(f"Schedule {schedule_id} is already cancelled")

        schedule.status = ScheduleStatus.CANCELLED
        schedule.next_run = None
        schedule.updated_at = datetime.now(UTC).timestamp()
        await self._persist_schedules(schedule.legion_id)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)
        legion_logger.info(f"Schedule cancelled: {schedule_id}")
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

    async def cancel_schedules_for_minion(self, minion_id: str) -> int:
        """Cancel all active/paused schedules for a minion (used on disposal).

        Returns:
            Number of schedules cancelled.
        """
        cancelled = 0
        affected_legions = set()
        for schedule in list(self._schedules.values()):
            if schedule.minion_id != minion_id:
                continue
            if schedule.status == ScheduleStatus.CANCELLED:
                continue
            schedule.status = ScheduleStatus.CANCELLED
            schedule.next_run = None
            schedule.updated_at = datetime.now(UTC).timestamp()
            affected_legions.add(schedule.legion_id)
            cancelled += 1

        for legion_id in affected_legions:
            await self._persist_schedules(legion_id)

        if cancelled > 0:
            legion_logger.info(
                f"Cancelled {cancelled} schedules for disposed minion {minion_id}"
            )
        return cancelled

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

    # ── Execution ──

    async def _fire_schedule(self, schedule: Schedule, now: float):
        """Fire a due schedule by enqueuing the prompt via SessionCoordinator."""
        legion_logger.info(
            f"Firing schedule {schedule.schedule_id} '{schedule.name}' "
            f"for minion {schedule.minion_id}"
        )

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
        )

        try:
            result = await self.system.session_coordinator.enqueue_message(
                session_id=schedule.minion_id,
                content=formatted_prompt,
                reset_session=schedule.reset_session,
                metadata={
                    "source": "cron",
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
        await self._append_execution(schedule.legion_id, execution)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)

    async def _handle_retry(self, schedule: Schedule):
        """Apply exponential backoff for retry: 60s, 120s, 240s, ..."""
        backoff = 60 * (2 ** (schedule.failure_count - 1))
        schedule.next_run = datetime.now(UTC).timestamp() + backoff
        legion_logger.info(
            f"Schedule {schedule.schedule_id} retry #{schedule.failure_count} "
            f"in {backoff}s"
        )

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
            for item in data:
                schedule = Schedule.from_dict(item)
                self._schedules[schedule.schedule_id] = schedule

                # Recalculate next_run for active schedules (skip missed windows)
                if schedule.status == ScheduleStatus.ACTIVE:
                    schedule.next_run = get_next_run(schedule.cron_expression)

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

        for legion_dir in legions_dir.iterdir():
            if legion_dir.is_dir():
                await self._load_schedules(legion_dir.name)

        total = len(self._schedules)
        active = sum(
            1 for s in self._schedules.values() if s.status == ScheduleStatus.ACTIVE
        )
        legion_logger.info(f"Loaded {total} schedules ({active} active) from all legions")

    async def _append_execution(self, legion_id: str, execution: ScheduleExecution):
        """Append execution record to schedule_history.jsonl."""
        data_dir = self.system.session_coordinator.data_dir
        legion_dir = data_dir / "legions" / legion_id
        legion_dir.mkdir(parents=True, exist_ok=True)
        history_file = legion_dir / "schedule_history.jsonl"

        try:
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
            "schedule": schedule.to_dict(),
            "deleted": deleted,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        try:
            await self._schedule_broadcast_callback(legion_id, event)
        except Exception as e:
            legion_logger.error(f"Failed to broadcast schedule event: {e}")
