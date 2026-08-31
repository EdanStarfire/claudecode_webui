"""Mount-exclusivity tests for issue #498's frontend/headless backend_mode split.

Asserts genuine mutual exclusivity: a frontend-mode Hub never registers
/api/backend/*, and a headless-mode instance never registers /api/* or serves the
UI — 404s prove the routes were never registered, not just rejected by auth.

All relay-eligible routers listed in routers/__init__.py's _RELAY_ELIGIBLE_MODULES
are mirrored under /api/backend (issue #498/#499). /health and /health/ready
(issue #499) are the sole exception to the "headless mounts only /api/backend/*"
rule — they're registered unprefixed and unauthenticated on both mounts so
orchestrator probes can reach them without a bearer token.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from ..web_server import ClaudeWebUI


@pytest.mark.asyncio
async def test_frontend_mode_never_exposes_backend_mount(tmp_path):
    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        response = await client.get("/api/backend/sessions")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_headless_mode_never_exposes_frontend_mount(tmp_path):
    webui = ClaudeWebUI(
        data_dir=tmp_path / "data", backend_mode="headless", backend_auth_token="secret-token"
    )
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        root_response = await client.get("/")
        api_response = await client.get("/api/sessions")
    assert root_response.status_code == 404
    assert api_response.status_code == 404


@pytest.mark.asyncio
async def test_headless_mode_backend_route_requires_bearer_token(tmp_path):
    webui = ClaudeWebUI(
        data_dir=tmp_path / "data", backend_mode="headless", backend_auth_token="secret-token"
    )
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        no_token = await client.get("/api/backend/sessions")
        wrong_token = await client.get(
            "/api/backend/sessions", headers={"Authorization": "Bearer wrong"}
        )
        right_token = await client.get(
            "/api/backend/sessions", headers={"Authorization": "Bearer secret-token"}
        )

    assert no_token.status_code == 401
    assert wrong_token.status_code == 401
    assert right_token.status_code == 200


@pytest.mark.asyncio
async def test_headless_mode_misconfigured_token_refuses_everything(tmp_path):
    """No backend_auth_token configured (misconfiguration) — refuse, don't run open."""
    webui = ClaudeWebUI(data_dir=tmp_path / "data", backend_mode="headless")
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        response = await client.get(
            "/api/backend/sessions", headers={"Authorization": "Bearer anything"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_headless_mode_health_endpoints_reachable_without_bearer_token(tmp_path):
    """GET /health and /health/ready must be reachable in headless mode with no
    Authorization header — orchestrator probes carry no bearer token."""
    webui = ClaudeWebUI(
        data_dir=tmp_path / "data", backend_mode="headless", backend_auth_token="secret-token"
    )
    try:
        await webui.initialize()
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            health = await client.get("/health")
            ready = await client.get("/health/ready")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
    finally:
        await webui.cleanup()


@pytest.mark.asyncio
async def test_headless_mode_readiness_503_before_initialize(tmp_path):
    """/health/ready 503s before initialize() completes; /health (liveness) is 200
    regardless — liveness is independent of readiness."""
    webui = ClaudeWebUI(
        data_dir=tmp_path / "data", backend_mode="headless", backend_auth_token="secret-token"
    )
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/health/ready")
    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_frontend_mode_health_endpoints_reachable_without_bearer_token(tmp_path):
    """Parity with headless mode: /health and /health/ready are exempt from
    AuthMiddleware and reachable with no token in frontend mode too."""
    webui = ClaudeWebUI(data_dir=tmp_path / "data", auth_enabled=True, auth_token="op-token")
    try:
        await webui.initialize()
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            health = await client.get("/health")
            ready = await client.get("/health/ready")
        assert health.status_code == 200
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
    finally:
        await webui.cleanup()


@pytest.mark.asyncio
async def test_frontend_mode_readiness_503_before_initialize(tmp_path):
    webui = ClaudeWebUI(data_dir=tmp_path / "data", auth_enabled=True, auth_token="op-token")
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/health/ready")
    assert health.status_code == 200
    assert ready.status_code == 503


@pytest.mark.asyncio
async def test_create_session_rejects_colliding_caller_supplied_id(tmp_path):
    """A caller-supplied session_id that already exists must 409, not silently
    overwrite the existing session's state.json (issue #498/#499 §11.1)."""
    data_dir = tmp_path / "data"
    (data_dir / "projects").mkdir(parents=True)
    (data_dir / "sessions").mkdir(parents=True)
    webui = ClaudeWebUI(data_dir=data_dir)
    async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
        project_resp = await client.post(
            "/api/projects", json={"name": "p1", "working_directory": str(tmp_path)}
        )
        assert project_resp.status_code == 200, project_resp.text
        project_id = project_resp.json()["project"]["project_id"]

        first = await client.post(
            "/api/sessions",
            json={"project_id": project_id, "session_id": "fixed-id-1"},
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/sessions",
            json={"project_id": project_id, "session_id": "fixed-id-1"},
        )
    assert second.status_code == 409
