"""Tests for OAuth AS-metadata normalization (issue #2240).

Home Assistant's authorization-server metadata uses endpoint paths relative
to its own origin and omits `issuer` entirely — both violate RFC 8414, so
`mcp.client.auth.utils.handle_auth_metadata_response`'s
`OAuthMetadata.model_validate_json()` call rejects it outright and the SDK
helper swallows the `ValidationError`, returning `(True, None)` as if the
server advertised no metadata at all. `OAuthFlowManager._fetch_as_metadata`
(backend/oauth_manager.py) replaces that call site with a version that first
normalizes the raw JSON body (`_normalize_oauth_metadata_document`) before
validating, so CIMD support and the correct endpoints become visible.

Covers:
- `_normalize_oauth_metadata_document`: relative-endpoint resolution against
  the discovery URL's origin, issuer defaulting, and the two documented edge
  cases (present-but-different issuer; discovery URL != resource URL).
- `OAuthFlowManager._fetch_as_metadata`: still-invalid-after-normalization
  returns (True, None, ...) without raising.
- `OAuthFlowManager.start_flow()`: UC1 (Home Assistant fixture, HTTPS metadata
  URL -> CIMD), UC2 (same fixture, HTTP-served WebUI -> non-HTTPS metadata URL
  -> HTTPS-required error before any auth_url is built — the frontend's
  buildClientMetadataUrl() always derives this from window.location, so an
  HTTP-served WebUI yields an http:// URL here, never None), T2
  (fully-absolute document -> unchanged behavior), AC4 (pre-registered
  client_id invalid for a CIMD-only server).

The full pre-existing OAuth suite (test_issue_813_oauth.py,
test_issue_2231_oauth_cimd.py, test_oauth_secret_bundle_manager.py,
test_application_service.py, test_oauth_import_as_secret.py) is run
unchanged alongside this file as the AC6 regression guard.
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from backend.oauth_manager import OAuthFlowManager, _normalize_oauth_metadata_document

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SERVER_URL = "https://homeassistant.secinaction.com/mcp_server/sse"
_REDIRECT_URI = "https://app.example.com/oauth/callback"
_VALID_CLIENT_METADATA_URL = "https://app.example.com/oauth/client-metadata.json"

# Exact document captured in the issue body (truncated "..." in the original
# is simply omitted here — every field actually asserted on is present).
_HOME_ASSISTANT_METADATA = {
    "authorization_endpoint": "/auth/authorize",
    "token_endpoint": "/auth/token",
    "revocation_endpoint": "/auth/revoke",
    "client_id_metadata_document_supported": True,
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["none"],
    "code_challenge_methods_supported": ["S256"],
    "response_types_supported": ["code"],
}


def _not_found_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    return resp


def _metadata_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200

    async def _aread():
        return json.dumps(body).encode()

    resp.aread = _aread
    return resp


def _dcr_failure_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    resp.next_request = None
    resp.text = "not found"

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
# _normalize_oauth_metadata_document unit tests
# ---------------------------------------------------------------------------


def test_normalize_resolves_relative_endpoints_against_discovery_url_origin():
    # Discovery URL origin differs from any "resource" URL a caller might also
    # have in hand — resolution must use the discovery URL's origin, not it.
    discovery_url = "https://auth.example.com/.well-known/oauth-authorization-server"

    normalized = _normalize_oauth_metadata_document(_HOME_ASSISTANT_METADATA, discovery_url)

    assert normalized["authorization_endpoint"] == "https://auth.example.com/auth/authorize"
    assert normalized["token_endpoint"] == "https://auth.example.com/auth/token"
    assert normalized["revocation_endpoint"] == "https://auth.example.com/auth/revoke"


def test_normalize_uses_discovery_url_not_a_different_resource_url():
    # Path-scoped discovery edge case: the discovery URL has its own path
    # component and lives at a different host than the resource server would.
    discovery_url = "https://auth.example.com/tenant1/.well-known/oauth-authorization-server"
    raw = {"authorization_endpoint": "/authorize"}

    normalized = _normalize_oauth_metadata_document(raw, discovery_url)

    assert normalized["authorization_endpoint"] == "https://auth.example.com/authorize"


def test_normalize_defaults_missing_issuer_to_discovery_origin():
    discovery_url = "https://homeassistant.secinaction.com/.well-known/oauth-authorization-server"

    normalized = _normalize_oauth_metadata_document(_HOME_ASSISTANT_METADATA, discovery_url)

    assert normalized["issuer"] == "https://homeassistant.secinaction.com"


def test_normalize_leaves_present_but_different_issuer_untouched():
    discovery_url = "https://homeassistant.secinaction.com/.well-known/oauth-authorization-server"
    raw = dict(_HOME_ASSISTANT_METADATA, issuer="https://other-issuer.example.com")

    normalized = _normalize_oauth_metadata_document(raw, discovery_url)

    assert normalized["issuer"] == "https://other-issuer.example.com"


def test_normalize_leaves_absolute_endpoints_untouched():
    discovery_url = "https://mcp.example.com/.well-known/oauth-authorization-server"
    raw = {
        "issuer": "https://mcp.example.com",
        "authorization_endpoint": "https://mcp.example.com/authorize",
        "token_endpoint": "https://mcp.example.com/token",
    }

    normalized = _normalize_oauth_metadata_document(raw, discovery_url)

    assert normalized == raw


# ---------------------------------------------------------------------------
# _fetch_as_metadata: still invalid after normalization
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    """Captures records directly off the `oauth` logger rather than relying on
    pytest's `caplog` (which depends on propagation to the root logger) —
    `shared/logging_config.py`'s `configure_logging()` sets `propagate = False`
    on this logger the first time any test boots the real backend app, after
    which caplog can no longer see anything logged to it for the rest of the
    pytest process. Attaching directly here is independent of that and of
    test execution order."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_fetch_as_metadata_logs_warning_and_swallows_when_still_invalid(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    # Missing token_endpoint entirely (a required field) — normalization has
    # nothing to resolve it from, so validation still fails afterward.
    response = _metadata_response({"authorization_endpoint": "/authorize"})

    oauth_logger = logging.getLogger("oauth")
    handler = _ListHandler()
    handler.setLevel(logging.WARNING)
    oauth_logger.addHandler(handler)
    try:
        ok, meta, normalized = await manager._fetch_as_metadata(
            response, "https://mcp.example.com/.well-known/oauth-authorization-server"
        )
    finally:
        oauth_logger.removeHandler(handler)

    assert ok is True
    assert meta is None
    assert normalized is False
    assert any("rejected by validation" in r.getMessage() for r in handler.records)


# ---------------------------------------------------------------------------
# UC1 (AC2): Home Assistant fixture, HTTPS client-metadata URL -> CIMD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_home_assistant_fixture_uses_cimd(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM path-based
            _not_found_response(),  # PRM root-based
            _metadata_response(_HOME_ASSISTANT_METADATA),  # AS metadata
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-ha",
            server_url=_SERVER_URL,
            redirect_uri=_REDIRECT_URI,
            client_metadata_url=_VALID_CLIENT_METADATA_URL,
        )

    assert auth_url.startswith("https://homeassistant.secinaction.com/auth/authorize?")
    query = parse_qs(urlparse(auth_url).query)
    assert query["client_id"][0] == _VALID_CLIENT_METADATA_URL

    store = manager.get_token_store("srv-ha")
    client_info = await store.get_client_info()
    assert client_info is not None
    assert client_info.client_id == _VALID_CLIENT_METADATA_URL
    assert client_info.token_endpoint_auth_method == "none"

    summary = manager.get_discovery_summary("srv-ha")
    assert summary is not None
    assert summary["as_metadata_found"] is True
    assert summary["as_metadata_normalized"] is True
    assert summary["mechanism"] == "cimd"


# ---------------------------------------------------------------------------
# UC2 (AC2/T3): Home Assistant fixture, no client-metadata URL -> HTTPS error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_home_assistant_fixture_without_metadata_url_raises_https_error(
    tmp_path: Path,
):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM path-based
            _not_found_response(),  # PRM root-based
            _metadata_response(_HOME_ASSISTANT_METADATA),  # AS metadata
            _dcr_failure_response(),  # blind DCR POST to /register, fails
        ])
        mock_client.return_value = client_instance

        with pytest.raises(RuntimeError) as exc_info:
            await manager.start_flow(
                server_id="srv-ha-http",
                server_url=_SERVER_URL,
                redirect_uri=_REDIRECT_URI,
                # The frontend always derives this from window.location (see
                # buildClientMetadataUrl() in mcpConfig.js) — a WebUI served over
                # HTTP produces an http:// URL here, never None.
                client_metadata_url="http://172.28.16.2:8000/oauth/client-metadata.json",
            )

    message = str(exc_info.value)
    assert "Client ID Metadata Document" in message
    assert "not HTTPS" in message

    # No auth_url was ever constructed — the exception fires before that line.
    store = manager.get_token_store("srv-ha-http")
    assert await store.get_client_info() is None


# ---------------------------------------------------------------------------
# T2: fully-absolute, compliant metadata document -> unchanged behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_compliant_document_unaffected_by_normalization(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    compliant = {
        "issuer": "https://mcp.example.com",
        "authorization_endpoint": "https://mcp.example.com/authorize",
        "token_endpoint": "https://mcp.example.com/token",
        "registration_endpoint": "https://mcp.example.com/register",
    }

    def _dcr_success_response() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 201

        async def _aread():
            return json.dumps({
                "client_id": "dcr-client",
                "redirect_uris": [_REDIRECT_URI],
                "token_endpoint_auth_method": "none",
            }).encode()

        resp.aread = _aread
        return resp

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM (root-based only - server_url has no path)
            _metadata_response(compliant),
            _dcr_success_response(),
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-compliant",
            server_url="https://mcp.example.com",
            redirect_uri=_REDIRECT_URI,
        )

    assert auth_url.startswith("https://mcp.example.com/authorize?")
    assert "client_id=dcr-client" in auth_url

    summary = manager.get_discovery_summary("srv-compliant")
    assert summary["as_metadata_normalized"] is False
    assert summary["mechanism"] == "dcr"


# ---------------------------------------------------------------------------
# AC4: pre-registered client_id invalid for a CIMD-only server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_rejects_pre_registered_client_id_for_cimd_only_server(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),
            _not_found_response(),
            _metadata_response(_HOME_ASSISTANT_METADATA),
        ])
        mock_client.return_value = client_instance

        with pytest.raises(RuntimeError) as exc_info:
            await manager.start_flow(
                server_id="srv-bad-preregistered",
                server_url=_SERVER_URL,
                redirect_uri=_REDIRECT_URI,
                pre_registered_client_id="https://app.example.com",  # bare origin, not CIMD-valid
                client_metadata_url=_VALID_CLIENT_METADATA_URL,
            )

    message = str(exc_info.value)
    assert "https://app.example.com" in message
    assert "Client ID Metadata Document" in message
    assert "oauth_client_id" in message


@pytest.mark.asyncio
async def test_start_flow_clears_stale_summary_when_a_retry_fails(tmp_path: Path):
    """A successful start_flow() followed by a failing retry for the same
    server_id must not leave get_discovery_summary() returning the earlier,
    no-longer-trustworthy summary."""
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),
            _not_found_response(),
            _metadata_response(_HOME_ASSISTANT_METADATA),
        ])
        mock_client.return_value = client_instance

        await manager.start_flow(
            server_id="srv-retry",
            server_url=_SERVER_URL,
            redirect_uri=_REDIRECT_URI,
            client_metadata_url=_VALID_CLIENT_METADATA_URL,
        )

    assert manager.get_discovery_summary("srv-retry") is not None

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),
            _not_found_response(),
            _metadata_response(_HOME_ASSISTANT_METADATA),
        ])
        mock_client.return_value = client_instance

        with pytest.raises(RuntimeError):
            await manager.start_flow(
                server_id="srv-retry",
                server_url=_SERVER_URL,
                redirect_uri=_REDIRECT_URI,
                pre_registered_client_id="https://app.example.com",  # bare origin, not CIMD-valid
                client_metadata_url=_VALID_CLIENT_METADATA_URL,
            )

    assert manager.get_discovery_summary("srv-retry") is None


@pytest.mark.asyncio
async def test_start_flow_allows_pre_registered_client_id_for_non_cimd_server(tmp_path: Path):
    """Regression guard: the AC4 check must not fire for an ordinary
    pre-registered-client server (e.g. Slack, Google) that doesn't advertise
    client_id_metadata_document_supported at all."""
    manager = OAuthFlowManager(tmp_path)
    compliant = {
        "issuer": "https://slack.example.com",
        "authorization_endpoint": "https://slack.example.com/authorize",
        "token_endpoint": "https://slack.example.com/token",
    }

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = _mock_http_client([
            _not_found_response(),  # PRM (root-based only - server_url has no path)
            _metadata_response(compliant),
        ])
        mock_client.return_value = client_instance

        auth_url = await manager.start_flow(
            server_id="srv-slack",
            server_url="https://slack.example.com",
            redirect_uri=_REDIRECT_URI,
            pre_registered_client_id="https://app.example.com",  # bare origin, fine here
        )

    assert "client_id=https%3A%2F%2Fapp.example.com" in auth_url
