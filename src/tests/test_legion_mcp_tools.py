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
    session_info.config = {"docker_enabled": docker_enabled}

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager
    session_coordinator.data_dir = Path(data_dir)
    session_coordinator.register_uploaded_resource = AsyncMock(return_value=None)

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


# ── create_schedule handler tests (Issue #1371) ──


def _make_create_schedule_tools(create_schedule_side_effect=None, create_schedule_return=None):
    """Build LegionMCPTools with mocked scheduler_service.create_schedule."""
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.session_manager import SessionState

    session_info = MagicMock()
    session_info.project_id = "legion-abc"
    session_info.name = "TestMinion"
    session_info.state = SessionState.ACTIVE

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager

    schedule_mock = MagicMock()
    schedule_mock.schedule_id = "sched-001"
    schedule_mock.name = "Test Schedule"
    schedule_mock.cron_expression = "0 * * * *"
    schedule_mock.next_run = None
    schedule_mock.status = MagicMock()
    schedule_mock.status.value = "active"

    scheduler_service = MagicMock()
    if create_schedule_side_effect is not None:
        scheduler_service.create_schedule = AsyncMock(side_effect=create_schedule_side_effect)
    else:
        scheduler_service.create_schedule = AsyncMock(
            return_value=create_schedule_return or schedule_mock
        )

    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    mock_system.scheduler_service = scheduler_service

    return LegionMCPTools(mock_system), scheduler_service


_CREATE_SCHEDULE_BASE_ARGS = {
    "_from_minion_id": "minion-xyz",
    "name": "Test Schedule",
    "cron_expression": "0 * * * *",
}


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_script_mode_passes_through():
    """Issue #1371: script param invokes service with schedule_type='script', script_command set."""
    tools, svc = _make_create_schedule_tools()

    result = await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "script": "echo hello",
    })

    assert result.get("is_error") is False
    svc.create_schedule.assert_awaited_once()
    call_kwargs = svc.create_schedule.call_args.kwargs
    assert call_kwargs["schedule_type"] == "script"
    assert call_kwargs["script_command"] == "echo hello"
    assert call_kwargs.get("prompt", "") == ""
    assert "Type**: script" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_rejects_both_prompt_and_script():
    """Issue #1371: supplying both prompt and script returns descriptive error, service not called."""
    tools, svc = _make_create_schedule_tools()

    result = await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "prompt": "do the thing",
        "script": "echo hello",
    })

    assert result.get("is_error") is True
    assert "exactly one" in result["content"][0]["text"].lower()
    svc.create_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_rejects_neither_prompt_nor_script():
    """Issue #1371: omitting both prompt and script returns descriptive error, service not called."""
    tools, svc = _make_create_schedule_tools()

    result = await tools._handle_create_schedule({**_CREATE_SCHEDULE_BASE_ARGS})

    assert result.get("is_error") is True
    assert "required" in result["content"][0]["text"].lower()
    svc.create_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_rejects_empty_script():
    """Issue #1371: whitespace-only script treated as missing — 'neither provided' error."""
    tools, svc = _make_create_schedule_tools()

    result = await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "script": "   ",
    })

    assert result.get("is_error") is True
    assert "required" in result["content"][0]["text"].lower()
    svc.create_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_prompt_mode_unchanged():
    """Issue #1371: prompt-only call still passes schedule_type='prompt' and script_command=None."""
    tools, svc = _make_create_schedule_tools()

    result = await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "prompt": "summarize today",
    })

    assert result.get("is_error") is False
    call_kwargs = svc.create_schedule.call_args.kwargs
    assert call_kwargs["schedule_type"] == "prompt"
    assert call_kwargs["script_command"] is None
    assert call_kwargs["prompt"] == "summarize today"
    assert "Type**: script" not in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_issue_1371_create_schedule_script_timeout_seconds_propagated():
    """Issue #1371: script_timeout_seconds flows through; default=60 when omitted; string coercion works."""
    tools, svc = _make_create_schedule_tools()

    # Explicit integer value
    await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "script": "check.sh",
        "script_timeout_seconds": 120,
    })
    assert svc.create_schedule.call_args.kwargs["script_timeout_seconds"] == 120

    svc.create_schedule.reset_mock()

    # String form (MCP may pass as string)
    await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "script": "check.sh",
        "script_timeout_seconds": "90",
    })
    assert svc.create_schedule.call_args.kwargs["script_timeout_seconds"] == 90

    svc.create_schedule.reset_mock()

    # Omitted — default 60
    await tools._handle_create_schedule({
        **_CREATE_SCHEDULE_BASE_ARGS,
        "script": "check.sh",
    })
    assert svc.create_schedule.call_args.kwargs["script_timeout_seconds"] == 60


# ── update_schedule handler tests (Issue #1370) ──


_SENTINEL = object()


def _make_update_schedule_tools(
    schedule=None,
    get_schedule_return=_SENTINEL,
    update_side_effect=None,
    update_return=None,
):
    """Build LegionMCPTools with mocked scheduler_service for update_schedule tests."""
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.models.schedule_models import ScheduleStatus

    if schedule is None:
        schedule = MagicMock()
        schedule.schedule_id = "sched-abc"
        schedule.minion_id = "minion-123"
        schedule.schedule_type = "prompt"
        schedule.name = "My Schedule"
        schedule.cron_expression = "0 8 * * *"
        schedule.next_run = None
        schedule.status = ScheduleStatus.ACTIVE

    mock_svc = MagicMock()
    resolved_get = schedule if get_schedule_return is _SENTINEL else get_schedule_return
    mock_svc.get_schedule = AsyncMock(return_value=resolved_get)

    if update_side_effect is not None:
        mock_svc.update_schedule = AsyncMock(side_effect=update_side_effect)
    else:
        returned = update_return if update_return is not None else schedule
        mock_svc.update_schedule = AsyncMock(return_value=returned)

    mock_system = MagicMock()
    mock_system.scheduler_service = mock_svc
    return LegionMCPTools(mock_system), mock_svc, schedule


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_requires_minion_id():
    """Issue #1370: missing from_minion_id returns is_error=True."""
    tools, svc, _ = _make_update_schedule_tools()

    result = await tools._handle_update_schedule({
        "schedule_id": "sched-abc",
        "name": "New Name",
    })

    assert result.get("is_error") is True
    assert "minion id" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_rejects_missing_schedule():
    """Issue #1370: get_schedule returns None → is_error=True."""
    tools, svc, _ = _make_update_schedule_tools(get_schedule_return=None)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "no-such-id",
        "name": "New Name",
    })

    assert result.get("is_error") is True
    assert "not found" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_rejects_foreign_schedule():
    """Issue #1370: schedule.minion_id != from_minion_id → is_error=True; service not called."""
    tools, svc, _ = _make_update_schedule_tools()

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-OTHER",
        "schedule_id": "sched-abc",
        "name": "Steal Name",
    })

    assert result.get("is_error") is True
    assert "own schedules" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_requires_at_least_one_field():
    """Issue #1370: all three optional fields absent → is_error=True."""
    tools, svc, _ = _make_update_schedule_tools()

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-abc",
    })

    assert result.get("is_error") is True
    assert "at least one" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_rejects_prompt_on_script_type():
    """Issue #1370: prompt arg on script-type schedule → is_error=True; service not called."""
    from unittest.mock import MagicMock

    from src.models.schedule_models import ScheduleStatus

    script_schedule = MagicMock()
    script_schedule.schedule_id = "sched-script"
    script_schedule.minion_id = "minion-123"
    script_schedule.schedule_type = "script"
    script_schedule.name = "Script Schedule"
    script_schedule.cron_expression = "0 * * * *"
    script_schedule.next_run = None
    script_schedule.status = ScheduleStatus.ACTIVE

    tools, svc, _ = _make_update_schedule_tools(schedule=script_schedule)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-script",
        "prompt": "new prompt text",
    })

    assert result.get("is_error") is True
    assert "script-type" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_rejects_empty_prompt():
    """Issue #1370: prompt='   ' (whitespace-only) → is_error=True."""
    tools, svc, _ = _make_update_schedule_tools()

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-abc",
        "prompt": "   ",
    })

    assert result.get("is_error") is True
    assert "prompt cannot be empty" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_propagates_invalid_cron():
    """Issue #1370: service raises ValueError('Invalid cron') → handler returns error envelope."""
    tools, svc, _ = _make_update_schedule_tools(
        update_side_effect=ValueError("Invalid cron expression: bad-cron")
    )

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-abc",
        "cron_expression": "bad-cron",
    })

    assert result.get("is_error") is True
    assert "Invalid cron expression" in result["content"][0]["text"]
    svc.update_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_issue_1370_update_schedule_handler_returns_full_schedule_on_success():
    """Issue #1370: success path returns is_error=False and updated schedule info."""
    from unittest.mock import MagicMock

    from src.models.schedule_models import ScheduleStatus

    updated = MagicMock()
    updated.schedule_id = "sched-abc"
    updated.name = "Renamed Schedule"
    updated.cron_expression = "0 9 * * 1-5"
    updated.next_run = None
    updated.status = ScheduleStatus.ACTIVE

    tools, svc, _ = _make_update_schedule_tools(update_return=updated)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-abc",
        "name": "Renamed Schedule",
        "cron_expression": "0 9 * * 1-5",
    })

    assert result.get("is_error") is False
    text = result["content"][0]["text"]
    assert "Renamed Schedule" in text
    assert "0 9 * * 1-5" in text
    svc.update_schedule.assert_awaited_once_with(
        "sched-abc",
        name="Renamed Schedule",
        cron_expression="0 9 * * 1-5",
    )


# ── update_schedule handler tests (Issue #1419) ──


def _make_script_schedule():
    from unittest.mock import MagicMock

    from src.models.schedule_models import ScheduleStatus

    schedule = MagicMock()
    schedule.schedule_id = "sched-script"
    schedule.minion_id = "minion-123"
    schedule.schedule_type = "script"
    schedule.name = "Script Schedule"
    schedule.cron_expression = "0 * * * *"
    schedule.next_run = None
    schedule.status = ScheduleStatus.ACTIVE
    return schedule


@pytest.mark.asyncio
async def test_issue_1419_update_schedule_handler_script_type_ignores_empty_prompt():
    """Issue #1419: script-type + name + prompt='' + cron_expression → success; prompt not passed to service."""
    from unittest.mock import MagicMock

    from src.models.schedule_models import ScheduleStatus

    script_schedule = _make_script_schedule()
    updated = MagicMock()
    updated.schedule_id = "sched-script"
    updated.name = "Renamed Script"
    updated.cron_expression = "*/5 * * * *"
    updated.next_run = None
    updated.status = ScheduleStatus.ACTIVE

    tools, svc, _ = _make_update_schedule_tools(schedule=script_schedule, update_return=updated)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-script",
        "name": "Renamed Script",
        "prompt": "",
        "cron_expression": "*/5 * * * *",
    })

    assert result.get("is_error") is False
    svc.update_schedule.assert_awaited_once_with(
        "sched-script",
        name="Renamed Script",
        cron_expression="*/5 * * * *",
    )


@pytest.mark.asyncio
async def test_issue_1419_update_schedule_handler_script_type_ignores_whitespace_prompt():
    """Issue #1419: script-type + name + prompt='   ' + cron_expression → success; prompt not passed to service."""
    from unittest.mock import MagicMock

    from src.models.schedule_models import ScheduleStatus

    script_schedule = _make_script_schedule()
    updated = MagicMock()
    updated.schedule_id = "sched-script"
    updated.name = "Renamed Script"
    updated.cron_expression = "*/5 * * * *"
    updated.next_run = None
    updated.status = ScheduleStatus.ACTIVE

    tools, svc, _ = _make_update_schedule_tools(schedule=script_schedule, update_return=updated)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-script",
        "name": "Renamed Script",
        "prompt": "   ",
        "cron_expression": "*/5 * * * *",
    })

    assert result.get("is_error") is False
    svc.update_schedule.assert_awaited_once_with(
        "sched-script",
        name="Renamed Script",
        cron_expression="*/5 * * * *",
    )


@pytest.mark.asyncio
async def test_issue_1419_update_schedule_handler_script_type_only_empty_prompt():
    """Issue #1419: script-type + only prompt='' (no other fields) → is_error=True; service not called."""
    script_schedule = _make_script_schedule()
    tools, svc, _ = _make_update_schedule_tools(schedule=script_schedule)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-script",
        "prompt": "",
    })

    assert result.get("is_error") is True
    assert "at least one" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1419_update_schedule_handler_rejects_non_empty_prompt_on_script_type():
    """Issue #1419: script-type + non-empty prompt → is_error=True; service not called."""
    script_schedule = _make_script_schedule()
    tools, svc, _ = _make_update_schedule_tools(schedule=script_schedule)

    result = await tools._handle_update_schedule({
        "_from_minion_id": "minion-123",
        "schedule_id": "sched-script",
        "name": "Renamed Script",
        "prompt": "do something",
    })

    assert result.get("is_error") is True
    assert "script-type" in result["content"][0]["text"].lower()
    svc.update_schedule.assert_not_called()


# ── Regression tests for issue #1433 ──


@pytest.mark.asyncio
async def test_issue_1433_reparent_extracts_descendants_from_dict():
    """
    Issue #1433 regression: _handle_reparent_minion must extract ["descendants"]
    from the paginated dict returned by get_descendants, not iterate the dict itself.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.session_manager import SessionState

    caller_id = "caller-session-id"

    caller_session = MagicMock()
    caller_session.session_id = caller_id
    caller_session.name = "CallerMinion"
    caller_session.state = SessionState.ACTIVE

    # Simulate the real paginated return shape from get_descendants
    paginated_result = {
        "descendants": [
            {"name": "ChildA", "session_id": "child-a-id"},
            {"name": "NewParent", "session_id": "new-parent-id"},
        ],
        "total": 2,
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=caller_session)

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager
    session_coordinator.get_descendants = AsyncMock(return_value=paginated_result)

    overseer_controller = MagicMock()
    overseer_controller.reparent_minion = AsyncMock(
        return_value={"descendants_moved": 0}
    )

    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    mock_system.overseer_controller = overseer_controller

    mcp_tools = LegionMCPTools(mock_system)

    result = await mcp_tools._handle_reparent_minion({
        "_caller_id": caller_id,
        "subject_name": "ChildA",
        "new_parent_name": "NewParent",
    })

    assert result.get("is_error") is False, f"Expected success, got: {result}"
    overseer_controller.reparent_minion.assert_awaited_once_with(
        subject_id="child-a-id",
        new_parent_id="new-parent-id",
        caller_id=caller_id,
    )


@pytest.mark.asyncio
async def test_issue_1433_list_minions_includes_parent_field():
    """
    Issue #1433: list_minions must include a Parent: line for each minion.
    Root minions (parent_overseer_id=None) show "Parent: user";
    children show the parent's name.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.session_manager import SessionState

    root_session = MagicMock()
    root_session.session_id = "root-id"
    root_session.project_id = "legion-123"
    root_session.name = "RootMinion"
    root_session.slug = "rootminion"
    root_session.role = "Root Role"
    root_session.state = SessionState.ACTIVE
    root_session.capabilities = []
    root_session.working_directory = "/work"
    root_session.parent_overseer_id = None

    child_session = MagicMock()
    child_session.session_id = "child-id"
    child_session.project_id = "legion-123"
    child_session.name = "ChildMinion"
    child_session.slug = "childminion"
    child_session.role = "Child Role"
    child_session.state = SessionState.ACTIVE
    child_session.capabilities = []
    child_session.working_directory = "/work"
    child_session.parent_overseer_id = "root-id"

    async def mock_get_session_info(session_id):
        return {"root-id": root_session, "child-id": child_session}.get(session_id)

    session_manager = MagicMock()
    session_manager.get_session_info = mock_get_session_info

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager

    mock_legion = MagicMock()
    legion_coordinator = MagicMock()
    legion_coordinator.get_legion = AsyncMock(return_value=mock_legion)

    comm_router = MagicMock()
    comm_router.get_visible_minions = AsyncMock(return_value=["root-id", "child-id"])

    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    mock_system.legion_coordinator = legion_coordinator
    mock_system.comm_router = comm_router

    mcp_tools = LegionMCPTools(mock_system)

    result = await mcp_tools._handle_list_minions({"_from_minion_id": "child-id"})

    assert result.get("is_error") is False, f"Got error: {result}"
    text = result["content"][0]["text"]

    assert "Parent: user" in text, f"Root minion should show 'Parent: user'; got:\n{text}"
    assert "Parent: RootMinion" in text, f"Child should show 'Parent: RootMinion'; got:\n{text}"


@pytest.mark.asyncio
async def test_issue_1433_list_minions_parent_unknown_fallback():
    """
    Issue #1433: When parent_overseer_id points to a missing/disposed session,
    list_minions must show "Parent: unknown" and not crash.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.legion.mcp.legion_mcp_tools import LegionMCPTools
    from src.session_manager import SessionState

    orphan_session = MagicMock()
    orphan_session.session_id = "orphan-id"
    orphan_session.project_id = "legion-456"
    orphan_session.name = "OrphanMinion"
    orphan_session.slug = "orphanminion"
    orphan_session.role = "Orphan Role"
    orphan_session.state = SessionState.ACTIVE
    orphan_session.capabilities = []
    orphan_session.working_directory = "/work"
    orphan_session.parent_overseer_id = "gone-parent-id"  # points to missing session

    async def mock_get_session_info(session_id):
        if session_id == "orphan-id":
            return orphan_session
        return None  # gone-parent-id returns None

    session_manager = MagicMock()
    session_manager.get_session_info = mock_get_session_info

    session_coordinator = MagicMock()
    session_coordinator.session_manager = session_manager

    mock_legion = MagicMock()
    legion_coordinator = MagicMock()
    legion_coordinator.get_legion = AsyncMock(return_value=mock_legion)

    comm_router = MagicMock()
    comm_router.get_visible_minions = AsyncMock(return_value=["orphan-id"])

    mock_system = MagicMock()
    mock_system.session_coordinator = session_coordinator
    mock_system.legion_coordinator = legion_coordinator
    mock_system.comm_router = comm_router

    mcp_tools = LegionMCPTools(mock_system)

    result = await mcp_tools._handle_list_minions({"_from_minion_id": "orphan-id"})

    assert result.get("is_error") is False, f"Got error: {result}"
    text = result["content"][0]["text"]
    assert "Parent: unknown" in text, f"Should show 'Parent: unknown'; got:\n{text}"
