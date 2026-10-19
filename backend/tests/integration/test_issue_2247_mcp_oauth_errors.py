"""Integration tests for issue #2247 (AC1/AC4): initiate_mcp_oauth must surface
OAuthFlowManager.start_flow()'s user-actionable RuntimeErrors to the client as the
real detail text, not handle_exceptions's generic "An internal error occurred"
(which only includes str(e) when debug_error_handler is enabled).
"""
from unittest.mock import AsyncMock, patch

import pytest


async def _create_http_config(client, **overrides):
    payload = {
        "name": "oauth-runtime-error-test-server",
        "type": "http",
        "url": "https://example.com/mcp",
        **overrides,
    }
    resp = await client.post("/api/mcp-configs", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestInitiateMcpOAuthRuntimeErrorSurfaced:
    @pytest.mark.parametrize(
        "message",
        [
            "OAuth Dynamic Client Registration failed for MCP server 'cfg-1' and no "
            "oauth_client_id is configured. Set 'oauth_client_id' in the MCP server "
            "config to use a pre-registered app.",
            "MCP server 'cfg-1' requires the OAuth Client ID Metadata Document "
            "mechanism (draft-ietf-oauth-client-id-metadata-document) rather than "
            "Dynamic Client Registration, but this app's own origin is not HTTPS. "
            "Remove any custom OAuth callback path/port for this server, and ensure "
            "this app is served over HTTPS, then retry.",
            "MCP server 'cfg-1' requires the OAuth Client ID Metadata Document "
            "mechanism, but no usable client-ID metadata document URL was available.",
        ],
    )
    async def test_runtime_error_returns_500_with_real_detail(
        self, api_integration_env, message
    ):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        config = await _create_http_config(client)
        config_id = config["id"]

        with patch.object(
            webui.service, "oauth_initiate_flow", AsyncMock(side_effect=RuntimeError(message))
        ):
            resp = await client.post(
                f"/api/mcp-configs/{config_id}/oauth/initiate",
                json={"redirect_uri": "http://localhost/oauth/callback"},
            )

        assert resp.status_code == 500, resp.text
        assert resp.json()["detail"] == message
        assert resp.json()["detail"] != "An internal error occurred"

        await client.delete(f"/api/mcp-configs/{config_id}")
