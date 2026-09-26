"""Tests for src/backend_supervisor.py (issue #498, Phase 3).

Mocks asyncio.create_subprocess_exec — no real subprocess spawned. Live,
two-real-process verification (auto-start, crash-triggers-restart, clean
shutdown with no orphans) was performed manually against real processes;
these tests cover the trickier logic (restart cap -> degraded, port
allocation) that's impractical to exercise live repeatedly.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.raw_log_rotator import RotatingRawLogWriter
from src.backend_supervisor import BackendSupervisor, _allocate_free_port


class FakeStreamReader:
    """Minimal async-iterable stand-in for asyncio.StreamReader, used since the
    log pump now reads from process.stdout (a PIPE) instead of a shared fd."""

    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = list(chunks or [])

    async def read(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


async def _never_exits():
    """AsyncMock side_effect for a process.wait() that should never resolve.

    Must be an actual async function (not a lambda returning a coroutine) so
    AsyncMock awaits it — a lambda would just return the inner coroutine
    object unawaited, leaving it un-run and making the mocked "returncode"
    that coroutine object instead of ever genuinely hanging.
    """
    await asyncio.sleep(100)


def test_allocate_free_port_returns_distinct_ports():
    ports = {_allocate_free_port() for _ in range(5)}
    assert len(ports) == 5
    assert all(1024 < p < 65536 for p in ports)


def test_supervisor_generates_fresh_token_and_port(tmp_path):
    a = BackendSupervisor(data_dir=tmp_path)
    b = BackendSupervisor(data_dir=tmp_path)
    assert a.token != b.token
    assert a.port != b.port
    assert a.base_url == f"http://127.0.0.1:{a.port}"


def test_build_command_includes_required_flags(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    cmd = sup._build_command()
    assert "-m" in cmd and "backend.main" in cmd
    assert "--host" in cmd and "127.0.0.1" in cmd
    assert "--port" in cmd and str(sup.port) in cmd
    assert "--token" in cmd and sup.token in cmd
    assert "--data-dir" in cmd and str(tmp_path) in cmd
    # Tells backend/main.py it's auto-started, not a manual/remote invocation
    # (issue #1850) — enables the loopback+Docker-bridge embedded bind set.
    assert "--embedded" in cmd
    # Never spawned via "uv run" — see class docstring for why (SIGTERM propagation).
    assert cmd[0] != "uv"


def test_build_command_passes_through_mock_sdk_and_fixtures(tmp_path):
    fixtures = tmp_path / "fixtures"
    sup = BackendSupervisor(data_dir=tmp_path, mock_sdk=True, fixtures_dir=fixtures)
    cmd = sup._build_command()
    assert "--mock-sdk" in cmd
    assert "--fixtures-dir" in cmd
    assert str(fixtures) in cmd


def test_build_command_passes_through_experimental_and_extra_args(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path, experimental=True, extra_backend_args=["--debug-sdk"])
    cmd = sup._build_command()
    assert "--experimental" in cmd
    assert "--debug-sdk" in cmd


@pytest.mark.asyncio
async def test_wait_ready_returns_true_once_backend_reports_ready(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    client = MagicMock()
    client.health = AsyncMock(return_value=True)
    client.ready = AsyncMock(return_value=True)

    result = await sup.wait_ready(client)

    assert result is True


@pytest.mark.asyncio
async def test_wait_ready_times_out_if_never_live(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    client = MagicMock()
    client.health = AsyncMock(return_value=False)
    client.ready = AsyncMock(return_value=False)

    with patch("src.backend_supervisor._READINESS_TIMEOUT", 0.3), \
         patch("src.backend_supervisor._READINESS_POLL_INTERVAL", 0.05):
        result = await sup.wait_ready(client)

    assert result is False


@pytest.mark.asyncio
async def test_monitor_loop_restarts_after_unexpected_exit(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._rotator = RotatingRawLogWriter(sup.log_dir / "backend.log")

    proc1 = MagicMock()
    proc1.wait = AsyncMock(return_value=1)
    proc1.returncode = None
    proc1.stdout = FakeStreamReader()
    # proc2 represents the still-running respawned process at the point stop()
    # is called: its wait() must stay pending until terminate()/kill() is
    # actually invoked (mirroring a real subprocess), not resolve instantly —
    # otherwise the monitor loop would observe a spurious second "exit" and
    # crash-loop again, and not hang forever either — otherwise sup.stop()'s
    # own wait_for() calls below would stall for the real _SHUTDOWN_TIMEOUT.
    proc2 = MagicMock()
    proc2_terminated = asyncio.Event()
    proc2.terminate = MagicMock(side_effect=proc2_terminated.set)
    proc2.kill = MagicMock(side_effect=proc2_terminated.set)

    async def _proc2_wait():
        await proc2_terminated.wait()
        return -15  # SIGTERM

    proc2.wait = AsyncMock(side_effect=_proc2_wait)
    proc2.returncode = None
    proc2.stdout = FakeStreamReader()

    call_count = 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return proc1 if call_count == 1 else proc2

    # Real (unmocked) sleep here — the first restart's backoff is 2**0 == 1 real
    # second, short enough to just wait it out rather than fight event-loop
    # scheduling by mocking asyncio.sleep (which doesn't reliably yield to the
    # monitor task the way a genuine suspension point does).
    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         patch("src.backend_supervisor._RESTART_WINDOW_SECONDS", 300):
        await sup._spawn()
        sup._monitor_task = asyncio.create_task(sup._monitor_loop())
        for _ in range(50):  # bounded wait (~5s) for the loop to observe proc1's exit and restart
            if call_count >= 2:
                break
            await asyncio.sleep(0.1)
        await sup.stop()

    assert call_count == 2  # initial spawn (proc1) + one restart (proc2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "returncode, expected_fragments",
    [
        # T2/AC5/AC6: a SIGKILL'd Backend must log both the raw code and
        # resolved signal name, at ERROR (so it reaches error.log).
        pytest.param(-9, ["code=-9", "signal=SIGKILL"], id="sigkill"),
        # Regression guard for the warning->error bump (AC6): a plain nonzero
        # exit (not signal-based) must also log at ERROR, not warning.
        pytest.param(1, ["code=1"], id="plain-nonzero-exit"),
        # Regression guard: an unmapped real-time signal number (not in
        # Python's signal.Signals enum) must not crash the monitor loop —
        # still log the raw code, just without a resolved signal name.
        pytest.param(-32, ["code=-32"], id="unmapped-realtime-signal"),
    ],
)
async def test_unexpected_exit_logs_at_error_level(
    tmp_path, caplog, returncode, expected_fragments
):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._rotator = RotatingRawLogWriter(sup.log_dir / "backend.log")

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.returncode = None
    proc.stdout = FakeStreamReader()
    sup._process = proc

    respawned = MagicMock()
    respawned.wait = AsyncMock(side_effect=_never_exits)
    respawned.returncode = None
    respawned.stdout = FakeStreamReader()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return respawned

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         caplog.at_level(logging.ERROR, logger="src.backend_supervisor"):
        monitor_task = asyncio.create_task(sup._monitor_loop())
        for _ in range(50):  # bounded wait for the loop to observe the exit
            if any("exited unexpectedly" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.05)
        sup._stopping = True
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    exit_records = [r for r in caplog.records if "exited unexpectedly" in r.getMessage()]
    assert len(exit_records) == 1
    assert exit_records[0].levelno == logging.ERROR
    for fragment in expected_fragments:
        assert fragment in exit_records[0].getMessage()


@pytest.mark.asyncio
async def test_monitor_loop_marks_degraded_after_exceeding_restart_cap(tmp_path, caplog):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._rotator = RotatingRawLogWriter(sup.log_dir / "backend.log")

    import asyncio

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=1)  # always "crashes" immediately
    proc.returncode = None
    proc.stdout = FakeStreamReader()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         patch("src.backend_supervisor._RESTART_WINDOW_SECONDS", 300):
        await sup._spawn()
        # Drive the monitor loop directly (no sleep-based backoff) by patching sleep to a no-op,
        # so the test doesn't take the real exponential-backoff wall-clock time.
        with patch("asyncio.sleep", new=AsyncMock(return_value=None)), \
             caplog.at_level(logging.ERROR, logger="src.backend_supervisor"):
            await asyncio.wait_for(sup._monitor_loop(), timeout=5)

    assert sup.degraded is True
    degraded_records = [
        r for r in caplog.records
        if "marking degraded" in r.getMessage() and r.levelno == logging.ERROR
    ]
    assert degraded_records, "degraded transition must be logged at ERROR level (AC6)"


@pytest.mark.asyncio
async def test_stop_terminates_process_and_drains_log_pump(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._rotator = RotatingRawLogWriter(sup.log_dir / "backend.log")

    import asyncio

    proc = MagicMock()
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.terminate = MagicMock()
    proc.stdout = FakeStreamReader([b"tail output\n"])
    sup._process = proc
    sup._pump_tasks.append(asyncio.create_task(sup._pump_log(proc)))
    sup._monitor_task = asyncio.create_task(asyncio.sleep(100))

    await sup.stop()

    proc.terminate.assert_called_once()
    assert all(task.done() for task in sup._pump_tasks)
    assert (sup.log_dir / "backend.log").read_bytes() == b"tail output\n"


@pytest.mark.asyncio
async def test_pump_log_keeps_draining_after_write_failure(tmp_path):
    """Regression: if RotatingRawLogWriter.write() ever raises (e.g. disk
    full), _pump_log must keep draining the pipe rather than dying — a dead
    pump leaves Backend's stdout pipe unread, which fills its kernel buffer
    and blocks Backend's own writes forever."""
    sup = BackendSupervisor(data_dir=tmp_path)
    sup._rotator = MagicMock()
    sup._rotator.write = MagicMock(side_effect=OSError("disk full"))

    proc = MagicMock()
    proc.stdout = FakeStreamReader([b"chunk-1", b"chunk-2"])

    await sup._pump_log(proc)  # must return normally, not raise

    assert sup._rotator.write.call_count == 1
