"""
Unit tests for SchedulerService auto-resume schedules (issue #1572).

Covers:
- upsert_auto_resume_schedule: creates new / bumps existing next_run (no duplicate),
  and leaves fire_count/repeat_count bookkeeping untouched on bump (decision #5 risk)
- cancel_auto_resume_schedule: deletes existing / no-op when none
- _fire_auto_resume_schedule:
  - ERROR -> TERMINATED unstick sequence (issue #1019 pattern), then start + enqueue
  - ACTIVE session: skips start_session entirely, goes straight to enqueue
  - PAUSED (mid-permission-wait): skip-don't-force guard (issue #1918/#2159) — does
    not consume the repeat_count=1 fire budget, schedule survives
  - session deleted before the timer fires: execution marked skipped, schedule
    still self-deletes
  - start failure: execution marked failed, schedule still self-deletes
    (repeat_count=1 "runs once, and disappears" regardless of outcome)
- _tick()/run_now() dispatch schedule_type == "auto_resume" to _fire_auto_resume_schedule
- two auto-resume schedules sharing the same fire_at both fire independently in one
  tick (no shared-lock/stampede issue — risk #2)
- _load_schedules() (backend restart / boot reload) preserves an auto-resume
  schedule's absolute next_run instead of recalculating it from the inert
  placeholder cron_expression, unlike prompt/script schedules which are
  correctly recalculated from their real cron on every boot
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.legion.scheduler_service import SchedulerService
from backend.models.schedule_models import Schedule, ScheduleStatus
from backend.session_manager import SessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_info(
    session_id: str = "sess-1",
    state: SessionState = SessionState.ERROR,
    project_id: str = "proj-1",
    name: str = "Test Session",
):
    info = MagicMock()
    info.session_id = session_id
    info.state = state
    info.project_id = project_id
    info.name = name
    return info


def _make_system(session_info=None, start_session_result: bool = True, enqueue_result: dict | None = None):
    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)
    session_manager.update_session_state = AsyncMock(return_value=True)

    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.start_session = AsyncMock(return_value=start_session_result)
    coordinator.enqueue_message = AsyncMock(return_value=enqueue_result or {"queue_id": "q-1"})
    coordinator.data_dir = Path("/nonexistent")

    system = MagicMock()
    system.session_coordinator = coordinator
    return system


def _make_svc(system) -> SchedulerService:
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._record_execution = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()
    return svc


def _make_auto_resume_schedule(
    schedule_id: str = "ar-1",
    legion_id: str = "proj-1",
    minion_id: str = "sess-1",
    next_run: float = 1000.0,
    fire_count: int = 0,
) -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        legion_id=legion_id,
        name="Auto-resume after rate limit",
        cron_expression="0 0 * * *",
        prompt="",
        minion_id=minion_id,
        status=ScheduleStatus.ACTIVE,
        next_run=next_run,
        schedule_type="auto_resume",
        repeat_count=1,
        fire_count=fire_count,
    )


# ---------------------------------------------------------------------------
# upsert_auto_resume_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_new_schedule_when_none_exists():
    svc = _make_svc(_make_system())

    schedule = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=5000.0
    )

    assert schedule.schedule_type == "auto_resume"
    assert schedule.minion_id == "sess-1"
    assert schedule.next_run == 5000.0
    assert schedule.repeat_count == 1
    assert schedule.fire_count == 0
    assert svc._schedules[schedule.schedule_id] is schedule


@pytest.mark.asyncio
async def test_upsert_bumps_existing_instead_of_duplicating():
    svc = _make_svc(_make_system())

    first = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=5000.0
    )
    second = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=9000.0
    )

    assert second.schedule_id == first.schedule_id
    assert second.next_run == 9000.0
    auto_resume_schedules = [s for s in svc._schedules.values() if s.schedule_type == "auto_resume"]
    assert len(auto_resume_schedules) == 1


@pytest.mark.asyncio
async def test_upsert_bump_leaves_fire_count_bookkeeping_untouched():
    """Decision #5 risk: bumping an existing not-yet-fired schedule must not leave
    stale fire_count/repeat_count state that could interact oddly with the
    repeat_count=1 auto-delete check."""
    svc = _make_svc(_make_system())

    schedule = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=5000.0
    )
    assert schedule.fire_count == 0

    bumped = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=9000.0
    )

    assert bumped.fire_count == 0
    assert bumped.repeat_count == 1


# ---------------------------------------------------------------------------
# cancel_auto_resume_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_deletes_existing_schedule():
    svc = _make_svc(_make_system())
    schedule = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=5000.0
    )

    result = await svc.cancel_auto_resume_schedule("sess-1")

    assert result is True
    assert schedule.schedule_id not in svc._schedules


@pytest.mark.asyncio
async def test_cancel_is_noop_when_none_exists():
    svc = _make_svc(_make_system())

    result = await svc.cancel_auto_resume_schedule("sess-1")

    assert result is False


@pytest.mark.asyncio
async def test_cancel_does_not_affect_other_schedule_types():
    """A session's ordinary cron schedule must survive cancel_auto_resume_schedule —
    it only ever targets schedule_type == 'auto_resume'."""
    svc = _make_svc(_make_system())
    other = Schedule(
        schedule_id="cron-1",
        legion_id="proj-1",
        name="Daily standup",
        cron_expression="0 9 * * *",
        prompt="standup",
        minion_id="sess-1",
        status=ScheduleStatus.ACTIVE,
    )
    svc._schedules[other.schedule_id] = other

    result = await svc.cancel_auto_resume_schedule("sess-1")

    assert result is False
    assert other.schedule_id in svc._schedules


# ---------------------------------------------------------------------------
# _fire_auto_resume_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_error_session_unsticks_via_terminated_then_starts():
    error_info = _make_session_info(state=SessionState.ERROR)
    terminated_info = _make_session_info(state=SessionState.TERMINATED)
    system = _make_system(session_info=error_info)
    # update_session_state(ERROR -> TERMINATED) is a direct setter in production;
    # the mock session_manager doesn't mutate session_info as a side effect, so
    # the refetch after it must be told to see the new state explicitly.
    system.session_coordinator.session_manager.get_session_info = AsyncMock(
        side_effect=[error_info, terminated_info]
    )
    svc = _make_svc(system)
    svc._ensure_session_active = AsyncMock(return_value=True)
    schedule = _make_auto_resume_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.session_manager.update_session_state.assert_awaited_once_with(
        "sess-1", SessionState.TERMINATED
    )
    svc._ensure_session_active.assert_awaited_once_with("sess-1")
    system.session_coordinator.enqueue_message.assert_awaited_once()
    _, kwargs = system.session_coordinator.enqueue_message.call_args
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["content"] == "Please continue where you left off."
    assert kwargs["reset_session"] is False
    assert kwargs["metadata"]["auto_resume"] is True
    assert kwargs["metadata"]["schedule_id"] == schedule.schedule_id
    assert schedule.fire_count == 1
    assert schedule.schedule_id not in svc._schedules, "repeat_count=1 self-delete"


@pytest.mark.asyncio
async def test_fire_active_session_skips_start_goes_straight_to_enqueue():
    session_info = _make_session_info(state=SessionState.ACTIVE)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    svc._ensure_session_active = AsyncMock(return_value=True)
    schedule = _make_auto_resume_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.session_manager.update_session_state.assert_not_awaited()
    svc._ensure_session_active.assert_not_awaited()
    system.session_coordinator.enqueue_message.assert_awaited_once()
    assert schedule.schedule_id not in svc._schedules


@pytest.mark.asyncio
async def test_fire_paused_session_defers_without_consuming_fire_count():
    session_info = _make_session_info(state=SessionState.PAUSED)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule(next_run=1000.0)
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.start_session.assert_not_awaited()
    system.session_coordinator.enqueue_message.assert_not_awaited()
    assert schedule.fire_count == 0, "PAUSED defer must not consume the repeat_count=1 budget"
    assert schedule.schedule_id in svc._schedules, "must not self-delete while deferring"
    assert schedule.next_run > 1000.0, "next_run bumped ahead to re-check on a later tick"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.STARTING, SessionState.TERMINATING])
async def test_fire_in_transition_session_defers_without_consuming_fire_count(state):
    """STARTING/TERMINATING mid-transition — nothing to force, same non-blocking
    defer as PAUSED rather than the bounded 30s poll _ensure_session_active uses
    elsewhere; blocking the whole scheduler tick loop would be worse."""
    session_info = _make_session_info(state=state)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule(next_run=1000.0)
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.start_session.assert_not_awaited()
    system.session_coordinator.enqueue_message.assert_not_awaited()
    assert schedule.fire_count == 0
    assert schedule.schedule_id in svc._schedules


@pytest.mark.asyncio
async def test_fire_session_deleted_before_timer_discards_and_self_deletes():
    system = _make_system(session_info=None)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.enqueue_message.assert_not_awaited()
    assert schedule.fire_count == 1
    assert schedule.schedule_id not in svc._schedules


@pytest.mark.asyncio
async def test_fire_start_failure_marks_failed_and_still_self_deletes():
    session_info = _make_session_info(state=SessionState.TERMINATED)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    svc._ensure_session_active = AsyncMock(return_value=False)
    schedule = _make_auto_resume_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_auto_resume_schedule(schedule, 1000.0)

    system.session_coordinator.enqueue_message.assert_not_awaited()
    assert schedule.fire_count == 1
    assert schedule.schedule_id not in svc._schedules


# ---------------------------------------------------------------------------
# _tick() / run_now() dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_dispatches_due_auto_resume_schedule():
    session_info = _make_session_info(state=SessionState.ACTIVE)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule(next_run=500.0)  # well in the past -> due
    svc._schedules[schedule.schedule_id] = schedule

    await svc._tick()

    system.session_coordinator.enqueue_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_skips_not_yet_due_auto_resume_schedule():
    session_info = _make_session_info(state=SessionState.ACTIVE)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule(next_run=99999999999.0)  # far future
    svc._schedules[schedule.schedule_id] = schedule

    await svc._tick()

    system.session_coordinator.enqueue_message.assert_not_awaited()
    assert schedule.schedule_id in svc._schedules


@pytest.mark.asyncio
async def test_run_now_dispatches_auto_resume_schedule_bypassing_next_run():
    session_info = _make_session_info(state=SessionState.ACTIVE)
    system = _make_system(session_info=session_info)
    svc = _make_svc(system)
    schedule = _make_auto_resume_schedule(next_run=99999999999.0)  # not due
    svc._schedules[schedule.schedule_id] = schedule

    result = await svc.run_now(schedule.schedule_id)

    assert result["status"] == "queued"
    system.session_coordinator.enqueue_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_fires_multiple_auto_resume_schedules_independently_same_fire_at():
    """Risk #2: an account-wide limit hit can plausibly trigger auto-resume
    detection on several concurrently-active sessions near-simultaneously, each
    with its own schedule sharing the same fire_at. Each schedule is independent —
    confirm no shared-lock/stampede issue when both are due in the same tick."""
    info1 = _make_session_info(session_id="sess-1", state=SessionState.ACTIVE)
    info2 = _make_session_info(session_id="sess-2", state=SessionState.ACTIVE)
    infos = {"sess-1": info1, "sess-2": info2}

    coordinator = MagicMock()
    coordinator.session_manager = MagicMock()
    coordinator.session_manager.get_session_info = AsyncMock(side_effect=lambda sid: infos[sid])
    coordinator.session_manager.update_session_state = AsyncMock(return_value=True)
    coordinator.start_session = AsyncMock(return_value=True)
    coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q-1"})
    coordinator.data_dir = Path("/nonexistent")
    system = MagicMock()
    system.session_coordinator = coordinator

    svc = _make_svc(system)
    s1 = _make_auto_resume_schedule(schedule_id="ar-1", minion_id="sess-1", next_run=500.0)
    s2 = _make_auto_resume_schedule(schedule_id="ar-2", minion_id="sess-2", next_run=500.0)
    svc._schedules[s1.schedule_id] = s1
    svc._schedules[s2.schedule_id] = s2

    await svc._tick()

    assert coordinator.enqueue_message.await_count == 2
    assert s1.schedule_id not in svc._schedules
    assert s2.schedule_id not in svc._schedules


# ---------------------------------------------------------------------------
# _load_schedules() boot-reload regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_schedules_preserves_auto_resume_next_run_across_restart(tmp_path):
    """An auto-resume schedule's absolute fire_at must survive a backend restart
    unchanged. _load_schedules() unconditionally recalculates next_run from
    cron_expression for every ACTIVE schedule (correct for prompt/script, whose
    next_run really is cron-derived) — auto_resume's cron_expression is only an
    inert placeholder, so without an explicit exclusion this would silently
    discard the real fire time on every boot."""
    system = _make_system()
    system.session_coordinator.data_dir = tmp_path
    svc = SchedulerService(system)

    fire_at = 1234567890.0
    schedule = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=fire_at
    )
    assert schedule.next_run == fire_at

    # Simulate a fresh process: a brand new SchedulerService loading from the
    # same on-disk schedules.json.
    svc2 = SchedulerService(system)
    await svc2._load_schedules("proj-1")

    reloaded = svc2._schedules[schedule.schedule_id]
    assert reloaded.next_run == fire_at, "auto_resume next_run must survive a reload unchanged"


# ---------------------------------------------------------------------------
# update_schedule() must reject edits to auto_resume schedules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_schedule_rejects_auto_resume_edits():
    """code-review follow-up: the generic update_schedule()/PUT endpoint has no
    schedule_type gate, so without this guard a user could edit an auto_resume
    schedule's cron_expression via the app-wide Library > Schedules view and
    silently clobber its one-time fire_at (same hazard as the _load_schedules()
    boot-reload bug fixed above, reachable through a second code path)."""
    svc = _make_svc(_make_system())
    schedule = await svc.upsert_auto_resume_schedule(
        legion_id="proj-1", session_id="sess-1", session_name="Test", fire_at=5000.0
    )

    with pytest.raises(ValueError, match="cannot be edited"):
        await svc.update_schedule(schedule.schedule_id, cron_expression="0 9 * * *")

    # next_run must be untouched by the rejected edit attempt
    assert schedule.next_run == 5000.0
