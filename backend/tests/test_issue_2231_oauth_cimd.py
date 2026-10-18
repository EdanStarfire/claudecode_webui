"""Tests for OAuth client-ID metadata document (CIMD) support (issue #2231).

draft-ietf-oauth-client-id-metadata-document: a third fallback below
pre_registered_client_id and RFC 7591 DCR for MCP servers (e.g. Home Assistant)
that advertise client_id_metadata_document_supported=True but no
registration_endpoint.

Covers:
- OAuthFlowManager.start_flow(): CIMD branch, DCR-wins-over-CIMD precedence,
  the two distinct CIMD error messages, and an explicit regression check that
  the pre-existing DCR-only path is unaffected when client_metadata_url is
  unset. (The full existing test_issue_813_oauth.py suite is also run
  unchanged as part of this PR's regression guard.)
- OAuthSecretBundleManager.oauth_initiate_flow(): the custom-callback-path
  guard that forces client_metadata_url to None before calling start_flow().

The Frontend-side GET /oauth/client-metadata.json route lives in src/ (a
different process/package — see src/tests/test_issue_2231_oauth_client_metadata.py
for its integration coverage), not here.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from backend.oauth_manager import OAuthFlowManager
from backend.oauth_secret_bundle_manager import OAuthSecretBundleManager

# ---------------------------------------------------------------------------
# Shared discovery fixtures
# ---------------------------------------------------------------------------

_SERVER_URL = "https://mcp.example.com"
_REDIRECT_URI = "https://app.example.com/oauth/callback"
_VALID_CLIENT_METADATA_URL = "https://app.example.com/oauth/client-metadata.json"


def _not_found_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    return resp


def _metadata_response(**overrides) -> MagicMock:
    """A 200 response whose body is a valid OAuthMetadata JSON document."""
    body = {
        "issuer": "https://mcp.example.com",
        "authorization_endpoint": "https://mcp.example.com/authorize",
        "token_endpoint": "https://mcp.example.com/token",
    }
    body.update(overrides)
    resp = MagicMock()
    resp.status_code = 200

    async def _aread():
        return json.dumps(body).encode()

    resp.aread = _aread
    return resp


def _dcr_success_response(client_id: str = "dcr-client") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 201

    async def _aread():
        return json.dumps(
            {
                "client_id": client_id,
                "redirect_uris": [_REDIRECT_URI],
                "token_endpoint_auth_method": "none",
            }
        ).encode()

    resp.aread = _aread
    return resp


def _dcr_failure_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 400
    resp.next_request = None
    resp.text = "registration not supported"

    async def _aread():
        return b""

    resp.aread = _aread
    return resp


def _mock_http_client(send_side_effect):
    client_instance = AsyncMock()
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_instance.send = AsyncMock(side_effect=send_side_effect)
    return client_instance


# ---------------------------------------------------------------------------
# 1. CIMD success: no registration_endpoint, CIMD supported, valid HTTPS URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_uses_cimd_when_server_supports_it_and_no_dcr(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM discovery
            _metadata_response(client_id_metadata_document_supported=True),  # AS metadata
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-cimd",
            server_url=_SERVER_URL,
            redirect_uri=_REDIRECT_URI,
            client_metadata_url=_VALID_CLIENT_METADATA_URL,
        )

        # No DCR POST attempted — only the 2 discovery GETs.
        assert client_instance.send.call_count == 2

    query = parse_qs(urlparse(auth_url).query)
    assert query["client_id"][0] == _VALID_CLIENT_METADATA_URL

    store = manager.get_token_store("srv-cimd")
    client_info = await store.get_client_info()
    assert client_info is not None
    assert client_info.client_id == _VALID_CLIENT_METADATA_URL
    assert client_info.token_endpoint_auth_method == "none"


# ---------------------------------------------------------------------------
# 2. Precedence: registration_endpoint present + CIMD supported -> DCR wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_prefers_dcr_over_cimd_when_registration_endpoint_present(
    tmp_path: Path,
):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM discovery
            _metadata_response(
                registration_endpoint="https://mcp.example.com/register",
                client_id_metadata_document_supported=True,
            ),  # AS metadata: advertises BOTH
            _dcr_success_response("dcr-won-client"),  # DCR attempted and succeeds
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-both",
            server_url=_SERVER_URL,
            redirect_uri=_REDIRECT_URI,
            client_metadata_url=_VALID_CLIENT_METADATA_URL,
        )

        # DCR POST was attempted (3rd send call) despite CIMD being usable.
        assert client_instance.send.call_count == 3

    assert "client_id=dcr-won-client" in auth_url


# ---------------------------------------------------------------------------
# 3. Custom-callback-path guard: client_metadata_url withheld (None) -> error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_raises_cimd_error_when_metadata_url_withheld(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM discovery
            _metadata_response(client_id_metadata_document_supported=True),  # AS metadata
            _dcr_failure_response(),  # DCR attempted (since CIMD unusable) and fails
        ])
        mock_client.return_value = client_instance

        with pytest.raises(RuntimeError) as exc_info:
            await manager.start_flow(
                server_id="srv-no-url",
                server_url=_SERVER_URL,
                redirect_uri=_REDIRECT_URI,
                client_metadata_url=None,
            )

    message = str(exc_info.value)
    assert "Client ID Metadata Document" in message
    assert "custom OAuth callback path/port is configured" in message


# ---------------------------------------------------------------------------
# 4. Non-HTTPS client_metadata_url -> distinct error, different reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_raises_cimd_error_when_metadata_url_not_https(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM discovery
            _metadata_response(client_id_metadata_document_supported=True),  # AS metadata
            _dcr_failure_response(),  # DCR attempted (since CIMD unusable) and fails
        ])
        mock_client.return_value = client_instance

        with pytest.raises(RuntimeError) as exc_info:
            await manager.start_flow(
                server_id="srv-http-url",
                server_url=_SERVER_URL,
                redirect_uri=_REDIRECT_URI,
                client_metadata_url="http://app.example.com/oauth/client-metadata.json",
            )

    message = str(exc_info.value)
    assert "Client ID Metadata Document" in message
    assert "not HTTPS" in message
    assert "custom OAuth callback path/port is configured" not in message


# ---------------------------------------------------------------------------
# 5. Regression: no client_metadata_url supplied -> byte-identical DCR path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_dcr_path_unchanged_when_no_metadata_url(tmp_path: Path):
    """Existing behavior: a server with no registration_endpoint and no CIMD
    support goes through DCR exactly as before #2231, whether or not a caller
    happens to pass client_metadata_url=None (the default)."""
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM discovery
            _metadata_response(),  # AS metadata: no registration_endpoint, no CIMD flag
            _dcr_success_response("registered-client"),
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-plain-dcr",
            server_url=_SERVER_URL,
            redirect_uri=_REDIRECT_URI,
        )

        assert client_instance.send.call_count == 3

    assert "client_id=registered-client" in auth_url
    assert "code_challenge=" in auth_url
    assert "state=" in auth_url


# ---------------------------------------------------------------------------
# 6. oauth_secret_bundle_manager guard: custom callback path forces None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_initiate_flow_forces_metadata_url_none_for_custom_callback_path():
    config = MagicMock()
    config.oauth_client_id = None
    config.oauth_client_secret = None
    config.oauth_custom_callback_path = "/custom/callback"
    config.oauth_custom_callback_port = None

    bundle_manager = OAuthSecretBundleManager(
        credential_vault=MagicMock(),
        oauth_manager=MagicMock(),
        mcp_config_manager=MagicMock(),
        vault_refresh_manager=MagicMock(),
    )
    bundle_manager.mcp_config_manager.get_config = AsyncMock(return_value=config)
    bundle_manager.oauth_manager.start_flow = AsyncMock(return_value="https://auth.example.com/authorize?...")

    await bundle_manager.oauth_initiate_flow(
        "cfg1", "https://mcp.example.com", _REDIRECT_URI, "App",
        client_metadata_url=_VALID_CLIENT_METADATA_URL,
    )

    call_kwargs = bundle_manager.oauth_manager.start_flow.call_args.kwargs
    assert call_kwargs["client_metadata_url"] is None


@pytest.mark.asyncio
async def test_oauth_initiate_flow_passes_metadata_url_through_without_custom_callback():
    config = MagicMock()
    config.oauth_client_id = None
    config.oauth_client_secret = None
    config.oauth_custom_callback_path = None
    config.oauth_custom_callback_port = None

    bundle_manager = OAuthSecretBundleManager(
        credential_vault=MagicMock(),
        oauth_manager=MagicMock(),
        mcp_config_manager=MagicMock(),
        vault_refresh_manager=MagicMock(),
    )
    bundle_manager.mcp_config_manager.get_config = AsyncMock(return_value=config)
    bundle_manager.oauth_manager.start_flow = AsyncMock(return_value="https://auth.example.com/authorize?...")

    await bundle_manager.oauth_initiate_flow(
        "cfg1", "https://mcp.example.com", _REDIRECT_URI, "App",
        client_metadata_url=_VALID_CLIENT_METADATA_URL,
    )

    call_kwargs = bundle_manager.oauth_manager.start_flow.call_args.kwargs
    assert call_kwargs["client_metadata_url"] == _VALID_CLIENT_METADATA_URL
