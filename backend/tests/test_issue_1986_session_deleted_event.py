"""
Tests for issue #1986: session "deleted" state change never emits and logs an
exception on every delete.

`delete_session()` used to call `_notify_state_change(session_id, "deleted")` —
a bare string passed where a `SessionState` enum member was required, which
raised `AttributeError` inside `_notify_state_change()`'s `new_state.value` and
was swallowed by its own broad `except Exception: logger.exception(...)`. Even
with a `SessionState.DELETED` member, `_on_state_change()`'s consumer re-fetches
`get_session_info()`, which is always `None` by the time delete's notify call
runs (the session is already removed from session_manager) — so the broadcast
would still silently no-op.

This file covers the replacement: a dedicated `_session_deleted_callbacks` /
`_notify_session_deleted()` pair mirroring the existing `session_reset` pattern
exactly, wired at the coordinator level so it fires uniformly for HTTP delete,
cascaded children, and minion disposal (which calls `delete_session()` directly,
bypassing the HTTP router).
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator


@pytest.fixture
async def temp_coordinator():
    with tempfile.TemporaryDirectory() as temp_dir:
        coordinator = SessionCoordinator(Path(temp_dir))
        await coordinator.initialize()
        yield coordinator
        await coordinator.cleanup()


@pytest.fixture
async def sample_session_config(temp_coordinator):
    coordinator = temp_coordinator
    project = await coordinator.project_manager.create_project(
        name="Test Project", working_directory="/test/project"
    )
    return {
        "session_id": str(uuid.uuid4()),
        "project_id": project.project_id,
        "config": SessionConfig(
            permission_mode="acceptEdits",
            system_prompt="Test system prompt",
            allowed_tools=["bash", "edit", "read"],
            model="claude-3-sonnet-20241022",
        ),
    }


class TestSessionDeletedCallbackRegistration:
    """add_session_deleted_callback() / _notify_session_deleted() basic wiring."""

    @pytest.mark.asyncio
    async def test_notify_session_deleted_calls_registered_async_callback(self, temp_coordinator):
        coordinator = temp_coordinator
        callback = AsyncMock()
        coordinator.add_session_deleted_callback(callback)

        await coordinator._notify_session_deleted("sess-1")

        callback.assert_awaited_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_notify_session_deleted_calls_registered_sync_callback(self, temp_coordinator):
        coordinator = temp_coordinator
        callback = Mock()
        coordinator.add_session_deleted_callback(callback)

        await coordinator._notify_session_deleted("sess-1")

        callback.assert_called_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_notify_session_deleted_no_callbacks_does_not_raise(self, temp_coordinator):
        coordinator = temp_coordinator
        await coordinator._notify_session_deleted("sess-1")  # must not raise

    @pytest.mark.asyncio
    async def test_a_raising_callback_does_not_propagate_or_block_others(self, temp_coordinator, caplog):
        coordinator = temp_coordinator
        failing = Mock(side_effect=RuntimeError("boom"))
        succeeding = AsyncMock()
        coordinator.add_session_deleted_callback(failing)
        coordinator.add_session_deleted_callback(succeeding)

        await coordinator._notify_session_deleted("sess-1")  # must not raise

        succeeding.assert_awaited_once_with("sess-1")


class TestDeleteSessionFiresSessionDeletedEvent:
    """delete_session() itself calls the new notify path, not the broken
    string-typed _notify_state_change() call, and never logs an exception."""

    @pytest.mark.asyncio
    async def test_delete_session_fires_callback_with_correct_session_id(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        received = []
        coordinator.add_session_deleted_callback(lambda sid: received.append(sid))

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        assert received == [session_id]

    @pytest.mark.asyncio
    async def test_delete_session_does_not_log_exception(
        self, temp_coordinator, sample_session_config, caplog
    ):
        """AC1/AC3: the fixed call path must never produce a traceback (regression
        test for the AttributeError previously swallowed by _notify_state_change())."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        with caplog.at_level("ERROR"):
            result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        assert not any(record.levelname == "ERROR" for record in caplog.records)
        assert not any(record.exc_info for record in caplog.records)

    @pytest.mark.asyncio
    async def test_cascading_delete_fires_once_per_child(
        self, temp_coordinator, sample_session_config
    ):
        """Cascading deletion is recursive self-calls to delete_session(), so each
        cascaded child must independently fire its own session_deleted event."""
        coordinator = temp_coordinator

        parent_id = await coordinator.create_session(**sample_session_config)

        child_config = dict(sample_session_config)
        child_config["session_id"] = str(uuid.uuid4())
        child_config["parent_overseer_id"] = parent_id
        child_id = await coordinator.create_session(**child_config)

        parent_info = await coordinator.session_manager.get_session_info(parent_id)
        parent_info.child_minion_ids = [child_id]

        received = []
        coordinator.add_session_deleted_callback(lambda sid: received.append(sid))

        result = await coordinator.delete_session(parent_id)

        assert result["success"] is True
        assert child_id in result["deleted_session_ids"]
        assert sorted(received) == sorted([parent_id, child_id])
