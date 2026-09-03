"""Tests for src/backend_supervisor.py (issue #498, Phase 3).

Mocks asyncio.create_subprocess_exec — no real subprocess spawned. Live,
two-real-process verification (auto-start, crash-triggers-restart, clean
shutdown with no orphans) was performed manually against real processes;
these tests cover the trickier logic (restart cap -> degraded, port
allocation) that's impractical to exercise live repeatedly.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backend_supervisor import BackendSupervisor, _allocate_free_port


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
    sup._log_file = open(sup.log_dir / "backend.log", "ab")

    proc1 = MagicMock()
    proc1.wait = AsyncMock(return_value=1)
    proc1.returncode = None
    proc2 = MagicMock()
    proc2.wait = AsyncMock(side_effect=lambda: asyncio.sleep(100))  # never exits
    proc2.returncode = None

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
    sup._log_file.close()


@pytest.mark.asyncio
async def test_monitor_loop_marks_degraded_after_exceeding_restart_cap(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._log_file = open(sup.log_dir / "backend.log", "ab")

    import asyncio

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=1)  # always "crashes" immediately
    proc.returncode = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         patch("src.backend_supervisor._RESTART_WINDOW_SECONDS", 300):
        await sup._spawn()
        # Drive the monitor loop directly (no sleep-based backoff) by patching sleep to a no-op,
        # so the test doesn't take the real exponential-backoff wall-clock time.
        with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
            await asyncio.wait_for(sup._monitor_loop(), timeout=5)

    assert sup.degraded is True
    sup._log_file.close()


@pytest.mark.asyncio
async def test_stop_terminates_process_and_closes_log(tmp_path):
    sup = BackendSupervisor(data_dir=tmp_path)
    sup.log_dir.mkdir(parents=True, exist_ok=True)
    sup._log_file = open(sup.log_dir / "backend.log", "ab")

    import asyncio

    proc = MagicMock()
    proc.returncode = None
    proc.wait = AsyncMock(return_value=0)
    proc.terminate = MagicMock()
    sup._process = proc
    sup._monitor_task = asyncio.create_task(asyncio.sleep(100))

    await sup.stop()

    proc.terminate.assert_called_once()
    assert sup._log_file.closed
