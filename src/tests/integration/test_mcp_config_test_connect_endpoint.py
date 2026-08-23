"""Integration tests for POST /api/mcp-configs/{config_id}/test-connect (issue #1800).

Mirrors test_mcp_config_tools_endpoint.py: 404 (missing config), 400 (shared_connection
config posted to this endpoint — the inverse of #1799's existing 400 case), and
200/disabled (skips connection entirely, so no real transport needed).
Status-resolution logic (needs-auth/connected/failed) is covered at the
ApplicationService unit-test level in test_application_service.py, and the
underlying connection lifecycle is covered in test_mcp_oneshot_connection.py.
"""


class TestMcpConfigTestConnectEndpoint:
    async def test_missing_config_returns_404(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post("/api/mcp-configs/nonexistent/test-connect")
        assert resp.status_code == 404

    async def test_shared_config_returns_400(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={"name": "shared", "type": "stdio", "command": "echo", "shared_connection": True},
        )
        config_id = create_resp.json()["id"]

        resp = await client.post(f"/api/mcp-configs/{config_id}/test-connect")
        assert resp.status_code == 400

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_disabled_non_shared_config_returns_disabled_status(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={
                "name": "disabled-non-shared",
                "type": "stdio",
                "command": "echo",
                "shared_connection": False,
                "enabled": False,
            },
        )
        config_id = create_resp.json()["id"]

        resp = await client.post(f"/api/mcp-configs/{config_id}/test-connect")
        assert resp.status_code == 200
        assert resp.json() == {"status": "disabled", "stage": None, "tools": [], "error": None}

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_disabled_config_deletable_immediately_after_check(self, api_integration_env):
        """Regression: a disabled-status check (no connection attempt) must not
        block an immediate delete."""
        client = api_integration_env["client"]
        create_resp = await client.post(
            "/api/mcp-configs",
            json={
                "name": "disabled-non-shared-2",
                "type": "stdio",
                "command": "echo",
                "shared_connection": False,
                "enabled": False,
            },
        )
        config_id = create_resp.json()["id"]

        await client.post(f"/api/mcp-configs/{config_id}/test-connect")

        delete_resp = await client.delete(f"/api/mcp-configs/{config_id}")
        assert delete_resp.status_code == 200
