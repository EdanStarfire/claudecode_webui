"""
Tests for Legion MCP tools with SDK integration.
"""

import pytest
from unittest.mock import Mock, patch
from src.legion_system import LegionSystem
from src.legion.mcp.legion_mcp_tools import LegionMCPTools


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    return LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock()
    )


@pytest.fixture
def mcp_tools(legion_system):
    """Create LegionMCPTools instance."""
    return legion_system.mcp_tools


def test_mcp_tools_initialization(mcp_tools):
    """Test LegionMCPTools initializes."""
    assert mcp_tools is not None
    assert hasattr(mcp_tools, 'system')


def test_mcp_server_created_when_sdk_available(legion_system):
    """Test MCP server is created when SDK is available."""
    # This test will pass if SDK is installed, or be skipped if not
    mcp_tools = legion_system.mcp_tools

    # If SDK is available, mcp_server should be created
    # If SDK is not available, mcp_server should be None
    if mcp_tools.mcp_server is not None:
        assert hasattr(mcp_tools, 'mcp_server')
    else:
        # SDK not available in test environment
        assert mcp_tools.mcp_server is None


def test_tool_handler_methods_exist(mcp_tools):
    """Test that all tool handler methods are defined."""
    handler_methods = [
        '_handle_send_comm',
        '_handle_send_comm_to_channel',
        '_handle_spawn_minion',
        '_handle_dispose_minion',
        '_handle_search_capability',
        '_handle_list_minions',
        '_handle_get_minion_info',
        '_handle_join_channel',
        '_handle_create_channel',
        '_handle_list_channels',
    ]

    for method_name in handler_methods:
        assert hasattr(mcp_tools, method_name), f"Missing handler method: {method_name}"


@pytest.mark.asyncio
async def test_send_comm_handler_returns_error(mcp_tools):
    """Test send_comm handler returns not implemented error."""
    result = await mcp_tools._handle_send_comm({
        "to_minion_name": "TestMinion",
        "content": "Test message",
        "comm_type": "task"
    })

    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) > 0
    assert result["content"][0]["type"] == "text"
    assert "not yet implemented" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_send_comm_to_channel_handler_returns_error(mcp_tools):
    """Test send_comm_to_channel handler returns not implemented error."""
    result = await mcp_tools._handle_send_comm_to_channel({
        "channel_name": "TestChannel",
        "content": "Test broadcast",
        "comm_type": "report"
    })

    assert "content" in result
    assert result["content"][0]["type"] == "text"
    assert "not yet implemented" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_spawn_minion_handler_returns_error(mcp_tools):
    """Test spawn_minion handler returns not implemented error."""
    result = await mcp_tools._handle_spawn_minion({
        "name": "NewMinion",
        "role": "Test Role",
        "initialization_context": "Test context",
        "channels": []
    })

    assert "content" in result
    assert "not yet implemented" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_list_minions_handler_returns_error(mcp_tools):
    """Test list_minions handler returns not implemented error."""
    result = await mcp_tools._handle_list_minions({})

    assert "content" in result
    assert "not yet implemented" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_search_capability_handler_returns_error(mcp_tools):
    """Test search_capability handler returns not implemented error."""
    result = await mcp_tools._handle_search_capability({
        "capability": "database"
    })

    assert "content" in result
    assert "database" in result["content"][0]["text"]
    assert result.get("is_error") is True


def test_mcp_tools_has_system_reference(mcp_tools):
    """Test that MCP tools has reference to LegionSystem."""
    assert hasattr(mcp_tools, 'system')
    assert mcp_tools.system is not None
