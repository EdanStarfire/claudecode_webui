"""Tests for Claude Code SDK wrapper."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import tempfile
from pathlib import Path

from ..claude_sdk import ClaudeSDK, SessionState, SessionInfo
from ..logging_config import setup_logging

# Set up logging for tests
setup_logging(log_level="DEBUG", enable_console=True)


class TestClaudeSDK:
    """Test cases for ClaudeSDK class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield temp_dir

    @pytest.fixture
    def session_id(self):
        """Generate a test session ID."""
        return "test-session-12345"

    @pytest.fixture
    def sdk_instance(self, temp_dir, session_id):
        """Create a ClaudeSDK instance for testing."""
        return ClaudeSDK(
            session_id=session_id,
            working_directory=temp_dir,
            initial_prompt="Hello, test!"
        )

    def test_initialization(self, sdk_instance, session_id, temp_dir):
        """Test SDK wrapper initialization."""
        assert sdk_instance.session_id == session_id
        assert str(sdk_instance.working_directory) == temp_dir
        assert sdk_instance.initial_prompt == "Hello, test!"
        assert sdk_instance.info.state == SessionState.CREATED

    def test_session_info(self, sdk_instance):
        """Test session info retrieval."""
        info = sdk_instance.get_info()
        assert info["session_id"] == sdk_instance.session_id
        assert info["state"] == SessionState.CREATED.value
        assert "working_directory" in info

    @pytest.mark.asyncio
    async def test_start_success(self, sdk_instance):
        """Test successful SDK session start."""
        # Should use simulation mode for testing
        success = await sdk_instance.start()

        assert success is True
        assert sdk_instance.info.state == SessionState.RUNNING
        assert sdk_instance.info.start_time is not None

    @pytest.mark.asyncio
    async def test_start_nonexistent_directory(self, session_id):
        """Test SDK session start with nonexistent directory."""
        sdk_instance = ClaudeSDK(
            session_id=session_id,
            working_directory="/nonexistent/directory"
        )

        success = await sdk_instance.start()

        assert success is False
        assert sdk_instance.info.state == SessionState.FAILED
        assert sdk_instance.info.error_message is not None

    @pytest.mark.asyncio
    async def test_message_callbacks(self, temp_dir, session_id):
        """Test message and error callbacks."""
        messages_received = []
        errors_received = []

        def message_callback(message):
            messages_received.append(message)

        def error_callback(error_type, exception):
            errors_received.append((error_type, exception))

        sdk_instance = ClaudeSDK(
            session_id=session_id,
            working_directory=temp_dir,
            message_callback=message_callback,
            error_callback=error_callback
        )

        await sdk_instance.start()
        await sdk_instance.send_message("Test message")

        # Should have received simulated messages
        assert len(messages_received) > 0
        assert any(msg.get("type") == "system" for msg in messages_received)

    @pytest.mark.asyncio
    async def test_send_message_not_ready(self, sdk_instance):
        """Test sending message when session not ready."""
        # Don't start the session
        success = await sdk_instance.send_message("Test message")

        assert success is False

    @pytest.mark.asyncio
    async def test_send_message_success(self, sdk_instance):
        """Test successful message sending."""
        await sdk_instance.start()

        success = await sdk_instance.send_message("Test message")

        assert success is True
        assert sdk_instance.info.message_count == 1
        assert sdk_instance.info.last_activity is not None

    @pytest.mark.asyncio
    async def test_terminate(self, sdk_instance):
        """Test SDK session termination."""
        await sdk_instance.start()

        success = await sdk_instance.terminate()

        assert success is True
        assert sdk_instance.info.state == SessionState.TERMINATED

    def test_is_running_states(self, sdk_instance):
        """Test is_running method with different states."""
        # Initially not running
        assert sdk_instance.is_running() is False

        # Set to running state
        sdk_instance.info.state = SessionState.RUNNING
        assert sdk_instance.is_running() is True

        # Set to processing state
        sdk_instance.info.state = SessionState.PROCESSING
        assert sdk_instance.is_running() is True

        # Set to completed state
        sdk_instance.info.state = SessionState.COMPLETED
        assert sdk_instance.is_running() is False

    def test_convert_sdk_message_dict(self, sdk_instance):
        """Test SDK message conversion with dict-like object."""
        mock_message = {
            "type": "assistant",
            "content": "Test response"
        }

        converted = sdk_instance._convert_sdk_message(mock_message)

        assert converted["type"] == "assistant"
        assert converted["content"] == "Test response"
        assert "timestamp" in converted
        assert converted["session_id"] == sdk_instance.session_id

    def test_convert_sdk_message_object(self, sdk_instance):
        """Test SDK message conversion with SDK message object."""
        from claude_code_sdk import SystemMessage

        # Create a proper SystemMessage instance with correct parameters
        mock_message = SystemMessage(subtype="test", data={"message": "Test system message"})

        converted = sdk_instance._convert_sdk_message(mock_message)

        assert converted["type"] == "system"
        assert converted["data"]["message"] == "Test system message"
        assert "timestamp" in converted
        assert converted["session_id"] == sdk_instance.session_id

    def test_convert_sdk_message_error(self, sdk_instance):
        """Test SDK message conversion with unknown object."""
        # Use an object that will be treated as unknown (but not cause an exception)
        mock_message = object()

        converted = sdk_instance._convert_sdk_message(mock_message)

        # Should handle object() by converting to string content and marking as unknown
        assert converted["type"] == "unknown"
        assert "content" in converted
        assert converted["content"] == str(mock_message)
        assert converted["session_id"] == sdk_instance.session_id
        assert "timestamp" in converted
        # No conversion_error since this is handled gracefully, not as an exception



class TestSessionInfo:
    """Test cases for SessionInfo dataclass."""

    def test_session_info_creation(self):
        """Test SessionInfo creation and default values."""
        info = SessionInfo(
            session_id="test-123",
            working_directory="/test/dir"
        )

        assert info.session_id == "test-123"
        assert info.working_directory == "/test/dir"
        assert info.state == SessionState.CREATED
        assert info.start_time is None
        assert info.message_count == 0


class TestSessionState:
    """Test cases for SessionState enum."""

    def test_session_states(self):
        """Test SessionState enum values."""
        assert SessionState.CREATED.value == "created"
        assert SessionState.RUNNING.value == "running"
        assert SessionState.COMPLETED.value == "completed"
        assert SessionState.FAILED.value == "failed"
        assert SessionState.TERMINATED.value == "terminated"