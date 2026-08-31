"""Regression tests for issue #1822 — CLI entrypoint crash on startup.

`main.py` used to wire startup/shutdown via `app.add_event_handler(...)`, which was
removed in FastAPI 0.141 (bumped in #1815/#1816). Neither test exercised
`main.py`/`create_app()` together, so the crash went unnoticed. These tests close
that gap: one exercises the new `lifespan` wiring in-process, the other runs the
literal CLI entrypoint as a subprocess.
"""

import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_create_app_lifespan_serves_health(tmp_path: Path):
    """create_app() must wire a working lifespan; entering TestClient must not crash."""
    from src.web_server import create_app

    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


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


def test_remote_backend_token_without_url_rejected_by_argparse(tmp_path: Path):
    """--remote-backend-token requires --remote-backend-url (issue #499) — argparse
    must reject the combination before the server ever starts."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = subprocess.run(
        [
            "uv", "run", "python", "main.py",
            "--data-dir", str(tmp_path),
            "--no-auth",
            "--remote-backend-token", "some-token",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode != 0
    assert "--remote-backend-token requires --remote-backend-url" in proc.stderr
