"""
Tests for script schedule execution (issue #1356).

Covers: fire path, auto-start, template expansion, docker routing,
reset-enqueue guard (2-condition variant), inflight tracking, migration.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.legion.scheduler_service import SchedulerService
from src.models.schedule_models import (
    MAX_STREAM_BYTES,
    Schedule,
    ScheduleStatus,
    cap_stream,
    get_next_run,
)
from src.session_manager import SessionState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session_info(
    session_id: str = "sess-1",
    state: SessionState = SessionState.ACTIVE,
    is_processing: bool = False,
    docker_enabled: bool = False,
    working_directory: str = "/work",
):
    info = MagicMock()
    info.session_id = session_id
    info.name = "Test Session"
    info.state = state
    info.is_processing = is_processing
    info.config = {"docker_enabled": docker_enabled}
    info.working_directory = working_directory
    return info


def _make_system(session_info=None, data_dir: Path | None = None, enqueue_result=None):
    if session_info is None:
        session_info = _make_session_info()
    if enqueue_result is None:
        enqueue_result = {"queue_id": "q-1"}

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    queue_manager = MagicMock()
    queue_manager.get_pending_count = MagicMock(return_value=0)

    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.queue_manager = queue_manager
    coordinator.start_session = AsyncMock(return_value=True)
    coordinator.enqueue_message = AsyncMock(return_value=enqueue_result)
    coordinator.data_dir = data_dir or Path("/nonexistent")

    system = MagicMock()
    system.session_coordinator = coordinator
    return system


def _make_schedule(
    schedule_id: str = "sched-1",
    legion_id: str = "legion-1",
    minion_id: str = "sess-1",
    schedule_type: str = "script",
    script_command: str = "echo hello",
    script_timeout_seconds: int = 5,
    reset_session: bool = False,
    max_retries: int = 3,
    prompt: str = "",
    next_run: float | None = None,
) -> Schedule:
    return Schedule(
        schedule_id=schedule_id,
        legion_id=legion_id,
        name="Test Schedule",
        cron_expression="*/1 * * * *",
        prompt=prompt,
        minion_id=minion_id,
        schedule_type=schedule_type,
        script_command=script_command,
        script_timeout_seconds=script_timeout_seconds,
        reset_session=reset_session,
        max_retries=max_retries,
        next_run=next_run if next_run is not None else get_next_run("*/1 * * * *"),
        status=ScheduleStatus.ACTIVE,
    )


@pytest.fixture
def tmp_session_dir(tmp_path) -> Path:
    """Create a proper session directory structure under tmp_path."""
    session_dir = tmp_path / "sessions" / "sess-1"
    session_dir.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Script stdout → delivered message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_with_stdout_delivers_message(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(0, "CI is red\n", "", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "delivered"
    assert schedule.last_exit_code == 0
    system.session_coordinator.enqueue_message.assert_awaited_once()
    call_kwargs = system.session_coordinator.enqueue_message.call_args.kwargs
    assert "CI is red" in call_kwargs["content"]
    assert call_kwargs["metadata"]["script_filtered"] is True


# ---------------------------------------------------------------------------
# 2. Empty stdout → discarded (no agent touched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_with_empty_stdout_discards(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(0, "", "", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "discarded"
    system.session_coordinator.enqueue_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. Non-zero exit → error, no agent touched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_nonzero_exit_records_error(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(1, "", "something failed", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "error"
    assert schedule.last_exit_code == 1
    system.session_coordinator.enqueue_message.assert_not_awaited()
    execution = svc._append_execution.call_args.args[1]
    assert "code 1" in execution.error_message


# ---------------------------------------------------------------------------
# 4. Timeout → error recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_timeout_killed_and_recorded_as_error(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule(script_timeout_seconds=1)

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(-1, "", "Script exceeded timeout", True))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "error"
    execution = svc._append_execution.call_args.args[1]
    assert "timeout" in execution.error_message.lower()


# ---------------------------------------------------------------------------
# 5. Docker mode → uses run_command_in_container
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_in_docker_mode_routes_through_docker_exec(tmp_session_dir):
    session_info = _make_session_info(docker_enabled=True)
    system = _make_system(session_info=session_info, data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()

    with (
        patch("src.legion.scheduler_service.find_session_container", new=AsyncMock(return_value="container-abc")),
        patch("src.legion.scheduler_service.run_command_in_container", new=AsyncMock(return_value=(0, "output\n", "", False))) as mock_exec,
        patch("src.legion.scheduler_service.run_command_on_host") as mock_host,
    ):
        await svc._fire_script_schedule(schedule, 1000.0)

    mock_exec.assert_awaited_once()
    mock_host.assert_not_called()
    assert schedule.last_status == "delivered"


# ---------------------------------------------------------------------------
# 6. Prompt schedule path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_prompt_schedule_path_unchanged():
    system = _make_system()
    svc = SchedulerService(system)
    svc._fire_permanent_schedule = AsyncMock()
    svc._fire_ephemeral_schedule = AsyncMock()

    schedule = _make_schedule(schedule_type="prompt", prompt="Do the thing")
    schedule.session_config = None

    now = 1000.0
    await svc._tick.__func__(svc)  # Can't easily call _tick without setup; test dispatch directly
    # Direct delegation test
    await svc._fire_schedule(schedule, now)
    svc._fire_permanent_schedule.assert_awaited_once_with(schedule, now, trigger="cron")


# ---------------------------------------------------------------------------
# 7. Auto-start when session is TERMINATED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_start_session_when_terminated(tmp_session_dir):
    terminated_info = _make_session_info(state=SessionState.TERMINATED)
    active_info = _make_session_info(state=SessionState.ACTIVE)

    call_count = 0

    async def _get_info(session_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return terminated_info
        return active_info

    system = _make_system(data_dir=tmp_session_dir)
    system.session_coordinator.session_manager.get_session_info = AsyncMock(side_effect=_get_info)
    svc = SchedulerService(system)

    result = await svc._ensure_session_active("sess-1", timeout=5.0)

    assert result is True
    system.session_coordinator.start_session.assert_awaited_once_with("sess-1")


# ---------------------------------------------------------------------------
# 8. Auto-start failure → error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_start_failure_recorded_as_error(tmp_session_dir):
    error_info = _make_session_info(state=SessionState.ERROR)
    system = _make_system(data_dir=tmp_session_dir)
    system.session_coordinator.session_manager.get_session_info = AsyncMock(return_value=error_info)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()

    await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "error"
    system.session_coordinator.enqueue_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# 9. Script fire dispatches as task (non-blocking tick)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_run_does_not_block_tick_loop(tmp_session_dir):
    """Verify _tick fires scripts as tasks so the loop is not blocked."""
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._schedules = {}
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    fired = []

    async def _slow_script(schedule, target_id, now, trigger="cron"):
        await asyncio.sleep(0.05)
        fired.append(schedule.schedule_id)

    svc._fire_script_with_cleanup = _slow_script

    schedule = _make_schedule(next_run=0.0)
    svc._schedules[schedule.schedule_id] = schedule

    import time as _time
    start = _time.monotonic()
    await svc._tick()
    elapsed = _time.monotonic() - start

    # _tick should return without waiting for the async task
    assert elapsed < 0.04, f"Tick blocked: {elapsed:.3f}s"
    # Allow the task to complete
    await asyncio.sleep(0.1)
    assert schedule.schedule_id in fired


# ---------------------------------------------------------------------------
# 10. Same-schedule overlap guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_fire_skipped_when_inflight(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._schedules = {}
    svc._persist_schedules = AsyncMock()

    fire_calls = []

    async def _fire(schedule, target_id, now, trigger="cron"):
        fire_calls.append(schedule.schedule_id)

    svc._fire_script_with_cleanup = _fire

    schedule = _make_schedule(next_run=0.0)
    svc._schedules[schedule.schedule_id] = schedule

    # Mark as already in-flight
    svc._inflight_scripts_by_session["sess-1"] = {schedule.schedule_id}

    await svc._tick()
    await asyncio.sleep(0)

    assert len(fire_calls) == 0, "Should be skipped when already inflight"


# ---------------------------------------------------------------------------
# 11. has_inflight_scripts returns True while schedule is in-flight
# ---------------------------------------------------------------------------


def test_has_inflight_scripts_returns_true_during_execution():
    system = _make_system()
    svc = SchedulerService(system)
    svc._inflight_scripts_by_session["sess-1"] = {"sched-1"}
    assert svc.has_inflight_scripts("sess-1") is True


# ---------------------------------------------------------------------------
# 12. has_inflight_scripts returns False after cleanup
# ---------------------------------------------------------------------------


def test_has_inflight_scripts_returns_false_after_cleanup():
    system = _make_system()
    svc = SchedulerService(system)
    svc._inflight_scripts_by_session["sess-1"] = {"sched-1"}
    # Simulate _fire_script_with_cleanup finally block
    session_set = svc._inflight_scripts_by_session.get("sess-1", set())
    session_set.discard("sched-1")
    if not session_set:
        svc._inflight_scripts_by_session.pop("sess-1", None)
    assert svc.has_inflight_scripts("sess-1") is False


# ---------------------------------------------------------------------------
# 13. Script always enqueues immediately — no pre-enqueue guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_script_always_enqueues_immediately_no_pre_wait(tmp_session_dir):
    """Scripts must enqueue without any pre-enqueue wait, even with reset_session=True."""
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    # Mark another script as in-flight; in r4 this must NOT block enqueue
    svc._inflight_scripts_by_session["sess-1"] = {"other-sched"}

    schedule = _make_schedule(reset_session=True)

    enqueued = []

    async def _fast_enqueue(**kwargs):
        enqueued.append(kwargs)
        return {"queue_id": "q-1"}

    system.session_coordinator.enqueue_message = AsyncMock(side_effect=_fast_enqueue)

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(0, "output\n", "", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.last_status == "delivered"
    assert len(enqueued) == 1, "Should enqueue immediately without waiting"


# ---------------------------------------------------------------------------
# 14. Queue processor idle wait pauses while scripts are in flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_processor_idle_wait_pauses_while_scripts_inflight():
    """_wait_for_idle must not return True while has_inflight_scripts is True."""
    from src.queue_processor import QueueProcessor

    coordinator = MagicMock()
    coordinator.session_manager = MagicMock()

    scheduler = MagicMock()
    # First two calls: scripts in flight; third: clear
    call_count = 0

    def _has_inflight(sid):
        nonlocal call_count
        call_count += 1
        return call_count <= 2

    scheduler.has_inflight_scripts = _has_inflight

    legion_system = MagicMock()
    legion_system.scheduler_service = scheduler
    coordinator.legion_system = legion_system

    session_info = _make_session_info(is_processing=False)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=session_info)

    proc = QueueProcessor(coordinator)

    completed = await proc._wait_for_idle("sess-1", "q-1", min_idle_seconds=0.0)

    assert completed is True
    # must have polled at least 3 times (twice inflight, once clear+idle)
    assert call_count >= 3


# ---------------------------------------------------------------------------
# 14b. Queue processor idle wait resumes after scripts complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_processor_idle_wait_resumes_after_scripts_complete():
    """Once has_inflight_scripts returns False, idle timer starts and _wait_for_idle returns."""
    from src.queue_processor import QueueProcessor

    coordinator = MagicMock()
    scheduler = MagicMock()
    scheduler.has_inflight_scripts = MagicMock(return_value=False)

    legion_system = MagicMock()
    legion_system.scheduler_service = scheduler
    coordinator.legion_system = legion_system

    session_info = _make_session_info(is_processing=False)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=session_info)

    proc = QueueProcessor(coordinator)
    completed = await proc._wait_for_idle("sess-1", "q-1", min_idle_seconds=0.0)

    assert completed is True


# ---------------------------------------------------------------------------
# 14c. Queue processor unaffected when legion_system is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_processor_unaffected_when_legion_system_is_none():
    """Single-agent installs (no legion_system) must behave exactly as before."""
    from src.queue_processor import QueueProcessor

    coordinator = MagicMock()
    coordinator.legion_system = None

    session_info = _make_session_info(is_processing=False)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=session_info)

    proc = QueueProcessor(coordinator)
    completed = await proc._wait_for_idle("sess-1", "q-1", min_idle_seconds=0.0)

    assert completed is True


# ---------------------------------------------------------------------------
# 15. Failure count resets on discarded or delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_count_resets_on_discarded_or_delivered(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()
    schedule.failure_count = 2

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(0, "", "", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.failure_count == 0
    assert schedule.last_status == "discarded"


# ---------------------------------------------------------------------------
# 16. Failure count → pause after max_retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_count_pauses_after_max_retries_on_error(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule(max_retries=2)
    schedule.failure_count = 2  # Already at max; next error should pause

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(1, "", "fail", False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert schedule.status == ScheduleStatus.PAUSED
    assert schedule.failure_count == 3


# ---------------------------------------------------------------------------
# 17. Legacy schedule without schedule_type field loads as prompt
# ---------------------------------------------------------------------------


def test_load_legacy_schedule_without_schedule_type_field():
    raw = {
        "schedule_id": "old-1",
        "legion_id": "l-1",
        "name": "Old Schedule",
        "cron_expression": "0 * * * *",
        "prompt": "Do something",
        "status": "active",
        "reset_session": False,
        "max_retries": 3,
        "timeout_seconds": 3600,
        "created_at": 1000.0,
        "updated_at": 1000.0,
        "next_run": None,
        "last_run": None,
        "last_status": None,
        "execution_count": 0,
        "failure_count": 0,
    }
    schedule = Schedule.from_dict(raw)
    assert schedule.schedule_type == "prompt"
    assert schedule.script_command is None
    assert schedule.script_timeout_seconds == 60


# ---------------------------------------------------------------------------
# 18. stdout/stderr capped at MAX_STREAM_BYTES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_stderr_capped_at_max_stream_bytes(tmp_session_dir):
    system = _make_system(data_dir=tmp_session_dir)
    svc = SchedulerService(system)
    svc._persist_schedules = AsyncMock()
    svc._append_execution = AsyncMock()
    svc._broadcast_execution_event = AsyncMock()
    svc._broadcast_schedule_event = AsyncMock()

    schedule = _make_schedule()
    big = "x" * (MAX_STREAM_BYTES + 10000)

    with patch("src.legion.scheduler_service.run_command_on_host", new=AsyncMock(return_value=(1, big, big, False))):
        await svc._fire_script_schedule(schedule, 1000.0)

    assert len(schedule.last_stdout.encode("utf-8", errors="replace")) <= MAX_STREAM_BYTES + 100
    assert "[truncated:" in schedule.last_stdout
    assert len(schedule.last_stderr.encode("utf-8", errors="replace")) <= MAX_STREAM_BYTES + 100


def test_cap_stream_under_limit():
    s = "hello world"
    assert cap_stream(s) == s


def test_cap_stream_over_limit():
    big = "x" * (MAX_STREAM_BYTES + 1000)
    result = cap_stream(big)
    assert "[truncated:" in result
    assert len(result.encode("utf-8", errors="replace")) < MAX_STREAM_BYTES + 200


# ---------------------------------------------------------------------------
# 19. Template var expansion — session_id, working_dir
# ---------------------------------------------------------------------------


def test_template_var_expansion_session_id_working_dir_session_data(tmp_path):
    session_info = _make_session_info(session_id="abc-123", working_directory="/ws")
    system = _make_system(session_info=session_info, data_dir=tmp_path)
    svc = SchedulerService(system)

    # Create the session directory so the JSON file can be written
    (tmp_path / "sessions" / "abc-123").mkdir(parents=True)

    schedule = _make_schedule(
        script_command="myscript.sh {session_id} {working_dir}",
        minion_id="abc-123",
    )
    argv = svc._build_command_argv(schedule, session_info, docker_enabled=False)
    assert argv[0] == "myscript.sh"
    assert argv[1] == "abc-123"
    assert argv[2] == "/ws"


# ---------------------------------------------------------------------------
# 20. Unknown placeholder left intact
# ---------------------------------------------------------------------------


def test_template_var_unknown_placeholder_left_intact(tmp_path):
    session_info = _make_session_info(session_id="abc-123", working_directory="/ws")
    system = _make_system(session_info=session_info, data_dir=tmp_path)
    svc = SchedulerService(system)

    (tmp_path / "sessions" / "abc-123").mkdir(parents=True)

    schedule = _make_schedule(
        script_command='jq --arg x "{some_json}" .',
        minion_id="abc-123",
    )
    argv = svc._build_command_argv(schedule, session_info, docker_enabled=False)
    joined = " ".join(argv)
    assert "{some_json}" in joined


# ---------------------------------------------------------------------------
# 21. session_data path: host vs docker
# ---------------------------------------------------------------------------


def test_template_var_session_data_json_path_translation(tmp_path):
    session_info = _make_session_info(session_id="abc-123")
    system = _make_system(session_info=session_info, data_dir=tmp_path)
    svc = SchedulerService(system)

    (tmp_path / "sessions" / "abc-123").mkdir(parents=True)

    schedule = _make_schedule(
        script_command="script.sh {session_data}",
        minion_id="abc-123",
        schedule_id="sched-X",
    )

    # Host mode: path should be under the session's tmp dir, not the bare /tmp container path
    argv_host = svc._build_command_argv(schedule, session_info, docker_enabled=False)
    assert argv_host[1].endswith("schedule_sched-X_data.json")
    assert argv_host[1] != "/tmp/schedule_sched-X_data.json"  # must be a host filesystem path

    # Docker mode: path should be exactly /tmp/schedule_<id>_data.json (container /tmp)
    argv_docker = svc._build_command_argv(schedule, session_info, docker_enabled=True)
    assert argv_docker[1] == "/tmp/schedule_sched-X_data.json"


# ---------------------------------------------------------------------------
# 22. schedule_type immutable via update request validation
# ---------------------------------------------------------------------------


def test_schedule_type_immutable_in_update_request():
    from src.routers._models import ScheduleUpdateRequest

    req = ScheduleUpdateRequest(name="new name")
    raw = req.model_dump()
    # schedule_type should not be a field in ScheduleUpdateRequest
    assert "schedule_type" not in raw
