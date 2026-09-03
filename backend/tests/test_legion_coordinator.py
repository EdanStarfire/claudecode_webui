"""
Tests for LegionCoordinator (issue #1779 focus: resume_all timestamp-injection scoping).
"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from backend.legion_system import LegionSystem


@pytest.fixture
def legion_system():
    """Create a mock LegionSystem with one ACTIVE minion for resume_all tests."""
    from backend.session_manager import SessionInfo, SessionState

    active_minion = Mock(spec=SessionInfo)
    active_minion.session_id = "test-minion-123"
    active_minion.project_id = "test-legion-456"
    active_minion.state = SessionState.ACTIVE

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock(return_value=True)
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.session_manager.list_sessions = AsyncMock(return_value=[active_minion])
    mock_session_coordinator.data_dir = Path("/tmp/test")

    return LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock(),
    )


class TestIssue1779ResumeAllTimestampScoping:
    """resume_all()'s bulk "continue" nudge is system-generated, not a user message —
    it must opt out of timestamp injection the same way comm_router.py's comms do."""

    @pytest.mark.asyncio
    async def test_resume_all_skips_timestamp_injection(self, legion_system):
        await legion_system.legion_coordinator.resume_all("test-legion-456")

        send_mock = legion_system.session_coordinator.send_message
        send_mock.assert_called_once_with("test-minion-123", "continue", inject_timestamp=False)
