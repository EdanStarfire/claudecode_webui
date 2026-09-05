"""Regression tests for issue #1822 — CLI entrypoint crash on startup.

`main.py` used to wire startup/shutdown via `app.add_event_handler(...)`, which was
removed in FastAPI 0.141 (bumped in #1815/#1816). This test covers the Backend side
of that fix: `backend.web_server.create_app()`'s lifespan wiring. The Frontend's own
`main.py` subprocess smoke test (src/tests/test_main_entrypoint.py) boots both real
CLI entrypoints together (issue #498, Phase 2 onward).
"""

import os
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient


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
