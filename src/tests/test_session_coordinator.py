"""Tests for session_coordinator module."""

import asyncio
import tempfile
from datetime import UTC
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
        # CONFIG_FIELDS now live in session.config (issue #1230)
        assert session_info.config.get("allowed_tools") is None or session_info.config.get("allowed_tools") == []
        assert session_info.config.get("model") is None

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
        assert session_info.config.get("auto_memory_mode") == "session", (
            "auto_memory_mode='session' must survive create_session config copy"
        )

    @pytest.mark.asyncio
    async def test_issue_1401_session_mode_memory_dir_and_sdk_config(self, temp_coordinator):
        """Issue #1401: session mode creates memory/ dir, skips agent-guidance.md, wires auto_memory_directory."""
        import uuid
        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/test/project"
        )
        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(auto_memory_mode="session"),
        )

        mock_sdk_instance = AsyncMock()
        mock_sdk_instance.start.return_value = True
        mock_sdk_instance.is_running.return_value = False
        mock_factory = Mock(return_value=mock_sdk_instance)
        coordinator.set_sdk_factory(mock_factory)

        await coordinator.start_session(session_id)

        session_dir = coordinator.data_dir / "sessions" / session_id
        assert (session_dir / "memory").exists(), "memory/ dir must be created for session mode"
        assert not (session_dir / "memory" / "agent-guidance.md").exists(), (
            "agent-guidance.md must not be created (removed in issue #1401)"
        )

        call_kwargs = mock_factory.call_args.kwargs
        sdk_config = call_kwargs["config"]
        assert sdk_config.auto_memory_directory == str(session_dir / "memory"), (
            "auto_memory_directory must be forced to {session_dir}/memory in session mode"
        )
        assert "Agent Guidance" not in (sdk_config.system_prompt or ""), (
            "system prompt must not contain guidance-file vocabulary"
        )
        assert "guidance file" not in (sdk_config.system_prompt or ""), (
            "system prompt must not reference guidance files"
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

        result = await coordinator.list_sessions()

        assert result["total"] == 2
        session_ids = [s["session_id"] for s in result["sessions"]]
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

    @pytest.mark.asyncio
    async def test_issue_1019_reset_errored_session_succeeds(self, temp_coordinator, sample_session_config):
        """reset_session() on an ERROR-state session (no active SDK) transitions to TERMINATED then starts fresh."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Force session into ERROR state (simulates a failed start)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ERROR, "simulated error")
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.state == SessionState.ERROR

        # No active SDK (as happens with errored sessions)
        assert session_id not in coordinator._active_sdks

        # Mock start_session to succeed without actually launching SDK
        with patch.object(coordinator, "start_session", return_value=True) as mock_start:
            success = await coordinator.reset_session(session_id)

        assert success is True
        mock_start.assert_called_once_with(session_id, None)


class TestDeleteSessionScheduleCancellation:
    """Tests for schedule deletion when deleting sessions (Issue #671)."""

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_deletes_schedules(self, temp_coordinator, sample_session_config):
        """Deleting a session with active schedules deletes them."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock the legion system with scheduler_service
        mock_scheduler = AsyncMock()
        mock_scheduler.delete_schedules_for_minion = AsyncMock(return_value=2)
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
        mock_scheduler.delete_schedules_for_minion.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_no_schedules(self, temp_coordinator, sample_session_config):
        """Deleting a session with no schedules completes without error."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock legion system where delete returns 0 (no schedules)
        mock_scheduler = AsyncMock()
        mock_scheduler.delete_schedules_for_minion = AsyncMock(return_value=0)
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
        mock_scheduler.delete_schedules_for_minion.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    async def test_issue_671_delete_session_schedule_error_non_blocking(self, temp_coordinator, sample_session_config):
        """Schedule deletion failure does not block session deletion."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Mock legion system where delete raises an exception
        mock_scheduler = AsyncMock()
        mock_scheduler.delete_schedules_for_minion = AsyncMock(
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


class TestIssue1375SecretRefs:
    """Tests for ${secret:<name>} substitution in MCP server header values (issue #1375)."""

    # ── _substitute_secret_refs unit tests ────────────────────────────────────

    def test_issue_1375_resolves_correctly(self, temp_coordinator):
        """Placeholder generated by _generate_secret_placeholders matches substituted header."""
        coordinator = temp_coordinator
        name_to_placeholder = {"jira_basic_mcp_token": "CC_SECRET_jira_basic_mcp_token_abcd1234"}
        result = coordinator._substitute_secret_refs(
            {"Authorization": "Basic ${secret:jira_basic_mcp_token}"},
            name_to_placeholder,
            "jira-server",
        )
        assert result["Authorization"] == "Basic CC_SECRET_jira_basic_mcp_token_abcd1234"

    def test_issue_1375_embedded_in_longer_value(self, temp_coordinator):
        """Prefix text is preserved; only the ${secret:...} token is replaced."""
        coordinator = temp_coordinator
        name_to_placeholder = {"api_key": "CC_SECRET_api_key_deadbeef"}
        result = coordinator._substitute_secret_refs(
            {"Authorization": "Bearer ${secret:api_key}"},
            name_to_placeholder,
            "api-server",
        )
        assert result["Authorization"] == "Bearer CC_SECRET_api_key_deadbeef"

    def test_issue_1375_no_reference_unchanged(self, temp_coordinator):
        """Headers without ${secret:...} references pass through unchanged."""
        coordinator = temp_coordinator
        headers = {"X-Static": "hello", "Accept": "application/json"}
        result = coordinator._substitute_secret_refs(headers, {}, "test-server")
        assert result == headers

    def test_issue_1375_referenced_secret_not_assigned(self, temp_coordinator):
        """ValueError is raised when a referenced secret is not in name_to_placeholder."""
        coordinator = temp_coordinator
        with pytest.raises(ValueError, match="missing"):
            coordinator._substitute_secret_refs(
                {"Authorization": "${secret:missing}"},
                {},
                "test-server",
            )

    def test_issue_1375_multiple_references_in_multiple_headers(self, temp_coordinator):
        """Two headers each referencing a different secret are substituted independently."""
        coordinator = temp_coordinator
        name_to_placeholder = {
            "token_a": "CC_SECRET_token_a_11111111",
            "token_b": "CC_SECRET_token_b_22222222",
        }
        result = coordinator._substitute_secret_refs(
            {
                "X-Api-Key": "${secret:token_a}",
                "X-Org-Key": "${secret:token_b}",
            },
            name_to_placeholder,
            "multi-server",
        )
        assert result["X-Api-Key"] == "CC_SECRET_token_a_11111111"
        assert result["X-Org-Key"] == "CC_SECRET_token_b_22222222"

    # ── start_session integration test ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_issue_1375_fail_closed_when_proxy_disabled(self, temp_coordinator):
        """start_session returns False when secret refs exist but docker_proxy_enabled=False."""
        import uuid

        from ..mcp_config_manager import McpServerType
        from ..session_config import SessionConfig

        coordinator = temp_coordinator
        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/test"
        )
        fake_mcp_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                mcp_server_ids=[fake_mcp_id],
                docker_proxy_enabled=False,
            ),
        )

        mock_cfg = MagicMock()
        mock_cfg.slug = "test-mcp"
        mock_cfg.name = "Test MCP"
        mock_cfg.type = McpServerType.HTTP
        mock_cfg.headers = {"Authorization": "${secret:foo}"}
        mock_cfg.oauth_enabled = False
        coordinator.mcp_config_manager.get_configs_by_ids = MagicMock(return_value=[mock_cfg])

        result = await coordinator.start_session(session_id)

        assert result is False
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.state == SessionState.ERROR
        assert "docker_proxy_enabled" in (session_info.error_message or "")

    # ── _get_mcp_sdk_config tests ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_issue_1375_stdio_server_unchanged(self, temp_coordinator):
        """STDIO server env values with ${secret:...} are NOT substituted (out of scope)."""
        from ..mcp_config_manager import McpServerType

        coordinator = temp_coordinator
        mock_cfg = MagicMock()
        mock_cfg.type = McpServerType.STDIO
        mock_cfg.oauth_enabled = False
        mock_cfg.to_sdk_config.return_value = {
            "type": "stdio",
            "command": "mcp-server",
            "env": {"MY_VAR": "${secret:foo}"},
        }

        name_to_placeholder = {"foo": "CC_SECRET_foo_deadbeef"}
        result = await coordinator._get_mcp_sdk_config(mock_cfg, name_to_placeholder)

        assert result["env"]["MY_VAR"] == "${secret:foo}"

    @pytest.mark.asyncio
    async def test_issue_1375_oauth_and_secret_ref_interaction(self, temp_coordinator):
        """OAuth-enabled server: Authorization set by OAuth; other headers get refs substituted."""
        from ..mcp_config_manager import McpServerType

        coordinator = temp_coordinator
        mock_cfg = MagicMock()
        mock_cfg.id = "mcp-oauth-id"
        mock_cfg.name = "oauth-server"
        mock_cfg.type = McpServerType.HTTP
        mock_cfg.oauth_enabled = True
        mock_cfg.to_sdk_config.return_value = {
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {
                "Authorization": "${secret:foo}",
                "X-Custom": "${secret:bar}",
            },
        }

        mock_token = MagicMock()
        mock_token.access_token = "oauth-bearer-token"
        coordinator.oauth_manager.get_stored_token = AsyncMock(return_value=mock_token)
        mock_store = MagicMock()
        mock_store.get_token_expiry = AsyncMock(return_value=None)
        coordinator.oauth_manager.get_token_store = MagicMock(return_value=mock_store)

        name_to_placeholder = {
            "foo": "CC_SECRET_foo_11111111",
            "bar": "CC_SECRET_bar_22222222",
        }
        result = await coordinator._get_mcp_sdk_config(mock_cfg, name_to_placeholder)

        assert result["headers"]["Authorization"] == "Bearer oauth-bearer-token"
        assert result["headers"]["X-Custom"] == "CC_SECRET_bar_22222222"

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
    async def test_start_session_resets_processing_state(
        self, temp_coordinator, sample_session_config
    ):
        """start_session() must reset is_processing to False so a stale True from a
        previous run cannot leave the session appearing busy."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Simulate stale processing state from a previous run.
        await coordinator.session_manager.update_processing_state(session_id, True)

        mock_sdk = AsyncMock()
        mock_sdk.start.return_value = True
        mock_sdk.is_running.return_value = False
        coordinator.set_sdk_factory(Mock(return_value=mock_sdk))

        await coordinator.start_session(session_id)

        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False, (
            "start_session() must reset is_processing to False"
        )

    @pytest.mark.asyncio
    async def test_send_message_exception_resets_processing_state(
        self, temp_coordinator, sample_session_config
    ):
        """When send_message() raises an exception, is_processing must be reset
        to False so the session does not appear permanently busy."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        mock_sdk = AsyncMock()
        mock_sdk.send_message.side_effect = RuntimeError("SDK blew up")
        coordinator._active_sdks[session_id] = mock_sdk

        result = await coordinator.send_message(session_id, "Hello!")

        assert result is False
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False, (
            "Exception in send_message() must reset is_processing to False"
        )


class TestIssue1002IsProcessingStuckDuringProcessing:
    """Regression tests for issue #1002 / #1029 — is_processing state management."""

    @pytest.mark.asyncio
    async def test_error_callback_resets_processing_state(
        self, temp_coordinator, sample_session_config
    ):
        """Error callback must reset is_processing to False."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        await coordinator.session_manager.update_processing_state(session_id, True)

        # Fire the error callback (as SDK would on client.query() failure)
        error_cb = coordinator._create_error_callback(session_id)
        await error_cb("message_processing_error", RuntimeError("query failed"))

        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False

    @pytest.mark.asyncio
    async def test_interrupt_success_resets_processing_state(
        self, temp_coordinator, sample_session_config
    ):
        """interrupt_success must reset is_processing to False."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        await coordinator.session_manager.update_processing_state(session_id, True)

        # Simulate interrupt_success message arriving via the message callback
        callback = coordinator._create_message_callback(session_id)
        await callback({
            "type": "system",
            "content": "Session interrupted successfully",
            "subtype": "interrupt_success",
            "session_id": session_id,
            "timestamp": 1234567890.0,
        })

        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False

    @pytest.mark.asyncio
    async def test_pivot_interrupt_then_send_processing_state(
        self, temp_coordinator, sample_session_config
    ):
        """PIVOT: after interrupt clears is_processing, a new send_message sets it True."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        await coordinator.session_manager.update_processing_state(session_id, True)

        # Simulate interrupt_success — is_processing should become False
        callback = coordinator._create_message_callback(session_id)
        await callback({
            "type": "system",
            "content": "Session interrupted successfully",
            "subtype": "interrupt_success",
            "session_id": session_id,
            "timestamp": 1234567890.0,
        })

        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False

        # PIVOT sends new message — is_processing must be True again
        mock_sdk = AsyncMock()
        mock_sdk.send_message.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        result = await coordinator.send_message(session_id, "New direction")

        assert result is True
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is True

    @pytest.mark.asyncio
    async def test_issue_1029_assistant_message_sets_processing_true(
        self, temp_coordinator, sample_session_config
    ):
        """Issue #1029: After a result sets is_processing False, an incoming assistant
        message must set it back to True (self-healing for folded-message scenario)."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager.update_session_state(session_id, SessionState.ACTIVE)

        callback = coordinator._create_message_callback(session_id)

        # Simulate result arriving — is_processing goes False
        await callback({
            "type": "result",
            "content": "",
            "session_id": session_id,
            "timestamp": 1234567890.0,
        })
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False

        # Simulate assistant message arriving for a second (folded) query
        await callback({
            "type": "assistant",
            "content": "Here is the answer...",
            "session_id": session_id,
            "timestamp": 1234567891.0,
        })
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is True, (
            "assistant message must set is_processing True (self-heals folded-message case)"
        )

        # Simulate final result — is_processing clears again
        await callback({
            "type": "result",
            "content": "",
            "session_id": session_id,
            "timestamp": 1234567892.0,
        })
        session_info = await coordinator.session_manager.get_session_info(session_id)
        assert session_info.is_processing is False


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

        def fake_resolve(docker_image, docker_extra_mounts, workspace, session_data_dir, docker_home_directory, **kwargs):
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


class TestIssue820TmpMount:
    """Tests for session-specific /tmp mount (Issue #820)."""

    @pytest.mark.asyncio
    async def test_issue_820_terminate_session_cleans_tmp_dir(self, temp_coordinator, sample_session_config):
        """terminate_session() removes the {session_dir}/tmp/ directory when it exists."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        # Create the tmp directory and a file inside it
        session_dir = coordinator.session_manager.sessions_dir / session_id
        tmp_dir = session_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        (tmp_dir / "test.txt").write_text("hello")

        assert tmp_dir.exists()

        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        with patch.object(coordinator.session_manager, "terminate_session", return_value=True):
            await coordinator.terminate_session(session_id)

        # tmp directory must be removed
        assert not tmp_dir.exists()

    @pytest.mark.asyncio
    async def test_issue_820_terminate_session_no_tmp_dir_is_safe(self, temp_coordinator, sample_session_config):
        """terminate_session() succeeds even when {session_dir}/tmp/ does not exist."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        session_dir = coordinator.session_manager.sessions_dir / session_id
        tmp_dir = session_dir / "tmp"
        assert not tmp_dir.exists()

        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        with patch.object(coordinator.session_manager, "terminate_session", return_value=True):
            success = await coordinator.terminate_session(session_id)

        assert success is True


class TestIssue1088CredFilePermissions:
    """Regression tests for issue #1088 (updated for #1134 inline delivery).

    Issue #1134 removes file-based credentials.json / delivery_envs.json entirely.
    Secrets are now delivered as CLAUDE_DOCKER_DELIVERY_ENVS (inline JSON) and the
    placeholder→name map is persisted in session_info.secret_placeholders.
    """

    @pytest.mark.asyncio
    async def test_issue_1088_creds_path_is_world_readable(self, temp_coordinator):
        """Issue #1134: credentials.json and delivery_envs.json must NOT be written.

        Instead, session_info.secret_placeholders must contain an entry mapping
        the generated CC_SECRET_* placeholder to the secret name 'github-token'.
        """
        import uuid
        from datetime import datetime
        from unittest.mock import patch as _patch

        from ..models.secret_record import SecretRecord, SecretType

        coordinator = temp_coordinator

        # Register credential in the vault using new SecretsVault API (issue #827)
        now = datetime.now(UTC)
        record = SecretRecord(
            name="github-token",
            type=SecretType.API_KEY,
            target_hosts=["api.github.com"],
            inject_env="GH_TOKEN",
            created_at=now,
            updated_at=now,
        )
        with (
            _patch("src.credential_vault.set_secret_value"),
            _patch("src.credential_vault.get_secret_value", return_value="ghp_real"),
        ):
            await coordinator.credential_vault.create_secret(record, "ghp_real")

        project = await coordinator.project_manager.create_project(
            name="Test Project",
            working_directory="/test/project",
        )

        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(
                docker_enabled=True,
                docker_image="claude-code:local",
                docker_proxy_enabled=True,
                assigned_secrets=["github-token"],
            ),
        )

        # Locate the session tmp dir
        session_dir = coordinator.session_manager.sessions_dir / session_id
        tmp_dir = session_dir / "tmp"

        mock_sdk = AsyncMock()
        mock_sdk.start.return_value = True
        mock_sdk.is_running.return_value = False
        coordinator.set_sdk_factory(Mock(return_value=mock_sdk))

        with (
            _patch("src.credential_vault.get_secret_value", return_value="ghp_real"),
            _patch("src.docker_utils.resolve_docker_cli_path", return_value=("/usr/bin/docker", {})),
            _patch("src.skill_manager.NEW_GLOBAL_SKILLS_DIR") as mock_skills_dir,
        ):
            mock_skills_dir.exists.return_value = False
            await coordinator.start_session(session_id)

        # Issue #1134: file-based delivery is removed — these files must NOT exist
        assert not (tmp_dir / "credentials.json").exists(), (
            "credentials.json must NOT be written (removed in #1134)"
        )
        assert not (tmp_dir / "delivery_envs.json").exists(), (
            "delivery_envs.json must NOT be written (removed in #1134)"
        )

        # Instead, secret_placeholders must map the CC_SECRET_* placeholder to the name
        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.secret_placeholders, "secret_placeholders must be populated at session start"
        assert "github-token" in info.secret_placeholders.values(), (
            "secret_placeholders must contain 'github-token' as a value"
        )


@pytest.mark.asyncio
class TestIssue1115SessionConfigStorage:
    """Regression tests for issue #1115 / #1230 — CONFIG_FIELDS stored in session.config dict.

    Previously tested _init_session_overrides; now tests equivalent behavior in the
    unified config dict model: all CONFIG_FIELDS from SessionConfig go into session.config.
    """

    async def test_issue_1115_none_values_not_stored(self):
        """None CONFIG_FIELDS from SessionConfig must not be stored in session.config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir))
            await coordinator.initialize()

            project = await coordinator.project_manager.create_project(
                name="Test Project", working_directory="/tmp/test"
            )
            import uuid
            session_id = str(uuid.uuid4())
            # SessionConfig with all-None optional fields (user set nothing explicitly)
            config = SessionConfig()

            await coordinator.create_session(
                session_id=session_id,
                project_id=project.project_id,
                config=config,
            )

            session_info = await coordinator.session_manager.get_session_info(session_id)

            # Fields that default to None in SessionConfig must not appear in config
            for field_name in ("model", "thinking_mode", "docker_image",
                               "disallowed_tools", "setting_sources"):
                assert session_info.config.get(field_name) is None, (
                    f"Field '{field_name}' with None value should not be set in config"
                )

            await coordinator.cleanup()

    async def test_issue_1115_explicit_value_stored_in_config(self):
        """An explicit non-None CONFIG_FIELD value must be stored in session.config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir))
            await coordinator.initialize()

            project = await coordinator.project_manager.create_project(
                name="Test Project", working_directory="/tmp/test"
            )
            import uuid
            session_id = str(uuid.uuid4())
            config = SessionConfig(model="claude-opus-4-5")

            await coordinator.create_session(
                session_id=session_id,
                project_id=project.project_id,
                config=config,
            )

            session_info = await coordinator.session_manager.get_session_info(session_id)
            assert session_info.config.get("model") == "claude-opus-4-5", (
                "Explicit model value must be stored in session.config"
            )

            await coordinator.cleanup()

    async def test_issue_1115_empty_list_stored_in_config(self):
        """An explicit empty list [] must be stored in session.config."""
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir))
            await coordinator.initialize()

            project = await coordinator.project_manager.create_project(
                name="Test Project", working_directory="/tmp/test"
            )
            import uuid
            session_id = str(uuid.uuid4())
            config = SessionConfig(allowed_tools=[])

            await coordinator.create_session(
                session_id=session_id,
                project_id=project.project_id,
                config=config,
            )

            session_info = await coordinator.session_manager.get_session_info(session_id)
            assert "allowed_tools" in session_info.config, (
                "Explicit empty list must be stored in session.config"
            )
            assert session_info.config["allowed_tools"] == [], (
                "Empty list must be stored as [] not None"
            )

            await coordinator.cleanup()


class TestIssue1396GhMountRemoved:
    """Regression test for issue #1396 — ~/.config/gh must not be bind-mounted."""

    @pytest.mark.asyncio
    async def test_no_gh_config_mount_in_docker_session(self, temp_coordinator):
        """start_session() must not include any ~/.config/gh mount for Docker sessions."""
        import uuid

        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/tmp/test_gh"
        )
        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(docker_enabled=True, docker_image="claude-code:local"),
        )

        captured_mounts = []

        def fake_resolve(docker_image, docker_extra_mounts, workspace, session_data_dir, docker_home_directory, **kwargs):
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

        gh_mounts = [m for m in captured_mounts if ".config/gh" in m]
        assert not gh_mounts, (
            f"~/.config/gh must not be bind-mounted in Docker sessions (issue #1396). "
            f"Found: {gh_mounts}"
        )


class TestIssue1402ListSessionsEffectiveConfig:
    """Tests for issue #1402 — list_sessions must include effective_config for template-linked sessions."""

    @pytest.mark.asyncio
    async def test_list_sessions_includes_effective_config_for_template_linked(self, temp_coordinator):
        """Template-linked sessions in list response carry effective_config with resolved flags."""
        import uuid

        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/tmp/test_1402"
        )

        # Create a template with docker_enabled=True
        template = await coordinator.template_manager.create_template(
            name="Docker Template",
            config=SessionConfig(docker_enabled=True, permission_mode="bypassPermissions"),
        )

        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(template_id=template.template_id),
        )

        result = await coordinator.list_sessions()

        session_entries = {s["session_id"]: s for s in result["sessions"]}
        assert session_id in session_entries
        entry = session_entries[session_id]
        assert "effective_config" in entry, "template-linked session must have effective_config key"
        assert entry["effective_config"] is not None
        assert entry["effective_config"]["docker_enabled"] is True

    @pytest.mark.asyncio
    async def test_list_sessions_omits_effective_config_for_standalone(self, temp_coordinator):
        """Standalone sessions (no template) must not have an effective_config key."""
        import uuid

        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/tmp/test_1402_standalone"
        )

        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(),
        )

        result = await coordinator.list_sessions()

        session_entries = {s["session_id"]: s for s in result["sessions"]}
        assert session_id in session_entries
        entry = session_entries[session_id]
        assert "effective_config" not in entry, (
            "standalone session must not have an effective_config key in list response"
        )

    @pytest.mark.asyncio
    async def test_list_sessions_handles_resolution_failure(self, temp_coordinator):
        """When effective_config resolution fails for one session, that entry gets effective_config=None
        and other sessions in the list are still returned."""
        import uuid

        coordinator = temp_coordinator

        project = await coordinator.project_manager.create_project(
            name="Test Project", working_directory="/tmp/test_1402_failure"
        )

        template = await coordinator.template_manager.create_template(
            name="Docker Template 2",
            config=SessionConfig(docker_enabled=True, permission_mode="bypassPermissions"),
        )

        session_id_bad = str(uuid.uuid4())
        session_id_good = str(uuid.uuid4())

        await coordinator.create_session(
            session_id=session_id_bad,
            project_id=project.project_id,
            config=SessionConfig(template_id=template.template_id),
        )
        await coordinator.create_session(
            session_id=session_id_good,
            project_id=project.project_id,
            config=SessionConfig(),
        )

        # Patch resolve_effective_config to raise for all calls (simulates resolution failure).
        # _build_effective_config_payload catches the exception and returns (None, None),
        # so list_sessions must still return all sessions with effective_config=None for the
        # template-linked one.
        with patch("src.session_coordinator.resolve_effective_config", side_effect=RuntimeError("simulated")):
            result = await coordinator.list_sessions()

        assert result["total"] == 2
        session_entries = {s["session_id"]: s for s in result["sessions"]}

        # bad session: effective_config=None (failure handled gracefully)
        assert session_id_bad in session_entries
        assert session_entries[session_id_bad].get("effective_config") is None

        # good session (standalone): still returned normally, no effective_config key
        assert session_id_good in session_entries
        assert "effective_config" not in session_entries[session_id_good]
