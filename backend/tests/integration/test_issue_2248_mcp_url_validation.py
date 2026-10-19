"""Integration tests for MCP server URL validation on save (issue #2248).

AC1: create/update reject non-URL `url` values for HTTP/SSE types with 400 + a
field-level message.
AC3: OAuth initiate against a config with a malformed stored URL returns 400 (not
500) with an "invalid server URL" message, naming the URL.
"""


async def _create_http_config(client, **overrides):
    payload = {
        "name": "url-validation-test-server",
        "type": "http",
        "url": "https://example.com/mcp",
        **overrides,
    }
    return await client.post("/api/mcp-configs", json=payload)


class TestCreateConfigUrlValidation:
    async def test_garbage_url_rejected_with_400(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await _create_http_config(client, url="this is not a url, just pasted text")
        assert resp.status_code == 400, resp.text
        assert "URL" in resp.json()["detail"]

    async def test_whitespace_trimmed_before_storage(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await _create_http_config(client, url="  https://example.com/mcp\n")
        assert resp.status_code == 200, resp.text
        assert resp.json()["url"] == "https://example.com/mcp"

    async def test_valid_url_still_accepted(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await _create_http_config(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["url"] == "https://example.com/mcp"

    async def test_stdio_config_unaffected_by_url_format(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post("/api/mcp-configs", json={
            "name": "stdio-url-validation-test",
            "type": "stdio",
            "command": "npx",
        })
        assert resp.status_code == 200, resp.text


class TestUpdateConfigUrlValidation:
    async def test_garbage_url_rejected_with_400(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await _create_http_config(client)
        config_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/mcp-configs/{config_id}", json={"url": "not a url"}
        )
        assert resp.status_code == 400, resp.text
        assert "URL" in resp.json()["detail"]

    async def test_empty_url_rejected_on_update(self, api_integration_env):
        """Previously unvalidated: update_config() accepted '' for SSE/HTTP types.
        This closes that gap."""
        client = api_integration_env["client"]
        create_resp = await _create_http_config(client)
        config_id = create_resp.json()["id"]

        resp = await client.put(f"/api/mcp-configs/{config_id}", json={"url": ""})
        assert resp.status_code == 400, resp.text

    async def test_whitespace_trimmed_on_update(self, api_integration_env):
        client = api_integration_env["client"]
        create_resp = await _create_http_config(client)
        config_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/mcp-configs/{config_id}", json={"url": "  https://updated.example.com/mcp  "}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["url"] == "https://updated.example.com/mcp"


class TestListMcpConfigsUrlAnnotation:
    async def test_list_flags_invalid_stored_url_without_500(self, api_integration_env):
        """AC4: a legacy/pre-existing invalid-URL config must not 500 the whole list —
        and a well-formed config in the same list must not be caught up in the flag."""
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]

        good = await _create_http_config(client, name="good-server")
        assert good.status_code == 200, good.text
        bad = await _create_http_config(client, name="bad-server-pending-fix")
        assert bad.status_code == 200, bad.text

        # Simulate a config saved before this fix existed: bypass validation by
        # writing a malformed URL directly onto the in-memory config object.
        bad_config_id = bad.json()["id"]
        coordinator.mcp_config_manager.configs[bad_config_id].url = "not a url"

        resp = await client.get("/api/mcp-configs")
        assert resp.status_code == 200, resp.text
        configs_by_name = {c["name"]: c for c in resp.json()["configs"]}

        assert configs_by_name["good-server"]["url_valid"] is True
        assert configs_by_name["good-server"]["url_error"] is None
        assert configs_by_name["bad-server-pending-fix"]["url_valid"] is False
        assert configs_by_name["bad-server-pending-fix"]["url_error"]

    async def test_stdio_config_url_validity_is_none(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post("/api/mcp-configs", json={
            "name": "stdio-annotation-test",
            "type": "stdio",
            "command": "npx",
        })
        assert resp.status_code == 200, resp.text

        list_resp = await client.get("/api/mcp-configs")
        assert list_resp.status_code == 200
        config = next(c for c in list_resp.json()["configs"] if c["name"] == "stdio-annotation-test")
        assert config["url_valid"] is None
        assert config["url_error"] is None


class TestOAuthInitiateUrlValidation:
    async def test_malformed_stored_url_returns_400_not_500(self, api_integration_env):
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]

        created = await _create_http_config(client, name="oauth-url-validation-test")
        assert created.status_code == 200, created.text
        config_id = created.json()["id"]
        coordinator.mcp_config_manager.configs[config_id].url = "not a url"

        resp = await client.post(
            f"/api/mcp-configs/{config_id}/oauth/initiate",
            json={"redirect_uri": "http://localhost:1234/oauth/callback"},
        )
        assert resp.status_code == 400, resp.text
        assert "invalid" in resp.json()["detail"].lower()
        assert "not a url" in resp.json()["detail"]
