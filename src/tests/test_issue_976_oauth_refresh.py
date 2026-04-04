"""Tests for OAuth token refresh (issue #976).

Covers:
- FernetTokenStore: token endpoint persistence / retrieval
- OAuthFlowManager.refresh_token() happy path (normal + rotated refresh token)
- OAuthFlowManager.refresh_token() with revoked/invalid refresh token → clear + None
- OAuthFlowManager.refresh_token() when no stored token / no endpoint
- OAuthFlowManager.complete_flow() persists token endpoint
- SessionCoordinator._get_mcp_sdk_config() proactive refresh before expiry
- SessionCoordinator._get_mcp_sdk_config() reactive refresh on expired token
- SessionCoordinator._get_mcp_sdk_config() fallback when refresh fails
- OAuthRefreshManager.ensure_refresh / release_refresh ref counting (issue #989)
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.mcp_config_manager import McpServerType
from src.oauth_manager import FernetTokenStore, OAuthFlowManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    access_token: str = "access123",
    refresh_token: str | None = "refresh_tok",
    expires_in: int | None = 3600,
) -> OAuthToken:
    return OAuthToken(
        access_token=access_token,
        token_type="Bearer",
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


def _make_client_info(client_id: str = "cid") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
        token_endpoint_auth_method="none",
    )


def _make_token_http_response(body: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code

    async def _aread():
        return body

    resp.aread = _aread
    return resp


_TOKEN_RESPONSE_JSON = json.dumps(
    {"access_token": "new_access", "token_type": "Bearer", "expires_in": 3600}
).encode()

_TOKEN_RESPONSE_WITH_REFRESH_JSON = json.dumps(
    {
        "access_token": "new_access",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "new_refresh",
    }
).encode()


# ---------------------------------------------------------------------------
# Step 2: FernetTokenStore — token endpoint persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fernet_store_set_and_get_token_endpoint(tmp_path: Path):
    """set_token_endpoint / get_token_endpoint round-trip."""
    store = FernetTokenStore(tmp_path, "server1")
    assert store.get_token_endpoint() is None  # Nothing stored yet

    store.set_token_endpoint("https://auth.example.com/token")
    assert store.get_token_endpoint() == "https://auth.example.com/token"


@pytest.mark.asyncio
async def test_fernet_store_clear_removes_endpoint(tmp_path: Path):
    """clear() also removes the stored token endpoint."""
    store = FernetTokenStore(tmp_path, "server1")
    store.set_token_endpoint("https://auth.example.com/token")
    await store.set_tokens(_make_token())

    await store.clear()

    assert store.get_token_endpoint() is None
    assert await store.get_tokens() is None


def test_fernet_store_get_endpoint_empty_file_returns_none(tmp_path: Path):
    """get_token_endpoint() returns None when file is empty."""
    store = FernetTokenStore(tmp_path, "server1")
    endpoint_file = tmp_path / "oauth_endpoints" / "server1.endpoint"
    endpoint_file.parent.mkdir(parents=True, exist_ok=True)
    endpoint_file.write_text("")

    assert store.get_token_endpoint() is None


# ---------------------------------------------------------------------------
# Step 2: complete_flow() persists token endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_flow_persists_token_endpoint(tmp_path: Path):
    """complete_flow() persists token endpoint for later refresh calls."""
    manager = OAuthFlowManager(tmp_path)
    state = "test_state_123"
    token_endpoint = "https://auth.example.com/token"
    manager._pending[state] = {
        "server_id": "srv1",
        "token_endpoint": token_endpoint,
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

        await manager.complete_flow(state, code="auth_code")

    store = manager.get_token_store("srv1")
    assert store.get_token_endpoint() == token_endpoint


# ---------------------------------------------------------------------------
# Step 1: OAuthFlowManager.refresh_token()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_success(tmp_path: Path):
    """refresh_token() with valid refresh token returns new OAuthToken and stores it."""
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(_make_token("old_access", "old_refresh"))
    await store.set_client_info(_make_client_info("cid123"))
    store.set_token_endpoint("https://auth.example.com/token")

    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client:
        resp = _make_token_http_response(_TOKEN_RESPONSE_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=resp)
        mock_client.return_value = client_instance

        new_token = await manager.refresh_token("srv1")

    assert new_token is not None
    assert new_token.access_token == "new_access"
    # Original refresh_token preserved (response had none)
    assert new_token.refresh_token == "old_refresh"

    # Verify stored token updated
    stored = await store.get_tokens()
    assert stored is not None
    assert stored.access_token == "new_access"


@pytest.mark.asyncio
async def test_refresh_token_stores_rotated_refresh_token(tmp_path: Path):
    """refresh_token() stores new refresh_token when provider rotates it."""
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(_make_token("old_access", "old_refresh"))
    await store.set_client_info(_make_client_info("cid"))
    store.set_token_endpoint("https://auth.example.com/token")

    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client:
        resp = _make_token_http_response(_TOKEN_RESPONSE_WITH_REFRESH_JSON)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=resp)
        mock_client.return_value = client_instance

        new_token = await manager.refresh_token("srv1")

    assert new_token is not None
    assert new_token.refresh_token == "new_refresh"
    stored = await store.get_tokens()
    assert stored is not None
    assert stored.refresh_token == "new_refresh"


@pytest.mark.asyncio
async def test_refresh_token_revoked_clears_tokens(tmp_path: Path):
    """refresh_token() clears stored tokens and returns None when server rejects refresh."""
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(_make_token("old_access", "revoked_refresh"))
    await store.set_client_info(_make_client_info("cid"))
    store.set_token_endpoint("https://auth.example.com/token")

    error_body = json.dumps({"error": "invalid_grant"}).encode()

    with patch("src.oauth_manager.httpx.AsyncClient") as mock_client:
        resp = _make_token_http_response(error_body, status_code=400)
        client_instance = AsyncMock()
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        client_instance.post = AsyncMock(return_value=resp)
        mock_client.return_value = client_instance

        result = await manager.refresh_token("srv1")

    assert result is None
    # Tokens cleared so user must re-authenticate
    assert await store.get_tokens() is None


@pytest.mark.asyncio
async def test_refresh_token_no_stored_token_returns_none(tmp_path: Path):
    """refresh_token() returns None immediately when no token is stored."""
    manager = OAuthFlowManager(tmp_path)
    result = await manager.refresh_token("srv_no_token")
    assert result is None


@pytest.mark.asyncio
async def test_refresh_token_no_refresh_field_returns_none(tmp_path: Path):
    """refresh_token() returns None when stored OAuthToken has no refresh_token."""
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    # Token without refresh_token
    await store.set_tokens(_make_token("access", refresh_token=None))
    await store.set_client_info(_make_client_info())

    result = await manager.refresh_token("srv1")
    assert result is None


@pytest.mark.asyncio
async def test_refresh_token_no_endpoint_returns_none(tmp_path: Path):
    """refresh_token() returns None when token endpoint was never persisted."""
    manager = OAuthFlowManager(tmp_path)
    store = manager.get_token_store("srv1")
    await store.set_tokens(_make_token("access", "refresh"))
    await store.set_client_info(_make_client_info())
    # Intentionally do NOT call store.set_token_endpoint()

    result = await manager.refresh_token("srv1")
    assert result is None


# ---------------------------------------------------------------------------
# Step 3: SessionCoordinator._get_mcp_sdk_config() proactive & reactive refresh
# ---------------------------------------------------------------------------


def _make_mcp_cfg(server_id: str = "srv-oauth") -> MagicMock:
    mcp_cfg = MagicMock()
    mcp_cfg.id = server_id
    mcp_cfg.type = McpServerType.HTTP
    mcp_cfg.oauth_enabled = True
    mcp_cfg.to_sdk_config.return_value = {
        "type": "http",
        "url": "https://mcp.example.com/v1/mcp",
        "headers": {},
    }
    return mcp_cfg


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_proactive_refresh_before_expiry(tmp_path: Path):
    """_get_mcp_sdk_config() refreshes token when it expires within the buffer window."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    store = coordinator.oauth_manager.get_token_store("srv-oauth")
    await store.set_tokens(_make_token("old_token", "refresh_tok"))
    # Set expiry 200 seconds from now — inside the 300-second buffer
    future_expiry = time.time() + 200
    (tmp_path / "oauth_expiry" / "srv-oauth.expiry").write_text(str(future_expiry))
    store.set_token_endpoint("https://auth.example.com/token")
    await store.set_client_info(_make_client_info("cid"))

    new_token = _make_token("fresh_token", "refresh_tok")

    async def _mock_refresh(server_id):
        return new_token

    coordinator.oauth_manager.refresh_token = _mock_refresh

    sdk_config = await coordinator._get_mcp_sdk_config(_make_mcp_cfg())
    assert sdk_config["headers"]["Authorization"] == "Bearer fresh_token"


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_reactive_refresh_on_expired(tmp_path: Path):
    """_get_mcp_sdk_config() attempts refresh when token is already expired."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    store = coordinator.oauth_manager.get_token_store("srv-oauth")
    await store.set_tokens(_make_token("expired_token", "refresh_tok"))
    # Set expiry in the past
    past_expiry = time.time() - 100
    (tmp_path / "oauth_expiry" / "srv-oauth.expiry").write_text(str(past_expiry))
    store.set_token_endpoint("https://auth.example.com/token")
    await store.set_client_info(_make_client_info("cid"))

    new_token = _make_token("refreshed_token", "refresh_tok")

    async def _mock_refresh(server_id):
        return new_token

    coordinator.oauth_manager.refresh_token = _mock_refresh

    sdk_config = await coordinator._get_mcp_sdk_config(_make_mcp_cfg())
    assert sdk_config["headers"]["Authorization"] == "Bearer refreshed_token"


@pytest.mark.asyncio
async def test_get_mcp_sdk_config_fallback_when_refresh_fails(tmp_path: Path):
    """_get_mcp_sdk_config() falls back to the original token when refresh returns None."""
    from src.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    store = coordinator.oauth_manager.get_token_store("srv-oauth")
    await store.set_tokens(_make_token("stale_token", "bad_refresh"))
    # Expired
    past_expiry = time.time() - 60
    (tmp_path / "oauth_expiry" / "srv-oauth.expiry").write_text(str(past_expiry))

    async def _mock_refresh_fail(server_id):
        return None

    coordinator.oauth_manager.refresh_token = _mock_refresh_fail

    sdk_config = await coordinator._get_mcp_sdk_config(_make_mcp_cfg())
    # Falls back to original stale token rather than raising
    assert sdk_config["headers"]["Authorization"] == "Bearer stale_token"


# ---------------------------------------------------------------------------
# Step 4: OAuthRefreshManager.ensure_refresh / release_refresh ref counting
# (issue #989: extracted from SessionCoordinator)
# ---------------------------------------------------------------------------


def _make_manager(tmp_path):
    """Build an OAuthRefreshManager backed by a real OAuthFlowManager."""
    from src.oauth_manager import OAuthFlowManager
    from src.oauth_refresh_manager import OAuthRefreshManager

    oauth_mgr = OAuthFlowManager(tmp_path)
    return OAuthRefreshManager(oauth_mgr)


def test_ensure_oauth_refresh_creates_task_once(tmp_path):
    """ensure_refresh() creates a single task and increments ref count."""
    import asyncio

    manager = _make_manager(tmp_path)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_ensure_release(manager))
    finally:
        loop.close()


async def _run_ensure_release(manager):
    manager.ensure_refresh("srv1")
    manager.ensure_refresh("srv1")  # second session, same server

    assert manager._refresh_refcounts["srv1"] == 2
    assert "srv1" in manager._refresh_tasks
    task_id = id(manager._refresh_tasks["srv1"])

    # Second ensure does NOT replace the existing task
    manager.ensure_refresh("srv1")
    assert manager._refresh_refcounts["srv1"] == 3
    assert id(manager._refresh_tasks["srv1"]) == task_id

    # Releasing twice — task should still be alive
    manager.release_refresh("srv1")
    manager.release_refresh("srv1")
    assert manager._refresh_refcounts.get("srv1") == 1
    assert "srv1" in manager._refresh_tasks

    # Final release — task cancelled and removed
    manager.release_refresh("srv1")
    assert "srv1" not in manager._refresh_refcounts
    assert "srv1" not in manager._refresh_tasks


def test_release_below_zero_is_safe(tmp_path):
    """release_refresh() does not error when called with no matching task."""
    import asyncio

    manager = _make_manager(tmp_path)

    async def _run():
        manager.release_refresh("nonexistent")

    asyncio.run(_run())
