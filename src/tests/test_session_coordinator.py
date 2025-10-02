"""Tests for session_coordinator module."""

import asyncio
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from ..session_coordinator import SessionCoordinator
from ..session_manager import SessionState


@pytest.fixture
async def temp_coordinator():
    """Create a temporary session coordinator for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        coordinator = SessionCoordinator(Path(temp_dir))
        await coordinator.initialize()
        yield coordinator
        await coordinator.cleanup()


@pytest.fixture
def sample_session_config():
    """Sample session configuration for testing."""
    return {
        "working_directory": "/test/project",
        "permission_mode": "acceptEdits",
        "system_prompt": "Test system prompt",
        "tools": ["bash", "edit", "read"],
        "model": "claude-3-sonnet-20241022"
    }


class TestSessionCoordinator:
    """Test SessionCoordinator functionality."""

    @pytest.mark.asyncio
    async def test_initialization(self, temp_coordinator):
        """Test session coordinator initialization."""
        coordinator = temp_coordinator

        assert coordinator.data_dir.exists()
        assert coordinator.session_manager is not None
        assert coordinator.message_parser is not None
        assert isinstance(coordinator._active_sdks, dict)
        assert isinstance(coordinator._storage_managers, dict)
        assert isinstance(coordinator._message_callbacks, dict)

    @pytest.mark.asyncio
    async def test_create_session_basic(self, temp_coordinator, sample_session_config):
        """Test basic session creation through coordinator."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        assert session_id is not None
        assert session_id in coordinator._active_sdks
        assert session_id in coordinator._storage_managers
        assert session_id in coordinator._message_callbacks
        assert session_id in coordinator._error_callbacks

        # Verify session exists in session manager
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info is not None
        assert session_info.state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_create_session_with_defaults(self, temp_coordinator):
        """Test session creation with default parameters."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(
            working_directory="/default/project"
        )

        assert session_id is not None

        # Verify SDK has default configuration
        sdk = coordinator._active_sdks[session_id]
        assert sdk.current_permission_mode == "acceptEdits"
        assert sdk.tools == ["bash", "edit", "read"]
        assert sdk.model is None  # No default model set

    @pytest.mark.asyncio
    async def test_start_session(self, temp_coordinator, sample_session_config):
        """Test starting a session through coordinator."""
        coordinator = temp_coordinator

        # Mock the SDK start method to avoid actual Claude Code SDK calls
        with patch.object(coordinator, '_active_sdks', {}):
            session_id = await coordinator.create_session(**sample_session_config)

            # Create a mock SDK
            mock_sdk = AsyncMock()
            mock_sdk.start.return_value = True
            coordinator._active_sdks[session_id] = mock_sdk

            # Start the session
            success = await coordinator.start_session(session_id)

            assert success is True
            mock_sdk.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_session_sdk_failure(self, temp_coordinator, sample_session_config):
        """Test starting session when SDK start fails."""
        coordinator = temp_coordinator

        with patch.object(coordinator, '_active_sdks', {}):
            session_id = await coordinator.create_session(**sample_session_config)

            # Create a mock SDK that fails to start
            mock_sdk = AsyncMock()
            mock_sdk.start.return_value = False
            coordinator._active_sdks[session_id] = mock_sdk

            success = await coordinator.start_session(session_id)

            assert success is False

    @pytest.mark.asyncio
    async def test_start_nonexistent_session(self, temp_coordinator):
        """Test starting a non-existent session."""
        coordinator = temp_coordinator

        success = await coordinator.start_session("nonexistent-session")
        assert success is False

    @pytest.mark.asyncio
    async def test_pause_session(self, temp_coordinator, sample_session_config):
        """Test pausing a session through coordinator."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Mock session manager to simulate successful pause
        with patch.object(coordinator.session_manager, 'pause_session', return_value=True):
            success = await coordinator.pause_session(session_id)
            assert success is True

    @pytest.mark.asyncio
    async def test_terminate_session(self, temp_coordinator, sample_session_config):
        """Test terminating a session through coordinator."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Mock SDK terminate method
        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        with patch.object(coordinator.session_manager, 'terminate_session', return_value=True):
            success = await coordinator.terminate_session(session_id)

            assert success is True
            mock_sdk.terminate.assert_called_once()

            # Verify cleanup
            assert session_id not in coordinator._active_sdks
            assert session_id not in coordinator._storage_managers
            assert session_id not in coordinator._message_callbacks
            assert session_id not in coordinator._error_callbacks

    @pytest.mark.asyncio
    async def test_send_message(self, temp_coordinator, sample_session_config):
        """Test sending message through coordinator."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Mock SDK send_message method
        mock_sdk = AsyncMock()
        mock_sdk.send_message.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        success = await coordinator.send_message(session_id, "Hello, Claude!")

        assert success is True
        mock_sdk.send_message.assert_called_once_with("Hello, Claude!")

    @pytest.mark.asyncio
    async def test_send_message_no_sdk(self, temp_coordinator):
        """Test sending message when no SDK exists."""
        coordinator = temp_coordinator

        success = await coordinator.send_message("nonexistent-session", "Hello!")
        assert success is False

    @pytest.mark.asyncio
    async def test_get_session_info(self, temp_coordinator, sample_session_config):
        """Test getting comprehensive session information."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Mock SDK and storage for info
        mock_sdk = MagicMock()
        mock_sdk.get_info.return_value = {"state": "running", "message_count": 5}
        mock_sdk.get_queue_size.return_value = 2
        mock_sdk.is_processing.return_value = False
        coordinator._active_sdks[session_id] = mock_sdk

        mock_storage = AsyncMock()
        mock_storage.get_message_count.return_value = 10
        mock_storage.detect_corruption.return_value = {"corrupted": False, "issues": []}
        coordinator._storage_managers[session_id] = mock_storage

        info = await coordinator.get_session_info(session_id)

        assert info is not None
        assert "session" in info
        assert "sdk" in info
        assert "storage" in info
        assert info["sdk"]["queue_size"] == 2
        assert info["storage"]["message_count"] == 10

    @pytest.mark.asyncio
    async def test_get_session_info_nonexistent(self, temp_coordinator):
        """Test getting info for non-existent session."""
        coordinator = temp_coordinator

        info = await coordinator.get_session_info("nonexistent-session")
        assert info is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, temp_coordinator, sample_session_config):
        """Test listing all sessions through coordinator."""
        coordinator = temp_coordinator

        # Create multiple sessions
        session_id_1 = await coordinator.create_session(**sample_session_config)
        session_id_2 = await coordinator.create_session(**sample_session_config)

        sessions = await coordinator.list_sessions()

        assert len(sessions) == 2
        session_ids = [s["session_id"] for s in sessions]
        assert session_id_1 in session_ids
        assert session_id_2 in session_ids

    @pytest.mark.asyncio
    async def test_get_session_messages(self, temp_coordinator, sample_session_config):
        """Test getting messages from a session."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Mock storage manager
        mock_storage = AsyncMock()
        mock_storage.read_messages.return_value = [
            {"type": "user", "content": "Hello"},
            {"type": "assistant", "content": "Hi there!"}
        ]
        coordinator._storage_managers[session_id] = mock_storage

        messages = await coordinator.get_session_messages(session_id, limit=10, offset=0)

        assert len(messages) == 2
        assert messages[0]["content"] == "Hello"
        mock_storage.read_messages.assert_called_once_with(limit=10, offset=0)

    @pytest.mark.asyncio
    async def test_get_session_messages_no_storage(self, temp_coordinator):
        """Test getting messages when no storage manager exists."""
        coordinator = temp_coordinator

        messages = await coordinator.get_session_messages("nonexistent-session")
        assert len(messages) == 0

    def test_add_message_callback(self, temp_coordinator):
        """Test adding message callback."""
        coordinator = temp_coordinator

        callback = Mock()
        session_id = "test-session"

        coordinator.add_message_callback(session_id, callback)

        assert session_id in coordinator._message_callbacks
        assert callback in coordinator._message_callbacks[session_id]

    def test_add_error_callback(self, temp_coordinator):
        """Test adding error callback."""
        coordinator = temp_coordinator

        callback = Mock()
        session_id = "test-session"

        coordinator.add_error_callback(session_id, callback)

        assert session_id in coordinator._error_callbacks
        assert callback in coordinator._error_callbacks[session_id]

    def test_add_state_change_callback(self, temp_coordinator):
        """Test adding state change callback."""
        coordinator = temp_coordinator

        callback = Mock()
        coordinator.add_state_change_callback(callback)

        assert callback in coordinator._state_change_callbacks

    @pytest.mark.asyncio
    async def test_message_callback_execution(self, temp_coordinator):
        """Test that message callbacks are executed properly."""
        coordinator = temp_coordinator

        session_id = "test-session"
        callback = AsyncMock()
        coordinator._message_callbacks[session_id] = [callback]

        # Create message callback and test it
        message_callback = coordinator._create_message_callback(session_id)

        test_message = {"type": "user", "content": "Test message"}
        await message_callback(test_message)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_callback_execution(self, temp_coordinator):
        """Test that error callbacks are executed properly."""
        coordinator = temp_coordinator

        session_id = "test-session"
        callback = AsyncMock()
        coordinator._error_callbacks[session_id] = [callback]

        # Create error callback and test it
        error_callback = coordinator._create_error_callback(session_id)

        test_error = Exception("Test error")
        await error_callback("test_error_type", test_error)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_state_change_notification(self, temp_coordinator):
        """Test state change notifications."""
        coordinator = temp_coordinator

        callback = AsyncMock()
        coordinator.add_state_change_callback(callback)

        session_id = "test-session"
        new_state = SessionState.ACTIVE

        await coordinator._notify_state_change(session_id, new_state)

        callback.assert_called_once()
        call_args = callback.call_args[0][0]
        assert call_args["session_id"] == session_id
        assert call_args["new_state"] == new_state.value

    @pytest.mark.asyncio
    async def test_callback_error_handling(self, temp_coordinator):
        """Test that callback errors are handled gracefully."""
        coordinator = temp_coordinator

        session_id = "test-session"

        # Create a callback that raises an exception
        def failing_callback(*args, **kwargs):
            raise Exception("Callback error")

        coordinator._message_callbacks[session_id] = [failing_callback]

        # Create and execute message callback - should not raise exception
        message_callback = coordinator._create_message_callback(session_id)
        test_message = {"type": "user", "content": "Test"}

        # This should not raise an exception despite the failing callback
        await message_callback(test_message)

    @pytest.mark.asyncio
    async def test_cleanup_all_sessions(self, temp_coordinator, sample_session_config):
        """Test cleanup of all sessions."""
        coordinator = temp_coordinator

        # Create multiple sessions
        session_id_1 = await coordinator.create_session(**sample_session_config)
        session_id_2 = await coordinator.create_session(**sample_session_config)

        # Mock SDK terminate methods
        mock_sdk_1 = AsyncMock()
        mock_sdk_2 = AsyncMock()
        coordinator._active_sdks[session_id_1] = mock_sdk_1
        coordinator._active_sdks[session_id_2] = mock_sdk_2

        with patch.object(coordinator.session_manager, 'terminate_session', return_value=True):
            await coordinator.cleanup()

            # Verify all SDKs were terminated
            mock_sdk_1.terminate.assert_called_once()
            mock_sdk_2.terminate.assert_called_once()

            # Verify cleanup
            assert len(coordinator._active_sdks) == 0

    @pytest.mark.asyncio
    async def test_concurrent_session_operations(self, temp_coordinator, sample_session_config):
        """Test concurrent session operations."""
        coordinator = temp_coordinator

        # Create session
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock SDK methods
        mock_sdk = AsyncMock()
        mock_sdk.send_message = AsyncMock(return_value=True)
        mock_sdk.get_info = MagicMock(return_value={"state": "running", "message_count": 0})
        mock_sdk.get_queue_size = MagicMock(return_value=0)
        mock_sdk.is_processing = MagicMock(return_value=False)
        coordinator._active_sdks[session_id] = mock_sdk

        # Mock storage manager as well for get_session_info call
        mock_storage = AsyncMock()
        mock_storage.get_message_count.return_value = 0
        mock_storage.detect_corruption.return_value = {"corrupted": False, "issues": []}
        coordinator._storage_managers[session_id] = mock_storage

        # Run concurrent operations
        tasks = [
            coordinator.send_message(session_id, "Message 1"),
            coordinator.send_message(session_id, "Message 2"),
            coordinator.get_session_info(session_id)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify no exceptions occurred
        for result in results:
            assert not isinstance(result, Exception)

    @pytest.mark.asyncio
    async def test_integration_with_session_manager(self, temp_coordinator, sample_session_config):
        """Test integration with session manager."""
        coordinator = temp_coordinator

        # Create session through coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Verify session exists in session manager
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info is not None
        assert session_info.session_id == session_id
        assert session_info.working_directory == sample_session_config["working_directory"]

        # Verify session directory was created
        session_dir = await coordinator.session_manager.get_session_directory(session_id)
        assert session_dir is not None
        assert session_dir.exists()

    @pytest.mark.asyncio
    async def test_storage_manager_integration(self, temp_coordinator, sample_session_config):
        """Test integration with data storage managers."""
        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Verify storage manager was created and initialized
        storage_manager = coordinator._storage_managers[session_id]
        assert storage_manager is not None
        assert storage_manager.session_dir.exists()
        assert storage_manager.messages_file.exists()