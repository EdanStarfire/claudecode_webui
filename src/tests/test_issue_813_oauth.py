"""Tests for MCP OAuth 2.1 support (issue #813).

Covers:
- FernetTokenStore: encrypted token/client persistence (mocked filesystem)
- OAuthFlowManager.start_flow + complete_flow (mocked httpx)
- _get_mcp_sdk_config() injects Bearer token in headers dict
- XSS escaping in OAuth callback error page (issue #861)
"""

import html
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.mcp_config_manager import McpServerType
from src.oauth_manager import FernetTokenStore, OAuthFlowManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(access_token: str = "access123") -> OAuthToken:
    return OAuthToken(access_token=access_token, token_type="Bearer")


def _make_client_info(client_id: str = "cid") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
        token_endpoint_auth_method="none",
    )


# ---------------------------------------------------------------------------
# FernetTokenStore tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fernet_store_round_trip(tmp_path: Path):
    store = FernetTokenStore(tmp_path, "server1")
    token = _make_token("tok_abc")

    assert await store.get_tokens() is None  # Nothing stored yet

    await store.set_tokens(token)
    recovered = await store.get_tokens()
    assert recovered is not None
    assert recovered.access_token == "tok_abc"


@pytest.mark.asyncio
async def test_fernet_store_client_info_round_trip(tmp_path: Path):
    store = FernetTokenStore(tmp_path, "server1")
    info = _make_client_info("my-client-id")

    assert await store.get_client_info() is None

    await store.set_client_info(info)
    recovered = await store.get_client_info()
    assert recovered is not None
    assert recovered.client_id == "my-client-id"


@pytest.mark.asyncio
async def test_fernet_store_clear(tmp_path: Path):
    store = FernetTokenStore(tmp_path, "server1")
    await store.set_tokens(_make_token())
    await store.set_client_info(_make_client_info())

    await store.clear()

    assert await store.get_tokens() is None
    assert await store.get_client_info() is None


@pytest.mark.asyncio
async def test_fernet_store_reuses_key(tmp_path: Path):
    """Two store instances with same server_id share the same Fernet key."""
    store1 = FernetTokenStore(tmp_path, "server1")
    await store1.set_tokens(_make_token("shared_tok"))

    store2 = FernetTokenStore(tmp_path, "server1")
    recovered = await store2.get_tokens()
    assert recovered is not None
    assert recovered.access_token == "shared_tok"


@pytest.mark.asyncio
async def test_fernet_store_bad_ciphertext_returns_none(tmp_path: Path):
    """Corrupted ciphertext returns None instead of raising."""
    store = FernetTokenStore(tmp_path, "server1")
    # Write garbage bytes as token file
    token_file = tmp_path / "oauth_tokens" / "server1.enc"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_bytes(b"not-valid-fernet-data")

    result = await store.get_tokens()
    assert result is None


# ---------------------------------------------------------------------------
# OAuthFlowManager tests
# ---------------------------------------------------------------------------

# Minimal discovery responses used across flow tests
_DISCOVERY_NOT_FOUND = MagicMock(status_code=404)
_TOKEN_RESPONSE_JSON = json.dumps(
    {"access_token": "flow_token", "token_type": "Bearer"}
).encode()


def _make_token_http_response(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200

    async def _aread():
        return body

    resp.aread = _aread
    return resp


@pytest.mark.asyncio
async def test_start_flow_returns_auth_url(tmp_path: Path):
    """start_flow() returns a URL containing code_challenge and state."""
    manager = OAuthFlowManager(tmp_path)

    # Mock httpx so no real HTTP calls are made
    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client:
        # All discovery requests return 404 (fall back to path-derived endpoints)
        not_found = MagicMock()
        not_found.status_code = 404

        # DCR registration returns a client_id
        dcr_resp = MagicMock()
        dcr_resp.status_code = 201

        async def _dcr_aread():
            return json.dumps(
                {
                    "client_id": "registered-client",
                    "redirect_uris": ["http://localhost/oauth/callback"],
                    "token_endpoint_auth_method": "none",
                }
            ).encode()

        dcr_resp.aread = _dcr_aread

        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        # server_url has path /v1/mcp → 2 PRM URLs (path-based + root), 1 AS URL, then DCR
        client_instance.send = AsyncMock(side_effect=[
            not_found,  # PRM discovery #1 (path-based)
            not_found,  # PRM discovery #2 (root-based)
            not_found,  # AS metadata
            dcr_resp,   # DCR
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    assert "code_challenge=" in auth_url
    assert "state=" in auth_url
    assert "client_id=registered-client" in auth_url
    assert len(manager._pending) == 1


@pytest.mark.asyncio
async def test_complete_flow_reads_pending_state(tmp_path: Path):
    """complete_flow() reads token_endpoint and client_id from pending dict."""
    manager = OAuthFlowManager(tmp_path)

    # Pre-populate pending state (as start_flow would do)
    state = "test_state_abc"
    manager._pending[state] = {
        "server_id": "srv1",
        "token_endpoint": "https://auth.example.com/token",
        "client_id": "client-xyz",
        "code_verifier": "v" * 43,
        "redirect_uri": "http://localhost/oauth/callback",
    }

    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client.return_value = client_instance

        server_id = await manager.complete_flow(state, code="auth_code_123")

        # Verify the token endpoint from pending was used
        call_kwargs = client_instance.post.call_args
        assert call_kwargs[0][0] == "https://auth.example.com/token"
        form_data = call_kwargs[1]["data"]
        assert form_data["client_id"] == "client-xyz"
        assert form_data["code"] == "auth_code_123"
        assert form_data["code_verifier"] == "v" * 43

    assert server_id == "srv1"
    # Pending entry consumed
    assert state not in manager._pending

    # Token persisted
    stored = await manager.get_stored_token("srv1")
    assert stored is not None
    assert stored.access_token == "flow_token"


@pytest.mark.asyncio
async def test_complete_flow_raises_on_unknown_state(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    with pytest.raises(ValueError, match="No pending OAuth flow"):
        await manager.complete_flow("nonexistent_state", "code")


# ---------------------------------------------------------------------------
# _get_mcp_sdk_config integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_injects_bearer_token(tmp_path: Path):
    """_get_mcp_sdk_config() must add Authorization: Bearer header when OAuth token exists."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)

    # Store a token for server "srv-oauth"
    await coordinator.oauth_manager.get_token_store("srv-oauth").set_tokens(
        _make_token("injected_token")
    )

    # Build a minimal McpServerConfig stub
    mcp_cfg = MagicMock()
    mcp_cfg.id = "srv-oauth"
    mcp_cfg.type = McpServerType.HTTP
    mcp_cfg.oauth_enabled = True
    mcp_cfg.to_sdk_config.return_value = {
        "type": "http",
        "url": "https://mcp.example.com/v1/mcp",
        "headers": {},
    }

    sdk_config = await coordinator._get_mcp_sdk_config(mcp_cfg)

    assert sdk_config["headers"]["Authorization"] == "Bearer injected_token"


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_no_token_no_header(tmp_path: Path):
    """_get_mcp_sdk_config() does not add Authorization header when no token stored."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)

    mcp_cfg = MagicMock()
    mcp_cfg.id = "srv-no-token"
    mcp_cfg.type = McpServerType.HTTP
    mcp_cfg.oauth_enabled = True
    mcp_cfg.to_sdk_config.return_value = {
        "type": "http",
        "url": "https://mcp.example.com/v1/mcp",
        "headers": {},
    }

    sdk_config = await coordinator._get_mcp_sdk_config(mcp_cfg)

    assert "Authorization" not in sdk_config["headers"]


# ---------------------------------------------------------------------------
# XSS escaping tests (issue #861)
# ---------------------------------------------------------------------------


def test_issue_861_xss_in_error_description(tmp_path: Path):
    """OAuth callback with XSS payload in error_description must escape it."""
    from starlette.testclient import TestClient

    from src.web_server import ClaudeWebUI

    server = ClaudeWebUI(data_dir=tmp_path)

    xss_payload = "<script>alert(1)</script>"
    with TestClient(server.app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/oauth/callback",
            params={"error": "access_denied", "error_description": xss_payload},
        )

    assert resp.status_code == 400
    body = resp.text
    assert "<script>" not in body
    assert html.escape(xss_payload) in body


def test_issue_861_xss_in_exception_message(tmp_path: Path):
    """OAuth callback exception with HTML in message must escape it."""
    from unittest.mock import patch

    from starlette.testclient import TestClient

    from src.web_server import ClaudeWebUI

    server = ClaudeWebUI(data_dir=tmp_path)

    xss_payload = "<img src=x onerror=alert(1)>"

    async def _raise(*args, **kwargs):
        raise ValueError(xss_payload)

    with patch.object(server.coordinator.oauth_manager, "complete_flow", _raise):
        with TestClient(server.app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/oauth/callback",
                params={"code": "somecode", "state": "somestate"},
            )

    assert resp.status_code == 400
    body = resp.text
    assert "<img" not in body
    assert html.escape(xss_payload) in body


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_non_oauth_passthrough(tmp_path: Path):
    """_get_mcp_sdk_config() returns plain to_sdk_config() for non-OAuth servers."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)

    expected = {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"]}
    mcp_cfg = MagicMock()
    mcp_cfg.id = "srv-stdio"
    mcp_cfg.type = McpServerType.STDIO
    mcp_cfg.oauth_enabled = False
    mcp_cfg.to_sdk_config.return_value = expected

    sdk_config = await coordinator._get_mcp_sdk_config(mcp_cfg)

    assert sdk_config == expected
