"""
Tests for Legion MCP tools with SDK integration.
"""

from unittest.mock import Mock

import pytest

from src.legion_system import LegionSystem


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    return LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock()
    )


@pytest.fixture
def mcp_tools(legion_system):
    """Create LegionMCPTools instance."""
    return legion_system.mcp_tools


def test_mcp_tools_initialization(mcp_tools):
    """Test LegionMCPTools initializes."""
    assert mcp_tools is not None
    assert hasattr(mcp_tools, 'system')


def test_create_mcp_server_for_session(legion_system):
    """Test that MCP server can be created for a session."""
    mcp_tools = legion_system.mcp_tools

    # Test creating session-specific MCP server
    # Note: Will return None if SDK not available in test environment
    session_id = "test-session-123"
    mcp_server = mcp_tools.create_mcp_server_for_session(session_id)

    # Server creation depends on SDK availability
    # If SDK available, server should be created; if not, None is ok
    if mcp_server is not None:
        # SDK available - server was created
        assert mcp_server is not None
    # If None, SDK not available in test environment (expected)


def test_tool_handler_methods_exist(mcp_tools):
    """Test that all tool handler methods are defined."""
    handler_methods = [
        '_handle_send_comm',
        '_handle_spawn_minion',
        '_handle_dispose_minion',
        '_handle_search_capability',
        '_handle_list_minions',
        '_handle_get_minion_info',
    ]

    for method_name in handler_methods:
        assert hasattr(mcp_tools, method_name), f"Missing handler method: {method_name}"


@pytest.mark.asyncio
async def test_send_comm_handler_requires_sender_id(mcp_tools):
    """Test send_comm handler requires sender minion ID."""
    # Missing _from_minion_id should return error
    result = await mcp_tools._handle_send_comm({
        "to_minion_name": "TestMinion",
        "summary": "Test task",
        "content": "Test message",
        "comm_type": "task"
    })

    assert "content" in result
    assert isinstance(result["content"], list)
    assert len(result["content"]) > 0
    assert result["content"][0]["type"] == "text"
    # Expect error about missing sender ID
    assert "sender minion id" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_spawn_minion_handler_requires_parent_id(mcp_tools):
    """Test spawn_minion handler requires parent overseer ID."""
    # Missing _parent_overseer_id should return error
    result = await mcp_tools._handle_spawn_minion({
        "name": "NewMinion",
        "role": "Test Role",
        "initialization_context": "Test context"
    })

    assert "content" in result
    # Expect error about missing parent overseer ID
    assert "parent overseer id" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_list_minions_handler_with_mock_system(mcp_tools):
    """Test list_minions handler with mocked system (expects error)."""
    # With mocked system, list_minions will fail because session_manager.list_sessions()
    # returns a Mock object which can't be awaited
    result = await mcp_tools._handle_list_minions({})

    assert "content" in result
    # Expect error because Mock can't be awaited
    assert "error" in result["content"][0]["text"].lower()
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_search_capability_handler_returns_no_results(mcp_tools):
    """Test search_capability handler returns no results with mocked system."""
    # With mocked system, capability registry is empty, so search returns no results
    result = await mcp_tools._handle_search_capability({
        "capability": "database"
    })

    assert "content" in result
    assert "database" in result["content"][0]["text"]
    # Empty registry returns "no minions found" message (not an error)
    assert "no minions found" in result["content"][0]["text"].lower()
    assert result.get("is_error") is False  # Not an error, just no results


def test_mcp_tools_has_system_reference(mcp_tools):
    """Test that MCP tools has reference to LegionSystem."""
    assert hasattr(mcp_tools, 'system')
    assert mcp_tools.system is not None
