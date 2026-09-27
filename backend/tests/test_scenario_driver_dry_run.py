"""Scenario driver dry-run tests (issue #2038).

The mock SDK's raw-log replay is not interactive: `backend/mock_sdk.py`'s
raw-replay path (`_start_raw_replay`/`RawFixtureReplay._parse()`) only
reconstructs `kind == "sdk_message"` records from `raw_log.jsonl` and wires
no `permission_callback` at all. Only 3 of the 9 `REQUIRED_MARKERS`
(streaming deltas, subagent task with progress, compaction) are things
passive raw replay can produce; the rest depend on mechanics raw replay
doesn't implement, and need a real, credentialed session to exercise instead
— out of scope for this suite, which covers what's testable without
credentials:

- T1 (reduced scope): mock replay of the existing fixture produces at least
  one event for each of the 3 passively-replayable markers, and
  `predicates.py` correctly matches them.
- T2: with one `StreamEvent` record stripped from a copy of the fixture, the
  corresponding wait step times out with a message naming the awaited event,
  independent of whether the underlying replay is interactive.
- T3: `PollConsumer`'s reconnect logic survives a full Backend+Frontend
  process restart — pure poll-transport behavior, unrelated to whether the
  SDK underneath is mocked or real.
- Self-check: every `fixture_export.REQUIRED_MARKERS` entry is claimed by at
  least one scenario in `scenarios.py` — a drift guard between the two files.
"""

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from backend.fixture_export import REQUIRED_MARKERS
from backend.tools.scenario_driver import predicates
from backend.tools.scenario_driver.poll_consumer import (
    PollConsumer,
    SharedEventBuffer,
    TaggedEvent,
    WaitTimeoutError,
    wait_for,
)
from backend.tools.scenario_driver.scenarios import build_scenarios

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURES_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "raw"
PRIMARY_FIXTURE = "2026-09-23-primary"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_predicate(predicate, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - retry on any transient error
            last_exc = exc
        time.sleep(0.3)
    raise AssertionError(f"Timed out waiting for: {description} (last error: {last_exc})")


def _spawn_backend(port: int, token: str, data_dir: Path, fixtures_dir: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable, "-m", "backend.main",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--token", token,
            "--data-dir", str(data_dir),
            "--mock-sdk",
            "--fixtures-dir", str(fixtures_dir),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _spawn_frontend(port: int, backend_port: int, backend_token: str, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable, "main.py",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--no-auth",
            "--remote-backend-url", f"http://127.0.0.1:{backend_port}",
            "--remote-backend-token", backend_token,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _terminate(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _base_env() -> dict:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _create_and_start_session(base_url: str, token: str, name: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    project = httpx.post(
        f"{base_url}/api/projects", json={"name": "t", "working_directory": str(REPO_ROOT)},
        headers=headers, timeout=10,
    ).json()
    project_id = project["project"]["project_id"]
    session = httpx.post(
        f"{base_url}/api/sessions",
        json={"project_id": project_id, "name": name},
        headers=headers, timeout=10,
    ).json()
    session_id = session["session_id"]
    # Raw-mode mock replay is fully synchronous within `start()` — this call
    # blocks until the entire fixture has been replayed into the queues.
    httpx.post(f"{base_url}/api/sessions/{session_id}/start", headers=headers, timeout=120)
    return session_id


def _all_events(base_url: str, token: str, session_id: str) -> list[TaggedEvent]:
    body = httpx.get(
        f"{base_url}/api/poll/session/{session_id}", params={"since": 0, "timeout": 2},
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    ).json()
    return [TaggedEvent(source="session", event=e, seq=i) for i, e in enumerate(body["events"])]


def test_required_markers_all_covered_by_scenarios():
    """Drift guard: catches scenarios.py/fixture_export.py drift at test
    time rather than only at export time."""
    scenarios = build_scenarios(
        main_session_id="m", minion_id="mm", legion_id="l", scratch_repo=Path("/tmp/unused")
    )
    claimed: set[str] = set()
    for scenario in scenarios:
        claimed.update(scenario.coverage_markers)

    missing = [marker for marker in REQUIRED_MARKERS if marker not in claimed]
    assert not missing, f"REQUIRED_MARKERS not claimed by any scenario: {missing}"


@pytest.mark.skipif(not RAW_FIXTURES_DIR.exists(), reason="raw fixtures directory not present")
@pytest.mark.timeout(90)  # backend.main boot + full raw-log replay needs more than the 30s default
def test_t1_mock_replay_produces_passively_replayable_markers(tmp_path: Path):
    """T1 (reduced scope): the 3 markers raw replay can produce passively
    (streaming deltas, subagent task with progress, compaction) actually
    appear in the replayed stream, and predicates.py matches them."""
    port = _free_port()
    token = secrets.token_urlsafe(16)
    env = _base_env()

    proc = _spawn_backend(port, token, tmp_path / "data", RAW_FIXTURES_DIR, env)
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_predicate(
            lambda: httpx.get(f"{base_url}/health", timeout=1).status_code == 200,
            timeout=20, description="backend.main /health",
        )

        session_id = _create_and_start_session(base_url, token, PRIMARY_FIXTURE)
        events = _all_events(base_url, token, session_id)
        assert events, "raw replay produced no events at all"

        assert any(predicates.assistant_delta(session_id)(e) for e in events), (
            "expected at least one assistant_delta event from raw replay"
        )
        assert any(predicates.task_event(session_id, ("task_started", "task_progress", "task_notification"))(e)
                   for e in events), "expected at least one subagent task event from raw replay"
        assert any(predicates.system_subtype(session_id, "compact_boundary")(e) for e in events), (
            "expected at least one compaction boundary event from raw replay"
        )
    finally:
        _terminate(proc)
        assert proc.poll() is not None


@pytest.mark.skipif(not RAW_FIXTURES_DIR.exists(), reason="raw fixtures directory not present")
@pytest.mark.timeout(90)  # backend.main boot + full raw-log replay needs more than the 30s default
def test_t2_missing_event_times_out_with_clear_message(tmp_path: Path):
    """T2: strip every `StreamEvent` record from a copy of the fixture, then
    assert the wait for a streaming delta times out naming the awaited
    event."""
    mutated_root = tmp_path / "fixtures"
    mutated_name = f"{PRIMARY_FIXTURE}-no-stream"
    mutated_dir = mutated_root / mutated_name
    shutil.copytree(RAW_FIXTURES_DIR / PRIMARY_FIXTURE, mutated_dir)

    raw_log = mutated_dir / "raw_log.jsonl"
    kept_lines = [
        line for line in raw_log.read_text().splitlines()
        if json.loads(line).get("_type") != "StreamEvent"
    ]
    raw_log.write_text("\n".join(kept_lines) + "\n")

    port = _free_port()
    token = secrets.token_urlsafe(16)
    env = _base_env()

    proc = _spawn_backend(port, token, tmp_path / "data", mutated_root, env)
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_predicate(
            lambda: httpx.get(f"{base_url}/health", timeout=1).status_code == 200,
            timeout=20, description="backend.main /health",
        )

        session_id = _create_and_start_session(base_url, token, mutated_name)
        events = _all_events(base_url, token, session_id)
        assert not any(predicates.assistant_delta(session_id)(e) for e in events), (
            "expected zero assistant_delta events after stripping StreamEvent records"
        )

        async def seed_and_wait():
            buffer = SharedEventBuffer()
            for tagged in events:
                await buffer.append(tagged.source, tagged.event)
            await wait_for(
                buffer, predicates.assistant_delta(session_id), timeout=1.0,
                description="scenario 1: streaming delta",
            )

        with pytest.raises(WaitTimeoutError) as exc_info:
            asyncio.run(seed_and_wait())
        assert "scenario 1: streaming delta" in str(exc_info.value)
        assert "Timed out waiting for" in str(exc_info.value)
    finally:
        _terminate(proc)
        assert proc.poll() is not None


@pytest.mark.skipif(not RAW_FIXTURES_DIR.exists(), reason="raw fixtures directory not present")
@pytest.mark.timeout(150)  # two full backend+frontend boot cycles needs more than the 30s default
def test_t3_poll_consumer_reconnects_after_full_app_restart(tmp_path: Path):
    """T3: a full Backend+Frontend process restart — `PollConsumer` detects
    the outage, waits for `/ready`, and resumes."""
    backend_port = _free_port()
    frontend_port = _free_port()
    backend_token = secrets.token_urlsafe(16)
    env = _base_env()
    base_url = f"http://127.0.0.1:{frontend_port}"
    data_dir = tmp_path / "backend_data"
    procs: list[subprocess.Popen] = []

    def spawn_pair() -> None:
        backend_proc = _spawn_backend(backend_port, backend_token, data_dir, RAW_FIXTURES_DIR, env)
        procs.append(backend_proc)
        _wait_for_predicate(
            lambda: httpx.get(f"http://127.0.0.1:{backend_port}/health", timeout=1).status_code == 200,
            timeout=20, description="backend.main /health",
        )
        frontend_proc = _spawn_frontend(frontend_port, backend_port, backend_token, env)
        procs.append(frontend_proc)
        _wait_for_predicate(
            lambda: httpx.get(f"{base_url}/ready", timeout=1).json().get("ready") is True,
            timeout=20, description="main.py /ready",
        )

    async def drive() -> None:
        buffer = SharedEventBuffer()
        consumer = PollConsumer(
            stream="ui", base_url=base_url, token=None, buffer=buffer,
            reconnect_after_failures=2, reconnect_timeout=60.0,
        )
        consumer.start()
        try:
            await asyncio.sleep(1.0)

            # Full app restart: kill both processes, then respawn a fresh pair.
            for proc in procs:
                _terminate(proc)
            procs.clear()
            spawn_pair()

            # Prove the reconnected consumer resumes: a fresh project creation
            # after respawn must show up in its buffer.
            httpx.post(
                f"{base_url}/api/projects",
                json={"name": "post-restart", "working_directory": str(REPO_ROOT)},
                timeout=10,
            )
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if any(e.event.get("type") == "project_updated" for e in buffer.snapshot()):
                    return
                await asyncio.sleep(0.5)
            raise AssertionError("PollConsumer never resumed after full app restart")
        finally:
            await consumer.stop()

    spawn_pair()
    try:
        asyncio.run(drive())
    finally:
        for proc in procs:
            _terminate(proc)
        for proc in procs:
            assert proc.poll() is not None
