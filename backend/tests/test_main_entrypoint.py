"""Regression tests for issue #1822 — CLI entrypoint crash on startup.

`main.py` used to wire startup/shutdown via `app.add_event_handler(...)`, which was
removed in FastAPI 0.141 (bumped in #1815/#1816). This test covers the Backend side
of that fix: `backend.web_server.create_app()`'s lifespan wiring. The Frontend's own
`main.py` subprocess smoke test (src/tests/test_main_entrypoint.py) boots both real
CLI entrypoints together (issue #498, Phase 2 onward).
"""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from starlette.testclient import TestClient

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


def test_create_app_lifespan_serves_health(tmp_path: Path):
    """create_app() must wire a working lifespan; entering TestClient must not crash."""
    from backend.web_server import create_app

    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


class TestConfigureWebuiBaseUrl:
    """Regression tests for issue #498 Phase 4: Docker/LiteLLM-proxy sidecars call
    back into WEBUI_BASE_URL — this must point at Backend's own bind port (Docker
    lives entirely in Backend now), not Frontend's (the pre-#498 unified main.py's
    behavior, which would be wrong for sidecars that now call Backend directly)."""

    def test_sets_webui_base_url_to_own_port(self):
        from backend.main import configure_webui_base_url

        with patch.dict(os.environ, {}, clear=True):
            configure_webui_base_url(18100)
            assert os.environ["WEBUI_BASE_URL"] == "http://cc-webui.internal:18100"

    def test_does_not_override_an_explicitly_set_value(self):
        """setdefault semantics: an operator-provided override in the environment
        (e.g. a custom docker network setup) must not be clobbered."""
        from backend.main import configure_webui_base_url

        with patch.dict(os.environ, {"WEBUI_BASE_URL": "http://custom.example:9999"}, clear=True):
            configure_webui_base_url(18100)
            assert os.environ["WEBUI_BASE_URL"] == "http://custom.example:9999"

    def test_tracks_whatever_port_is_passed_not_a_hardcoded_one(self):
        """Auto-start passes backend_supervisor.py's OS-allocated port through as
        --port (src/backend_supervisor.py) — confirm the env var tracks whatever
        port is given, not a hardcoded default, since that port varies per run."""
        from backend.main import configure_webui_base_url

        with patch.dict(os.environ, {}, clear=True):
            configure_webui_base_url(54321)
            assert os.environ["WEBUI_BASE_URL"] == "http://cc-webui.internal:54321"


class TestEmbeddedModeNetworkBinding:
    """Regression tests for issue #1850: embedded mode (--embedded, set only by
    BackendSupervisor's own auto-start spawn) must boot successfully without any
    network-binding config approval — it never does a real network-facing bind, so
    check_network_binding doesn't apply to it (backend/main.py). Headless/manual
    invocations (no --embedded) must remain completely unaffected: still gated by
    check_network_binding exactly as before this issue."""

    def _base_env(self, tmp_path: Path) -> dict:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        # Isolate from the real ~/.config/cc_webui/config.json (backend/config_manager.py's
        # CONFIG_DIR is Path.home()-derived) so this test's outcome doesn't depend on
        # whatever network-binding settings happen to already be on the host machine.
        env["HOME"] = str(tmp_path)
        return env

    def test_headless_invocation_still_gated_on_non_loopback_host(self, tmp_path):
        """No --embedded: a manual/remote invocation requesting a non-loopback --host
        without prior config approval must still be rejected exactly as before #1850."""
        env = self._base_env(tmp_path)

        result = subprocess.run(
            [
                sys.executable, "-m", "backend.main",
                "--host", "0.0.0.0",
                "--port", str(_free_port()),
                "--token", secrets.token_urlsafe(16),
                "--data-dir", str(tmp_path / "data"),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode != 0
        assert "Network binding requires explicit configuration" in result.stdout

    def test_embedded_invocation_boots_without_gate_approval(self, tmp_path):
        """--embedded must boot and become healthy on loopback with zero config
        approval — it isn't gated by check_network_binding at all (issue #1850)."""
        env = self._base_env(tmp_path)
        port = _free_port()

        proc = subprocess.Popen(
            [
                sys.executable, "-m", "backend.main",
                "--embedded",
                "--port", str(port),
                "--token", secrets.token_urlsafe(16),
                "--data-dir", str(tmp_path / "data"),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_healthy(port, proc, "backend.main (embedded)")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
