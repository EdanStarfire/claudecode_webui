"""Unit tests for MCP server URL validation on save (issue #2248).

- AC1: McpConfigManager.create_config()/update_config() reject non-URL `url`
  values for HTTP/SSE types with ValueError (-> 400 via value_error_status=400),
  trimming whitespace before validation and storage.
- AC3: OAuthFlowManager.start_flow() validates the URL up front and raises
  ValueError (not RuntimeError) before any network call, naming the URL.
- AC4: ApplicationService._annotate_url_validity() flags invalid stored URLs
  without raising, for stdio/sse/http configs alike.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.application_service import ApplicationService
from backend.mcp_config_manager import McpConfigManager, McpServerType, validate_mcp_server_url
from backend.oauth_manager import OAuthFlowManager

# ---------------------------------------------------------------------------
# validate_mcp_server_url
# ---------------------------------------------------------------------------


def test_validate_rejects_non_url_text():
    with pytest.raises(ValueError, match="http"):
        validate_mcp_server_url("this is just pasted text")


def test_validate_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname"):
        validate_mcp_server_url("https://")


def test_validate_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        validate_mcp_server_url("ftp://example.com/resource")


def test_validate_strips_and_returns_trimmed_url():
    assert validate_mcp_server_url("  https://example.com/mcp\n") == "https://example.com/mcp"


def test_validate_accepts_plain_http():
    assert validate_mcp_server_url("http://example.com") == "http://example.com"


# ---------------------------------------------------------------------------
# McpConfigManager.create_config — AC1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_config_rejects_garbage_url(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    with pytest.raises(ValueError):
        await manager.create_config(
            name="srv", server_type=McpServerType.HTTP, url="pasted page text"
        )


@pytest.mark.asyncio
async def test_create_config_trims_whitespace(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.HTTP, url="  https://example.com/mcp  "
    )
    assert config.url == "https://example.com/mcp"


@pytest.mark.asyncio
async def test_create_config_valid_sse_url_accepted(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.SSE, url="https://example.com/sse"
    )
    assert config.url == "https://example.com/sse"


@pytest.mark.asyncio
async def test_create_config_stdio_unaffected_by_url_format(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.STDIO, command="npx"
    )
    assert config.command == "npx"


# ---------------------------------------------------------------------------
# McpConfigManager.update_config — AC1 (previously had zero validation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_config_rejects_garbage_url(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.HTTP, url="https://example.com/mcp"
    )
    with pytest.raises(ValueError):
        await manager.update_config(config_id=config.id, url="not a url at all")


@pytest.mark.asyncio
async def test_update_config_rejects_empty_url_for_http(tmp_path: Path):
    """Gap this fix closes: update_config() previously accepted '' unconditionally."""
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.HTTP, url="https://example.com/mcp"
    )
    with pytest.raises(ValueError):
        await manager.update_config(config_id=config.id, url="")


@pytest.mark.asyncio
async def test_update_config_trims_whitespace(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.HTTP, url="https://example.com/mcp"
    )
    updated = await manager.update_config(
        config_id=config.id, url="  https://updated.example.com/mcp\t"
    )
    assert updated.url == "https://updated.example.com/mcp"


@pytest.mark.asyncio
async def test_update_config_stdio_unaffected_by_url_format(tmp_path: Path):
    manager = McpConfigManager(tmp_path)
    config = await manager.create_config(
        name="srv", server_type=McpServerType.STDIO, command="npx"
    )
    # stdio configs don't use url — garbage here must not raise
    updated = await manager.update_config(config_id=config.id, url="not a url")
    assert updated.url == "not a url"


# ---------------------------------------------------------------------------
# OAuthFlowManager.start_flow — AC3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_rejects_malformed_url_before_any_network_call(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx2.AsyncClient") as mock_client:
        with pytest.raises(ValueError, match="Invalid MCP server URL"):
            await manager.start_flow(
                server_id="srv1",
                server_url="pasted page text, not a url",
                redirect_uri="http://localhost/oauth/callback",
            )
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_start_flow_error_names_the_url(tmp_path: Path):
    manager = OAuthFlowManager(tmp_path)

    with patch("backend.oauth_manager.httpx2.AsyncClient"):
        with pytest.raises(ValueError, match="garbage-not-a-url"):
            await manager.start_flow(
                server_id="srv1",
                server_url="garbage-not-a-url",
                redirect_uri="http://localhost/oauth/callback",
            )


# ---------------------------------------------------------------------------
# ApplicationService._annotate_url_validity — AC4
# ---------------------------------------------------------------------------


def test_annotate_url_validity_valid_http_config():
    result = ApplicationService._annotate_url_validity(
        {"type": "http", "url": "https://example.com/mcp"}
    )
    assert result["url_valid"] is True
    assert result["url_error"] is None


def test_annotate_url_validity_invalid_stored_url_does_not_raise():
    result = ApplicationService._annotate_url_validity(
        {"type": "sse", "url": "not a url"}
    )
    assert result["url_valid"] is False
    assert result["url_error"]


def test_annotate_url_validity_stdio_is_not_applicable():
    result = ApplicationService._annotate_url_validity(
        {"type": "stdio", "command": "npx"}
    )
    assert result["url_valid"] is None
    assert result["url_error"] is None


def test_annotate_url_validity_one_bad_one_good_both_annotated():
    """Regression guard: a list containing one bad and one good config must not
    raise out of the annotation helper for either entry."""
    good = ApplicationService._annotate_url_validity(
        {"type": "http", "url": "https://good.example.com"}
    )
    bad = ApplicationService._annotate_url_validity(
        {"type": "http", "url": "garbage"}
    )
    assert good["url_valid"] is True
    assert bad["url_valid"] is False
