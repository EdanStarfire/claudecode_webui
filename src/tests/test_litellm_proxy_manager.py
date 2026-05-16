"""Unit tests for LiteLLMProxyManager — lifecycle, key registry, auth callback."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_manager():
    """Build a LiteLLMProxyManager with LiteLLM imports fully mocked."""
    mock_app = MagicMock()
    mock_proxy_server = MagicMock(app=mock_app)
    mock_types = MagicMock()

    modules = {
        "litellm": MagicMock(),
        "litellm.proxy": MagicMock(),
        "litellm.proxy.proxy_server": mock_proxy_server,
        "litellm.proxy._types": mock_types,
    }
    with patch.dict("sys.modules", modules):
        from src.litellm_proxy_manager import LiteLLMProxyManager

        catalog = AsyncMock()
        catalog.list_entries.return_value = []
        catalog.resolve_params.return_value = {}

        vault = AsyncMock()
        vault.get_secret.return_value = "test-secret-value"

        mgr = LiteLLMProxyManager.__new__(LiteLLMProxyManager)
        mgr._catalog_manager = catalog
        mgr._vault = vault
        mgr._port = 4000
        mgr._key_registry = {}
        mgr._server_task = None
        mgr._rebuild_lock = asyncio.Lock()
        mgr._running = False
        return mgr, mock_types


@pytest.fixture
def manager():
    mgr, _ = _make_manager()
    return mgr


@pytest.fixture
def manager_with_types():
    return _make_manager()


# ── Key Registry Tests ─────────────────────────────────────────────────────


def test_register_session_key_returns_lc_prefixed_key(manager):
    key = manager.register_session_key("sess-1")
    assert key.startswith("lc-")


def test_register_session_key_unique_per_call(manager):
    key1 = manager.register_session_key("sess-1")
    key2 = manager.register_session_key("sess-1")
    assert key1 != key2


def test_lookup_session_for_registered_key(manager):
    key = manager.register_session_key("sess-abc")
    assert manager.lookup_session_for_key(key) == "sess-abc"


def test_lookup_session_for_unknown_key_returns_none(manager):
    assert manager.lookup_session_for_key("nonexistent-key") is None


def test_unregister_session_key_removes_all_keys_for_session(manager):
    key1 = manager.register_session_key("sess-x")
    key2 = manager.register_session_key("sess-x")
    manager.unregister_session_key("sess-x")
    assert manager.lookup_session_for_key(key1) is None
    assert manager.lookup_session_for_key(key2) is None


def test_unregister_nonexistent_session_is_noop(manager):
    manager.unregister_session_key("never-registered")
    assert manager._key_registry == {}


# ── Auth Callback Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_callback_valid_key_returns_user_api_key_auth(manager_with_types):
    mgr, mock_types = manager_with_types

    # Configure mock UserAPIKeyAuth to return a simple object
    mock_auth_obj = MagicMock()
    mock_auth_obj.user_id = "sess-valid"
    mock_types.UserAPIKeyAuth.return_value = mock_auth_obj

    key = mgr.register_session_key("sess-valid")

    modules = {
        "litellm": MagicMock(),
        "litellm.proxy": MagicMock(),
        "litellm.proxy.proxy_server": MagicMock(),
        "litellm.proxy._types": mock_types,
    }
    with patch.dict("sys.modules", modules):
        result = await mgr.auth_callback(request=MagicMock(), api_key=key)

    mock_types.UserAPIKeyAuth.assert_called_once_with(api_key=key, user_id="sess-valid")
    assert result is mock_auth_obj


@pytest.mark.asyncio
async def test_auth_callback_invalid_key_raises_401(manager):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await manager.auth_callback(request=MagicMock(), api_key="bad-key")

    assert exc_info.value.status_code == 401


# ── Lifecycle State Tests ──────────────────────────────────────────────────


def test_is_running_false_before_start(manager):
    assert manager.is_running is False
