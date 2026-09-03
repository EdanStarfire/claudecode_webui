"""Unit tests for McpServerConfig dataclass (issue #1109).

Covers to_sdk_config() oauth pass-through and from_dict() backward compat
for the oauth_client_id / oauth_callback_port fields added in #1109.
"""

import pytest

from backend.mcp_config_manager import McpConfigManager, McpServerConfig, McpServerType


def _http_config(**kwargs) -> McpServerConfig:
    """Minimal HTTP McpServerConfig for testing."""
    return McpServerConfig(
        id="test-id",
        name="test-server",
        slug="test-server",
        type=McpServerType.HTTP,
        url="https://example.com/mcp",
        **kwargs,
    )


def _sse_config(**kwargs) -> McpServerConfig:
    """Minimal SSE McpServerConfig for testing."""
    return McpServerConfig(
        id="test-id",
        name="test-server",
        slug="test-server",
        type=McpServerType.SSE,
        url="https://example.com/sse",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# to_sdk_config — HTTP type
# ---------------------------------------------------------------------------


def test_issue_1109_http_no_oauth_fields_no_oauth_key():
    """Neither field set → no 'oauth' key emitted."""
    cfg = _http_config()
    result = cfg.to_sdk_config()
    assert "oauth" not in result
    assert result["url"] == "https://example.com/mcp"
    assert result["type"] == "http"


def test_issue_1109_http_only_client_id():
    """Only oauth_client_id set → oauth has clientId only, no callbackPort."""
    cfg = _http_config(oauth_client_id="my-client-id")
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"clientId": "my-client-id"}
    assert "callbackPort" not in result["oauth"]


def test_issue_1109_http_only_callback_port():
    """Only oauth_callback_port set → oauth has callbackPort only, no clientId."""
    cfg = _http_config(oauth_callback_port=3118)
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"callbackPort": 3118}
    assert "clientId" not in result["oauth"]


def test_issue_1109_http_both_oauth_fields():
    """Both fields set → oauth has clientId and callbackPort."""
    cfg = _http_config(oauth_client_id="abc123", oauth_callback_port=3118)
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"clientId": "abc123", "callbackPort": 3118}


def test_issue_1109_http_url_and_headers_preserved_with_oauth():
    """Existing url and headers are preserved when oauth fields are set."""
    cfg = _http_config(
        headers={"Authorization": "Bearer token"},
        oauth_client_id="abc123",
        oauth_callback_port=3118,
    )
    result = cfg.to_sdk_config()
    assert result["url"] == "https://example.com/mcp"
    assert result["headers"] == {"Authorization": "Bearer token"}
    assert result["oauth"]["clientId"] == "abc123"
    assert result["oauth"]["callbackPort"] == 3118


# ---------------------------------------------------------------------------
# to_sdk_config — SSE type (same code path)
# ---------------------------------------------------------------------------


def test_issue_1109_sse_no_oauth_fields_no_oauth_key():
    cfg = _sse_config()
    result = cfg.to_sdk_config()
    assert "oauth" not in result
    assert result["type"] == "sse"


def test_issue_1109_sse_only_client_id():
    cfg = _sse_config(oauth_client_id="slack-client-id")
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"clientId": "slack-client-id"}


def test_issue_1109_sse_only_callback_port():
    cfg = _sse_config(oauth_callback_port=9000)
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"callbackPort": 9000}


def test_issue_1109_sse_both_oauth_fields():
    cfg = _sse_config(oauth_client_id="slack-client-id", oauth_callback_port=9000)
    result = cfg.to_sdk_config()
    assert result["oauth"] == {"clientId": "slack-client-id", "callbackPort": 9000}


def test_issue_1109_sse_headers_preserved_with_oauth():
    cfg = _sse_config(
        headers={"X-Api-Key": "secret"},
        oauth_client_id="slack-client-id",
        oauth_callback_port=9000,
    )
    result = cfg.to_sdk_config()
    assert result["headers"] == {"X-Api-Key": "secret"}
    assert result["oauth"] == {"clientId": "slack-client-id", "callbackPort": 9000}


# ---------------------------------------------------------------------------
# from_dict — backward compat
# ---------------------------------------------------------------------------


def test_issue_1109_from_dict_backward_compat_no_oauth_fields():
    """Loading a dict without the new fields defaults both to None."""
    data = {
        "id": "abc",
        "name": "legacy-server",
        "slug": "legacy-server",
        "type": "http",
        "url": "https://example.com",
    }
    cfg = McpServerConfig.from_dict(data)
    assert cfg.oauth_client_id is None
    assert cfg.oauth_callback_port is None


def test_issue_1109_from_dict_with_oauth_fields_preserved():
    """Loading a dict with both oauth fields preserves their values."""
    data = {
        "id": "abc",
        "name": "test-server",
        "slug": "test-server",
        "type": "http",
        "url": "https://example.com",
        "oauth_client_id": "my-client",
        "oauth_callback_port": 3118,
    }
    cfg = McpServerConfig.from_dict(data)
    assert cfg.oauth_client_id == "my-client"
    assert cfg.oauth_callback_port == 3118


def test_issue_1109_from_dict_round_trip():
    """to_dict() → from_dict() preserves oauth fields."""
    original = _http_config(oauth_client_id="round-trip-id", oauth_callback_port=4000)
    restored = McpServerConfig.from_dict(original.to_dict())
    assert restored.oauth_client_id == "round-trip-id"
    assert restored.oauth_callback_port == 4000


# ---------------------------------------------------------------------------
# shared_connection — Issue #1484
# ---------------------------------------------------------------------------


def test_shared_connection_defaults_to_false():
    """shared_connection field defaults to False for new configs."""
    cfg = _http_config()
    assert cfg.shared_connection is False


def test_shared_connection_round_trips_to_dict_and_from_dict():
    """shared_connection True/False survives to_dict() → from_dict()."""
    for value in (True, False):
        cfg = _http_config(shared_connection=value)
        restored = McpServerConfig.from_dict(cfg.to_dict())
        assert restored.shared_connection is value


def test_shared_connection_backward_compat_missing_key():
    """Loading a dict without shared_connection key defaults to False."""
    data = {
        "id": "abc",
        "name": "legacy-server",
        "slug": "legacy-server",
        "type": "http",
        "url": "https://example.com",
    }
    cfg = McpServerConfig.from_dict(data)
    assert cfg.shared_connection is False


def test_shared_connection_persists_to_disk(tmp_path):
    """Write config JSON to disk and reload; shared_connection is preserved."""
    import json

    cfg = _http_config(shared_connection=True)
    config_file = tmp_path / f"{cfg.slug}.json"
    with open(config_file, "w") as f:
        json.dump(cfg.to_dict(), f)

    with open(config_file) as f:
        data = json.load(f)
    restored = McpServerConfig.from_dict(data)
    assert restored.shared_connection is True


# ---------------------------------------------------------------------------
# Issue #1789: oauth_custom_callback_path / oauth_custom_callback_port
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path):
    return McpConfigManager(tmp_path)


async def _http_manager_config(manager, name="server", **kwargs):
    return await manager.create_config(
        name=name,
        server_type=McpServerType.HTTP,
        url="https://example.com/mcp",
        shared_connection=True,
        **kwargs,
    )


async def test_custom_callback_accepted_with_shared_connection(manager):
    cfg = await _http_manager_config(
        manager,
        oauth_custom_callback_path="/callback",
        oauth_custom_callback_port=8765,
    )
    assert cfg.oauth_custom_callback_path == "/callback"
    assert cfg.oauth_custom_callback_port == 8765


async def test_custom_callback_path_only_accepted(manager):
    cfg = await _http_manager_config(manager, oauth_custom_callback_path="/callback")
    assert cfg.oauth_custom_callback_path == "/callback"
    assert cfg.oauth_custom_callback_port is None


async def test_custom_callback_port_only_accepted(manager):
    cfg = await _http_manager_config(manager, oauth_custom_callback_port=8765)
    assert cfg.oauth_custom_callback_port == 8765
    assert cfg.oauth_custom_callback_path is None


async def test_custom_callback_rejected_without_shared_connection(manager):
    with pytest.raises(ValueError, match="shared_connection"):
        await manager.create_config(
            name="server",
            server_type=McpServerType.HTTP,
            url="https://example.com/mcp",
            shared_connection=False,
            oauth_custom_callback_path="/callback",
        )


async def test_custom_callback_path_cannot_equal_default(manager):
    with pytest.raises(ValueError, match="default"):
        await _http_manager_config(manager, oauth_custom_callback_path="/oauth/callback")


async def test_custom_callback_port_conflicts_with_main_app_port(manager):
    with pytest.raises(ValueError, match="main application"):
        await _http_manager_config(
            manager, oauth_custom_callback_port=8001, main_app_port=8001
        )


async def test_custom_callback_port_conflicts_with_litellm_port(manager):
    with pytest.raises(ValueError, match="LiteLLM"):
        await _http_manager_config(
            manager, oauth_custom_callback_port=4000, litellm_port=4000
        )


async def test_duplicate_port_and_path_pair_rejected(manager):
    await _http_manager_config(
        manager, name="server-a", oauth_custom_callback_path="/callback", oauth_custom_callback_port=8765
    )
    with pytest.raises(ValueError, match="already used"):
        await _http_manager_config(
            manager, name="server-b", oauth_custom_callback_path="/callback", oauth_custom_callback_port=8765
        )


async def test_shared_port_with_distinct_paths_allowed(manager):
    """Two configs may share a custom port via distinct paths (routed to one listener)."""
    a = await _http_manager_config(
        manager, name="server-a", oauth_custom_callback_path="/callback-a", oauth_custom_callback_port=8765
    )
    b = await _http_manager_config(
        manager, name="server-b", oauth_custom_callback_path="/callback-b", oauth_custom_callback_port=8765
    )
    assert a.oauth_custom_callback_port == b.oauth_custom_callback_port == 8765
    assert a.oauth_custom_callback_path != b.oauth_custom_callback_path


async def test_duplicate_path_only_pair_rejected(manager):
    """Two path-only configs (no custom port) at the same path collide on the main app."""
    await _http_manager_config(manager, name="server-a", oauth_custom_callback_path="/callback")
    with pytest.raises(ValueError, match="already used"):
        await _http_manager_config(manager, name="server-b", oauth_custom_callback_path="/callback")


async def test_default_configs_never_conflict_with_each_other(manager):
    """Two configs with no custom path/port at all never collide (shared static route)."""
    await _http_manager_config(manager, name="server-a")
    await _http_manager_config(manager, name="server-b")  # must not raise


async def test_update_disallows_toggling_shared_connection_off_with_custom_path_set(manager):
    cfg = await _http_manager_config(manager, oauth_custom_callback_path="/callback")
    with pytest.raises(ValueError, match="shared_connection"):
        await manager.update_config(cfg.id, shared_connection=False)


async def test_update_allows_resaving_own_config_unchanged(manager):
    cfg = await _http_manager_config(
        manager, oauth_custom_callback_path="/callback", oauth_custom_callback_port=8765
    )
    updated = await manager.update_config(cfg.id, name="renamed-server")
    assert updated.oauth_custom_callback_path == "/callback"
    assert updated.oauth_custom_callback_port == 8765


async def test_update_rejects_new_conflict_with_another_config(manager):
    await _http_manager_config(
        manager, name="server-a", oauth_custom_callback_path="/callback", oauth_custom_callback_port=8765
    )
    cfg_b = await _http_manager_config(
        manager, name="server-b", oauth_custom_callback_path="/other", oauth_custom_callback_port=8765
    )
    with pytest.raises(ValueError, match="already used"):
        await manager.update_config(cfg_b.id, oauth_custom_callback_path="/callback")
