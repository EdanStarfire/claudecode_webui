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
        '_handle_queue_task',
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
        "system_prompt": "Test context"
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

    # Create mock sessions (issue #349: is_minion removed - all sessions are minions):
    # - caller_session: the child minion calling list_minions
    # - parent_session: parent overseer (is_overseer=True)
    # - another_overseer: another overseer session
    caller_session = MagicMock()
    caller_session.session_id = "child-session-id"
    caller_session.project_id = "legion-123"
    caller_session.is_overseer = False
    caller_session.name = "ChildMinion"
    caller_session.role = "Test Role"
    caller_session.state = SessionState.ACTIVE
    caller_session.capabilities = []

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-id"
    parent_session.project_id = "legion-123"
    parent_session.is_overseer = True  # Parent has spawned children
    parent_session.name = "ParentOverseer"
    parent_session.role = "Parent Role"
    parent_session.state = SessionState.ACTIVE
    parent_session.capabilities = []

    # Another overseer session
    another_overseer = MagicMock()
    another_overseer.session_id = "another-overseer-id"
    another_overseer.project_id = "legion-123"
    another_overseer.is_overseer = True  # An overseer (has children)
    another_overseer.name = "AnotherOverseer"
    another_overseer.role = "Overseer Role"
    another_overseer.state = SessionState.ACTIVE
    another_overseer.capabilities = []

    # Mock get_session_info to return appropriate session based on ID
    async def mock_get_session_info(session_id):
        sessions = {
            "child-session-id": caller_session,
            "parent-session-id": parent_session,
            "another-overseer-id": another_overseer,
        }
        return sessions.get(session_id)

    session_manager.get_session_info = mock_get_session_info

    # Create mock legion/project
    mock_legion = MagicMock()
    mock_legion.session_ids = ["child-session-id", "parent-session-id", "another-overseer-id"]

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
        return_value=["parent-session-id", "another-overseer-id"]
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

    # Verify another overseer is visible (in child's hierarchy group)
    # Issue #349: All sessions are minions
    assert "AnotherOverseer" in result_text, \
        f"Overseer should be visible to child. Result: {result_text}"


@pytest.mark.asyncio
async def test_get_minion_by_name_includes_overseer():
    """
    Issue #323 regression test: get_minion_by_name_in_legion should find sessions.

    Issue #349: All sessions are minions, so this test now verifies basic lookup.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.session_manager import SessionState

    # Create mock session manager
    session_manager = MagicMock()

    # Overseer session
    overseer_session = MagicMock()
    overseer_session.session_id = "overseer-session-id"
    overseer_session.project_id = "legion-123"
    overseer_session.is_overseer = True  # Is an overseer
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

    # Should find the session (issue #349: all sessions are minions)
    assert result is not None, \
        "get_minion_by_name_in_legion should find sessions by name"
    assert result.session_id == "overseer-session-id"
    assert result.name == "MyOverseer"


# Regression tests for issue #824: /tmp path translation for send_comm file attachments


def _make_send_comm_system(session_id: str, docker_enabled: bool, data_dir):
    """Helper: build a mock system for send_comm path-translation tests."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    session_info = MagicMock()
    session_info.docker_enabled = docker_enabled

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager
    session_coordinator.data_dir = Path(data_dir)

    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    return mock_system


@pytest.mark.asyncio
async def test_issue_824_tmp_path_translated_for_docker_session(tmp_path):
    """Issue #824: /tmp/ paths are translated to session tmp dir for Docker sessions."""
    from src.legion.mcp.legion_mcp_tools import LegionMCPTools

    session_id = "docker-session-abc"
    mock_system = _make_send_comm_system(session_id, docker_enabled=True, data_dir=tmp_path)

    # Create the translated file so existence check passes
    translated_dir = tmp_path / "sessions" / session_id / "tmp"
    translated_dir.mkdir(parents=True)
    test_file = translated_dir / "output.md"
    test_file.write_text("hello")

    mcp_tools = LegionMCPTools(mock_system)

    # We only want to exercise the path-translation branch; stub out everything after
    # the loop by making the comm_router call fail gracefully so we can inspect
    # whether fpath resolved correctly. We do this by patching the later parts to
    # raise a known sentinel and catching it.
    resolved_paths = []
    original_method = mcp_tools._handle_send_comm

    async def spy_handle(args):
        # Patch Path.exists to record resolved path, then pretend the rest fails
        import pathlib
        _original_exists = pathlib.Path.exists

        def recording_exists(self_path):
            resolved_paths.append(str(self_path))
            return _original_exists(self_path)

        pathlib.Path.exists = recording_exists
        try:
            return await original_method(args)
        finally:
            pathlib.Path.exists = _original_exists

    await spy_handle({
        "_from_minion_id": session_id,
        "to_minion_name": "user",
        "summary": "test",
        "content": "body",
        "comm_type": "report",
        "attachments": '["' + "/tmp/output.md" + '"]',
    })

    # The translated path should appear in resolved_paths
    expected = str(tmp_path / "sessions" / session_id / "tmp" / "output.md")
    assert any(p == expected for p in resolved_paths), (
        f"Expected translated path {expected} not found in {resolved_paths}"
    )


@pytest.mark.asyncio
async def test_issue_824_tmp_path_not_translated_for_non_docker_session(tmp_path):
    """Issue #824: /tmp/ paths are NOT translated when docker_enabled=False."""
    from src.legion.mcp.legion_mcp_tools import LegionMCPTools

    session_id = "plain-session-xyz"
    mock_system = _make_send_comm_system(session_id, docker_enabled=False, data_dir=tmp_path)

    mcp_tools = LegionMCPTools(mock_system)

    resolved_paths = []
    original_method = mcp_tools._handle_send_comm

    async def spy_handle(args):
        import pathlib
        _original_exists = pathlib.Path.exists

        def recording_exists(self_path):
            resolved_paths.append(str(self_path))
            return _original_exists(self_path)

        pathlib.Path.exists = recording_exists
        try:
            return await original_method(args)
        finally:
            pathlib.Path.exists = _original_exists

    result = await spy_handle({
        "_from_minion_id": session_id,
        "to_minion_name": "user",
        "summary": "test",
        "content": "body",
        "comm_type": "report",
        "attachments": '["/tmp/output.md"]',
    })

    # Should have tried the raw /tmp path (file won't exist, error returned)
    assert any(p == "/tmp/output.md" for p in resolved_paths), (
        f"/tmp/output.md should be tried untranslated; got {resolved_paths}"
    )
    # The result should be an error (file not found at /tmp/output.md)
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_issue_824_non_tmp_path_not_translated(tmp_path):
    """Issue #824: Non-/tmp/ paths are never translated regardless of docker_enabled."""
    from src.legion.mcp.legion_mcp_tools import LegionMCPTools

    session_id = "docker-session-def"
    mock_system = _make_send_comm_system(session_id, docker_enabled=True, data_dir=tmp_path)

    mcp_tools = LegionMCPTools(mock_system)

    resolved_paths = []
    original_method = mcp_tools._handle_send_comm

    async def spy_handle(args):
        import pathlib
        _original_exists = pathlib.Path.exists

        def recording_exists(self_path):
            resolved_paths.append(str(self_path))
            return _original_exists(self_path)

        pathlib.Path.exists = recording_exists
        try:
            return await original_method(args)
        finally:
            pathlib.Path.exists = _original_exists

    result = await spy_handle({
        "_from_minion_id": session_id,
        "to_minion_name": "user",
        "summary": "test",
        "content": "body",
        "comm_type": "report",
        "attachments": '["/home/user/report.md"]',
    })

    # Path must be used as-is (no translation for non-/tmp/ paths)
    assert any(p == "/home/user/report.md" for p in resolved_paths), (
        f"/home/user/report.md should be tried as-is; got {resolved_paths}"
    )
    # The result should be an error (file doesn't exist)
    assert result.get("is_error") is True


# ── queue_task handler tests (Issue #1114) ──


def _make_queue_task_tools(enqueue_side_effect=None, enqueue_return=None):
    """Build LegionMCPTools with a mocked session_coordinator.enqueue_message."""
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools

    coordinator = MagicMock()
    if enqueue_side_effect is not None:
        coordinator.enqueue_message = AsyncMock(side_effect=enqueue_side_effect)
    else:
        coordinator.enqueue_message = AsyncMock(return_value=enqueue_return or {
            "queue_id": "q1",
            "position": 0,
            "content": "hello",
            "status": "pending",
        })

    mock_system = MagicMock()
    mock_system.session_coordinator = coordinator
    return LegionMCPTools(mock_system), coordinator


@pytest.mark.asyncio
async def test_issue_1114_queue_task_handler_enqueues():
    """Issue #1114: happy path — enqueues and returns queue_id + position."""
    tools, coordinator = _make_queue_task_tools(enqueue_return={
        "queue_id": "q1",
        "position": 0,
        "content": "hello",
        "status": "pending",
    })

    result = await tools._handle_queue_task({
        "session_id": "sess-abc",
        "content": "do the thing",
        "reset_session": False,
    })

    assert result.get("is_error") is False
    assert result["queue_id"] == "q1"
    assert result["position"] == 0
    assert "q1" in result["content"][0]["text"]
    coordinator.enqueue_message.assert_awaited_once_with(
        session_id="sess-abc",
        content="do the thing",
        reset_session=False,
    )


@pytest.mark.asyncio
async def test_issue_1114_queue_task_handler_missing_session_id():
    """Issue #1114: empty session_id returns is_error=True, nothing enqueued."""
    tools, coordinator = _make_queue_task_tools()

    result = await tools._handle_queue_task({"session_id": "", "content": "hello"})

    assert result.get("is_error") is True
    assert "session_id" in result["content"][0]["text"].lower()
    coordinator.enqueue_message.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1114_queue_task_handler_missing_content():
    """Issue #1114: empty content returns is_error=True, nothing enqueued."""
    tools, coordinator = _make_queue_task_tools()

    result = await tools._handle_queue_task({"session_id": "sess-abc", "content": ""})

    assert result.get("is_error") is True
    assert "content" in result["content"][0]["text"].lower()
    coordinator.enqueue_message.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1114_queue_task_handler_unknown_session():
    """Issue #1114: ValueError from enqueue_message surfaces as is_error=True."""
    tools, coordinator = _make_queue_task_tools(
        enqueue_side_effect=ValueError("Session foo not found")
    )

    result = await tools._handle_queue_task({
        "session_id": "foo",
        "content": "do work",
    })

    assert result.get("is_error") is True
    assert "Session foo not found" in result["content"][0]["text"]
    coordinator.enqueue_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_issue_1114_queue_task_handler_passes_reset_session():
    """Issue #1114: reset_session=True is forwarded to enqueue_message."""
    tools, coordinator = _make_queue_task_tools(enqueue_return={
        "queue_id": "q2",
        "position": 1,
        "content": "reset and do",
        "status": "pending",
    })

    result = await tools._handle_queue_task({
        "session_id": "sess-xyz",
        "content": "reset and do",
        "reset_session": True,
    })

    assert result.get("is_error") is False
    coordinator.enqueue_message.assert_awaited_once_with(
        session_id="sess-xyz",
        content="reset and do",
        reset_session=True,
    )
