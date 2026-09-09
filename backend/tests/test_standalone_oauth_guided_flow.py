"""Unit tests for standalone OAuth2 vault-secret guided authorization (issue #1871).

Covers OAuthFlowManager.start_flow_with_endpoints()/complete_flow(persist=False)/
get_pending_context()/cancel_pending()/TTL sweep, and
ApplicationService.standalone_oauth_initiate/complete/reconnect_initiate/cancel.

Uses mocked HTTP (matching test_oauth_import_as_secret.py's pattern) + a real
OAuthFlowManager and SecretsVault against tmp_path, with the OS keyring mocked.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthToken

from backend.application_service import ApplicationService, NoRefreshTokenError
from backend.credential_vault import SecretsVault
from backend.models.secret_record import RefreshSpec, SecretRecord, SecretType
from backend.oauth_manager import OAuthFlowManager

_AUTHZ_ENDPOINT = "https://provider.example.com/oauth/authorize"
_TOKEN_ENDPOINT = "https://provider.example.com/oauth/token"
_CLIENT_ID = "test-client-id"
_REDIRECT_URI = "http://localhost:8000/oauth/callback"


def _http_response(body: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code

    async def _aread():
        return body

    resp.aread = _aread
    return resp


def _token_body(access_token="access1", refresh_token="refresh1", expires_in=3600) -> bytes:
    data = {"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in}
    if refresh_token is not None:
        data["refresh_token"] = refresh_token
    return json.dumps(data).encode()


@pytest.fixture
def keyring_patch():
    with (
        patch("backend.credential_vault.set_secret_value"),
        patch("backend.credential_vault.get_secret_value", return_value="value"),
        patch("backend.credential_vault.delete_secret_value"),
    ):
        yield


def _make_service(tmp_path: Path):
    """Build an ApplicationService with a real OAuthFlowManager + SecretsVault."""
    coordinator = MagicMock()
    oauth_manager = OAuthFlowManager(tmp_path)
    coordinator.oauth_manager = oauth_manager
    vault = SecretsVault(tmp_path)
    coordinator.credential_vault = vault
    service = ApplicationService(coordinator)
    return service, oauth_manager, vault


# ---------------------------------------------------------------------------
# OAuthFlowManager: start_flow_with_endpoints() / complete_flow(persist=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_with_endpoints_no_discovery(tmp_path: Path):
    """No discovery/DCR HTTP calls are made — auth_url built directly from the
    supplied endpoints."""
    manager = OAuthFlowManager(tmp_path)
    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        auth_url, state = await manager.start_flow_with_endpoints(
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            context={"flow_type": "standalone_secret"},
        )
        mock_client.assert_not_called()

    assert auth_url.startswith(_AUTHZ_ENDPOINT + "?")
    assert f"client_id={_CLIENT_ID}" in auth_url
    assert "code_challenge=" in auth_url
    assert f"state={state}" in auth_url
    assert state in manager._pending
    assert manager._pending[state]["context"]["flow_type"] == "standalone_secret"


@pytest.mark.asyncio
async def test_start_flow_with_endpoints_confidential_client_params(tmp_path: Path):
    """Confidential client (client_secret supplied) adds access_type=offline +
    prompt=consent, same as start_flow()'s existing confidential-client handling."""
    manager = OAuthFlowManager(tmp_path)
    auth_url, _ = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        client_secret="shh",
    )
    assert "access_type=offline" in auth_url
    assert "prompt=consent" in auth_url


@pytest.mark.asyncio
async def test_start_flow_with_endpoints_public_client_omits_confidential_params(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    auth_url, _ = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )
    assert "access_type" not in auth_url
    assert "prompt" not in auth_url


@pytest.mark.asyncio
async def test_complete_flow_persist_false_never_touches_fernet_store(tmp_path: Path):
    """persist=False: FernetTokenStore is never written to; the raw OAuthToken plus
    the caller's context dict is returned instead of a server_id."""
    manager = OAuthFlowManager(tmp_path)
    _, state = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        context={"flow_type": "standalone_secret", "base_name": "svc_oauth"},
    )

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=_http_response(_token_body()))
        mock_client.return_value = client_instance

        token, context = await manager.complete_flow(state, "auth_code", persist=False)

    assert isinstance(token, OAuthToken)
    assert token.access_token == "access1"
    assert token.refresh_token == "refresh1"
    assert context["base_name"] == "svc_oauth"
    assert state not in manager._pending
    # No FernetTokenStore artifacts were ever written for this flow.
    assert not any((tmp_path / "oauth_tokens").glob("*.enc"))
    assert not any((tmp_path / "oauth_clients").glob("*.enc"))


@pytest.mark.asyncio
async def test_complete_flow_persist_false_includes_client_secret_in_exchange(tmp_path: Path):
    """Confidential client: client_secret stashed at initiate time is included in the
    token-exchange POST body (read directly from pending, not from FernetTokenStore)."""
    manager = OAuthFlowManager(tmp_path)
    _, state = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        client_secret="confidential-secret-value",
    )

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=_http_response(_token_body()))
        mock_client.return_value = client_instance

        await manager.complete_flow(state, "auth_code", persist=False)
        exchange_body = client_instance.post.call_args[1]["data"]

    assert exchange_body["client_secret"] == "confidential-secret-value"


@pytest.mark.asyncio
async def test_get_pending_context_and_cancel_pending(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    _, state = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        context={"flow_type": "standalone_reconnect"},
    )
    ctx = await manager.get_pending_context(state)
    assert ctx["flow_type"] == "standalone_reconnect"

    cancelled_ctx = await manager.cancel_pending(state)
    assert cancelled_ctx["flow_type"] == "standalone_reconnect"
    assert state not in manager._pending
    assert await manager.get_pending_context(state) is None
    assert await manager.cancel_pending(state) is None


@pytest.mark.asyncio
async def test_pending_ttl_sweep_discards_stale_entries(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)
    _, old_state = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )
    manager._pending[old_state]["created_at"] = time.time() - 700  # older than the 600s TTL

    _, fresh_state = await manager.start_flow_with_endpoints(
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )

    assert old_state not in manager._pending
    assert fresh_state in manager._pending


# ---------------------------------------------------------------------------
# ApplicationService.standalone_oauth_initiate()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_400_on_invalid_base_name(tmp_path: Path):
    service, _, _ = _make_service(tmp_path)
    with pytest.raises(ValueError):
        await service.standalone_oauth_initiate(
            base_name="Invalid Name!",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
        )


@pytest.mark.asyncio
async def test_initiate_409_on_base_name_collision(tmp_path: Path, keyring_patch):
    service, _, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(name="svc_oauth", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
        "existing",
    )
    with pytest.raises(KeyError):
        await service.standalone_oauth_initiate(
            base_name="svc_oauth",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
        )


@pytest.mark.asyncio
async def test_initiate_409_on_refresh_sibling_collision(tmp_path: Path, keyring_patch):
    service, _, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(name="svc_oauth_refresh", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
        "existing",
    )
    with pytest.raises(KeyError):
        await service.standalone_oauth_initiate(
            base_name="svc_oauth",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
        )


@pytest.mark.asyncio
async def test_initiate_404_on_missing_client_secret_sibling(tmp_path: Path, keyring_patch):
    service, _, _ = _make_service(tmp_path)
    with patch("backend.secrets_keyring.get_secret_value", return_value=None):
        with pytest.raises(LookupError):
            await service.standalone_oauth_initiate(
                base_name="svc_oauth",
                authorization_endpoint=_AUTHZ_ENDPOINT,
                token_endpoint=_TOKEN_ENDPOINT,
                client_id=_CLIENT_ID,
                redirect_uri=_REDIRECT_URI,
                client_secret_secret_name="nonexistent-secret",
            )


@pytest.mark.asyncio
async def test_initiate_returns_auth_url_and_flow_id(tmp_path: Path, keyring_patch):
    service, oauth_manager, _ = _make_service(tmp_path)
    result = await service.standalone_oauth_initiate(
        base_name="svc_oauth",
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
        custom_callback_path="/oauth/callback",
        custom_callback_port=8090,
    )
    assert result["auth_url"].startswith(_AUTHZ_ENDPOINT)
    assert result["flow_id"] in oauth_manager._pending
    assert result["custom_callback_path"] == "/oauth/callback"
    assert result["custom_callback_port"] == 8090
    ctx = oauth_manager._pending[result["flow_id"]]["context"]
    assert ctx["flow_type"] == "standalone_secret"
    assert ctx["base_name"] == "svc_oauth"


# ---------------------------------------------------------------------------
# ApplicationService.standalone_oauth_complete() — new secret
# ---------------------------------------------------------------------------


async def _initiate_and_complete(
    service: ApplicationService,
    oauth_manager: OAuthFlowManager,
    *,
    base_name: str = "svc_oauth",
    client_secret_secret_name: str | None = None,
    refresh_token: str | None = "refresh1",
    secret_name_for_reconnect: str | None = None,
):
    """Drive initiate() then complete() with mocked HTTP token exchange, returning
    the service result dict."""
    if secret_name_for_reconnect:
        init_result = await service.standalone_oauth_reconnect_initiate(
            secret_name=secret_name_for_reconnect,
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            client_secret_secret_name=client_secret_secret_name,
        )
    else:
        init_result = await service.standalone_oauth_initiate(
            base_name=base_name,
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            client_secret_secret_name=client_secret_secret_name,
        )
    state = init_result["flow_id"]

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(
            return_value=_http_response(_token_body(refresh_token=refresh_token))
        )
        mock_client.return_value = client_instance
        return await service.standalone_oauth_complete(state, "auth_code_123")


@pytest.mark.asyncio
async def test_complete_creates_primary_and_refresh_secrets(tmp_path: Path, keyring_patch):
    service, oauth_manager, vault = _make_service(tmp_path)
    result = await _initiate_and_complete(service, oauth_manager, base_name="svc_oauth")

    assert set(result["secrets_created"]) == {"svc_oauth", "svc_oauth_refresh"}
    assert result["auto_refresh_enabled"] is True
    assert result["base_name"] == "svc_oauth"

    primary_meta = await vault.get_secret("svc_oauth")
    assert primary_meta["type"] == "oauth2"
    assert primary_meta["refresh"]["refresh_token_secret_name"] == "svc_oauth_refresh"
    assert primary_meta["refresh"]["authorization_endpoint"] == _AUTHZ_ENDPOINT
    assert primary_meta["refresh"]["token_url"] == _TOKEN_ENDPOINT

    service.coordinator.vault_refresh_manager.schedule_secret.assert_called_once_with("svc_oauth")


@pytest.mark.asyncio
async def test_complete_creates_client_secret_sibling_when_referenced(tmp_path: Path, keyring_patch):
    service, oauth_manager, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(name="my-client-secret", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
        "confidential-value",
    )

    with patch("backend.secrets_keyring.get_secret_value", return_value="confidential-value"):
        result = await _initiate_and_complete(
            service, oauth_manager, base_name="svc_oauth2", client_secret_secret_name="my-client-secret"
        )

    assert set(result["secrets_created"]) == {"svc_oauth2", "svc_oauth2_refresh"}
    primary_meta = await vault.get_secret("svc_oauth2")
    assert primary_meta["refresh"]["client_secret_secret_name"] == "my-client-secret"


@pytest.mark.asyncio
async def test_complete_includes_client_secret_in_token_exchange(tmp_path: Path, keyring_patch):
    """Confidential client: client_secret is included in the token-exchange POST body
    when a client-secret sibling is referenced."""
    service, oauth_manager, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(name="my-client-secret", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
        "confidential-value",
    )

    with patch("backend.secrets_keyring.get_secret_value", return_value="confidential-value"):
        init_result = await service.standalone_oauth_initiate(
            base_name="svc_oauth3",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
            client_secret_secret_name="my-client-secret",
        )
        state = init_result["flow_id"]

        with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=_http_response(_token_body()))
            mock_client.return_value = client_instance

            await service.standalone_oauth_complete(state, "auth_code_123")
            exchange_body = client_instance.post.call_args[1]["data"]

    assert exchange_body["client_secret"] == "confidential-value"


@pytest.mark.asyncio
async def test_complete_no_refresh_token_raises_and_creates_nothing(tmp_path: Path, keyring_patch):
    service, oauth_manager, vault = _make_service(tmp_path)
    with pytest.raises(NoRefreshTokenError):
        await _initiate_and_complete(service, oauth_manager, base_name="svc_norefresh", refresh_token=None)

    assert await vault.get_secret("svc_norefresh") is None
    assert await vault.get_secret("svc_norefresh_refresh") is None


@pytest.mark.asyncio
async def test_complete_rolls_back_on_partial_failure(tmp_path: Path, keyring_patch):
    """Failure injected after the refresh-token sibling is created (but before the
    primary record) leaves no orphaned secrets."""
    service, oauth_manager, vault = _make_service(tmp_path)
    init_result = await service.standalone_oauth_initiate(
        base_name="svc_rollback",
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )
    state = init_result["flow_id"]

    original_create_secret = vault.create_secret
    call_count = 0

    async def _flaky_create_secret(record, value):
        nonlocal call_count
        call_count += 1
        if record.type == SecretType.OAUTH2:
            raise RuntimeError("simulated primary-record write failure")
        return await original_create_secret(record, value)

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=_http_response(_token_body()))
        mock_client.return_value = client_instance

        with patch.object(vault, "create_secret", side_effect=_flaky_create_secret):
            with pytest.raises(RuntimeError, match="simulated primary-record write failure"):
                await service.standalone_oauth_complete(state, "auth_code_123")

    assert await vault.get_secret("svc_rollback") is None
    assert await vault.get_secret("svc_rollback_refresh") is None


@pytest.mark.asyncio
async def test_complete_409_if_base_name_created_during_flow(tmp_path: Path, keyring_patch):
    """Defense in depth: base_name pre-checked at initiate time can still collide by
    completion time if something else created it in the interim."""
    service, oauth_manager, vault = _make_service(tmp_path)
    init_result = await service.standalone_oauth_initiate(
        base_name="svc_race",
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )
    state = init_result["flow_id"]

    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(name="svc_race", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
        "raced-in",
    )

    with patch("backend.oauth_manager.httpx.AsyncClient") as mock_client:
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=_http_response(_token_body()))
        mock_client.return_value = client_instance

        with pytest.raises(KeyError):
            await service.standalone_oauth_complete(state, "auth_code_123")


# ---------------------------------------------------------------------------
# ApplicationService.standalone_oauth_reconnect_initiate() / Reconnect completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_initiate_404_when_secret_missing(tmp_path: Path):
    service, _, _ = _make_service(tmp_path)
    with pytest.raises(LookupError):
        await service.standalone_oauth_reconnect_initiate(
            secret_name="does-not-exist",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
        )


@pytest.mark.asyncio
async def test_reconnect_initiate_rejects_manual_paste_in_secret(tmp_path: Path, keyring_patch):
    """A manually paste-in oauth2 secret (no refresh.authorization_endpoint) can never
    gain a Reconnect action — locked in during mockup review."""
    service, _, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    await vault.create_secret(
        SecretRecord(
            name="manual_oauth",
            type=SecretType.OAUTH2,
            target_hosts=["example.com"],
            scrub=None,
            refresh=RefreshSpec(
                token_url=_TOKEN_ENDPOINT,
                client_id=_CLIENT_ID,
                refresh_token_secret_name="manual_oauth_refresh",
            ),
            created_at=now,
            updated_at=now,
        ),
        "manual_access_token",
    )
    with pytest.raises(ValueError, match="cannot be reconnected"):
        await service.standalone_oauth_reconnect_initiate(
            secret_name="manual_oauth",
            authorization_endpoint=_AUTHZ_ENDPOINT,
            token_endpoint=_TOKEN_ENDPOINT,
            client_id=_CLIENT_ID,
            redirect_uri=_REDIRECT_URI,
        )


@pytest.mark.asyncio
async def test_reconnect_completes_replaces_bundle_in_place(tmp_path: Path, keyring_patch):
    service, oauth_manager, vault = _make_service(tmp_path)

    # First, create a guided-flow secret the normal way.
    first = await _initiate_and_complete(service, oauth_manager, base_name="svc_guided")
    assert set(first["secrets_created"]) == {"svc_guided", "svc_guided_refresh"}

    # Now Reconnect it with a fresh token exchange.
    second = await _initiate_and_complete(
        service, oauth_manager, refresh_token="refresh2", secret_name_for_reconnect="svc_guided"
    )

    assert set(second["secrets_updated"]) == {"svc_guided", "svc_guided_refresh"}
    assert second["base_name"] == "svc_guided"

    # Metadata (stored as plaintext JSON, unaffected by the keyring stub) reflects the
    # replace-in-place: last_refresh_* reset, expiry recomputed from the fresh exchange.
    updated_meta = await vault.get_secret("svc_guided")
    assert updated_meta["refresh"]["last_refresh_status"] is None
    assert updated_meta["refresh"]["expires_at"] is not None
    assert updated_meta["refresh"]["authorization_endpoint"] == _AUTHZ_ENDPOINT


@pytest.mark.asyncio
async def test_reconnect_can_change_client_secret_reference(tmp_path: Path, keyring_patch):
    """Unlike the MCP path's sibling-ownership guard (which protects a sibling *it
    created*), a standalone secret's client-secret reference is just a pointer to an
    independently-owned existing secret — Reconnect may freely repoint it, and must
    never attempt to create/overwrite either referenced secret's value."""
    service, oauth_manager, vault = _make_service(tmp_path)
    now = datetime.now(UTC)
    for name in ("secret-a", "secret-b"):
        await vault.create_secret(
            SecretRecord(name=name, type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now),
            "value",
        )

    with patch("backend.secrets_keyring.get_secret_value", return_value="value"):
        await _initiate_and_complete(
            service, oauth_manager, base_name="svc_guarded", client_secret_secret_name="secret-a"
        )
        first_meta = await vault.get_secret("svc_guarded")
        assert first_meta["refresh"]["client_secret_secret_name"] == "secret-a"

        result = await _initiate_and_complete(
            service,
            oauth_manager,
            client_secret_secret_name="secret-b",
            secret_name_for_reconnect="svc_guarded",
        )

    assert set(result["secrets_updated"]) == {"svc_guarded", "svc_guarded_refresh"}
    updated_meta = await vault.get_secret("svc_guarded")
    assert updated_meta["refresh"]["client_secret_secret_name"] == "secret-b"
    # Neither referenced secret was touched as a "sibling" — both still exist untouched.
    assert (await vault.get_secret("secret-a"))["type"] == "generic"
    assert (await vault.get_secret("secret-b"))["type"] == "generic"


# ---------------------------------------------------------------------------
# ApplicationService.standalone_oauth_cancel()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_pops_pending_flow_and_creates_nothing(tmp_path: Path, keyring_patch):
    service, oauth_manager, vault = _make_service(tmp_path)
    init_result = await service.standalone_oauth_initiate(
        base_name="svc_cancelled",
        authorization_endpoint=_AUTHZ_ENDPOINT,
        token_endpoint=_TOKEN_ENDPOINT,
        client_id=_CLIENT_ID,
        redirect_uri=_REDIRECT_URI,
    )
    state = init_result["flow_id"]

    cancelled = await service.standalone_oauth_cancel(state)
    assert cancelled is True
    assert state not in oauth_manager._pending
    assert await vault.get_secret("svc_cancelled") is None

    # Cancelling again (already gone) reports False, not an error.
    assert await service.standalone_oauth_cancel(state) is False


# ---------------------------------------------------------------------------
# Router-level regression tests (issue #1871 builder-review findings)
# ---------------------------------------------------------------------------


def test_initiate_rejects_custom_callback_path_colliding_with_app_route(tmp_path: Path):
    """Regression: the standalone initiate endpoints must reject a
    custom_callback_path that shadows a real application route, matching the
    equivalent guard already enforced for MCP config create/update
    (backend/routers/mcp.py's oauth_callback_path_conflicts_with_app_route()).
    Before this fix, no such check existed here, so _add_dynamic_oauth_route()
    would insert a route ahead of the real one for the pending flow's lifetime,
    and later delete it on teardown."""
    from starlette.testclient import TestClient

    from backend.web_server import BackendApp

    server = BackendApp(data_dir=tmp_path)
    with TestClient(server.app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/secrets/oauth/initiate",
            json={
                "base_name": "svc_conflict",
                "authorization_endpoint": _AUTHZ_ENDPOINT,
                "token_endpoint": _TOKEN_ENDPOINT,
                "client_id": _CLIENT_ID,
                "redirect_uri": _REDIRECT_URI,
                "custom_callback_path": "/health",
            },
        )

    assert resp.status_code == 400
    assert "conflicts with an existing application route" in resp.json()["detail"]


def test_initiate_409_message_not_mangled_by_keyerror_repr_quoting(tmp_path: Path, keyring_patch):
    """Regression: KeyError's __str__ applies repr() to a single-arg message (unlike
    LookupError/ValueError), so a message already containing a quote character was
    getting re-wrapped in stray double-quotes instead of being cleanly parsed into
    the 409 response detail — .strip("'") never touched the outer double quotes."""
    import asyncio

    from starlette.testclient import TestClient

    from backend.web_server import BackendApp

    server = BackendApp(data_dir=tmp_path)
    now = datetime.now(UTC)
    with (
        patch("backend.credential_vault.set_secret_value"),
        patch("backend.credential_vault.get_secret_value", return_value="value"),
        patch("backend.credential_vault.delete_secret_value"),
    ):
        asyncio.run(
            server.coordinator.credential_vault.create_secret(
                SecretRecord(
                    name="svc_taken", type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now
                ),
                "value",
            )
        )

        with TestClient(server.app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/secrets/oauth/initiate",
                json={
                    "base_name": "svc_taken",
                    "authorization_endpoint": _AUTHZ_ENDPOINT,
                    "token_endpoint": _TOKEN_ENDPOINT,
                    "client_id": _CLIENT_ID,
                    "redirect_uri": _REDIRECT_URI,
                },
            )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert not detail.startswith('"')
    assert "Secret 'svc_taken' already exists" in detail
