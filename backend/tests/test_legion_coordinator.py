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


def _make_project_manager(snapshot=None):
    """Mock ProjectManager backing the fleet snapshot (issue #2159 stage 6c)."""
    pm = Mock()
    pm.get_fleet_snapshot = AsyncMock(
        return_value=snapshot or {"stopped_session_ids": [], "processing_session_ids": []}
    )
    pm.record_fleet_stopped = AsyncMock(return_value=Mock())
    pm.clear_fleet_stopped = AsyncMock(return_value=Mock())
    return pm


@pytest.fixture
def legion_system():
    """Create a mock LegionSystem with one TERMINATED, snapshot-tracked minion
    for resume_all tests."""
    from backend.session_manager import SessionInfo, SessionState

    terminated_minion = Mock(spec=SessionInfo)
    terminated_minion.session_id = "test-minion-123"
    terminated_minion.project_id = "test-legion-456"
    terminated_minion.state = SessionState.TERMINATED

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock(return_value=True)
    mock_session_coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "test-queue-id"})
    mock_session_coordinator.start_session = AsyncMock(return_value=True)
    mock_session_coordinator._permission_callback_factory = None
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=terminated_minion)
    mock_session_coordinator.session_manager.list_sessions = AsyncMock(return_value=[terminated_minion])
    mock_session_coordinator.project_manager = _make_project_manager(
        {"stopped_session_ids": ["test-minion-123"], "processing_session_ids": ["test-minion-123"]}
    )
    mock_session_coordinator.data_dir = Path("/tmp/test")

    return LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock(),
    )


class TestIssue1779ResumeAllTimestampScoping:
    """resume_all()'s bulk "continue"-equivalent nudge is system-generated, not a
    user message — it must opt out of timestamp injection the same way
    comm_router.py's comms do."""

    @pytest.mark.asyncio
    async def test_resume_all_skips_timestamp_injection(self, legion_system):
        await legion_system.legion_coordinator.resume_all("test-legion-456")

        enqueue_mock = legion_system.session_coordinator.enqueue_message
        enqueue_mock.assert_called_once()
        args, kwargs = enqueue_mock.call_args
        assert args[0] == "test-minion-123"
        assert kwargs["inject_timestamp"] is False
        assert kwargs["reset_session"] is False


class TestIssue2159ResumeAllUsesSnapshot:
    """resume_all() used to raw-string-compare `minion.state.value == "active"` and
    skip PAUSED minions outright, or (post-6b) scan every is_deliverable minion in
    the project regardless of whether Stop All ever touched it. Issue #2159 stage
    6c: it must resume exactly the persisted fleet snapshot — nothing more, nothing
    less — distinguishing a catch-up message (was processing) from a bare cold
    start (wasn't), and clearing resolved entries from the snapshot."""

    @staticmethod
    def _minion(session_id: str, project_id: str, state) -> Mock:
        from backend.session_manager import SessionInfo

        m = Mock(spec=SessionInfo)
        m.session_id = session_id
        m.project_id = project_id
        m.state = state
        return m

    @pytest.mark.asyncio
    async def test_resume_all_sends_catchup_message_for_processing_entry(self):
        from backend.session_manager import SessionState

        minion = self._minion("was-processing", "legion-1", SessionState.TERMINATED)

        mock_session_coordinator = Mock()
        mock_session_coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q1"})
        mock_session_coordinator.session_manager = Mock()
        mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=minion)
        mock_session_coordinator.project_manager = _make_project_manager(
            {"stopped_session_ids": ["was-processing"], "processing_session_ids": ["was-processing"]}
        )
        mock_session_coordinator.data_dir = Path("/tmp/test")

        system = LegionSystem(
            session_coordinator=mock_session_coordinator,
            data_storage_manager=Mock(),
            template_manager=Mock(),
        )

        result = await system.legion_coordinator.resume_all("legion-1")

        mock_session_coordinator.enqueue_message.assert_called_once()
        mock_session_coordinator.project_manager.clear_fleet_stopped.assert_awaited_once_with(
            "legion-1", ["was-processing"]
        )
        assert result["resumed_count"] == 1
        assert result["failed_minions"] == []

    @pytest.mark.asyncio
    async def test_resume_all_cold_starts_non_processing_entry(self):
        from backend.session_manager import SessionState

        minion = self._minion("was-idle", "legion-1", SessionState.TERMINATED)

        mock_session_coordinator = Mock()
        mock_session_coordinator.start_session = AsyncMock(return_value=True)
        mock_session_coordinator._permission_callback_factory = None
        mock_session_coordinator.session_manager = Mock()
        mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=minion)
        mock_session_coordinator.project_manager = _make_project_manager(
            {"stopped_session_ids": ["was-idle"], "processing_session_ids": []}
        )
        mock_session_coordinator.data_dir = Path("/tmp/test")

        system = LegionSystem(
            session_coordinator=mock_session_coordinator,
            data_storage_manager=Mock(),
            template_manager=Mock(),
        )

        result = await system.legion_coordinator.resume_all("legion-1")

        mock_session_coordinator.start_session.assert_called_once_with("was-idle", None)
        assert result["resumed_count"] == 1

    @pytest.mark.asyncio
    async def test_resume_all_skips_entry_not_in_snapshot(self):
        """A deliverable minion that Stop All never touched must not be resumed —
        the old project-wide is_deliverable scan would have included it."""
        from backend.session_manager import SessionState

        untouched_minion = self._minion("never-stopped", "legion-1", SessionState.PAUSED)

        mock_session_coordinator = Mock()
        mock_session_coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q1"})
        mock_session_coordinator.start_session = AsyncMock(return_value=True)
        mock_session_coordinator.session_manager = Mock()
        mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=untouched_minion)
        mock_session_coordinator.session_manager.list_sessions = AsyncMock(return_value=[untouched_minion])
        mock_session_coordinator.project_manager = _make_project_manager()  # empty snapshot
        mock_session_coordinator.data_dir = Path("/tmp/test")

        system = LegionSystem(
            session_coordinator=mock_session_coordinator,
            data_storage_manager=Mock(),
            template_manager=Mock(),
        )

        result = await system.legion_coordinator.resume_all("legion-1")

        mock_session_coordinator.enqueue_message.assert_not_called()
        mock_session_coordinator.start_session.assert_not_called()
        assert result["resumed_count"] == 0
        assert result["total_minions"] == 0

    @pytest.mark.asyncio
    async def test_resume_all_clears_snapshot_entry_already_active(self):
        """A snapshot entry already brought back some other way (e.g. started
        individually) is skipped but still cleared from the stale snapshot."""
        from backend.session_manager import SessionState

        already_active = self._minion("already-active", "legion-1", SessionState.ACTIVE)

        mock_session_coordinator = Mock()
        mock_session_coordinator.enqueue_message = AsyncMock(return_value={"queue_id": "q1"})
        mock_session_coordinator.start_session = AsyncMock(return_value=True)
        mock_session_coordinator.session_manager = Mock()
        mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=already_active)
        mock_session_coordinator.project_manager = _make_project_manager(
            {"stopped_session_ids": ["already-active"], "processing_session_ids": []}
        )
        mock_session_coordinator.data_dir = Path("/tmp/test")

        system = LegionSystem(
            session_coordinator=mock_session_coordinator,
            data_storage_manager=Mock(),
            template_manager=Mock(),
        )

        result = await system.legion_coordinator.resume_all("legion-1")

        mock_session_coordinator.enqueue_message.assert_not_called()
        mock_session_coordinator.start_session.assert_not_called()
        mock_session_coordinator.project_manager.clear_fleet_stopped.assert_awaited_once_with(
            "legion-1", ["already-active"]
        )
        assert result["resumed_count"] == 1


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
            s.is_processing = False
            sessions.append(s)

        async def fake_terminate_session(session_id):
            await asyncio.sleep(per_session_delay)
            return True

        mock_session_coordinator = Mock()
        mock_session_coordinator.terminate_session = AsyncMock(side_effect=fake_terminate_session)
        mock_session_coordinator.project_manager = _make_project_manager()
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
