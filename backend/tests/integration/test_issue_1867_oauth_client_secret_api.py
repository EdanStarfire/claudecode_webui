"""Integration tests for the oauth_client_secret router wiring (issue #1867).

Covers two router-level bugs found and fixed during review, in addition to the
unit-level coverage in test_mcp_config_manager.py and
test_issue_1867_oauth_confidential_client.py:

- PUT /api/mcp-configs/{id} must NOT clear oauth_client_secret on a partial update
  that omits the field (mirrors the existing oauth_custom_callback_path/port guard).
- POST /api/mcp-configs/{id}/oauth/initiate must surface an unresolvable
  ${secret:NAME} reference as 400, not a generic 500.
"""


async def _create_confidential_config(client, **overrides):
    payload = {
        "name": "google-confidential-test-server",
        "type": "http",
        "url": "https://example.com/mcp",
        "oauth_client_id": "google-client-id",
        "oauth_client_secret": "${secret:google-client-secret}",
        **overrides,
    }
    resp = await client.post("/api/mcp-configs", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestUpdateDoesNotClearClientSecret:
    async def test_partial_update_preserves_client_secret(self, api_integration_env):
        client = api_integration_env["client"]
        config = await _create_confidential_config(client)
        config_id = config["id"]
        assert config["oauth_client_secret"] == "${secret:google-client-secret}"

        # Partial update that doesn't mention oauth_client_secret at all.
        resp = await client.put(f"/api/mcp-configs/{config_id}", json={"enabled": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["oauth_client_secret"] == "${secret:google-client-secret}"

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_explicit_null_clears_client_secret(self, api_integration_env):
        client = api_integration_env["client"]
        config = await _create_confidential_config(client)
        config_id = config["id"]

        resp = await client.put(
            f"/api/mcp-configs/{config_id}", json={"oauth_client_secret": None}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["oauth_client_secret"] is None

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_explicit_new_value_updates_client_secret(self, api_integration_env):
        client = api_integration_env["client"]
        config = await _create_confidential_config(client)
        config_id = config["id"]

        resp = await client.put(
            f"/api/mcp-configs/{config_id}",
            json={"oauth_client_secret": "${secret:rotated-secret}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["oauth_client_secret"] == "${secret:rotated-secret}"

        await client.delete(f"/api/mcp-configs/{config_id}")


class TestOAuthInitiateSecretResolutionError:
    async def test_missing_vault_secret_returns_400_not_500(self, api_integration_env):
        client = api_integration_env["client"]
        config = await _create_confidential_config(client)
        config_id = config["id"]

        resp = await client.post(
            f"/api/mcp-configs/{config_id}/oauth/initiate",
            json={"redirect_uri": "http://localhost/oauth/callback"},
        )

        assert resp.status_code == 400, resp.text
        assert "google-client-secret" in resp.json()["detail"]

        await client.delete(f"/api/mcp-configs/{config_id}")
