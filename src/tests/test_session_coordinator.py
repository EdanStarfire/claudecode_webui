"""Tests for session_coordinator module."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from ..session_config import SessionConfig
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
async def sample_session_config(temp_coordinator):
    """Sample session configuration for testing with project setup."""
    import uuid
    coordinator = temp_coordinator

    # Create the project first (returns ProjectInfo with generated project_id)
    project = await coordinator.project_manager.create_project(
        name="Test Project",
        working_directory="/test/project"
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
        import uuid
        coordinator = temp_coordinator

        # Create a test project first
        project = await coordinator.project_manager.create_project(
            name="Test Project",
            working_directory="/default/project"
        )

        session_id = str(uuid.uuid4())
        returned_session_id = await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(),
        )

        assert returned_session_id == session_id

        # Verify session has default configuration
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.current_permission_mode == "acceptEdits"
        assert session_info.allowed_tools == []  # Default allowed_tools is empty list
        assert session_info.model is None  # No default model set

    @pytest.mark.asyncio
    async def test_issue_709_auto_memory_mode_preserved_on_create(self, temp_coordinator):
        """Test that auto_memory_mode set at creation is persisted to SessionInfo.

        Regression test: SessionCoordinator.create_session() builds an intermediate
        SessionConfig copy (sm_config). If auto_memory_mode is omitted from that
        copy, the field silently defaults to 'claude' regardless of what the caller passed.
        """
        import uuid
        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project",
            working_directory="/test/project"
        )

        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(auto_memory_mode="session"),
        )

        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.auto_memory_mode == "session", (
            "auto_memory_mode='session' must survive create_session config copy"
        )

    @pytest.mark.asyncio
    async def test_start_session(self, temp_coordinator, sample_session_config):
        """Test starting a session through coordinator."""
        coordinator = temp_coordinator

        # Create session first
        session_id = await coordinator.create_session(**sample_session_config)

        # Use SDK factory injection (issue #559) to avoid actual SDK calls
        mock_sdk_instance = AsyncMock()
        mock_sdk_instance.start.return_value = True
        mock_sdk_instance.is_running.return_value = False
        mock_factory = Mock(return_value=mock_sdk_instance)
        coordinator.set_sdk_factory(mock_factory)

        # Start the session
        success = await coordinator.start_session(session_id)

        assert success is True
        mock_sdk_instance.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_session_sdk_failure(self, temp_coordinator, sample_session_config):
        """Test starting session when SDK start fails."""
        coordinator = temp_coordinator

        # Create session first
        session_id = await coordinator.create_session(**sample_session_config)

        # Use SDK factory injection (issue #559) with failing start
        mock_sdk_instance = AsyncMock()
        mock_sdk_instance.start.return_value = False
        mock_sdk_instance.is_running.return_value = False
        # Add info attribute with error_message for error handling
        mock_sdk_instance.info = Mock()
        mock_sdk_instance.info.error_message = "Test SDK start failure"
        mock_factory = Mock(return_value=mock_sdk_instance)
        coordinator.set_sdk_factory(mock_factory)

        success = await coordinator.start_session(session_id)

        assert success is False

    @pytest.mark.asyncio
    async def test_start_nonexistent_session(self, temp_coordinator):
        """Test starting a non-existent session."""
        coordinator = temp_coordinator

        success = await coordinator.start_session("nonexistent-session")
        assert success is False

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
        from ..session_manager import SessionState

        coordinator = temp_coordinator

        session_id = await coordinator.create_session(**sample_session_config)

        # Set session to ACTIVE state (normally done by SDK when ready)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        # Mock SDK send_message method
        mock_sdk = AsyncMock()
        mock_sdk.send_message.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        success = await coordinator.send_message(session_id, "Hello, Claude!")

        assert success is True
        mock_sdk.send_message.assert_called_once_with("Hello, Claude!", metadata=None)

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
        import uuid
        coordinator = temp_coordinator

        # Create multiple sessions with unique IDs
        config1 = {**sample_session_config, "session_id": str(uuid.uuid4())}
        config2 = {**sample_session_config, "session_id": str(uuid.uuid4())}

        session_id_1 = await coordinator.create_session(**config1)
        session_id_2 = await coordinator.create_session(**config2)

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
            {"type": "user", "content": "Hello", "metadata": {}},
            {"type": "assistant", "content": "Hi there!", "metadata": {}}
        ]
        mock_storage.get_message_count.return_value = 2
        coordinator._storage_managers[session_id] = mock_storage

        result = await coordinator.get_session_messages(session_id, limit=10, offset=0)

        # API now returns dict with pagination metadata
        assert isinstance(result, dict)
        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][0]["content"] == "Hello"
        assert result["total_count"] == 2
        mock_storage.read_messages.assert_called_once_with(limit=10, offset=0)

    @pytest.mark.asyncio
    async def test_get_session_messages_no_storage(self, temp_coordinator):
        """Test getting messages when no storage manager exists."""
        coordinator = temp_coordinator

        result = await coordinator.get_session_messages("nonexistent-session")

        # API returns dict with empty messages when no storage
        assert isinstance(result, dict)
        assert "messages" in result
        assert len(result["messages"]) == 0
        assert result["total_count"] == 0

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
        import uuid
        coordinator = temp_coordinator

        # Create multiple sessions with unique IDs
        config1 = {**sample_session_config, "session_id": str(uuid.uuid4())}
        config2 = {**sample_session_config, "session_id": str(uuid.uuid4())}

        session_id_1 = await coordinator.create_session(**config1)
        session_id_2 = await coordinator.create_session(**config2)

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
        assert session_info.project_id == sample_session_config["project_id"]

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


class TestDeleteSessionScheduleCancellation:
    """Tests for schedule cancellation when deleting sessions (Issue #671)."""

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_cancels_schedules(self, temp_coordinator, sample_session_config):
        """Deleting a session with active schedules cancels them."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock the legion system with scheduler_service
        mock_scheduler = AsyncMock()
        mock_scheduler.cancel_schedules_for_minion = AsyncMock(return_value=2)
        mock_legion = MagicMock()
        mock_legion.scheduler_service = mock_scheduler
        mock_legion.legion_coordinator = MagicMock()
        mock_legion.legion_coordinator.unregister_minion_capabilities = MagicMock()
        mock_legion.archive_manager = AsyncMock()
        mock_legion.archive_manager.archive_minion = AsyncMock(
            return_value=MagicMock(success=True, archive_path="/tmp/archive")
        )
        coordinator.legion_system = mock_legion

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        mock_scheduler.cancel_schedules_for_minion.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_no_schedules(self, temp_coordinator, sample_session_config):
        """Deleting a session with no schedules completes without error."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock legion system where cancel returns 0 (no schedules)
        mock_scheduler = AsyncMock()
        mock_scheduler.cancel_schedules_for_minion = AsyncMock(return_value=0)
        mock_legion = MagicMock()
        mock_legion.scheduler_service = mock_scheduler
        mock_legion.legion_coordinator = MagicMock()
        mock_legion.legion_coordinator.unregister_minion_capabilities = MagicMock()
        mock_legion.archive_manager = AsyncMock()
        mock_legion.archive_manager.archive_minion = AsyncMock(
            return_value=MagicMock(success=True, archive_path="/tmp/archive")
        )
        coordinator.legion_system = mock_legion

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        mock_scheduler.cancel_schedules_for_minion.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_schedule_error_non_blocking(self, temp_coordinator, sample_session_config):
        """Schedule cancellation failure does not block session deletion."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock legion system where cancel raises an exception
        mock_scheduler = AsyncMock()
        mock_scheduler.cancel_schedules_for_minion = AsyncMock(
            side_effect=RuntimeError("Scheduler unavailable")
        )
        mock_legion = MagicMock()
        mock_legion.scheduler_service = mock_scheduler
        mock_legion.legion_coordinator = MagicMock()
        mock_legion.legion_coordinator.unregister_minion_capabilities = MagicMock()
        mock_legion.archive_manager = AsyncMock()
        mock_legion.archive_manager.archive_minion = AsyncMock(
            return_value=MagicMock(success=True, archive_path="/tmp/archive")
        )
        coordinator.legion_system = mock_legion

        result = await coordinator.delete_session(session_id)

        # Deletion should still succeed despite scheduler error
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_no_legion_system(self, temp_coordinator, sample_session_config):
        """Deleting a session without legion system skips schedule cancellation."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Ensure no legion system
        coordinator.legion_system = None

        result = await coordinator.delete_session(session_id)

        assert result["success"] is True


class TestIssue811IsProcessingStuck:
    """Regression tests for issue #811 — is_processing stuck true when agent is idle."""

    @pytest.mark.asyncio
    async def test_issue_811_start_session_resets_pending_results_counter(
        self, temp_coordinator, sample_session_config
    ):
        """start_session() must zero _pending_results so a stale counter from a
        previous run cannot leave is_processing permanently stuck true."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Simulate a stale counter left over from a previous session run.
        coordinator._pending_results[session_id] = 3

        mock_sdk = AsyncMock()
        mock_sdk.start.return_value = True
        mock_sdk.is_running.return_value = False
        coordinator.set_sdk_factory(Mock(return_value=mock_sdk))

        await coordinator.start_session(session_id)

        assert coordinator._pending_results.get(session_id) == 0, (
            "start_session() must reset _pending_results to 0 to prevent is_processing getting stuck"
        )

    @pytest.mark.asyncio
    async def test_issue_811_send_message_exception_decrements_pending_results(
        self, temp_coordinator, sample_session_config
    ):
        """When send_message() raises an exception after incrementing the counter,
        the except block must decrement it so is_processing can clear on next result."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        mock_sdk = AsyncMock()
        mock_sdk.send_message.side_effect = RuntimeError("SDK blew up")
        coordinator._active_sdks[session_id] = mock_sdk

        # Counter starts at 0.
        assert coordinator._pending_results.get(session_id, 0) == 0

        result = await coordinator.send_message(session_id, "Hello!")

        assert result is False
        # Counter must be back to 0 — not left at 1.
        assert coordinator._pending_results.get(session_id, 0) == 0, (
            "Exception in send_message() must decrement _pending_results so is_processing can clear"
        )


class TestIssue819DockerHistoryMount:
    """Regression tests for issue #819 — history/ dir must be mounted in Docker sessions."""

    @pytest.mark.asyncio
    async def test_issue_819_history_in_extra_mounts(self, temp_coordinator):
        """start_session() must include history/ in Docker extra mounts (read-only)."""
        import uuid

        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project",
            working_directory="/test/project",
        )

        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(docker_enabled=True, docker_image="claude-code:local"),
        )

        captured_mounts = []

        def fake_resolve(docker_image, docker_extra_mounts, workspace, session_data_dir, docker_home_directory):
            captured_mounts.extend(docker_extra_mounts or [])
            return "/usr/bin/docker", {}

        mock_sdk = AsyncMock()
        mock_sdk.start.return_value = True
        mock_sdk.is_running.return_value = False
        coordinator.set_sdk_factory(Mock(return_value=mock_sdk))

        with patch("src.docker_utils.resolve_docker_cli_path", fake_resolve):
            with patch("src.skill_manager.NEW_GLOBAL_SKILLS_DIR") as mock_skills_dir:
                mock_skills_dir.exists.return_value = False
                await coordinator.start_session(session_id)

        history_mounts = [m for m in captured_mounts if "/history" in m]
        assert history_mounts, (
            f"history/ must appear in Docker extra_mounts. Got: {captured_mounts}"
        )
        assert any(m.endswith(":ro") for m in history_mounts), (
            "history/ must be mounted read-only (:ro)"
        )
