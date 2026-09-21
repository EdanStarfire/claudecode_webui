"""Mandatory live two-process E2E lifecycle test (issue #498, Phase 5).

This is the issue's hard acceptance gate, not optional coverage: "a real
session runs its full lifecycle (create -> start -> message -> interrupt ->
terminate) end-to-end through the backend-relay mechanism, with zero code path
in the Frontend API that executes a session in-process." --mock-sdk still
counts as "real processes" per the plan — it only fakes the SDK subprocess
layer inside Backend, not the process boundary itself, so this exercises the
full two-process path with MockClaudeSDK doing deterministic fixture replay
instead of a live Anthropic API call.

Not marked @pytest.mark.slow — pyproject.toml's default addopts deselect slow
tests, and this test must never be silently skipped; it's the AC's core claim.
"""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "backend" / "tests" / "fixtures"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - genuinely want to retry on any transient error
            last_exc = exc
        time.sleep(0.3)
    raise AssertionError(f"Timed out waiting for: {description} (last error: {last_exc})")


def test_live_session_lifecycle_through_two_real_processes(tmp_path: Path):
    """create -> start -> message -> interrupt -> terminate, through two real
    subprocesses (backend.main + main.py), verified via the actual HTTP relay —
    not an in-process TestClient, not a stubbed Backend.
    """
    backend_port = _free_port()
    frontend_port = _free_port()
    backend_token = secrets.token_urlsafe(16)
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    backend_proc = subprocess.Popen(
        [
            sys.executable, "-m", "backend.main",
            "--host", "127.0.0.1",
            "--port", str(backend_port),
            "--token", backend_token,
            "--data-dir", str(tmp_path / "backend_data"),
            "--mock-sdk",
            "--fixtures-dir", str(FIXTURES_DIR),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    frontend_proc = None

    try:
        _wait_for(
            lambda: httpx.get(f"http://127.0.0.1:{backend_port}/health", timeout=1).status_code == 200,
            timeout=20,
            description="backend.main /health",
        )

        frontend_proc = subprocess.Popen(
            [
                sys.executable, "main.py",
                "--host", "127.0.0.1",
                "--port", str(frontend_port),
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
        _wait_for(
            lambda: httpx.get(f"http://127.0.0.1:{frontend_port}/ready", timeout=1).json().get("ready") is True,
            timeout=20,
            description="main.py /ready (gated on backend.main being ready)",
        )

        # --- Verify two distinct real processes, not one process wearing two hats ---
        assert backend_proc.pid != frontend_proc.pid
        assert backend_proc.poll() is None, "backend.main exited unexpectedly"
        assert frontend_proc.poll() is None, "main.py exited unexpectedly"
        ps_output = subprocess.run(["ps", "-p", f"{backend_proc.pid},{frontend_proc.pid}", "-o", "pid,comm"],
                                     capture_output=True, text=True).stdout
        assert str(backend_proc.pid) in ps_output
        assert str(frontend_proc.pid) in ps_output

        base_url = f"http://127.0.0.1:{frontend_port}"

        with httpx.Client(base_url=base_url, timeout=10) as client:
            # --- CREATE (project, then session) ---
            proj_resp = client.post("/api/projects", json={
                "name": "e2e-lifecycle-test",
                "working_directory": str(tmp_path),
            })
            assert proj_resp.status_code == 200, proj_resp.text
            project_id = proj_resp.json()["project"]["project_id"]

            sess_resp = client.post("/api/sessions", json={
                "project_id": project_id,
                "name": "single_turn",  # matches backend/tests/fixtures/single_turn
            })
            assert sess_resp.status_code == 200, sess_resp.text
            session_id = sess_resp.json()["session_id"]

            # --- START ---
            start_resp = client.post(f"/api/sessions/{session_id}/start")
            assert start_resp.status_code == 200, start_resp.text
            assert start_resp.json()["success"] is True

            def _is_active():
                info = client.get(f"/api/sessions/{session_id}").json()
                return info["session"]["state"] == "active"

            _wait_for(_is_active, timeout=15, description="session reaches ACTIVE state")

            # --- MESSAGE ---
            msg_resp = client.post(f"/api/sessions/{session_id}/messages", json={
                "message": "Hello from the live two-process E2E test",
            })
            assert msg_resp.status_code == 200, msg_resp.text
            assert msg_resp.json()["success"] is True

            # Confirm the message round-tripped all the way through the poll-relay:
            # Backend's SessionCoordinator -> Backend's EventQueue -> Backend's own
            # poll.py -> Frontend's poll_relay background task -> Frontend's local
            # EventQueue -> Frontend's poll.py -> this HTTP response. This is the
            # actual streaming mechanism the browser depends on, not a side detail.
            def _got_streamed_message():
                events = client.get(f"/api/poll/session/{session_id}?since=0&timeout=2").json()["events"]
                return any(e.get("type") == "message" for e in events)

            _wait_for(_got_streamed_message, timeout=15, description="streamed message event via poll-relay")

            # --- INTERRUPT ---
            interrupt_resp = client.post(f"/api/sessions/{session_id}/interrupt")
            assert interrupt_resp.status_code == 200, interrupt_resp.text
            assert "success" in interrupt_resp.json()

            # --- TERMINATE ---
            terminate_resp = client.post(f"/api/sessions/{session_id}/terminate")
            assert terminate_resp.status_code == 200, terminate_resp.text
            assert terminate_resp.json()["success"] is True

            def _is_terminated():
                info = client.get(f"/api/sessions/{session_id}").json()
                return info["session"]["state"] == "terminated"

            _wait_for(_is_terminated, timeout=15, description="session reaches TERMINATED state")

        # Re-confirm both processes are still the same two PIDs throughout — neither
        # crashed or got silently restarted mid-lifecycle.
        assert backend_proc.poll() is None
        assert frontend_proc.poll() is None

    finally:
        for proc in (frontend_proc, backend_proc):
            if proc is None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        # Verify clean shutdown left no orphaned process behind (issue #498's own
        # standard, applied to this test's own subprocesses too).
        time.sleep(1)
        for proc, label in ((backend_proc, "backend"), (frontend_proc, "frontend")):
            if proc is None:
                continue
            assert proc.poll() is not None, f"{label} process did not exit after terminate/kill"


@pytest.mark.timeout(90)  # a second backend.main boot + health/ready gate needs more than the 30s default
def test_live_session_survives_backend_restart_mid_poll(tmp_path: Path):
    """Issue #1984 (T2): Backend restarts in place (supervisor-restart-in-place,
    UC2) while Frontend — and a browser holding a pre-restart poll cursor —
    keeps running. The restarted Backend recreates every EventQueue at cursor 0
    (backend/web_server.py's message-callback registrar only lazily creates a
    session's queue the first time a message flows through in this new process,
    per _get_message_callback_registrar), so a stale, still-high `since` must
    not silently skip whatever gets appended post-restart. Before the fix,
    events_since() mis-sliced this into an out-of-range `[]`, and
    polling.js unconditionally adopted the new (lower) next_cursor, dropping
    every event in between with no signal anything was lost.
    """
    backend_port = _free_port()
    frontend_port = _free_port()
    backend_token = secrets.token_urlsafe(16)
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    data_dir = tmp_path / "backend_data"

    def _spawn_backend():
        return subprocess.Popen(
            [
                sys.executable, "-m", "backend.main",
                "--host", "127.0.0.1",
                "--port", str(backend_port),
                "--token", backend_token,
                "--data-dir", str(data_dir),
                "--mock-sdk",
                "--fixtures-dir", str(FIXTURES_DIR),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def _terminate(proc):
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    backend_proc = _spawn_backend()
    frontend_proc = None

    try:
        _wait_for(
            lambda: httpx.get(f"http://127.0.0.1:{backend_port}/health", timeout=1).status_code == 200,
            timeout=20,
            description="backend.main /health (pre-restart instance)",
        )

        frontend_proc = subprocess.Popen(
            [
                sys.executable, "main.py",
                "--host", "127.0.0.1",
                "--port", str(frontend_port),
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
        _wait_for(
            lambda: httpx.get(f"http://127.0.0.1:{frontend_port}/ready", timeout=1).json().get("ready") is True,
            timeout=20,
            description="main.py /ready (gated on backend.main being ready)",
        )

        base_url = f"http://127.0.0.1:{frontend_port}"

        with httpx.Client(base_url=base_url, timeout=10) as client:
            # --- Get a session to ACTIVE and confirm one message round-trips,
            # exactly like the baseline lifecycle test. ---
            proj_resp = client.post("/api/projects", json={
                "name": "e2e-restart-test",
                "working_directory": str(tmp_path),
            })
            assert proj_resp.status_code == 200, proj_resp.text
            project_id = proj_resp.json()["project"]["project_id"]

            sess_resp = client.post("/api/sessions", json={
                "project_id": project_id,
                "name": "single_turn",  # matches backend/tests/fixtures/single_turn
            })
            assert sess_resp.status_code == 200, sess_resp.text
            session_id = sess_resp.json()["session_id"]

            start_resp = client.post(f"/api/sessions/{session_id}/start")
            assert start_resp.status_code == 200, start_resp.text

            def _is_active():
                info = client.get(f"/api/sessions/{session_id}").json()
                return info["session"]["state"] == "active"

            _wait_for(_is_active, timeout=15, description="session reaches ACTIVE state")

            msg_resp = client.post(f"/api/sessions/{session_id}/messages", json={
                "message": "Hello before the backend restart",
            })
            assert msg_resp.status_code == 200, msg_resp.text

            def _got_streamed_message():
                events = client.get(f"/api/poll/session/{session_id}?since=0&timeout=2").json()["events"]
                return any(e.get("type") == "message" for e in events)

            _wait_for(_got_streamed_message, timeout=15,
                      description="streamed message event via poll-relay before restart")

            # Record the poll cursor a connected browser would be holding right
            # before the restart — the "stale, still-high since" from the bug
            # report.
            stale_poll = client.get(f"/api/poll/session/{session_id}?since=0&timeout=1").json()
            stale_cursor = stale_poll["next_cursor"]
            assert stale_cursor > 0

            # --- Simulate a supervisor-restart-in-place (UC2): terminate
            # Backend, start a NEW backend.main subprocess on the same
            # port/token/--data-dir, while Frontend (and the stale-cursor
            # client) keeps running. ---
            _terminate(backend_proc)
            backend_proc = _spawn_backend()
            _wait_for(
                lambda: httpx.get(f"http://127.0.0.1:{backend_port}/health", timeout=1).status_code == 200,
                timeout=20,
                description="backend.main /health (restarted instance)",
            )

            def _session_reachable():
                return client.get(f"/api/sessions/{session_id}").status_code == 200

            _wait_for(_session_reachable, timeout=15,
                      description="session reachable through restarted backend")

            # Starting the session again in the fresh process unconditionally
            # sends a client_launched system message through the message
            # callback, which lazily (re)creates this session's per-session
            # EventQueue at cursor 0 and appends into it — a genuine post-restart
            # event, independent of mock-sdk replay position.
            restart_start_resp = client.post(f"/api/sessions/{session_id}/start")
            assert restart_start_resp.status_code == 200, restart_start_resp.text

            # --- The core assertion: polling with the pre-restart stale cursor
            # must surface the post-restart event(s) — never a silent empty
            # list with a silently-adopted next_cursor (the original bug). ---
            def _poll_with_stale_cursor():
                resp = client.get(f"/api/poll/session/{session_id}?since={stale_cursor}&timeout=2")
                data = resp.json()
                return data if data["events"] else None

            deadline = time.monotonic() + 20
            result = None
            while time.monotonic() < deadline:
                result = _poll_with_stale_cursor()
                if result is not None:
                    break
                time.sleep(0.3)

            assert result is not None, (
                "stale-cursor poll never received any post-restart events — "
                "post-restart data was silently lost"
            )
            assert len(result["events"]) > 0
            # The restarted Backend's session queue is a fresh, low-cursor instance,
            # so this is deterministically a reset relative to the pre-restart
            # stale_cursor — assert both halves of AC2 unconditionally rather than
            # only checking `reset` under a condition that's already guaranteed true.
            assert result["next_cursor"] < stale_cursor
            assert result["reset"] is True

        assert frontend_proc.poll() is None

    finally:
        for proc in (frontend_proc, backend_proc):
            if proc is None:
                continue
            _terminate(proc)

        # Verify clean shutdown left no orphaned process behind.
        time.sleep(1)
        for proc, label in ((backend_proc, "backend"), (frontend_proc, "frontend")):
            if proc is None:
                continue
            assert proc.poll() is not None, f"{label} process did not exit after terminate/kill"
