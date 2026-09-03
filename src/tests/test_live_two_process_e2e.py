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
