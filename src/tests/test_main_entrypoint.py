"""Regression test for issue #1822 — CLI entrypoint crash on startup.

Runs the literal `main.py` (Frontend API) CLI entrypoint as a subprocess,
against a real `backend.main` subprocess (issue #498: Frontend now requires a
reachable Backend to do anything, including serve /health via genuine
liveness — this proves the real two-binary CLI setup actually boots, not just
create_app() in-process). The Backend-side half of the original #1822
regression (create_app()'s lifespan wiring) lives in
backend/tests/test_main_entrypoint.py and passes independently.
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(port: int, proc: subprocess.Popen, label: str, deadline_seconds: float = 15) -> None:
    deadline = time.monotonic() + deadline_seconds
    last_error = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read()
            raise AssertionError(f"{label} exited early with code {proc.returncode}:\n{output}")
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            if resp.status_code == 200:
                return
        except httpx.TransportError as exc:
            last_error = exc
        time.sleep(0.3)
    raise AssertionError(f"{label} never became healthy on port {port}: {last_error}")


def test_main_entrypoint_subprocess_smoke(tmp_path: Path):
    """Run both real CLI entrypoints as subprocesses and confirm they boot and relay."""
    backend_port = _free_port()
    frontend_port = _free_port()
    backend_token = secrets.token_urlsafe(16)
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    # Issue #498 finding (relevant to Phase 3's backend_supervisor.py too): spawn
    # via sys.executable directly, not "uv run python ..." — uv run's wrapper
    # process doesn't propagate SIGTERM to the actual grandchild, leaving an
    # orphaned Backend process behind after proc.terminate().
    backend_proc = subprocess.Popen(
        [
            sys.executable, "-m", "backend.main",
            "--host", "127.0.0.1",
            "--port", str(backend_port),
            "--token", backend_token,
            "--data-dir", str(tmp_path / "backend_data"),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    frontend_proc = None
    try:
        _wait_healthy(backend_port, backend_proc, "backend.main")

        frontend_proc = subprocess.Popen(
            [
                sys.executable, "main.py",
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
        _wait_healthy(frontend_port, frontend_proc, "main.py")

        # Prove the relay actually reaches Backend, not just that both processes boot.
        resp = httpx.get(f"http://127.0.0.1:{frontend_port}/api/projects", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["projects"] == []
    finally:
        for proc in (frontend_proc, backend_proc):
            if proc is None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
