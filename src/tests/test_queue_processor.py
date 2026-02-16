"""Tests for QueueProcessor (issue #500)."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..queue_manager import QueueManager
from ..queue_processor import QueueProcessor
from ..session_manager import SessionInfo, SessionState


def _make_session_info(
    session_id="s1",
    state=SessionState.ACTIVE,
    is_processing=False,
    queue_paused=False,
    queue_config=None,
):
    """Build a minimal SessionInfo-like object for tests."""
    info = MagicMock(spec=SessionInfo)
    info.session_id = session_id
    info.state = state
    info.is_processing = is_processing
    info.queue_paused = queue_paused
    info.queue_config = queue_config
    return info


def _make_coordinator(session_info, session_dir):
    """Build a mock SessionCoordinator with wired QueueManager."""
    coord = MagicMock()
    coord.session_manager = AsyncMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=session_info)
    coord.session_manager.get_session_directory = AsyncMock(return_value=session_dir)
    coord.send_message = AsyncMock(return_value=True)
    coord.start_session = AsyncMock(return_value=True)
    coord.reset_session = AsyncMock(return_value=True)
    coord._permission_callback_factory = None
    coord.queue_manager = QueueManager()
    return coord


class TestQueueProcessorLifecycle:
    """Lifecycle: ensure_running, stop, is_running."""

    def test_not_running_initially(self):
        coord = MagicMock()
        coord.queue_manager = QueueManager()
        proc = QueueProcessor(coord)
        assert proc.is_running("s1") is False

    @pytest.mark.asyncio
    async def test_ensure_running_starts_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info()
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)

            # Need at least one pending item so the loop doesn't exit immediately
            await coord.queue_manager.enqueue("s1", sdir, "test")

            # Pause so the loop doesn't actually send anything
            info.queue_paused = True

            proc.ensure_running("s1")
            assert proc.is_running("s1") is True

            proc.stop("s1")
            await asyncio.sleep(0.1)
            assert proc.is_running("s1") is False

    @pytest.mark.asyncio
    async def test_ensure_running_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info(queue_paused=True)
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "test")

            proc.ensure_running("s1")
            task1 = proc._tasks.get("s1")
            proc.ensure_running("s1")
            task2 = proc._tasks.get("s1")
            assert task1 is task2  # Same task, not replaced

            proc.stop("s1")

    @pytest.mark.asyncio
    async def test_stop_nonexistent_is_safe(self):
        coord = MagicMock()
        coord.queue_manager = QueueManager()
        proc = QueueProcessor(coord)
        proc.stop("nonexistent")  # Should not raise


class TestQueueProcessorPause:
    """Pause behavior."""

    @pytest.mark.asyncio
    async def test_paused_queue_does_not_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info(queue_paused=True)
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "paused msg")

            proc.ensure_running("s1")
            await asyncio.sleep(0.3)
            proc.stop("s1")

            coord.send_message.assert_not_called()


class TestQueueProcessorErrorHalt:
    """Error state halts processing."""

    @pytest.mark.asyncio
    async def test_error_state_halts_processor(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info(state=SessionState.ERROR)
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "error msg")

            proc.ensure_running("s1")
            await asyncio.sleep(0.3)

            # Processor should have exited
            assert proc.is_running("s1") is False
            coord.send_message.assert_not_called()


class TestQueueProcessorDelivery:
    """Message delivery flow."""

    @pytest.mark.asyncio
    async def test_delivers_message_when_active_and_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            # Start active, not processing
            info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
            coord = _make_coordinator(info, sdir)

            # Use very short timing for tests
            info.queue_config = {"min_wait_seconds": 0, "min_idle_seconds": 0}

            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "deliver me", reset_session=False)

            proc.ensure_running("s1")
            # Wait for delivery cycle
            await asyncio.sleep(1.5)
            proc.stop("s1")

            coord.send_message.assert_called_once_with("s1", "deliver me")

    @pytest.mark.asyncio
    async def test_auto_starts_terminated_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            # First call returns TERMINATED, subsequent calls return ACTIVE
            call_count = 0

            async def dynamic_info(sid):
                nonlocal call_count
                call_count += 1
                if call_count <= 1:
                    return _make_session_info(
                        state=SessionState.TERMINATED,
                        queue_config={"min_wait_seconds": 0, "min_idle_seconds": 0},
                    )
                return _make_session_info(
                    state=SessionState.ACTIVE,
                    is_processing=False,
                    queue_config={"min_wait_seconds": 0, "min_idle_seconds": 0},
                )

            coord = _make_coordinator(
                _make_session_info(state=SessionState.TERMINATED),
                sdir,
            )
            coord.session_manager.get_session_info = AsyncMock(side_effect=dynamic_info)

            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "auto-start msg", reset_session=False)

            proc.ensure_running("s1")
            await asyncio.sleep(2)
            proc.stop("s1")

            coord.start_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_session_called_when_flag_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info(
                state=SessionState.ACTIVE,
                is_processing=False,
                queue_config={"min_wait_seconds": 0, "min_idle_seconds": 0},
            )
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)
            await coord.queue_manager.enqueue("s1", sdir, "reset msg", reset_session=True)

            proc.ensure_running("s1")
            await asyncio.sleep(2)
            proc.stop("s1")

            coord.reset_session.assert_called_once()


class TestQueueProcessorBroadcast:
    """Broadcast callback."""

    @pytest.mark.asyncio
    async def test_broadcast_callback_invoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            info = _make_session_info(
                state=SessionState.ACTIVE,
                is_processing=False,
                queue_config={"min_wait_seconds": 0, "min_idle_seconds": 0},
            )
            coord = _make_coordinator(info, sdir)
            proc = QueueProcessor(coord)

            callback = AsyncMock()
            proc.set_broadcast_callback(callback)

            await coord.queue_manager.enqueue("s1", sdir, "bc msg", reset_session=False)

            proc.ensure_running("s1")
            await asyncio.sleep(2)
            proc.stop("s1")

            # Should have been called with "sent" action
            assert callback.called
            calls = [c for c in callback.call_args_list if c[0][1] == "sent"]
            assert len(calls) >= 1
