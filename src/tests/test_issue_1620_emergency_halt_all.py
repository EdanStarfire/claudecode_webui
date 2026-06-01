"""
Tests for issue #1620: emergency_halt_all() pre-terminated session filtering.

Verifies that sessions already in TERMINATED state are silently skipped:
- not counted in total_sessions
- not added to stopped_session_ids
- not reported as failures
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.legion.legion_coordinator import LegionCoordinator
from src.session_manager import SessionInfo, SessionState

LEGION_ID = "legion-test-1620"


def _make_session(session_id: str, state: SessionState) -> Mock:
    """Build a minimal SessionInfo mock with the given state."""
    s = Mock(spec=SessionInfo)
    s.session_id = session_id
    s.project_id = LEGION_ID
    s.state = state
    return s


def _make_system(sessions: list) -> Mock:
    """Build a minimal LegionSystem mock that returns the given sessions."""
    system = Mock()
    system.session_coordinator = Mock()
    system.session_coordinator.session_manager = Mock()
    system.session_coordinator.session_manager.list_sessions = AsyncMock(return_value=sessions)
    system.session_coordinator.terminate_session = AsyncMock(return_value=True)
    return system


@pytest.mark.asyncio
async def test_issue_1620_mixed_sessions_skips_terminated():
    """Test A: 2 TERMINATED + 2 ACTIVE → total_sessions=2, stopped=2, failed=[]."""
    sessions = [
        _make_session("s-terminated-1", SessionState.TERMINATED),
        _make_session("s-terminated-2", SessionState.TERMINATED),
        _make_session("s-active-1", SessionState.ACTIVE),
        _make_session("s-active-2", SessionState.ACTIVE),
    ]
    system = _make_system(sessions)
    coordinator = LegionCoordinator(system)

    result = await coordinator.emergency_halt_all(LEGION_ID)

    assert result["total_sessions"] == 2
    assert len(result["stopped_session_ids"]) == 2
    assert "s-active-1" in result["stopped_session_ids"]
    assert "s-active-2" in result["stopped_session_ids"]
    assert result["failed_sessions"] == []
    # Pre-terminated sessions must NOT appear anywhere in the result
    assert "s-terminated-1" not in result["stopped_session_ids"]
    assert "s-terminated-2" not in result["stopped_session_ids"]


@pytest.mark.asyncio
async def test_issue_1620_all_terminated_returns_empty():
    """Test B: all TERMINATED → total_sessions=0, stopped=[], failed=[]."""
    sessions = [
        _make_session("s-terminated-1", SessionState.TERMINATED),
        _make_session("s-terminated-2", SessionState.TERMINATED),
    ]
    system = _make_system(sessions)
    coordinator = LegionCoordinator(system)

    result = await coordinator.emergency_halt_all(LEGION_ID)

    assert result["total_sessions"] == 0
    assert result["stopped_session_ids"] == []
    assert result["failed_sessions"] == []
    # terminate_session must not have been called at all
    system.session_coordinator.terminate_session.assert_not_called()


@pytest.mark.asyncio
async def test_issue_1620_all_active_unchanged():
    """Test C: all ACTIVE → all stopped, total_sessions equals session count, no failures."""
    sessions = [
        _make_session("s-active-1", SessionState.ACTIVE),
        _make_session("s-active-2", SessionState.ACTIVE),
        _make_session("s-active-3", SessionState.ACTIVE),
    ]
    system = _make_system(sessions)
    coordinator = LegionCoordinator(system)

    result = await coordinator.emergency_halt_all(LEGION_ID)

    assert result["total_sessions"] == 3
    assert len(result["stopped_session_ids"]) == 3
    assert result["failed_sessions"] == []
    assert set(result["stopped_session_ids"]) == {"s-active-1", "s-active-2", "s-active-3"}
