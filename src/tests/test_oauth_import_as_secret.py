"""Unit tests for ApplicationService.import_oauth_as_secret (issue #1381).

Tests the import of stored OAuth 2.1 tokens as proxy-injectable vault secrets.
Uses mocked FernetTokenStore + in-memory vault (via tmp_path + mocked keyring).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.application_service import ApplicationService
from src.credential_vault import SecretsVault
from src.mcp_config_manager import McpServerConfig, McpServerType
from src.oauth_manager import FernetTokenStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_ID = "cfg-abc123"
_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
_TOKEN_URL = "https://auth.atlassian.com/oauth/token"


def _make_token(
    access_token: str = "access_tok",
    refresh_token: str | None = "refresh_tok",
    expires_in: int | None = 3600,
) -> OAuthToken:
    return OAuthToken(
        access_token=access_token,
        token_type="Bearer",
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


def _make_client_info(
    client_id: str = "my_client_id",
    client_secret: str | None = "my_client_secret",
) -> OAuthClientInformationFull:
    info = OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
        token_endpoint_auth_method="none",
    )
    # OAuthClientInformationFull may not expose client_secret via constructor;
    # use model_copy or attribute assignment to attach it for tests.
    object.__setattr__(info, "client_secret", client_secret)
    return info


def _make_mcp_config(
    config_id: str = _CONFIG_ID,
    url: str | None = _MCP_URL,
    headers: dict | None = None,
    oauth_client_id: str | None = None,
) -> McpServerConfig:
    cfg = McpServerConfig.__new__(McpServerConfig)
    cfg.id = config_id
    cfg.name = "Atlassian"
    cfg.slug = "atlassian"
    cfg.type = McpServerType.HTTP if url else McpServerType.STDIO
    cfg.url = url
    cfg.command = None
    cfg.args = []
    cfg.env = {}
    cfg.headers = headers or {}
    cfg.enabled = True
    cfg.oauth_enabled = True
    cfg.oauth_client_id = oauth_client_id
    cfg.oauth_callback_port = None
    cfg.oauth_scope = None
    return cfg


def _make_service(tmp_path: Path, mock_config: McpServerConfig | None, store: FernetTokenStore):
    """Build an ApplicationService with mocked coordinator internals."""
    coordinator = MagicMock()

    # mcp_config_manager
    async def _get_config(config_id):
        return mock_config

    async def _update_config(config_id, **kwargs):
        if mock_config:
            if "headers" in kwargs:
                mock_config.headers = kwargs["headers"]
        return mock_config

    coordinator.mcp_config_manager.get_config = _get_config
    coordinator.mcp_config_manager.update_config = _update_config

    # oauth_manager
    coordinator.oauth_manager.get_token_store.return_value = store

    # credential_vault — use real SecretsVault with mocked keyring
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        vault = SecretsVault(tmp_path)

    coordinator.credential_vault = vault
    return ApplicationService(coordinator)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path: Path) -> FernetTokenStore:
    return FernetTokenStore(tmp_path, _CONFIG_ID)


@pytest.fixture
def keyring_patch():
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        yield


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_creates_all_three_secrets_when_dcr(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Full DCR case: refresh_token + client_secret → 3 secrets created."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    expiry = time.time() + 3600
    expiry_file = tmp_path / "oauth_expiry" / f"{_CONFIG_ID}.expiry"
    expiry_file.write_text(str(expiry))
    tmp_store.set_token_endpoint(_TOKEN_URL)
    client_info = _make_client_info()
    await tmp_store.set_client_info(client_info)

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        result = await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    assert set(result["secrets_created"]) == {"jira_oauth", "jira_oauth_refresh", "jira_oauth_client_secret"}
    assert result["auto_refresh_enabled"] is True
    assert result["header_injected"] == "Authorization: ${secret:jira_oauth}"
    assert result["expires_at"] is not None


@pytest.mark.asyncio
async def test_issue_1381_import_creates_two_secrets_when_no_client_secret(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Pre-registered app without client_secret → 2 secrets (primary + refresh)."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)
    # client_info without client_secret
    info = _make_client_info(client_secret=None)
    await tmp_store.set_client_info(info)

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        result = await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    assert set(result["secrets_created"]) == {"jira_oauth", "jira_oauth_refresh"}
    assert "jira_oauth_client_secret" not in result["secrets_created"]
    assert result["auto_refresh_enabled"] is True


@pytest.mark.asyncio
async def test_issue_1381_import_skips_refresh_when_no_refresh_token(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Token without refresh_token → 1 secret only, auto_refresh_enabled=false."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        result = await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    assert result["secrets_created"] == ["jira_oauth"]
    assert result["auto_refresh_enabled"] is False


# ---------------------------------------------------------------------------
# Error paths — 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_400_on_invalid_base_name(tmp_path: Path, tmp_store: FernetTokenStore):
    """base_name = 'Invalid-Name!' should raise ValueError (→ 400)."""
    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with pytest.raises(ValueError, match="base_name must match"):
        await service.import_oauth_as_secret(_CONFIG_ID, "Invalid-Name!")


@pytest.mark.asyncio
async def test_issue_1381_import_400_when_stdio_config(tmp_path: Path, tmp_store: FernetTokenStore):
    """STDIO MCP config (no url) should raise ValueError (→ 400)."""
    stdio_cfg = _make_mcp_config(url=None)
    service = _make_service(tmp_path, stdio_cfg, tmp_store)
    with pytest.raises(ValueError, match="STDIO"):
        await service.import_oauth_as_secret(_CONFIG_ID, "valid_name")


# ---------------------------------------------------------------------------
# Error paths — 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_404_when_config_missing(tmp_path: Path, tmp_store: FernetTokenStore):
    """No MCP config found → LookupError (→ 404)."""
    service = _make_service(tmp_path, None, tmp_store)
    with pytest.raises(LookupError, match="404"):
        await service.import_oauth_as_secret(_CONFIG_ID, "valid_name")


@pytest.mark.asyncio
async def test_issue_1381_import_404_when_no_stored_tokens(tmp_path: Path, tmp_store: FernetTokenStore):
    """No stored OAuth tokens → LookupError (→ 404)."""
    # tmp_store has no tokens set
    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with pytest.raises(LookupError, match="404"):
        await service.import_oauth_as_secret(_CONFIG_ID, "valid_name")


# ---------------------------------------------------------------------------
# Error paths — 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_409_on_primary_name_collision(
    tmp_path: Path, tmp_store: FernetTokenStore
):
    """Pre-existing secret with same base_name → KeyError (→ 409), no partial writes."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    # Pre-create the primary secret metadata file
    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    import json
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    (creds_dir / "jira_oauth.json").write_text(json.dumps({
        "name": "jira_oauth", "type": "generic", "target_hosts": ["example.com"],
        "created_at": now, "updated_at": now,
    }))

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        with pytest.raises(KeyError, match="409"):
            await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    # Primary should not have been created (no new files)
    assert (creds_dir / "jira_oauth.json").exists()
    assert not (creds_dir / "jira_oauth_refresh.json").exists()


@pytest.mark.asyncio
async def test_issue_1381_import_409_on_sibling_collision(
    tmp_path: Path, tmp_store: FernetTokenStore
):
    """Pre-existing _refresh secret → KeyError (→ 409), primary not created."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)
    import json
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    (creds_dir / "jira_oauth_refresh.json").write_text(json.dumps({
        "name": "jira_oauth_refresh", "type": "generic", "target_hosts": ["example.com"],
        "created_at": now, "updated_at": now,
    }))

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        with pytest.raises(KeyError, match="409"):
            await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    assert not (creds_dir / "jira_oauth.json").exists()


# ---------------------------------------------------------------------------
# Rollback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_rolls_back_on_primary_failure(
    tmp_path: Path, tmp_store: FernetTokenStore
):
    """If primary create fails, sibling secrets are deleted (rollback)."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)
    await tmp_store.set_client_info(_make_client_info())

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)

    create_call_count = 0

    original_create = service.coordinator.credential_vault.create_secret

    async def _failing_create(record, value):
        nonlocal create_call_count
        create_call_count += 1
        if record.name == "jira_oauth":
            raise RuntimeError("forced primary failure")
        return await original_create(record, value)

    service.coordinator.credential_vault.create_secret = _failing_create

    original_delete = service.coordinator.credential_vault.delete_secret
    deleted_names = []

    async def _tracking_delete(name):
        deleted_names.append(name)
        return await original_delete(name)

    service.coordinator.credential_vault.delete_secret = _tracking_delete

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        with pytest.raises(RuntimeError, match="forced primary failure"):
            await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    # Siblings that were created before failure should have been rolled back
    assert "jira_oauth_refresh" in deleted_names or "jira_oauth_client_secret" in deleted_names


@pytest.mark.asyncio
async def test_issue_1381_import_rolls_back_on_headers_update_failure(
    tmp_path: Path, tmp_store: FernetTokenStore
):
    """If headers update fails, all created secrets are deleted."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    cfg = _make_mcp_config()
    service = _make_service(tmp_path, cfg, tmp_store)

    async def _failing_update(config_id, **kwargs):
        raise RuntimeError("forced headers update failure")

    service.coordinator.mcp_config_manager.update_config = _failing_update

    deleted_names = []
    original_delete = service.coordinator.credential_vault.delete_secret

    async def _tracking_delete(name):
        deleted_names.append(name)
        return await original_delete(name)

    service.coordinator.credential_vault.delete_secret = _tracking_delete

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        with pytest.raises(RuntimeError, match="forced headers update failure"):
            await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    # All created secrets must be deleted
    assert "jira_oauth" in deleted_names
    assert "jira_oauth_refresh" in deleted_names


# ---------------------------------------------------------------------------
# Field verification tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1381_import_sets_correct_refresh_spec_fields(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """RefreshSpec has correct token_url, client_id, names, expires_at, buffer_seconds=300."""
    token = _make_token()
    await tmp_store.set_tokens(token)
    expiry = time.time() + 3600
    (tmp_path / "oauth_expiry").mkdir(parents=True, exist_ok=True)
    (tmp_path / "oauth_expiry" / f"{_CONFIG_ID}.expiry").write_text(str(expiry))
    tmp_store.set_token_endpoint(_TOKEN_URL)
    await tmp_store.set_client_info(_make_client_info(client_id="the_client_id"))

    captured_records = []
    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    original_create = service.coordinator.credential_vault.create_secret

    async def _capture_create(record, value):
        captured_records.append(record)
        return await original_create(record, value)

    service.coordinator.credential_vault.create_secret = _capture_create

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    primary = next(r for r in captured_records if r.name == "jira_oauth")
    assert primary.refresh is not None
    assert primary.refresh.token_url == _TOKEN_URL
    assert primary.refresh.client_id == "the_client_id"
    assert primary.refresh.refresh_token_secret_name == "jira_oauth_refresh"
    assert primary.refresh.buffer_seconds == 300
    assert primary.refresh.expires_at is not None


@pytest.mark.asyncio
async def test_issue_1381_import_sets_correct_scrub_spec_url_path(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """scrub.url_path is the path component of the token_url."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint("https://auth.example.com/oauth/token")

    captured_records = []
    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    original_create = service.coordinator.credential_vault.create_secret

    async def _capture_create(record, value):
        captured_records.append(record)
        return await original_create(record, value)

    service.coordinator.credential_vault.create_secret = _capture_create

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    primary = next(r for r in captured_records if r.name == "jira_oauth")
    assert primary.scrub is not None
    assert primary.scrub.url_path == "/oauth/token"


@pytest.mark.asyncio
async def test_issue_1381_import_overwrites_existing_authorization_header(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Existing Authorization header is silently replaced; other headers preserved."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    existing_headers = {"Authorization": "Bearer manual", "X-Foo": "bar"}
    cfg = _make_mcp_config(headers=dict(existing_headers))

    updated_headers = {}

    async def _capture_update(config_id, **kwargs):
        if "headers" in kwargs:
            updated_headers.update(kwargs["headers"])
        return cfg

    service = _make_service(tmp_path, cfg, tmp_store)
    service.coordinator.mcp_config_manager.update_config = _capture_update

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    assert updated_headers["Authorization"] == "${secret:jira_oauth}"
    assert updated_headers["X-Foo"] == "bar"


@pytest.mark.asyncio
async def test_issue_1381_import_target_hosts_uses_mcp_url_host(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """target_hosts on created secrets equals the MCP server URL host."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    cfg = _make_mcp_config(url="https://mcp.atlassian.com/v1/mcp")
    captured_records = []
    service = _make_service(tmp_path, cfg, tmp_store)
    original_create = service.coordinator.credential_vault.create_secret

    async def _capture_create(record, value):
        captured_records.append(record)
        return await original_create(record, value)

    service.coordinator.credential_vault.create_secret = _capture_create

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    primary = next(r for r in captured_records if r.name == "jira_oauth")
    assert primary.target_hosts == ["mcp.atlassian.com"]


@pytest.mark.asyncio
async def test_issue_1381_import_preserves_oauth_refresh_manager_state(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Import does not call oauth_manager methods that would disturb refresh state."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)
    service.coordinator.oauth_manager.disconnect = AsyncMock()
    service.coordinator.oauth_manager.refresh_token = AsyncMock()

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        await service.import_oauth_as_secret(_CONFIG_ID, "jira_oauth")

    service.coordinator.oauth_manager.disconnect.assert_not_called()
    service.coordinator.oauth_manager.refresh_token.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1381_import_idempotent_after_disconnect_and_reauth(
    tmp_path: Path, tmp_store: FernetTokenStore, keyring_patch
):
    """Two separate imports with different base_names coexist in vault."""
    token = _make_token(refresh_token=None)
    await tmp_store.set_tokens(token)
    tmp_store.set_token_endpoint(_TOKEN_URL)

    service = _make_service(tmp_path, _make_mcp_config(), tmp_store)

    with (
        patch("src.credential_vault.set_secret_value"),
        patch("src.credential_vault.get_secret_value", return_value="value"),
        patch("src.credential_vault.delete_secret_value"),
    ):
        r1 = await service.import_oauth_as_secret(_CONFIG_ID, "first_oauth")
        r2 = await service.import_oauth_as_secret(_CONFIG_ID, "second_oauth")

    assert "first_oauth" in r1["secrets_created"]
    assert "second_oauth" in r2["secrets_created"]
    # Both should exist as JSON files
    creds_dir = tmp_path / "credentials"
    assert (creds_dir / "first_oauth.json").exists()
    assert (creds_dir / "second_oauth.json").exists()
