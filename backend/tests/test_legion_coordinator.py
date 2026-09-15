"""
Tests for LegionCoordinator (issue #1779 focus: resume_all timestamp-injection scoping;
issue #1933 focus: emergency_halt_all concurrency).
"""

import asyncio
import time
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


class TestIssue1933EmergencyHaltAllConcurrency:
    """emergency_halt_all() dispatches terminate_session() for every session via
    asyncio.gather(). Before issue #1933, each termination's blocking gc.collect()
    calls serialized these "concurrent" tasks onto the event loop anyway. This test
    proves the dispatch itself doesn't reintroduce serialization: N sessions whose
    termination does real async work (not blocking work) must complete in roughly the
    time of one termination, not N times that."""

    @pytest.fixture
    def legion_system_with_sessions(self):
        from backend.session_manager import SessionInfo, SessionState

        session_count = 25
        per_session_delay = 0.05

        sessions = []
        for i in range(session_count):
            s = Mock(spec=SessionInfo)
            s.session_id = f"minion-{i}"
            s.project_id = "test-legion-456"
            s.state = SessionState.ACTIVE
            sessions.append(s)

        async def fake_terminate_session(session_id):
            await asyncio.sleep(per_session_delay)
            return True

        mock_session_coordinator = Mock()
        mock_session_coordinator.terminate_session = AsyncMock(side_effect=fake_terminate_session)
        mock_session_coordinator.session_manager = Mock()
        mock_session_coordinator.session_manager.list_sessions = AsyncMock(return_value=sessions)
        mock_session_coordinator.data_dir = Path("/tmp/test")

        system = LegionSystem(
            session_coordinator=mock_session_coordinator,
            data_storage_manager=Mock(),
            template_manager=Mock(),
        )
        return system, session_count, per_session_delay

    @pytest.mark.asyncio
    async def test_emergency_halt_all_runs_concurrently(self, legion_system_with_sessions):
        system, session_count, per_session_delay = legion_system_with_sessions

        start = time.monotonic()
        result = await system.legion_coordinator.emergency_halt_all("test-legion-456")
        elapsed = time.monotonic() - start

        assert len(result["stopped_session_ids"]) == session_count
        # Serialized would take session_count * per_session_delay (~1.25s); concurrent
        # dispatch should stay close to a single per_session_delay with generous margin.
        assert elapsed < per_session_delay * (session_count / 4)
