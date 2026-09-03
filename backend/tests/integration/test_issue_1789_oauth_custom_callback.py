"""Integration tests for issue #1789 — per-server custom OAuth callback path/port.

Covers T6 for both mechanisms:
- path-only (no custom port): a dynamic Route + AuthMiddleware.EXEMPT_PATHS entry
  registered directly on the main app; torn down on delete, confirmed via a 404 (and
  re-imposed auth) on the very next request — the case the plan calls out as easy to
  under-test if only the listener-port variant is covered.
- custom port: a dedicated OAuthCallbackListener bound to a real socket; reachable while
  the config is active, torn down (socket released) on delete.
"""

import socket
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.web_server import AuthMiddleware, BackendApp


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _create_mcp_config(client, auth_headers, **overrides):
    payload = {
        "name": "oauth-1789-test-server",
        "type": "http",
        "url": "https://example.com/mcp",
        "shared_connection": True,
        **overrides,
    }
    resp = await client.post("/api/mcp-configs", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def auth_env(tmp_path):
    """A BackendApp instance with auth ENABLED, to verify EXEMPT_PATHS wiring for real."""
    data_dir = tmp_path / "test_data_oauth_1789"
    data_dir.mkdir()
    config_file = tmp_path / "config.json"
    webui = BackendApp(
        data_dir=data_dir,
        config_file=config_file,
        auth_token="test-token-1789",
        host="127.0.0.1",
        port=8000,
    )
    await webui.initialize()

    transport = ASGITransport(app=webui.app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    yield webui, client, {"Authorization": "Bearer test-token-1789"}

    await client.aclose()
    try:
        await webui.cleanup()
    except Exception:
        pass


class TestPathOnlyCustomCallback:
    async def test_registers_and_tears_down_on_delete(self, auth_env):
        webui, client, auth_headers = auth_env
        webui.coordinator.oauth_manager.complete_flow = AsyncMock(return_value="server-id-1")

        # Sanity: an unauthenticated request to a normal API route is rejected.
        unauth_resp = await client.get("/api/mcp-configs")
        assert unauth_resp.status_code == 401

        config = await _create_mcp_config(
            client, auth_headers, oauth_custom_callback_path="/my-callback"
        )
        config_id = config["id"]
        assert "/my-callback" in AuthMiddleware.EXEMPT_PATHS

        # No auth header — proves the dynamic route is genuinely exempt, not just present.
        resp = await client.get("/my-callback", params={"state": "s1", "code": "c1"})
        assert resp.status_code == 200
        assert "Connected Successfully" in resp.text
        webui.coordinator.oauth_manager.complete_flow.assert_awaited_once_with("s1", "c1")

        del_resp = await client.delete(f"/api/mcp-configs/{config_id}", headers=auth_headers)
        assert del_resp.status_code == 200

        # T6: same path, very next request — no orphaned route, no orphaned exemption.
        # Unauthenticated: exemption is gone, so auth is required again (not a bare 404 —
        # AuthMiddleware runs before routing and rejects first).
        resp2 = await client.get("/my-callback", params={"state": "s1", "code": "c1"})
        assert resp2.status_code == 401
        # Authenticated: proves the route itself is truly gone, not just re-protected.
        resp3 = await client.get(
            "/my-callback", params={"state": "s1", "code": "c1"}, headers=auth_headers
        )
        assert resp3.status_code == 404
        assert "/my-callback" not in AuthMiddleware.EXEMPT_PATHS
        assert config_id not in webui._dynamic_oauth_routes

    async def test_torn_down_on_disable(self, auth_env):
        """Disabling via update (enabled=False) must tear down the route the same way delete does."""
        webui, client, auth_headers = auth_env
        webui.coordinator.oauth_manager.complete_flow = AsyncMock(return_value="server-id-1")

        config = await _create_mcp_config(
            client, auth_headers, oauth_custom_callback_path="/disable-callback"
        )
        config_id = config["id"]
        assert "/disable-callback" in AuthMiddleware.EXEMPT_PATHS

        upd_resp = await client.put(
            f"/api/mcp-configs/{config_id}",
            json={"enabled": False},
            headers=auth_headers,
        )
        assert upd_resp.status_code == 200

        resp = await client.get("/disable-callback", params={"state": "s1", "code": "c1"})
        assert resp.status_code == 401
        resp_auth = await client.get(
            "/disable-callback", params={"state": "s1", "code": "c1"}, headers=auth_headers
        )
        assert resp_auth.status_code == 404
        assert "/disable-callback" not in AuthMiddleware.EXEMPT_PATHS

    async def test_path_change_on_update_swaps_route(self, auth_env):
        webui, client, auth_headers = auth_env
        webui.coordinator.oauth_manager.complete_flow = AsyncMock(return_value="server-id-1")

        config = await _create_mcp_config(
            client, auth_headers, oauth_custom_callback_path="/old-callback"
        )
        config_id = config["id"]

        upd_resp = await client.put(
            f"/api/mcp-configs/{config_id}",
            json={"oauth_custom_callback_path": "/new-callback"},
            headers=auth_headers,
        )
        assert upd_resp.status_code == 200

        old_resp = await client.get("/old-callback", params={"state": "s1", "code": "c1"})
        assert old_resp.status_code == 401
        old_resp_auth = await client.get(
            "/old-callback", params={"state": "s1", "code": "c1"}, headers=auth_headers
        )
        assert old_resp_auth.status_code == 404
        assert "/old-callback" not in AuthMiddleware.EXEMPT_PATHS

        new_resp = await client.get("/new-callback", params={"state": "s1", "code": "c1"})
        assert new_resp.status_code == 200
        assert "/new-callback" in AuthMiddleware.EXEMPT_PATHS


class TestCustomPortListener:
    async def test_listener_reachable_and_torn_down_on_delete(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        webui.coordinator.oauth_manager.complete_flow = AsyncMock(return_value="server-id-2")

        port = _free_port()
        create_resp = await client.post(
            "/api/mcp-configs",
            json={
                "name": "oauth-1789-listener-server",
                "type": "http",
                "url": "https://example.com/mcp",
                "shared_connection": True,
                "oauth_custom_callback_port": port,
            },
        )
        assert create_resp.status_code == 200, create_resp.text
        config_id = create_resp.json()["id"]

        assert webui.coordinator.oauth_callback_listener_manager.is_port_active(port)

        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as listener_client:
            resp = await listener_client.get(
                "/oauth/callback", params={"state": "s1", "code": "c1"}
            )
            assert resp.status_code == 200
            assert "Connected Successfully" in resp.text

        del_resp = await client.delete(f"/api/mcp-configs/{config_id}")
        assert del_resp.status_code == 200

        assert not webui.coordinator.oauth_callback_listener_manager.is_port_active(port)

        with pytest.raises(Exception):  # noqa: B017 — connection refused, exact type is OS-dependent
            async with AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1.0) as c:
                await c.get("/oauth/callback", params={"state": "s1", "code": "c1"})

    async def test_two_configs_share_listener_via_distinct_paths(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        webui.coordinator.oauth_manager.complete_flow = AsyncMock(return_value="shared-id")

        port = _free_port()
        resp_a = await client.post(
            "/api/mcp-configs",
            json={
                "name": "oauth-1789-shared-a",
                "type": "http",
                "url": "https://example.com/mcp-a",
                "shared_connection": True,
                "oauth_custom_callback_port": port,
                "oauth_custom_callback_path": "/callback-a",
            },
        )
        assert resp_a.status_code == 200, resp_a.text
        resp_b = await client.post(
            "/api/mcp-configs",
            json={
                "name": "oauth-1789-shared-b",
                "type": "http",
                "url": "https://example.com/mcp-b",
                "shared_connection": True,
                "oauth_custom_callback_port": port,
                "oauth_custom_callback_path": "/callback-b",
            },
        )
        assert resp_b.status_code == 200, resp_b.text

        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as listener_client:
            resp1 = await listener_client.get("/callback-a", params={"state": "s1", "code": "c1"})
            resp2 = await listener_client.get("/callback-b", params={"state": "s2", "code": "c2"})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
