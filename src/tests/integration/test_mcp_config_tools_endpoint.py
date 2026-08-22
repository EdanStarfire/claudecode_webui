"""Integration tests for GET /api/mcp-configs/{config_id}/tools (issue #1799).

Router-level wiring: 404 (missing config), 400 (shared_connection=false),
and 200/disabled (skips connection entirely, so no real transport needed).
Status-resolution logic (needs-auth/connected/failed) is covered at the
ApplicationService unit-test level in test_application_service.py, and the
underlying refcount-safety of SharedMcpConnectionManager.list_tools() is
covered in test_shared_mcp_connection.py.
"""


class TestMcpConfigToolsEndpoint:
    async def test_missing_config_returns_404(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.get("/api/mcp-configs/nonexistent/tools")
        assert resp.status_code == 404

    async def test_non_shared_config_returns_400(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={"name": "not-shared", "type": "stdio", "command": "echo", "shared_connection": False},
        )
        config_id = create_resp.json()["id"]

        resp = await client.get(f"/api/mcp-configs/{config_id}/tools")
        assert resp.status_code == 400

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_disabled_shared_config_returns_disabled_status(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={
                "name": "disabled-shared",
                "type": "stdio",
                "command": "echo",
                "shared_connection": True,
                "enabled": False,
            },
        )
        config_id = create_resp.json()["id"]

        resp = await client.get(f"/api/mcp-configs/{config_id}/tools")
        assert resp.status_code == 200
        assert resp.json() == {"status": "disabled", "tools": [], "error": None}

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_disabled_config_deletable_immediately_after_check(self, api_integration_env):
        """Regression: a disabled-status check (no connection attempt) must not
        block an immediate delete."""
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={
                "name": "disabled-shared-2",
                "type": "stdio",
                "command": "echo",
                "shared_connection": True,
                "enabled": False,
            },
        )
        config_id = create_resp.json()["id"]

        await client.get(f"/api/mcp-configs/{config_id}/tools")

        delete_resp = await client.delete(f"/api/mcp-configs/{config_id}")
        assert delete_resp.status_code == 200
