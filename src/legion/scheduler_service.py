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
        name: str,
        cron_expression: str,
        prompt: str,
        minion_id: str | None = None,
        minion_name: str | None = None,
        reset_session: bool = False,
        max_retries: int = 3,
        timeout_seconds: int = 3600,
        session_config: dict | None = None,
        ephemeral_agent_id: str | None = None,
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

        allowed = {"name", "cron_expression", "prompt", "max_retries", "timeout_seconds", "session_config"}
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
        """Fire a due schedule — delegates to permanent or ephemeral path."""
        if schedule.session_config is not None:
            await self._fire_ephemeral_schedule(schedule, now)
        else:
            await self._fire_permanent_schedule(schedule, now)

    async def _fire_permanent_schedule(self, schedule: Schedule, now: float):
        """Fire a permanent schedule by enqueuing the prompt to an existing minion."""
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

    async def _fire_ephemeral_schedule(self, schedule: Schedule, now: float):
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

        execution = ScheduleExecution(
            execution_id=str(uuid.uuid4()),
            schedule_id=schedule.schedule_id,
            scheduled_time=schedule.next_run or now,
            actual_time=now,
            status="queued",
            minion_state=session_info.state.value if session_info else "unknown",
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
                    "source": "cron",
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
            asyncio.create_task(
                self._monitor_ephemeral_session(schedule.schedule_id, agent_id)
            )

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
        await self._append_execution(schedule.legion_id, execution)
        await self._broadcast_schedule_event(schedule.legion_id, schedule)

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

    async def _monitor_ephemeral_session(self, schedule_id: str, session_id: str):
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
                f"Error monitoring ephemeral session {session_id}: {e}"
            )

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
