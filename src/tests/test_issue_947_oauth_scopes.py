"""Tests for OAuth re-auth scope fix (issue #947).

Verifies that start_flow() includes a ``scope`` parameter in the authorization
URL and token exchange request when ``scopes_supported`` is advertised by
Protected Resource Metadata (RFC 9728) or AS Metadata (RFC 8414).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthClientInformationFull

from src.oauth_manager import OAuthFlowManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_RESPONSE_JSON = json.dumps(
    {"access_token": "tok_947", "token_type": "Bearer"}
).encode()


def _make_client_info(client_id: str = "pre-registered") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
        token_endpoint_auth_method="none",
    )


def _make_token_http_response(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200

    async def _aread():
        return body

    resp.aread = _aread
    return resp


def _make_prm_mock(scopes: list[str] | None) -> MagicMock:
    """Return a mock ProtectedResourceMetadata with the given scopes_supported."""
    prm = MagicMock()
    prm.authorization_servers = None
    prm.scopes_supported = scopes
    return prm


def _make_asm_mock(scopes: list[str] | None) -> MagicMock:
    """Return a mock OAuthMetadata (AS Metadata) with the given scopes_supported."""
    asm = MagicMock()
    asm.token_endpoint = "https://auth.example.com/token"
    asm.authorization_endpoint = "https://auth.example.com/authorize"
    asm.registration_endpoint = None
    asm.scopes_supported = scopes
    return asm


def _make_http_client_class() -> MagicMock:
    """Return a patched httpx.AsyncClient class whose instances respond with 200."""
    http_resp = MagicMock()
    http_resp.status_code = 200

    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.send = AsyncMock(return_value=http_resp)

    cls_mock = MagicMock()
    cls_mock.return_value = instance
    return cls_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_947_scope_included_from_prm(tmp_path: Path):
    """scope is included in auth URL when PRM advertises scopes_supported."""
    manager = OAuthFlowManager(tmp_path)
    # Pre-store client so DCR is skipped (focuses test on scope logic)
    await manager.get_token_store("srv1").set_client_info(_make_client_info())

    mock_prm = _make_prm_mock(["read:jira-work", "write:jira-work"])
    mock_asm = _make_asm_mock(scopes=None)

    with (
        patch("src.oauth_manager.handle_protected_resource_response", AsyncMock(return_value=mock_prm)),
        patch("src.oauth_manager.handle_auth_metadata_response", AsyncMock(return_value=(True, mock_asm))),
        patch("src.oauth_manager.httpx.AsyncClient", _make_http_client_class()),
    ):
        auth_url = await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    assert "scope=" in auth_url
    state = list(manager._pending.keys())[0]
    assert manager._pending[state]["requested_scopes"] == ["read:jira-work", "write:jira-work"]


@pytest.mark.asyncio
async def test_issue_947_scope_falls_back_to_asm(tmp_path: Path):
    """scope falls back to AS metadata scopes when PRM has no scopes_supported."""
    manager = OAuthFlowManager(tmp_path)
    await manager.get_token_store("srv1").set_client_info(_make_client_info())

    mock_prm = _make_prm_mock(scopes=None)
    mock_asm = _make_asm_mock(scopes=["read", "write"])

    with (
        patch("src.oauth_manager.handle_protected_resource_response", AsyncMock(return_value=mock_prm)),
        patch("src.oauth_manager.handle_auth_metadata_response", AsyncMock(return_value=(True, mock_asm))),
        patch("src.oauth_manager.httpx.AsyncClient", _make_http_client_class()),
    ):
        auth_url = await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    assert "scope=" in auth_url
    state = list(manager._pending.keys())[0]
    assert manager._pending[state]["requested_scopes"] == ["read", "write"]


@pytest.mark.asyncio
async def test_issue_947_scope_omitted_when_no_metadata_scopes(tmp_path: Path):
    """scope is omitted from auth URL when neither PRM nor ASM advertises scopes."""
    manager = OAuthFlowManager(tmp_path)
    await manager.get_token_store("srv1").set_client_info(_make_client_info())

    mock_prm = _make_prm_mock(scopes=None)
    mock_asm = _make_asm_mock(scopes=None)

    with (
        patch("src.oauth_manager.handle_protected_resource_response", AsyncMock(return_value=mock_prm)),
        patch("src.oauth_manager.handle_auth_metadata_response", AsyncMock(return_value=(True, mock_asm))),
        patch("src.oauth_manager.httpx.AsyncClient", _make_http_client_class()),
    ):
        auth_url = await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    assert "scope=" not in auth_url
    state = list(manager._pending.keys())[0]
    assert manager._pending[state]["requested_scopes"] is None


@pytest.mark.asyncio
async def test_issue_947_scope_included_on_reauth_stored_client(tmp_path: Path):
    """scope is included in auth URL on re-auth where DCR is skipped (stored client)."""
    manager = OAuthFlowManager(tmp_path)
    # Simulate re-authentication: client already registered from a previous flow
    await manager.get_token_store("srv1").set_client_info(_make_client_info("existing-client"))

    mock_prm = _make_prm_mock(["profile", "email"])
    mock_asm = _make_asm_mock(scopes=None)

    with (
        patch("src.oauth_manager.handle_protected_resource_response", AsyncMock(return_value=mock_prm)),
        patch("src.oauth_manager.handle_auth_metadata_response", AsyncMock(return_value=(True, mock_asm))),
        patch("src.oauth_manager.httpx.AsyncClient", _make_http_client_class()),
    ):
        auth_url = await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    # Both scope and the stored client_id must appear in the URL
    assert "scope=" in auth_url
    assert "client_id=existing-client" in auth_url
    state = list(manager._pending.keys())[0]
    assert manager._pending[state]["requested_scopes"] == ["profile", "email"]


@pytest.mark.asyncio
async def test_issue_947_token_exchange_includes_scope(tmp_path: Path):
    """complete_flow() includes scope in the token exchange POST body."""
    manager = OAuthFlowManager(tmp_path)

    state = "test_state_947"
    manager._pending[state] = {
        "server_id": "srv1",
        "token_endpoint": "https://auth.example.com/token",
        "client_id": "client-abc",
        "code_verifier": "v" * 43,
        "redirect_uri": "http://localhost/oauth/callback",
        "requested_scopes": ["read", "write"],
    }

    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client_cls:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client_cls.return_value = client_instance

        await manager.complete_flow(state, code="auth_code_947")

        call_kwargs = client_instance.post.call_args
        form_data = call_kwargs[1]["data"]
        assert form_data.get("scope") == "read write"
