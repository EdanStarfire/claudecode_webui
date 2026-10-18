"""Tests for SchedulerService._monitor_ephemeral_session (issue #2193 PR7, AC13).

No prior dedicated coverage existed for this loop before PR7 — these tests cover
both the regression fix (the dead pending-queue check removed) and the loop's
pre-existing idle_since/idle_grace timing, schedule-deletion, and terminal-state
behavior, which PR7 deliberately leaves unchanged (see the method's own
docstring for the four-point non-fold reasoning).
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.legion.scheduler_service import SchedulerService
from backend.models.schedule_models import Schedule, ScheduleStatus, get_next_run
from backend.session_manager import SessionState


def _make_session_info(
    session_id: str = "agent-1",
    state: SessionState = SessionState.ACTIVE,
    is_processing: bool = False,
):
    info = MagicMock()
    info.session_id = session_id
    info.state = state
    info.is_processing = is_processing
    return info


def _make_system(session_info=None, data_dir: Path | None = None):
    if session_info is None:
        session_info = _make_session_info()

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)
    session_manager.get_session_directory = AsyncMock(return_value=data_dir)

    queue_manager = MagicMock()

    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.queue_manager = queue_manager
    coordinator.archive_and_clear_session = AsyncMock()
    coordinator.data_dir = data_dir or Path("/nonexistent")

    system = MagicMock()
    system.session_port = coordinator
    return system


def _make_ephemeral_schedule(schedule_id: str = "sched-1", legion_id: str = "legion-1") -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        legion_id=legion_id,
        name="Ephemeral Schedule",
        cron_expression="*/1 * * * *",
        prompt="Do the thing",
        minion_id=None,
        schedule_type="prompt",
        ephemeral_agent_id="agent-1",
        next_run=get_next_run("*/1 * * * *"),
        status=ScheduleStatus.ACTIVE,
    )


async def _run_monitor_until_done(svc, schedule_id, session_id, legion_id):
    """Run _monitor_ephemeral_session with asyncio.sleep faked so the loop
    advances immediately — poll_interval/idle_grace are both real-time (10s)
    constants inside the method, not injectable, so time itself is faked instead."""
    fake_time = [0.0]

    def _get_time():
        return fake_time[0]

    async def _fake_sleep(_seconds):
        fake_time[0] += 10.0

    with (
        patch("backend.legion.scheduler_service.asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)),
        patch("backend.legion.scheduler_service.asyncio.get_event_loop") as mock_loop,
    ):
        mock_loop.return_value.time.side_effect = _get_time
        await svc._monitor_ephemeral_session(schedule_id, session_id, legion_id)


# ---------------------------------------------------------------------------
# Regression: the removed dead pending-queue check must not resurrect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removed_pending_check_does_not_resurrect(tmp_path):
    """Issue #2193 PR7: before this PR, a pending item on queue_manager's ledger
    (dead since PR6's cutover — no producer writes there anymore for ephemeral
    prompts) would have forced is_idle=False forever, preventing archival. Even
    with queue_manager wired to report pending items, idle-detection today must
    depend only on is_processing."""
    idle_info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
    system = _make_system(session_info=idle_info, data_dir=tmp_path)

    # If the dead check were still present, this would force is_idle=False.
    stale_pending_item = MagicMock(status="pending")
    system.session_port.queue_manager.get_queue = AsyncMock(return_value=[stale_pending_item])

    svc = SchedulerService(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule
    svc._persist_schedules = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    await _run_monitor_until_done(svc, schedule.schedule_id, "agent-1", "legion-1")

    system.session_port.queue_manager.get_queue.assert_not_called()
    system.session_port.archive_and_clear_session.assert_awaited_once_with("agent-1")


# ---------------------------------------------------------------------------
# idle_since/idle_grace timing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archives_after_idle_grace_elapses(tmp_path):
    idle_info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
    system = _make_system(session_info=idle_info, data_dir=tmp_path)
    svc = SchedulerService(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule
    svc._persist_schedules = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    await _run_monitor_until_done(svc, schedule.schedule_id, "agent-1", "legion-1")

    system.session_port.archive_and_clear_session.assert_awaited_once_with("agent-1")


@pytest.mark.asyncio
async def test_still_processing_resets_idle_timer_and_keeps_polling(tmp_path):
    """A session that's always processing never accumulates idle_since —
    the loop keeps polling (here cut short via CancelledError) and never
    archives."""
    busy_info = _make_session_info(state=SessionState.ACTIVE, is_processing=True)
    system = _make_system(session_info=busy_info, data_dir=tmp_path)
    svc = SchedulerService(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule
    svc._persist_schedules = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    call_count = 0

    async def _fake_sleep(_seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.CancelledError()

    with patch("backend.legion.scheduler_service.asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)):
        await svc._monitor_ephemeral_session(schedule.schedule_id, "agent-1", "legion-1")

    # CancelledError returns early — no archive attempted.
    system.session_port.archive_and_clear_session.assert_not_awaited()
    assert call_count >= 3


# ---------------------------------------------------------------------------
# Schedule deleted mid-monitor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_deleted_mid_monitor_stops_and_archives(tmp_path):
    idle_info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
    system = _make_system(session_info=idle_info, data_dir=tmp_path)
    svc = SchedulerService(system)
    # Schedule never registered in svc._schedules — simulates deletion before
    # the monitor's first tick observes it.

    with patch("backend.legion.scheduler_service.asyncio.sleep", new=AsyncMock()):
        await svc._monitor_ephemeral_session("sched-deleted", "agent-1", "legion-1")

    system.session_port.archive_and_clear_session.assert_awaited_once_with("agent-1")


# ---------------------------------------------------------------------------
# Terminal state handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_state_stops_monitor_and_archives(tmp_path):
    terminated_info = _make_session_info(state=SessionState.TERMINATED)
    system = _make_system(session_info=terminated_info, data_dir=tmp_path)
    svc = SchedulerService(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule
    svc._persist_schedules = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    with patch("backend.legion.scheduler_service.asyncio.sleep", new=AsyncMock()):
        await svc._monitor_ephemeral_session(schedule.schedule_id, "agent-1", "legion-1")

    system.session_port.archive_and_clear_session.assert_awaited_once_with("agent-1")


@pytest.mark.asyncio
async def test_session_disappeared_stops_monitor_and_archives(tmp_path):
    system = _make_system(session_info=None, data_dir=tmp_path)
    system.session_port.session_manager.get_session_info = AsyncMock(return_value=None)
    svc = SchedulerService(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule
    svc._persist_schedules = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    with patch("backend.legion.scheduler_service.asyncio.sleep", new=AsyncMock()):
        await svc._monitor_ephemeral_session(schedule.schedule_id, "agent-1", "legion-1")

    system.session_port.archive_and_clear_session.assert_awaited_once_with("agent-1")
