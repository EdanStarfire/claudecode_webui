"""
Regression tests for issue #1918: cron/schedule automated triggers must not
force-start a session that is PAUSED mid-permission-wait (e.g. AskUserQuestion).

Covers the three call sites fixed in backend/legion/scheduler_service.py:
- _fire_ephemeral_schedule: must skip (not start_session) when PAUSED (buggy before this fix)
- run_now(): must raise busy error when PAUSED (buggy before this fix)
- _recover_orphaned_ephemeral_sessions: made an explicit no-op for PAUSED for consistency/
  documentation — PAUSED was already excluded from the termination tuple before this fix,
  so this call site had no functional bug; the test pins the (unchanged) safe behavior

Plus regression checks that ACTIVE/STARTING (already-guarded) and
CREATED/TERMINATED (auto-start-eligible) behavior is unchanged.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.legion.scheduler_service import SchedulerService
from backend.models.schedule_models import Schedule, ScheduleStatus, get_next_run
from backend.session_manager import SessionInfo, SessionManager, SessionState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session_info(session_id: str = "agent-1", state: SessionState = SessionState.CREATED):
    info = MagicMock()
    info.session_id = session_id
    info.state = state
    return info


def _make_system(session_info=None, data_dir: Path | None = None):
    if session_info is None:
        session_info = _make_session_info()

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.start_session = AsyncMock(return_value=True)
    coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q-1"})
    coordinator.archive_and_clear_session = AsyncMock(return_value=True)
    coordinator.create_ephemeral_session = AsyncMock(return_value="agent-1")
    coordinator.data_dir = data_dir or Path("/nonexistent")

    system = MagicMock()
    system.session_coordinator = coordinator
    return system


def _make_svc(system) -> SchedulerService:
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()
    return svc


def _make_ephemeral_schedule(
    schedule_id: str = "sched-1",
    legion_id: str = "legion-1",
    ephemeral_agent_id: str = "agent-1",
) -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        legion_id=legion_id,
        name="Test Ephemeral Schedule",
        cron_expression="*/1 * * * *",
        prompt="do the scheduled thing",
        session_config={"model": "sonnet"},
        ephemeral_agent_id=ephemeral_agent_id,
        next_run=get_next_run("*/1 * * * *"),
        status=ScheduleStatus.ACTIVE,
    )


# ---------------------------------------------------------------------------
# _fire_ephemeral_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_ephemeral_schedule_skips_when_paused():
    """New: a PAUSED ephemeral agent must not be force-started."""
    session_info = _make_session_info(state=SessionState.PAUSED)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()

    await svc._fire_ephemeral_schedule(schedule, 1000.0)

    system.session_coordinator.start_session.assert_not_awaited()
    system.session_coordinator.enqueue_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.STARTING])
async def test_fire_ephemeral_schedule_skips_when_active_or_starting(state):
    """Regression: already-busy states continue to be skipped, not started."""
    session_info = _make_session_info(state=state)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()

    await svc._fire_ephemeral_schedule(schedule, 1000.0)

    system.session_coordinator.start_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.CREATED, SessionState.TERMINATED])
async def test_fire_ephemeral_schedule_starts_when_created_or_terminated(state, tmp_path):
    """Regression: genuinely idle/dead sessions still get started normally."""
    session_info = _make_session_info(state=state)
    system = _make_system(session_info=session_info, data_dir=tmp_path)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()

    await svc._fire_ephemeral_schedule(schedule, 1000.0)

    system.session_coordinator.start_session.assert_awaited_once_with("agent-1")
    system.session_coordinator.enqueue_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_now()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_now_raises_when_paused():
    """New: manually running a schedule whose agent is PAUSED must raise, not force-start."""
    session_info = _make_session_info(state=SessionState.PAUSED)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    with pytest.raises(RuntimeError, match="currently active"):
        await svc.run_now(schedule.schedule_id)

    system.session_coordinator.start_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_now_raises_when_active():
    """Regression: existing active-agent busy check is unchanged."""
    session_info = _make_session_info(state=SessionState.ACTIVE)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    with pytest.raises(RuntimeError, match="currently active"):
        await svc.run_now(schedule.schedule_id)


# ---------------------------------------------------------------------------
# _recover_orphaned_ephemeral_sessions (startup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_orphaned_does_not_terminate_paused():
    """New: a PAUSED agent found at boot must not be classified as an orphan and terminated."""
    session_info = _make_session_info(state=SessionState.PAUSED)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._recover_orphaned_ephemeral_sessions()

    system.session_coordinator.archive_and_clear_session.assert_not_awaited()
    assert schedule.ephemeral_agent_id == "agent-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.STARTING])
async def test_recover_orphaned_terminates_active_or_starting(state):
    """Regression: a genuinely orphaned running agent left over from a crash is still cleaned up."""
    session_info = _make_session_info(state=state)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._recover_orphaned_ephemeral_sessions()

    system.session_coordinator.archive_and_clear_session.assert_awaited_once_with("agent-1")


# ---------------------------------------------------------------------------
# End-to-end-style: mirrors the reported production scenario, using a real
# SessionManager state machine (not just a mock) so the fix is exercised
# against the actual guard in start_session()/pause_session().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_paused_session_survives_schedule_fire_then_resumes(tmp_path):
    session_manager = SessionManager(data_dir=tmp_path)
    session_manager.sessions_dir.mkdir(parents=True, exist_ok=True)

    agent_id = "agent-e2e"
    now = datetime.now(UTC)
    session_manager._active_sessions[agent_id] = SessionInfo(
        session_id=agent_id,
        state=SessionState.ACTIVE,
        created_at=now,
        updated_at=now,
    )

    # Session pauses mid-AskUserQuestion (mirrors permission_service.pause_session)
    paused_ok = await session_manager.pause_session(agent_id)
    assert paused_ok is True
    assert (await session_manager.get_session_info(agent_id)).state == SessionState.PAUSED

    # Cron schedule fires while the session is paused — must not clobber state
    system = MagicMock()
    system.session_coordinator = MagicMock()
    system.session_coordinator.session_manager = session_manager
    system.session_coordinator.start_session = AsyncMock(
        side_effect=lambda sid: session_manager.start_session(sid)
    )
    system.session_coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q-1"})

    svc = _make_svc(system)
    schedule = _make_ephemeral_schedule(ephemeral_agent_id=agent_id)

    await svc._fire_ephemeral_schedule(schedule, 1000.0)

    # Fix verified: still PAUSED, not clobbered into STARTING (the reported wedge)
    assert (await session_manager.get_session_info(agent_id)).state == SessionState.PAUSED
    system.session_coordinator.start_session.assert_not_awaited()

    # User answers the permission prompt — session resumes normally
    resumed_ok = await session_manager.update_session_state(agent_id, SessionState.ACTIVE)
    assert resumed_ok is True
    assert (await session_manager.get_session_info(agent_id)).state == SessionState.ACTIVE
