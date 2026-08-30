"""Mount-exclusivity tests for issue #498's frontend/headless backend_mode split.

Asserts genuine mutual exclusivity: a frontend-mode Hub never registers
/api/backend/*, and a headless-mode instance never registers /api/* or serves the
UI — 404s prove the routes were never registered, not just rejected by auth.

Session-domain routers (poll, session_runtime, sessions) are the only routers
mirrored under /api/backend so far (issue #498 Batch 1) — other relay-eligible
routers (projects, queue, mcp, ...) join this mirror incrementally in later
batches, per routers/__init__.py's _RELAY_ELIGIBLE_MODULES.
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
