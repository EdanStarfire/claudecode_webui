"""Unit tests for McpServerConfig dataclass (issue #1109).

Covers to_sdk_config() oauth pass-through and from_dict() backward compat
for the oauth_client_id / oauth_callback_port fields added in #1109.
"""

import pytest
from src.mcp_config_manager import McpServerConfig, McpServerType


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
