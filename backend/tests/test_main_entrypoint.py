"""Regression tests for issue #1822 — CLI entrypoint crash on startup.

`main.py` used to wire startup/shutdown via `app.add_event_handler(...)`, which was
removed in FastAPI 0.141 (bumped in #1815/#1816). This test covers the Backend side
of that fix: `backend.web_server.create_app()`'s lifespan wiring. The Frontend's own
`main.py` subprocess smoke test lives in src/tests/test_main_entrypoint.py — it
stays failing until issue #498 Phase 2 rewires web_server.py, since main.py's
create_app() still imports modules that moved to backend/ in this phase.
"""

from pathlib import Path

from starlette.testclient import TestClient


def test_create_app_lifespan_serves_health(tmp_path: Path):
    """create_app() must wire a working lifespan; entering TestClient must not crash."""
    from backend.web_server import create_app

    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
