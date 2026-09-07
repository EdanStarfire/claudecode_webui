"""Tests for OAuth MCP confidential-client (client_secret) support (issue #1867).

Covers:
- start_flow(): pre-registered client with client_secret => token_endpoint_auth_method
  "client_secret_post", client_secret persisted via store.set_client_info().
- start_flow(): pre-registered client without client_secret (Slack-shaped) is byte-for-byte
  unchanged (auth_method "none", no client_secret) EXCEPT it now also persists client_info
  (the refresh-persistence bug fix) so refresh_token() can find it later.
- complete_flow(): includes client_secret in the token-exchange body when the persisted
  client_info has one; omits it otherwise (DCR / Slack).
- refresh_token(): includes client_secret in the refresh body when present; omits it
  otherwise.
- End-to-end (start_flow -> complete_flow -> refresh_token) for both the confidential
  (Google-shaped) and public (Slack-shaped) paths, asserting the full body contents.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.oauth_manager import OAuthFlowManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOT_FOUND = MagicMock(status_code=404)


def _make_token_http_response(body: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code

    async def _aread():
        return body

    resp.aread = _aread
    return resp


_TOKEN_RESPONSE_JSON = json.dumps(
    {"access_token": "flow_token", "token_type": "Bearer", "refresh_token": "flow_refresh"}
).encode()

_REFRESH_RESPONSE_JSON = json.dumps(
    {"access_token": "refreshed_token", "token_type": "Bearer"}
).encode()


async def _start_flow_pre_registered(
    manager: OAuthFlowManager, *, client_secret: str | None
) -> str:
    """Run start_flow() with all discovery calls 404ing (path-derived endpoints)."""
    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        # 2 PRM discovery attempts (path + root) + 1 AS metadata attempt, all 404
        client_instance.send = AsyncMock(side_effect=[_NOT_FOUND, _NOT_FOUND, _NOT_FOUND])
        mock_client.return_value = client_instance

        return await manager.start_flow(
            server_id="srv1",
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
            pre_registered_client_id="pre-registered-id",
            client_secret=client_secret,
        )


# ---------------------------------------------------------------------------
# start_flow() — pre-registered client, confidential vs public
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_confidential_client_sets_auth_method_and_persists(tmp_path: Path):
    """Confidential pre-registered client => client_secret_post, persisted client_info."""
    manager = OAuthFlowManager(tmp_path)

    auth_url = await _start_flow_pre_registered(manager, client_secret="s3cr3t")

    assert "client_id=pre-registered-id" in auth_url

    store = manager.get_token_store("srv1")
    client_info = await store.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "pre-registered-id"
    assert client_info.client_secret == "s3cr3t"
    assert client_info.token_endpoint_auth_method == "client_secret_post"


@pytest.mark.asyncio
async def test_start_flow_confidential_client_requests_offline_refresh(tmp_path: Path):
    """Google never issues a refresh_token without access_type=offline, and won't
    re-issue one for an already-granted app without prompt=consent — both must be in
    the authorization URL for a confidential (client_secret-bearing) request, or
    refresh silently can never work (issue #1867's "No refresh token was stored"
    symptom, found during live testing against real Google OAuth)."""
    manager = OAuthFlowManager(tmp_path)

    auth_url = await _start_flow_pre_registered(manager, client_secret="s3cr3t")

    assert "access_type=offline" in auth_url
    assert "prompt=consent" in auth_url


@pytest.mark.asyncio
async def test_start_flow_public_client_unchanged_but_now_persists(tmp_path: Path):
    """Public pre-registered client (Slack-shaped): auth_method stays 'none', no secret,
    but client_info is now persisted (closes the refresh-persistence gap for issue #1867).
    """
    manager = OAuthFlowManager(tmp_path)

    auth_url = await _start_flow_pre_registered(manager, client_secret=None)

    assert "client_id=pre-registered-id" in auth_url

    store = manager.get_token_store("srv1")
    client_info = await store.get_client_info()
    assert client_info is not None  # NEW: previously None for the pre-registered path
    assert client_info.client_id == "pre-registered-id"
    assert client_info.client_secret is None
    assert client_info.token_endpoint_auth_method == "none"


@pytest.mark.asyncio
async def test_start_flow_public_client_omits_offline_params(tmp_path: Path):
    """Slack (and any other public pre-registered client) must not get
    access_type=offline/prompt=consent — those are scoped to confidential clients only,
    to keep the public-client request byte-for-byte unchanged."""
    manager = OAuthFlowManager(tmp_path)

    auth_url = await _start_flow_pre_registered(manager, client_secret=None)

    assert "access_type" not in auth_url
    assert "prompt" not in auth_url


# ---------------------------------------------------------------------------
# complete_flow() — client_secret in token-exchange body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_flow_includes_client_secret_when_present(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    from mcp.shared.auth import OAuthClientInformationFull

    await store.set_client_info(
        OAuthClientInformationFull(
            client_id="cid",
            client_secret="s3cr3t",
            redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
            token_endpoint_auth_method="client_secret_post",
        )
    )

    state = "state1"
    manager._pending[state] = {
        "server_id": "srv1",
        "token_endpoint": "https://auth.example.com/token",
        "client_id": "cid",
        "code_verifier": "v" * 43,
        "redirect_uri": "http://localhost/oauth/callback",
    }

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client.return_value = client_instance

        await manager.complete_flow(state, code="auth_code")

        form_data = client_instance.post.call_args[1]["data"]

    assert form_data["client_secret"] == "s3cr3t"


@pytest.mark.asyncio
async def test_complete_flow_omits_client_secret_when_absent(tmp_path: Path):
    """No stored client_info (or one with no secret) => no client_secret key at all."""
    manager = OAuthFlowManager(tmp_path)
    # Intentionally do NOT call store.set_client_info() — simulates DCR-less edge case.

    state = "state2"
    manager._pending[state] = {
        "server_id": "srv1",
        "token_endpoint": "https://auth.example.com/token",
        "client_id": "cid",
        "code_verifier": "v" * 43,
        "redirect_uri": "http://localhost/oauth/callback",
    }

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client.return_value = client_instance

        await manager.complete_flow(state, code="auth_code")

        form_data = client_instance.post.call_args[1]["data"]

    assert "client_secret" not in form_data


# ---------------------------------------------------------------------------
# refresh_token() — client_secret in refresh body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_includes_client_secret_when_present(tmp_path: Path):
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(
        OAuthToken(access_token="old", token_type="Bearer", refresh_token="old_refresh")
    )
    await store.set_client_info(
        OAuthClientInformationFull(
            client_id="cid",
            client_secret="s3cr3t",
            redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
            token_endpoint_auth_method="client_secret_post",
        )
    )
    store.set_token_endpoint("https://auth.example.com/token")

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        resp = _make_token_http_response(_REFRESH_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=resp)
        mock_client.return_value = client_instance

        result = await manager.refresh_token("srv1")

        form_data = client_instance.post.call_args[1]["data"]

    assert result is not None
    assert form_data["client_secret"] == "s3cr3t"


@pytest.mark.asyncio
async def test_refresh_token_omits_client_secret_when_absent(tmp_path: Path):
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(
        OAuthToken(access_token="old", token_type="Bearer", refresh_token="old_refresh")
    )
    await store.set_client_info(
        OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
            token_endpoint_auth_method="none",
        )
    )
    store.set_token_endpoint("https://auth.example.com/token")

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        resp = _make_token_http_response(_REFRESH_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=resp)
        mock_client.return_value = client_instance

        result = await manager.refresh_token("srv1")

        form_data = client_instance.post.call_args[1]["data"]

    assert result is not None
    assert "client_secret" not in form_data


# ---------------------------------------------------------------------------
# End-to-end: start_flow -> complete_flow -> refresh_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_confidential_client_full_flow(tmp_path: Path):
    """Google-shaped confidential client: client_secret flows through every stage."""
    manager = OAuthFlowManager(tmp_path)

    await _start_flow_pre_registered(manager, client_secret="google-secret")
    state = next(iter(manager._pending))

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client.return_value = client_instance

        await manager.complete_flow(state, code="auth_code")
        exchange_body = client_instance.post.call_args[1]["data"]

    assert exchange_body["client_secret"] == "google-secret"

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        refresh_resp = _make_token_http_response(_REFRESH_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=refresh_resp)
        mock_client.return_value = client_instance

        new_token = await manager.refresh_token("srv1")
        refresh_body = client_instance.post.call_args[1]["data"]

    assert new_token is not None
    assert refresh_body["client_secret"] == "google-secret"


# ---------------------------------------------------------------------------
# Always-visible error logging (issue #1867 — oauth logging was previously
# completely invisible regardless of --debug-all; see shared/logging_config.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_missing_client_info_logs_error(tmp_path: Path, caplog):
    """A token with no persisted client_info can never be refreshed — this must be
    logged at ERROR (always visible) rather than the old invisible WARNING, since it's
    a silent, otherwise-undiagnosable dead end."""
    from mcp.shared.auth import OAuthToken

    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(
        OAuthToken(access_token="old", token_type="Bearer", refresh_token="old_refresh")
    )
    # Intentionally no store.set_client_info() call.

    with caplog.at_level("ERROR", logger="backend.oauth_manager"):
        result = await manager.refresh_token("srv1")

    assert result is None
    assert any(
        "cannot refresh" in r.message and r.levelname == "ERROR" for r in caplog.records
    )


@pytest.mark.asyncio
async def test_complete_flow_unknown_state_logs_error(tmp_path: Path, caplog):
    """An unrecognized state (e.g. backend restarted since start_flow()) must be logged
    at ERROR — this fully explains an otherwise-mysterious "it worked once" OAuth
    failure and must not require --debug-oauth to see."""
    manager = OAuthFlowManager(tmp_path)

    with caplog.at_level("ERROR", logger="backend.oauth_manager"):
        with pytest.raises(ValueError, match="No pending OAuth flow"):
            await manager.complete_flow("nonexistent_state", "code")

    assert any(r.levelname == "ERROR" for r in caplog.records)


@pytest.mark.asyncio
async def test_e2e_public_client_no_regression(tmp_path: Path):
    """Slack-shaped public client: no client_secret key appears anywhere, ever.

    Also confirms the refresh-persistence fix doesn't change Slack's request shape —
    only that refresh_token() can now actually attempt the call (previously it always
    returned None early due to missing client_info).
    """
    manager = OAuthFlowManager(tmp_path)

    await _start_flow_pre_registered(manager, client_secret=None)
    state = next(iter(manager._pending))

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        token_resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=token_resp)
        mock_client.return_value = client_instance

        await manager.complete_flow(state, code="auth_code")
        exchange_body = client_instance.post.call_args[1]["data"]

    assert "client_secret" not in exchange_body

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        refresh_resp = _make_token_http_response(_REFRESH_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=refresh_resp)
        mock_client.return_value = client_instance

        # Before the persistence fix this would have returned None here (no client_info).
        new_token = await manager.refresh_token("srv1")
        refresh_body = client_instance.post.call_args[1]["data"]

    assert new_token is not None
    assert "client_secret" not in refresh_body
