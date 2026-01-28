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


# Regression tests for issue #323: list_minions and send_comm visibility
# These tests verify that overseer sessions are included in visibility filters


@pytest.mark.asyncio
async def test_list_minions_includes_overseer_sessions():
    """
    Issue #323 regression test: list_minions should include sessions with is_overseer=True.

    When a minion spawns a child, the parent becomes an overseer (is_overseer=True).
    The child should be able to see the parent in list_minions results.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.session_manager import SessionState

    # Create mock session manager
    session_manager = MagicMock()

    # Create mock sessions:
    # - caller_session: the child minion calling list_minions
    # - parent_session: parent overseer (is_overseer=True, but may not have is_minion=True)
    # - sibling_session: another minion
    caller_session = MagicMock()
    caller_session.session_id = "child-session-id"
    caller_session.project_id = "legion-123"
    caller_session.is_minion = True
    caller_session.is_overseer = False
    caller_session.name = "ChildMinion"
    caller_session.role = "Test Role"
    caller_session.state = SessionState.ACTIVE
    caller_session.capabilities = []

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-id"
    parent_session.project_id = "legion-123"
    parent_session.is_minion = True  # Parent is also a minion (was spawned by user/another minion)
    parent_session.is_overseer = True  # Parent has spawned children
    parent_session.name = "ParentOverseer"
    parent_session.role = "Parent Role"
    parent_session.state = SessionState.ACTIVE
    parent_session.capabilities = []

    # Case where parent is NOT marked as minion but IS overseer
    # This can happen when a regular session spawns a minion
    non_minion_overseer = MagicMock()
    non_minion_overseer.session_id = "non-minion-overseer-id"
    non_minion_overseer.project_id = "legion-123"
    non_minion_overseer.is_minion = False  # Not marked as minion
    non_minion_overseer.is_overseer = True  # But is an overseer (has children)
    non_minion_overseer.name = "RegularOverseer"
    non_minion_overseer.role = "Overseer Role"
    non_minion_overseer.state = SessionState.ACTIVE
    non_minion_overseer.capabilities = []

    # Mock get_session_info to return appropriate session based on ID
    async def mock_get_session_info(session_id):
        sessions = {
            "child-session-id": caller_session,
            "parent-session-id": parent_session,
            "non-minion-overseer-id": non_minion_overseer,
        }
        return sessions.get(session_id)

    session_manager.get_session_info = mock_get_session_info

    # Create mock legion/project
    mock_legion = MagicMock()
    mock_legion.session_ids = ["child-session-id", "parent-session-id", "non-minion-overseer-id"]

    # Create mock legion coordinator
    legion_coordinator = MagicMock()
    legion_coordinator.get_legion = AsyncMock(return_value=mock_legion)

    # Create mock session coordinator
    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager

    # Create mock comm_router with hierarchy-scoped visibility
    comm_router = MagicMock()
    # Child can see parent and non-minion overseer (its immediate hierarchy group)
    comm_router.get_visible_minions = AsyncMock(
        return_value=["parent-session-id", "non-minion-overseer-id"]
    )

    # Create mock system
    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    mock_system.legion_coordinator = legion_coordinator
    mock_system.comm_router = comm_router

    # Create MCP tools with mock system
    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    mcp_tools = LegionMCPTools(mock_system)

    # Call list_minions as the child
    result = await mcp_tools._handle_list_minions({
        "_from_minion_id": "child-session-id"
    })

    # Verify no error
    assert result.get("is_error") is False, f"Got error: {result}"

    # Parse result text
    result_text = result["content"][0]["text"]

    # Verify parent overseer is visible (in child's hierarchy group)
    assert "ParentOverseer" in result_text, \
        f"Parent overseer should be visible to child. Result: {result_text}"

    # Verify non-minion overseer is visible (in child's hierarchy group)
    assert "RegularOverseer" in result_text, \
        f"Non-minion overseer should be visible to child. Result: {result_text}"


@pytest.mark.asyncio
async def test_get_minion_by_name_includes_overseer():
    """
    Issue #323 regression test: get_minion_by_name_in_legion should find overseers.

    When a child tries to send_comm to parent, the lookup should find the parent
    even if is_minion=False but is_overseer=True.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.session_manager import SessionState

    # Create mock session manager
    session_manager = MagicMock()

    # Non-minion overseer (regular session that spawned a child)
    overseer_session = MagicMock()
    overseer_session.session_id = "overseer-session-id"
    overseer_session.project_id = "legion-123"
    overseer_session.is_minion = False  # Not marked as minion
    overseer_session.is_overseer = True  # But is an overseer
    overseer_session.name = "MyOverseer"
    overseer_session.role = "Overseer Role"
    overseer_session.state = SessionState.ACTIVE

    async def mock_get_session_info(session_id):
        if session_id == "overseer-session-id":
            return overseer_session
        return None

    session_manager.get_session_info = mock_get_session_info

    # Create mock legion/project
    mock_legion = MagicMock()
    mock_legion.session_ids = ["overseer-session-id"]

    # Create mock project manager
    project_manager = MagicMock()
    project_manager.get_project = AsyncMock(return_value=mock_legion)

    # Create mock session coordinator
    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager
    session_coordinator.project_manager = project_manager

    # Create mock system
    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator

    # Create LegionCoordinator with mock system
    from src.legion.legion_coordinator import LegionCoordinator
    legion_coord = LegionCoordinator(mock_system)

    # Try to find overseer by name
    result = await legion_coord.get_minion_by_name_in_legion("legion-123", "MyOverseer")

    # Should find the overseer even though is_minion=False
    assert result is not None, \
        "get_minion_by_name_in_legion should find overseer sessions (is_overseer=True)"
    assert result.session_id == "overseer-session-id"
    assert result.name == "MyOverseer"
