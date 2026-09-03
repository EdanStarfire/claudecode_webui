"""Regression test for issue #1822 — CLI entrypoint crash on startup.

Runs the literal `main.py` CLI entrypoint (Frontend API) as a subprocess.

Issue #498, Phase 1: this is EXPECTED TO FAIL until Phase 2 rewires
src/web_server.py into a pure relay shell — main.py's create_app() still
imports session_coordinator/application_service/etc., which moved to
backend/ in Phase 1. See src/web_server.py's Phase 1 NOTE comment. The
Backend-side half of this regression test (create_app()'s lifespan wiring)
lives in backend/tests/test_main_entrypoint.py and passes today.
"""

import os
import socket
import subprocess
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_main_entrypoint_subprocess_smoke(tmp_path: Path):
    """Run `main.py` as a real subprocess — the literal CLI entrypoint that crashed."""
    port = _free_port()
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = subprocess.Popen(
        [
            "uv", "run", "python", "main.py",
            "--port", str(port),
            "--data-dir", str(tmp_path),
            "--no-auth",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.monotonic() + 15
        last_error = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read()
                raise AssertionError(
                    f"main.py exited early with code {proc.returncode}:\n{output}"
                )
            try:
                resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                if resp.status_code == 200:
                    assert resp.json()["status"] == "healthy"
                    break
            except httpx.TransportError as exc:
                last_error = exc
            time.sleep(0.3)
        else:
            raise AssertionError(f"main.py never became healthy on port {port}: {last_error}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
