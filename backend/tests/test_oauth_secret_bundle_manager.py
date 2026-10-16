"""Unit tests for OAuthSecretBundleManager (issue #2189 AC5, router-boundary PR B).

OAuthSecretBundleManager is constructed directly with fake/stub collaborators —
no SessionCoordinator involved — per the issue's own T2 test scenario ("unit
tests construct each extracted module without the coordinator").

Detailed behavioral coverage (rollback, sibling-ownership guard, drift
detection, etc.) lives in test_oauth_import_as_secret.py and
test_standalone_oauth_guided_flow.py, which wire this same class with a real
CredentialVault/OAuthFlowManager. This file covers isolated construction and
a few delegation-shaped behaviors with pure stubs.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.oauth_secret_bundle_manager import OAuthSecretBundleManager


@pytest.fixture
def manager():
    return OAuthSecretBundleManager(
        credential_vault=MagicMock(),
        oauth_manager=MagicMock(),
        mcp_config_manager=MagicMock(),
        vault_refresh_manager=MagicMock(),
    )


@pytest.mark.asyncio
async def test_oauth_complete_flow_delegates_to_oauth_manager(manager):
    manager.oauth_manager.complete_flow = AsyncMock(return_value="server-123")

    result = await manager.oauth_complete_flow("state-abc", "code-xyz")

    assert result == "server-123"
    manager.oauth_manager.complete_flow.assert_called_once_with("state-abc", "code-xyz")


@pytest.mark.asyncio
async def test_oauth_initiate_flow_returns_none_when_config_not_found(manager):
    manager.mcp_config_manager.get_config = AsyncMock(return_value=None)

    result = await manager.oauth_initiate_flow("nonexistent", "http://srv", "http://cb", "App")

    assert result is None
    manager.oauth_manager.start_flow.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_disconnect_returns_false_when_config_not_found(manager):
    manager.mcp_config_manager.get_config = AsyncMock(return_value=None)

    result = await manager.oauth_disconnect("nonexistent")

    assert result is False
    manager.oauth_manager.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_get_status_returns_none_when_config_not_found(manager):
    manager.mcp_config_manager.get_config = AsyncMock(return_value=None)

    result = await manager.oauth_get_status("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_oauth_get_status_returns_unauthenticated_when_no_token(manager):
    manager.mcp_config_manager.get_config = AsyncMock(return_value=MagicMock())
    manager.oauth_manager.get_stored_token = AsyncMock(return_value=None)

    result = await manager.oauth_get_status("cfg1")

    assert result == {
        "status": "unauthenticated",
        "expires_at": None,
        "has_refresh_token": False,
    }


@pytest.mark.asyncio
async def test_import_oauth_as_secret_raises_on_invalid_base_name(manager):
    with pytest.raises(ValueError, match="base_name must match"):
        await manager.import_oauth_as_secret("cfg1", "Invalid Name!")


@pytest.mark.asyncio
async def test_import_oauth_as_secret_raises_lookup_error_when_config_missing(manager):
    manager.mcp_config_manager.get_config = AsyncMock(return_value=None)

    with pytest.raises(LookupError, match="404"):
        await manager.import_oauth_as_secret("nonexistent", "base_name")


@pytest.mark.asyncio
async def test_standalone_oauth_cancel_returns_false_when_no_pending_flow(manager):
    manager.oauth_manager.cancel_pending = AsyncMock(return_value=None)

    result = await manager.standalone_oauth_cancel("flow-1")

    assert result is False


@pytest.mark.asyncio
async def test_resolve_sibling_secret_value_raises_when_not_found(manager):
    manager.credential_vault.get_secret = AsyncMock(return_value=None)

    with pytest.raises(LookupError, match="404"):
        await manager._resolve_sibling_secret_value("missing-secret")
