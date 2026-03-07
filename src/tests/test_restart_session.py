"""
Tests for the restart_session MCP tool (Issue #680).
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.legion_system import LegionSystem
from src.session_manager import SessionState


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock(),
    )
    # Set up session_coordinator mocks
    system.session_coordinator.session_manager = AsyncMock()
    system.session_coordinator.restart_session = AsyncMock(return_value=True)
    system.session_coordinator.enqueue_message = AsyncMock()
    system.session_coordinator.send_message = AsyncMock(return_value=True)
    return system


@pytest.fixture
def mcp_tools(legion_system):
    """Create LegionMCPTools instance."""
    return legion_system.mcp_tools


def _make_session_info(state=SessionState.ACTIVE, is_processing=True):
    """Create a mock SessionInfo."""
    info = MagicMock()
    info.state = state
    info.is_processing = is_processing
    return info


@pytest.mark.asyncio
async def test_issue_680_restart_returns_correct_format(mcp_tools):
    """Tool returns success with restart instructions."""
    session_info = _make_session_info(state=SessionState.ACTIVE)
    mcp_tools.system.session_coordinator.session_manager.get_session_info = AsyncMock(
        return_value=session_info
    )

    result = await mcp_tools._handle_restart_session({
        "_from_minion_id": "session-1",
        "reason": "context overflow",
    })

    assert result["is_error"] is False
    text = result["content"][0]["text"]
    assert "STOP all work immediately" in text
    assert "Restart ID:" in text


@pytest.mark.asyncio
async def test_issue_680_restart_missing_session_id(mcp_tools):
    """Tool returns error when session ID is missing."""
    result = await mcp_tools._handle_restart_session({"reason": "test"})

    assert result["is_error"] is True
    assert "session id" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_issue_680_restart_session_not_found(mcp_tools):
    """Tool returns error when session doesn't exist."""
    mcp_tools.system.session_coordinator.session_manager.get_session_info = AsyncMock(
        return_value=None
    )

    result = await mcp_tools._handle_restart_session({
        "_from_minion_id": "nonexistent",
        "reason": "",
    })

    assert result["is_error"] is True
    assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_issue_680_restart_session_not_active(mcp_tools):
    """Tool returns error when session is not active."""
    session_info = _make_session_info(state=SessionState.TERMINATED)
    mcp_tools.system.session_coordinator.session_manager.get_session_info = AsyncMock(
        return_value=session_info
    )

    result = await mcp_tools._handle_restart_session({
        "_from_minion_id": "session-1",
        "reason": "",
    })

    assert result["is_error"] is True
    assert "not active" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_issue_680_duplicate_restart_prevention(mcp_tools):
    """Second restart call returns error while first is pending."""
    session_info = _make_session_info(state=SessionState.ACTIVE)
    mcp_tools.system.session_coordinator.session_manager.get_session_info = AsyncMock(
        return_value=session_info
    )

    # First call succeeds
    result1 = await mcp_tools._handle_restart_session({
        "_from_minion_id": "session-1",
        "reason": "first",
    })
    assert result1["is_error"] is False

    # Second call should fail (restart already pending)
    result2 = await mcp_tools._handle_restart_session({
        "_from_minion_id": "session-1",
        "reason": "second",
    })
    assert result2["is_error"] is True
    assert "already in progress" in result2["content"][0]["text"].lower()

    # Clean up pending restarts for other tests
    mcp_tools._pending_restarts.discard("session-1")


@pytest.mark.asyncio
async def test_issue_680_monitor_detects_idle_and_restarts(mcp_tools):
    """Monitor detects idle session and triggers restart."""
    coordinator = mcp_tools.system.session_coordinator

    # Session becomes idle on first check
    idle_info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=idle_info)

    # Set up permission callback factory
    mock_callback = Mock()
    mcp_tools.system.permission_callback_factory = Mock(return_value=mock_callback)

    # Set up websocket manager
    mcp_tools.system.ui_websocket_manager = AsyncMock()
    mcp_tools.system.ui_websocket_manager.broadcast_to_all = AsyncMock()

    with patch("src.legion.mcp.legion_mcp_tools.asyncio.sleep", new_callable=AsyncMock):
        await mcp_tools._restart_monitor("session-1", "restart-123", "test reason")

    coordinator.restart_session.assert_awaited_once_with("session-1", mock_callback)
    coordinator.enqueue_message.assert_awaited_once()
    enqueue_kwargs = coordinator.enqueue_message.call_args
    assert "test reason" in enqueue_kwargs.kwargs.get("content", enqueue_kwargs[1].get("content", ""))

    mcp_tools.system.ui_websocket_manager.broadcast_to_all.assert_awaited_once()
    broadcast_msg = mcp_tools.system.ui_websocket_manager.broadcast_to_all.call_args[0][0]
    assert broadcast_msg["type"] == "session_self_restart"
    assert broadcast_msg["data"]["session_id"] == "session-1"
    assert broadcast_msg["data"]["restart_id"] == "restart-123"
    assert broadcast_msg["data"]["reason"] == "test reason"

    # Pending restart should be cleaned up
    assert "session-1" not in mcp_tools._pending_restarts


@pytest.mark.asyncio
async def test_issue_680_monitor_timeout_sends_failure(mcp_tools):
    """Monitor sends failure message after 30s timeout."""
    coordinator = mcp_tools.system.session_coordinator

    # Session stays processing for all checks
    busy_info = _make_session_info(state=SessionState.ACTIVE, is_processing=True)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=busy_info)

    mcp_tools._pending_restarts.add("session-1")

    with patch("src.legion.mcp.legion_mcp_tools.asyncio.sleep", new_callable=AsyncMock):
        await mcp_tools._restart_monitor("session-1", "restart-456", "stuck")

    # restart_session should NOT have been called
    coordinator.restart_session.assert_not_awaited()

    # Timeout message should be sent
    coordinator.send_message.assert_awaited_once()
    msg_args = coordinator.send_message.call_args[0]
    assert msg_args[0] == "session-1"
    assert "timed out" in msg_args[1].lower()

    # Pending restart should be cleaned up
    assert "session-1" not in mcp_tools._pending_restarts


@pytest.mark.asyncio
async def test_issue_680_monitor_session_disappears(mcp_tools):
    """Monitor exits gracefully if session disappears."""
    coordinator = mcp_tools.system.session_coordinator
    coordinator.session_manager.get_session_info = AsyncMock(return_value=None)

    mcp_tools._pending_restarts.add("session-1")

    with patch("src.legion.mcp.legion_mcp_tools.asyncio.sleep", new_callable=AsyncMock):
        await mcp_tools._restart_monitor("session-1", "restart-789", "")

    coordinator.restart_session.assert_not_awaited()
    coordinator.send_message.assert_not_awaited()

    # Pending restart should be cleaned up
    assert "session-1" not in mcp_tools._pending_restarts


@pytest.mark.asyncio
async def test_issue_680_continuation_message_no_reason(mcp_tools):
    """Continuation message omits reason when empty."""
    coordinator = mcp_tools.system.session_coordinator

    idle_info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
    coordinator.session_manager.get_session_info = AsyncMock(return_value=idle_info)
    mcp_tools.system.permission_callback_factory = None
    mcp_tools.system.ui_websocket_manager = None

    with patch("src.legion.mcp.legion_mcp_tools.asyncio.sleep", new_callable=AsyncMock):
        await mcp_tools._restart_monitor("session-2", "restart-abc", "")

    coordinator.restart_session.assert_awaited_once_with("session-2", None)
    enqueue_call = coordinator.enqueue_message.call_args
    content = enqueue_call.kwargs.get("content", enqueue_call[1].get("content", ""))
    assert "Restart reason:" not in content
