"""
Tests for Legion MCP tool schemas.
"""

import pytest
from unittest.mock import Mock
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
    """Test LegionMCPTools initializes with tool schemas."""
    assert mcp_tools is not None
    assert hasattr(mcp_tools, 'tool_schemas')
    assert isinstance(mcp_tools.tool_schemas, list)
    assert len(mcp_tools.tool_schemas) > 0


def test_all_tools_defined(mcp_tools):
    """Test that all expected tools are defined."""
    tool_names = {tool["name"] for tool in mcp_tools.tool_schemas}

    expected_tools = {
        # Communication
        "send_comm",
        "send_comm_to_channel",
        # Lifecycle
        "spawn_minion",
        "dispose_minion",
        # Discovery
        "search_capability",
        "list_minions",
        "get_minion_info",
        # Channels
        "join_channel",
        "create_channel",
        "list_channels",
    }

    assert tool_names == expected_tools, f"Missing or extra tools. Expected: {expected_tools}, Got: {tool_names}"


def test_tool_schema_structure(mcp_tools):
    """Test that all tools have required schema fields."""
    for tool in mcp_tools.tool_schemas:
        # Required top-level fields
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool

        # Schema structure
        schema = tool["input_schema"]
        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_send_comm_schema(mcp_tools):
    """Test send_comm tool schema details."""
    tool = next(t for t in mcp_tools.tool_schemas if t["name"] == "send_comm")

    assert "send a communication" in tool["description"].lower()
    assert "#minion-name" in tool["description"]

    props = tool["input_schema"]["properties"]
    assert "to_minion_name" in props
    assert "content" in props
    assert "comm_type" in props

    # comm_type should be enum
    assert "enum" in props["comm_type"]
    assert set(props["comm_type"]["enum"]) == {"task", "question", "report", "guide"}

    # Required fields
    required = tool["input_schema"]["required"]
    assert set(required) == {"to_minion_name", "content", "comm_type"}


def test_spawn_minion_schema(mcp_tools):
    """Test spawn_minion tool schema details."""
    tool = next(t for t in mcp_tools.tool_schemas if t["name"] == "spawn_minion")

    assert "create a new child minion" in tool["description"].lower()

    props = tool["input_schema"]["properties"]
    assert "name" in props
    assert "role" in props
    assert "initialization_context" in props
    assert "channels" in props

    # Channels should be array
    assert props["channels"]["type"] == "array"
    assert "items" in props["channels"]

    # Required fields
    required = tool["input_schema"]["required"]
    assert set(required) == {"name", "role", "initialization_context"}


def test_search_capability_schema(mcp_tools):
    """Test search_capability tool schema details."""
    tool = next(t for t in mcp_tools.tool_schemas if t["name"] == "search_capability")

    assert "capability registry" in tool["description"].lower()

    props = tool["input_schema"]["properties"]
    assert "capability" in props

    required = tool["input_schema"]["required"]
    assert required == ["capability"]


def test_create_channel_schema(mcp_tools):
    """Test create_channel tool schema details."""
    tool = next(t for t in mcp_tools.tool_schemas if t["name"] == "create_channel")

    assert "create a new channel" in tool["description"].lower()

    props = tool["input_schema"]["properties"]
    assert "name" in props
    assert "description" in props
    assert "purpose" in props
    assert "initial_members" in props

    # Purpose should be enum
    assert "enum" in props["purpose"]
    assert set(props["purpose"]["enum"]) == {"coordination", "planning", "research", "scene"}

    # initial_members should be array
    assert props["initial_members"]["type"] == "array"


def test_list_tools_no_required_params(mcp_tools):
    """Test that list tools have no required parameters."""
    list_tools = ["list_minions", "list_channels"]

    for tool_name in list_tools:
        tool = next(t for t in mcp_tools.tool_schemas if t["name"] == tool_name)
        required = tool["input_schema"]["required"]
        assert required == [], f"{tool_name} should have no required parameters"


def test_tool_descriptions_mention_tags(mcp_tools):
    """Test that relevant tools mention #tag syntax in descriptions."""
    tools_with_tags = ["send_comm", "send_comm_to_channel"]

    for tool_name in tools_with_tags:
        tool = next(t for t in mcp_tools.tool_schemas if t["name"] == tool_name)
        assert "#minion-name" in tool["description"] or "#channel-name" in tool["description"], \
            f"{tool_name} should mention #tag syntax"


@pytest.mark.asyncio
async def test_handle_tool_call_not_implemented(mcp_tools):
    """Test that tool handler returns not implemented error."""
    result = await mcp_tools.handle_tool_call(
        tool_name="send_comm",
        minion_id="minion-123",
        arguments={"to_minion_name": "TestMinion", "content": "Test", "comm_type": "task"}
    )

    assert "success" in result
    assert result["success"] is False
    assert "error" in result
    assert "not yet implemented" in result["error"].lower()
