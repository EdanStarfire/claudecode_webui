"""
Unit tests for SchedulerService repeat_count / fire_count mechanics (issue #1538).

Covers:
- fire_count increments once per fire dispatch
- auto-delete triggered when fire_count reaches repeat_count
- failure path still consumes fire_count
- unlimited schedule (repeat_count=None) never auto-deletes
- legacy schedule (no repeat_count/fire_count in JSON) loads as unlimited
- update_schedule auto-delete when fire_count >= new repeat_count
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.legion.scheduler_service import SchedulerService, _ScheduleAutoDeletedError
from src.models.schedule_models import Schedule, ScheduleStatus, get_next_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_system(enqueue_raises: Exception | None = None, data_dir: Path | None = None):
    enqueue_result = {"queue_id": "q-1"}
    session_info = MagicMock()
    session_info.session_id = "sess-1"
    session_info.name = "Test"
    session_info.state = MagicMock()
    session_info.state.value = "active"
    session_info.is_processing = False
    session_info.template_id = None
    session_info.secret_placeholders = {}

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    queue_manager = MagicMock()
    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.queue_manager = queue_manager
    coordinator.data_dir = data_dir or Path("/nonexistent")
    coordinator.template_manager = MagicMock()
    coordinator.template_manager.get_template = AsyncMock(return_value=None)
    coordinator.profile_manager = MagicMock()
    coordinator.profile_manager.get_profile = AsyncMock(return_value=None)
    coordinator.credential_vault = MagicMock()
    coordinator.credential_vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])

    if enqueue_raises:
        coordinator.enqueue_message = AsyncMock(side_effect=enqueue_raises)
    else:
        coordinator.enqueue_message = AsyncMock(return_value=enqueue_result)

    system = MagicMock()
    system.session_coordinator = coordinator
    return system


def _make_svc(system=None, **kwargs) -> SchedulerService:
    if system is None:
        system = _make_system(**kwargs)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._record_execution = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()
    return svc


def _make_schedule(
    schedule_id: str = "sched-1",
    legion_id: str = "leg-1",
    minion_id: str = "sess-1",
    repeat_count: int | None = None,
    fire_count: int = 0,
) -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        legion_id=legion_id,
        name="Test Schedule",
        cron_expression="*/1 * * * *",
        prompt="do something",
        minion_id=minion_id,
        repeat_count=repeat_count,
        fire_count=fire_count,
        next_run=get_next_run("*/1 * * * *"),
        status=ScheduleStatus.ACTIVE,
    )


# ---------------------------------------------------------------------------
# 1. fire_count increments once per fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_increments_fire_count():
    svc = _make_svc()
    schedule = _make_schedule()
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_permanent_schedule(schedule, 1000.0)

    assert schedule.fire_count == 1


# ---------------------------------------------------------------------------
# 2. Auto-delete when fire_count reaches repeat_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_auto_deletes_at_repeat_count():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=1)
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_permanent_schedule(schedule, 1000.0)

    assert schedule.fire_count == 1
    assert schedule.schedule_id not in svc._schedules, "Schedule should have been auto-deleted"


@pytest.mark.asyncio
async def test_fire_does_not_delete_before_count_reached():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=3, fire_count=0)
    svc._schedules[schedule.schedule_id] = schedule

    await svc._fire_permanent_schedule(schedule, 1000.0)
    assert schedule.schedule_id in svc._schedules, "Should still exist after 1st of 3 fires"
    assert schedule.fire_count == 1

    await svc._fire_permanent_schedule(schedule, 1001.0)
    assert schedule.schedule_id in svc._schedules, "Should still exist after 2nd of 3 fires"
    assert schedule.fire_count == 2

    await svc._fire_permanent_schedule(schedule, 1002.0)
    assert schedule.schedule_id not in svc._schedules, "Should be deleted after 3rd fire"
    assert schedule.fire_count == 3


# ---------------------------------------------------------------------------
# 3. Failure path still consumes fire_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_failure_consumes_fire_count():
    system = _make_system(enqueue_raises=RuntimeError("queue down"))
    svc = _make_svc(system=system)
    schedule = _make_schedule(repeat_count=1)
    svc._schedules[schedule.schedule_id] = schedule

    # Expect failure but fire_count still increments
    await svc._fire_permanent_schedule(schedule, 1000.0)

    assert schedule.fire_count == 1
    # auto-delete should still trigger after the failure
    assert schedule.schedule_id not in svc._schedules


# ---------------------------------------------------------------------------
# 4. Unlimited schedule never auto-deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlimited_schedule_never_deletes():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=None)
    svc._schedules[schedule.schedule_id] = schedule

    for i in range(10):
        await svc._fire_permanent_schedule(schedule, float(1000 + i))
        assert schedule.schedule_id in svc._schedules, f"Unlimited schedule deleted after {i+1} fires"

    assert schedule.fire_count == 10


# ---------------------------------------------------------------------------
# 5. Legacy schedule loaded as unlimited (no repeat_count/fire_count in JSON)
# ---------------------------------------------------------------------------


def test_legacy_schedule_loaded_as_unlimited(tmp_path):
    """Schedules persisted before issue #1538 must load as repeat_count=None, fire_count=0."""
    legacy_data = [
        {
            "schedule_id": "legacy-1",
            "legion_id": "leg-1",
            "minion_id": "sess-1",
            "name": "Legacy Schedule",
            "cron_expression": "*/5 * * * *",
            "prompt": "hello",
            "status": "active",
            "max_retries": 3,
            "timeout_seconds": 3600,
            "created_at": 1700000000.0,
            "updated_at": 1700000000.0,
            "next_run": 1700001000.0,
            "last_run": None,
            "last_status": None,
            "execution_count": 5,
            "failure_count": 0,
            "reset_session": False,
            # No repeat_count or fire_count keys
        }
    ]

    legion_dir = tmp_path / "legions" / "leg-1"
    legion_dir.mkdir(parents=True)
    (legion_dir / "schedules.json").write_text(json.dumps(legacy_data))

    from src.models.schedule_models import Schedule
    schedule = Schedule.from_dict(legacy_data[0])
    assert schedule.repeat_count is None, "Legacy schedule should default to unlimited"
    assert schedule.fire_count == 0, "Legacy schedule fire_count should default to 0"


# ---------------------------------------------------------------------------
# 6. update_schedule auto-deletes when fire_count >= new repeat_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_schedule_auto_deletes_when_threshold_crossed():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=5, fire_count=3)
    svc._schedules[schedule.schedule_id] = schedule

    with pytest.raises(_ScheduleAutoDeletedError) as exc_info:
        await svc.update_schedule(schedule.schedule_id, repeat_count=2)

    assert exc_info.value.schedule.schedule_id == schedule.schedule_id
    assert schedule.schedule_id not in svc._schedules


@pytest.mark.asyncio
async def test_update_schedule_does_not_delete_when_threshold_not_crossed():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=3, fire_count=1)
    svc._schedules[schedule.schedule_id] = schedule

    updated = await svc.update_schedule(schedule.schedule_id, repeat_count=5)

    assert updated.repeat_count == 5
    assert schedule.schedule_id in svc._schedules


@pytest.mark.asyncio
async def test_update_schedule_set_repeat_count_to_unlimited():
    svc = _make_svc()
    schedule = _make_schedule(repeat_count=3, fire_count=2)
    svc._schedules[schedule.schedule_id] = schedule

    updated = await svc.update_schedule(schedule.schedule_id, repeat_count=None)

    assert updated.repeat_count is None
    assert schedule.schedule_id in svc._schedules
