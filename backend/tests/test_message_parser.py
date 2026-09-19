"""Tests for SDK message parser."""

import time

import pytest
from claude_agent_sdk import TaskUpdatedMessage

from backend.message_parser import (
    AssistantMessageHandler,
    ErrorHandler,
    MessageParser,
    MessageProcessor,
    MessageType,
    ParsedMessage,
    ResultMessageHandler,
    SystemMessageHandler,
    TaskUpdatedHandler,
    ToolUseHandler,
    UnknownMessageHandler,
    UserMessageHandler,
)


class TestMessageHandlers:
    """Test cases for individual message handlers."""

    def test_system_message_handler(self):
        """Test SystemMessageHandler."""
        handler = SystemMessageHandler()

        message_data = {
            "type": "system",
            "subtype": "session_start",
            "session_id": "test-123",
            "cwd": "/test/dir",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.SYSTEM
        assert parsed.session_id == "test-123"
        assert "System session_start" in parsed.content
        assert parsed.metadata["subtype"] == "session_start"

    def test_assistant_message_handler(self):
        """Test AssistantMessageHandler."""
        handler = AssistantMessageHandler()

        message_data = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello, how can I help?"}],
                "model": "claude-3-sonnet-20241022"
            },
            "session_id": "test-123",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.ASSISTANT
        assert parsed.content == "Hello, how can I help?"
        assert parsed.metadata["model"] == "claude-3-sonnet-20241022"
        assert parsed.metadata["role"] == "assistant"

    def test_assistant_message_handler_string_content(self):
        """Test AssistantMessageHandler with string content."""
        handler = AssistantMessageHandler()

        message_data = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "Simple string content",
                "model": "claude-3-sonnet-20241022"
            },
            "session_id": "test-123",
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.ASSISTANT
        assert parsed.content == "Simple string content"

    def test_user_message_handler(self):
        """Test UserMessageHandler."""
        handler = UserMessageHandler()

        message_data = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Please help me"}]
            },
            "session_id": "test-123",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.USER
        assert parsed.content == "Please help me"
        assert parsed.metadata["role"] == "user"
        assert parsed.metadata["has_tool_results"] is False

    def test_user_message_handler_with_tool_results(self):
        """Test UserMessageHandler with tool results."""
        handler = UserMessageHandler()

        message_data = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-123", "content": "Result data"},
                    {"type": "text", "text": "Additional text"}
                ]
            },
            "session_id": "test-123",
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.USER
        assert "Additional text" in parsed.content
        assert parsed.metadata["has_tool_results"] is True
        assert len(parsed.metadata["tool_results"]) == 1
        assert parsed.metadata["tool_results"][0]["tool_use_id"] == "tool-123"

    def test_result_message_handler(self):
        """Test ResultMessageHandler."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "conversation_completed",
            "result": "Conversation finished successfully",
            "session_id": "test-123",
            "duration_ms": 1500,
            "num_turns": 3,
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.RESULT
        assert parsed.content == "Conversation finished successfully"
        assert parsed.metadata["subtype"] == "conversation_completed"
        assert parsed.metadata["duration_ms"] == 1500
        assert parsed.metadata["num_turns"] == 3
        assert parsed.metadata["errors"] is None

    def test_result_message_handler_with_errors(self):
        """Test ResultMessageHandler extracts errors field."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "conversation_completed",
            "result": "Done",
            "session_id": "test-123",
            "errors": ["Tool X failed: file not found", "API rate limit exceeded"],
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)
        assert parsed.metadata["errors"] == ["Tool X failed: file not found", "API rate limit exceeded"]

    def test_result_message_handler_with_empty_errors(self):
        """Test ResultMessageHandler handles empty errors list."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "conversation_completed",
            "result": "Done",
            "session_id": "test-123",
            "errors": [],
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)
        assert parsed.metadata["errors"] == []

    def test_result_message_handler_errors_none(self):
        """Test ResultMessageHandler handles missing errors field (backward compat)."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "conversation_completed",
            "result": "Done",
            "session_id": "test-123",
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)
        assert parsed.metadata["errors"] is None

    def test_result_message_handler_deferred_tool_use(self):
        """Test ResultMessageHandler extracts deferred_tool_use into metadata."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "deferred",
            "result": None,
            "session_id": "test-123",
            "timestamp": time.time(),
            "deferred_tool_use": {"id": "tool-1", "name": "bash", "input": {"command": "ls"}},
        }

        parsed = handler.parse(message_data)
        assert parsed.metadata["deferred_tool_use"] == {
            "id": "tool-1",
            "name": "bash",
            "input": {"command": "ls"},
        }

    def test_result_message_handler_no_deferred_tool_use(self):
        """Test ResultMessageHandler sets deferred_tool_use to None when absent."""
        handler = ResultMessageHandler()

        message_data = {
            "type": "result",
            "subtype": "conversation_completed",
            "result": "Done",
            "session_id": "test-123",
            "timestamp": time.time()
        }

        parsed = handler.parse(message_data)
        assert parsed.metadata["deferred_tool_use"] is None

    def test_tool_use_handler(self):
        """Test ToolUseHandler."""
        handler = ToolUseHandler()

        message_data = {
            "type": "tool_use",
            "tool_name": "bash",
            "input": {"command": "ls -la"},
            "id": "tool-456",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.TOOL_USE
        assert "Using tool: bash" in parsed.content
        assert parsed.metadata["tool_name"] == "bash"
        assert parsed.metadata["tool_input"] == {"command": "ls -la"}

    def test_error_handler(self):
        """Test ErrorHandler."""
        handler = ErrorHandler()

        message_data = {
            "type": "error",
            "message": "Something went wrong",
            "code": "E001",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.ERROR
        assert parsed.content == "Something went wrong"
        assert parsed.error_message == "Something went wrong"
        assert parsed.metadata["error_code"] == "E001"

    def test_unknown_message_handler(self):
        """Test UnknownMessageHandler."""
        handler = UnknownMessageHandler()

        message_data = {
            "type": "custom_type",
            "data": "some custom data",
            "timestamp": time.time()
        }

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.UNKNOWN
        assert parsed.metadata["original_type"] == "custom_type"
        assert parsed.metadata["unknown_format"] is True


class TestIssue1746TaskUpdatedHandler:
    """Live-path coverage for TaskUpdatedHandler (issue #1746).

    Before this handler existed, TaskUpdatedMessage fell through to
    SystemMessageHandler in the live message-parsing path — its subtype
    defaulted to "init" and task_id/status/patch were silently dropped from
    the live event stream, even though the storage-replay path (issue #1657)
    already handled it correctly.
    """

    def test_can_handle_sdk_message(self):
        handler = TaskUpdatedHandler()
        sdk_msg = TaskUpdatedMessage(
            subtype="task_updated", data={}, task_id="t1",
            patch={"status": "killed"}, status="killed",
            session_id="sub-sess", uuid="u1",
        )
        message_data = {"type": "system", "sdk_message": sdk_msg, "session_id": "sess-1"}
        assert handler.can_handle(message_data) is True

    def test_does_not_handle_other_task_messages(self):
        handler = TaskUpdatedHandler()
        message_data = {"type": "system", "metadata": {"subtype": "task_started"}}
        assert handler.can_handle(message_data) is False

    def test_extracts_fields_from_sdk_message(self):
        handler = TaskUpdatedHandler()
        sdk_msg = TaskUpdatedMessage(
            subtype="task_updated", data={}, task_id="task-abc",
            patch={"status": "completed", "end_time": 123}, status="completed",
            session_id="sub-sess-1", uuid="uuid-1",
        )
        message_data = {"type": "system", "sdk_message": sdk_msg, "session_id": "sess-1",
                         "timestamp": time.time()}

        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)

        assert parsed.type == MessageType.SYSTEM
        assert parsed.metadata["subtype"] == "task_updated"
        assert parsed.metadata["task_id"] == "task-abc"
        assert parsed.metadata["status"] == "completed"
        assert parsed.metadata["patch"] == {"status": "completed", "end_time": 123}
        assert parsed.metadata["uuid"] == "uuid-1"
        assert parsed.metadata["task_session_id"] == "sub-sess-1"

    def test_extracts_fields_from_stored_dict_fallback(self):
        """The reload/websocket-replay dict shape (no live sdk_message object)."""
        handler = TaskUpdatedHandler()
        message_data = {
            "type": "system",
            "session_id": "sess-1",
            "timestamp": time.time(),
            "metadata": {
                "subtype": "task_updated",
                "task_id": "task-xyz",
                "status": "failed",
                "patch": {"status": "failed"},
                "uuid": "uuid-2",
                "task_session_id": "sub-sess-2",
            },
        }
        assert handler.can_handle(message_data) is True
        parsed = handler.parse(message_data)
        assert parsed.metadata["task_id"] == "task-xyz"
        assert parsed.metadata["status"] == "failed"
        assert parsed.metadata["patch"] == {"status": "failed"}

    def test_full_parser_routes_to_task_updated_handler_not_system_default(self):
        """Regression guard: without this handler, MessageParser would route a
        TaskUpdatedMessage to SystemMessageHandler and lose task_id/status,
        defaulting subtype to "init"."""
        parser = MessageParser()
        sdk_msg = TaskUpdatedMessage(
            subtype="task_updated", data={}, task_id="task-live",
            patch={"status": "killed"}, status="killed",
            session_id="sub-sess", uuid="u1",
        )
        message_data = {"type": "system", "sdk_message": sdk_msg, "session_id": "sess-1",
                         "timestamp": time.time()}

        parsed = parser.parse_message(message_data)

        assert parsed.metadata["subtype"] == "task_updated"
        assert parsed.metadata["subtype"] != "init"
        assert parsed.metadata["task_id"] == "task-live"
        assert parsed.metadata["status"] == "killed"


class TestMessageParser:
    """Test cases for MessageParser class."""

    @pytest.fixture
    def parser(self):
        """Create a MessageParser instance for testing."""
        return MessageParser()

    def test_initialization(self, parser):
        """Test parser initialization."""
        assert len(parser.handlers) > 0
        assert isinstance(parser.handlers[-1], UnknownMessageHandler)
        assert parser.stats["total_parsed"] == 0

    def test_register_handler(self, parser):
        """Test handler registration."""
        initial_count = len(parser.handlers)

        # Create a custom handler
        class CustomHandler(SystemMessageHandler):
            def can_handle(self, message_data):
                return message_data.get("type") == "custom"

        custom_handler = CustomHandler()
        parser.register_handler(custom_handler)

        # Should be inserted before UnknownMessageHandler
        assert len(parser.handlers) == initial_count + 1
        assert isinstance(parser.handlers[-1], UnknownMessageHandler)
        assert isinstance(parser.handlers[-2], CustomHandler)

    def test_parse_system_message(self, parser):
        """Test parsing system message."""
        message_data = {
            "type": "system",
            "subtype": "session_start",
            "session_id": "test-123",
            "timestamp": time.time()
        }

        parsed = parser.parse_message(message_data)

        assert parsed.type == MessageType.SYSTEM
        assert parser.stats["total_parsed"] == 1
        assert parser.stats["type_counts"]["system"] == 1

    def test_parse_assistant_message(self, parser):
        """Test parsing assistant message."""
        message_data = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}]
            },
            "timestamp": time.time()
        }

        parsed = parser.parse_message(message_data)

        assert parsed.type == MessageType.ASSISTANT
        assert "Hello!" in parsed.content

    def test_parse_unknown_message(self, parser):
        """Test parsing unknown message type."""
        message_data = {
            "type": "unknown_type",
            "data": "some data",
            "timestamp": time.time()
        }

        parsed = parser.parse_message(message_data)

        assert parsed.type == MessageType.UNKNOWN
        assert parser.stats["unknown_types"] == 1
        assert "unknown_type" in parser.unknown_types

    def test_parse_message_error_handling(self, parser):
        """Test error handling during parsing."""
        # Create a message that will cause parsing errors
        message_data = None

        parsed = parser.parse_message(message_data)

        assert parsed.type == MessageType.ERROR
        assert parser.stats["parse_errors"] == 1
        assert "Parse error" in parsed.content

    def test_get_stats(self, parser):
        """Test statistics retrieval."""
        # Parse a few messages
        parser.parse_message({"type": "system", "timestamp": time.time()})
        parser.parse_message({"type": "assistant", "message": {"content": "test"}, "timestamp": time.time()})
        parser.parse_message({"type": "unknown_type", "timestamp": time.time()})

        stats = parser.get_stats()

        assert stats["total_parsed"] == 3
        assert stats["unknown_types"] == 1
        assert "system" in stats["type_counts"]
        assert "assistant" in stats["type_counts"]
        assert len(stats["unknown_types_seen"]) == 1

    def test_reset_stats(self, parser):
        """Test statistics reset."""
        # Parse some messages
        parser.parse_message({"type": "system", "timestamp": time.time()})
        parser.parse_message({"type": "unknown_type", "timestamp": time.time()})

        assert parser.stats["total_parsed"] == 2

        # Reset stats
        parser.reset_stats()

        assert parser.stats["total_parsed"] == 0
        assert parser.stats["unknown_types"] == 0
        assert len(parser.unknown_types) == 0

    def test_get_unknown_types(self, parser):
        """Test unknown types tracking."""
        parser.parse_message({"type": "type1", "timestamp": time.time()})
        parser.parse_message({"type": "type2", "timestamp": time.time()})
        parser.parse_message({"type": "type1", "timestamp": time.time()})  # Duplicate

        unknown_types = parser.get_unknown_types()

        assert len(unknown_types) == 2
        assert "type1" in unknown_types
        assert "type2" in unknown_types


class TestMessageType:
    """Test cases for MessageType enum."""

    def test_message_type_values(self):
        """Test MessageType enum values."""
        assert MessageType.SYSTEM.value == "system"
        assert MessageType.ASSISTANT.value == "assistant"
        assert MessageType.USER.value == "user"
        assert MessageType.RESULT.value == "result"
        assert MessageType.UNKNOWN.value == "unknown"


class TestParsedMessage:
    """Test cases for ParsedMessage dataclass."""

    def test_parsed_message_creation(self):
        """Test ParsedMessage creation."""
        timestamp = time.time()
        parsed = ParsedMessage(
            type=MessageType.ASSISTANT,
            timestamp=timestamp,
            content="Test message",
            session_id="test-123"
        )

        assert parsed.type == MessageType.ASSISTANT
        assert parsed.timestamp == timestamp
        assert parsed.content == "Test message"

    def test_thinking_block_handler(self):
        """Test ThinkingBlockHandler."""
        from backend.message_parser import ThinkingBlockHandler

        handler = ThinkingBlockHandler()

        # Test can_handle for dict-based message
        thinking_message = {"type": "thinking", "content": "Let me think about this..."}
        assert handler.can_handle(thinking_message)

        # Test parsing
        parsed = handler.parse(thinking_message)
        assert parsed.type == MessageType.THINKING
        assert parsed.content == "Let me think about this..."
        assert parsed.metadata["thinking_content"] == "Let me think about this..."

    def test_tool_use_handler_dict(self):
        """Test ToolUseHandler with dict-based message."""
        from backend.message_parser import ToolUseHandler

        handler = ToolUseHandler()

        tool_message = {
            "type": "tool_use",
            "tool_name": "bash",
            "id": "tool_123",
            "input": {"command": "ls -la"}
        }
        assert handler.can_handle(tool_message)

        parsed = handler.parse(tool_message)
        assert parsed.type == MessageType.TOOL_USE
        assert parsed.content == "Using tool: bash"
        assert parsed.metadata["tool_name"] == "bash"
        assert parsed.metadata["tool_id"] == "tool_123"
        assert parsed.metadata["tool_input"] == {"command": "ls -la"}

    def test_tool_result_handler_dict(self):
        """Test ToolResultHandler with dict-based message."""
        from backend.message_parser import ToolResultHandler

        handler = ToolResultHandler()

        tool_result_message = {
            "type": "tool_result",
            "content": "Command output here",
            "tool_use_id": "tool_123",
            "is_error": False
        }
        assert handler.can_handle(tool_result_message)

        parsed = handler.parse(tool_result_message)
        assert parsed.type == MessageType.TOOL_RESULT
        assert parsed.content == "Command output here"
        assert parsed.metadata["tool_use_id"] == "tool_123"
        assert parsed.metadata["is_error"] is False
        assert parsed.error_message is None

    def test_tool_result_handler_with_error(self):
        """Test ToolResultHandler with error message."""
        from backend.message_parser import ToolResultHandler

        handler = ToolResultHandler()

        error_message = {
            "type": "tool_result",
            "content": "Command failed with error",
            "tool_use_id": "tool_123",
            "is_error": True,
            "session_id": "test-123"
        }

        parsed = handler.parse(error_message)
        assert parsed.type == MessageType.TOOL_RESULT
        assert parsed.content == "Command failed with error"
        assert parsed.error_message == "Command failed with error"
        assert parsed.metadata["is_error"] is True
        assert parsed.session_id == "test-123"


class TestHookMessageHandling:
    """Test cases for hook_started and hook_response system messages (Issue #571)."""

    def test_hook_started_dict_format(self):
        """Test hook_started message synthesizes content from dict format."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_started",
                "init_data": {
                    "hook_name": "pre-tool-guard",
                    "hook_event": "PreToolUse",
                    "hook_id": "hook-001",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.type == MessageType.SYSTEM
        assert parsed.metadata["subtype"] == "hook_started"
        assert "pre-tool-guard" in parsed.content
        assert "PreToolUse" in parsed.content

    def test_hook_response_dict_format_success(self):
        """Test hook_response message with exit_code 0."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_response",
                "init_data": {
                    "hook_name": "pre-tool-guard",
                    "hook_event": "PreToolUse",
                    "hook_id": "hook-001",
                    "outcome": "approved",
                    "exit_code": 0,
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata["subtype"] == "hook_response"
        assert "pre-tool-guard" in parsed.content
        assert "approved" in parsed.content
        assert parsed.metadata.get("exit_code") == 0

    def test_hook_response_dict_format_error(self):
        """Test hook_response message with non-zero exit_code."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_response",
                "init_data": {
                    "hook_name": "post-tool-logger",
                    "hook_event": "PostToolUse",
                    "hook_id": "hook-002",
                    "outcome": "failed",
                    "exit_code": 1,
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert "post-tool-logger" in parsed.content
        assert "failed" in parsed.content
        assert parsed.metadata.get("exit_code") == 1

    def test_hook_started_with_camelcase_fields(self):
        """Test hook_started handles camelCase field names (hookName, hookEvent)."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_started",
                "init_data": {
                    "hookName": "my-hook",
                    "hookEvent": "Stop",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert "my-hook" in parsed.content
        assert "Stop" in parsed.content

    def test_hook_response_uses_stdout_on_success(self):
        """Test hook_response prefers stdout over outcome string when exit_code == 0."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_response",
                "init_data": {
                    "hook_name": "my-hook",
                    "hook_event": "PreToolUse",
                    "hook_id": "hook-003",
                    "outcome": "success",
                    "exit_code": 0,
                    "stdout": "hook ran OK",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert "hook ran OK" in parsed.content
        assert "success" not in parsed.content

    def test_hook_response_uses_stderr_on_failure(self):
        """Test hook_response prefers stderr over outcome string when exit_code != 0."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_response",
                "init_data": {
                    "hook_name": "my-hook",
                    "hook_event": "PostToolUse",
                    "hook_id": "hook-004",
                    "outcome": "failed",
                    "exit_code": 2,
                    "stderr": "permission denied",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert "permission denied" in parsed.content
        assert "failed" not in parsed.content

    def test_hook_parser_integration(self):
        """Test hook messages through the full MessageParser pipeline."""
        parser = MessageParser()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_started",
                "init_data": {
                    "hook_name": "guard",
                    "hook_event": "PreToolUse",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = parser.parse_message(message_data)
        assert parsed.type == MessageType.SYSTEM
        assert "guard" in parsed.content
        assert "PreToolUse" in parsed.content


class TestAgentNotificationHandling:
    """Test cases for background subagent Notification hook events (Issue #1676)."""

    def test_notification_sdk_object_needs_input(self):
        """Notification hook (SDK object) with agent_needs_input is tagged and labeled."""
        from claude_agent_sdk.types import HookEventMessage

        handler = SystemMessageHandler()
        sdk_msg = HookEventMessage(
            subtype="hook_response",
            data={
                "session_id": "test-session",
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp",
                "hook_event_name": "Notification",
                "message": "alpha needs your input",
                "title": "Agent waiting",
                "notification_type": "agent_needs_input",
            },
            hook_event_name="Notification",
            session_id="test-session",
            uuid="uuid-1",
        )
        message_data = {
            "sdk_message": sdk_msg,
            # Issue #571/#1676: claude_sdk.py._convert_sdk_message() copies sdk_msg.subtype
            # to the top-level dict before it reaches SystemMessageHandler.
            "subtype": sdk_msg.subtype,
            "session_id": "test-session",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.type == MessageType.SYSTEM
        assert parsed.metadata["subtype"] == "agent_notification"
        assert parsed.metadata["notification_type"] == "agent_needs_input"
        assert parsed.metadata["label"] == "alpha"
        assert parsed.metadata["title"] == "Agent waiting"
        assert parsed.content == "alpha needs your input"

    def test_notification_sdk_object_completed(self):
        """Notification hook (SDK object) with agent_completed parses a 'finished' label."""
        from claude_agent_sdk.types import HookEventMessage

        handler = SystemMessageHandler()
        sdk_msg = HookEventMessage(
            subtype="hook_response",
            data={
                "session_id": "test-session",
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp",
                "hook_event_name": "Notification",
                "message": "beta finished",
                "notification_type": "agent_completed",
            },
            hook_event_name="Notification",
            session_id="test-session",
            uuid="uuid-2",
        )
        message_data = {
            "sdk_message": sdk_msg,
            "subtype": sdk_msg.subtype,
            "session_id": "test-session",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata["subtype"] == "agent_notification"
        assert parsed.metadata["notification_type"] == "agent_completed"
        assert parsed.metadata["label"] == "beta"

    def test_notification_dict_format_live(self):
        """Notification hook (stored-dict path, live first pass) is tagged from init_data."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_response",
                "init_data": {
                    "hook_event_name": "Notification",
                    "message": "gamma failed",
                    "notification_type": "agent_completed",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata["subtype"] == "agent_notification"
        assert parsed.metadata["notification_type"] == "agent_completed"
        assert parsed.metadata["label"] == "gamma"
        assert parsed.content == "gamma failed"

    def test_notification_dict_format_reload(self):
        """Notification fields survive reload, when the persisted subtype is already
        'agent_notification' (not hook_started/hook_response) and init_data is absent
        from the flat stored metadata view used on this path."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "delta needs your input",
            "metadata": {
                "subtype": "agent_notification",
                "notification_type": "agent_needs_input",
                "message": "delta needs your input",
                "title": None,
                "label": "delta",
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata["subtype"] == "agent_notification"
        assert parsed.metadata["notification_type"] == "agent_needs_input"
        assert parsed.metadata["label"] == "delta"

    def test_non_notification_hooks_unaffected(self):
        """Regular hook_started/hook_response events are not misclassified as notifications."""
        handler = SystemMessageHandler()
        message_data = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "hook_started",
                "init_data": {
                    "hook_name": "pre-tool-guard",
                    "hook_event": "PreToolUse",
                },
            },
            "session_id": "test-hooks",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata["subtype"] == "hook_started"
        assert "pre-tool-guard" in parsed.content


class TestIssue1486MessageIdPropagation:
    """Regression tests for issue #1486 duplicate-message bug.

    Root cause: _createStreamingPlaceholder used StreamEvent.uuid (per-event CLI UUID)
    as the placeholder's message_id.  The terminal AssistantMessage carries a different
    identifier (the Anthropic message ID stored in AssistantMessage.message_id), so
    the dedup check in addMessage() always missed, producing a duplicate entry.

    Fix: AssistantMessageHandler now captures sdk_msg.message_id into metadata so that
    web_server.py can propagate it in the poll event, and the frontend placeholder is
    keyed on data.event.message.id from the message_start Anthropic streaming event.

    Issue #1958: the captured value is TURN-level identity, not per-record identity —
    AssistantMessageHandler now writes it under metadata["turn_id"] instead of the
    ambiguous metadata["message_id"] name it originally shared with the per-record
    concept elsewhere in the codebase.
    """

    def test_assistant_turn_id_captured_from_sdk_object(self):
        """turn_id from AssistantMessage SDK object is surfaced in parsed metadata."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hello")],
            model="claude-3-5-sonnet-20241022",
            message_id="msg_abc123",
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()
        parsed = handler.parse(message_data)

        assert parsed.metadata.get("turn_id") == "msg_abc123", (
            "Anthropic message_id must be propagated as turn_id so the frontend can dedup "
            "the streaming placeholder against the terminal AssistantMessage"
        )

    def test_assistant_turn_id_absent_when_none(self):
        """metadata turn_id is absent when SDK object has no message_id."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hello")],
            model="claude-3-5-sonnet-20241022",
            message_id=None,
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()
        parsed = handler.parse(message_data)

        assert "turn_id" not in parsed.metadata or parsed.metadata["turn_id"] is None

    def test_assistant_turn_id_restored_from_stored_dict(self):
        """turn_id is restored when re-parsing a stored assistant message that has it in metadata."""
        handler = AssistantMessageHandler()
        message_data = {
            "type": "assistant",
            "content": "Stored response",
            "metadata": {
                "turn_id": "msg_stored456",
                "model": "claude-3-5-sonnet-20241022",
            },
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata.get("turn_id") == "msg_stored456"

    def test_assistant_turn_id_restored_from_legacy_message_id_key(self):
        """Issue #1958: pre-#1958 history stored the turn id under the old ambiguous
        metadata["message_id"] key. Re-parsing that legacy shape must still resolve
        turn_id correctly, with no migration step required."""
        handler = AssistantMessageHandler()
        message_data = {
            "type": "assistant",
            "content": "Stored response from before the rename",
            "metadata": {
                "message_id": "msg_legacy789",
                "model": "claude-3-5-sonnet-20241022",
            },
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata.get("turn_id") == "msg_legacy789", (
            "Legacy metadata['message_id'] must be readable as turn_id with no migration"
        )


class TestIssue1845UserMessageIdPropagation:
    """Regression tests for issue #1845 reload-duplicated-user-message bug.

    Original root cause: UserMessageHandler never copied the stable message_id
    (assigned by data_storage.append_message() at persistence time) into
    ParsedMessage.metadata, so a user message redelivered live via the poll stream
    during the jsonl-write/queue-push race carried no id and rendered twice.

    Issue #1958 superseded the original fix: UserMessageHandler's metadata["message_id"]
    write is removed outright. The per-record identity it existed to propagate is now
    populated centrally for every message type by MessageProcessor.process_message()
    as ParsedMessage.record_id, sourced directly from the top-level message_id field —
    the same field this handler used to dig for and re-stash into metadata. No consumer
    ever read it back out of metadata, so the write was verified dead once record_id
    existed as a first-class field.
    """

    def test_user_handler_no_longer_writes_metadata_message_id(self):
        """UserMessageHandler must not resurrect the removed metadata['message_id'] write —
        that concept is now record_id, populated centrally by process_message()."""
        handler = UserMessageHandler()
        message_data = {
            "type": "user",
            "content": "Please help me",
            "session_id": "sess-1",
            "timestamp": time.time(),
            "message_id": "msg_user_abc123",
        }
        parsed = handler.parse(message_data)

        assert "message_id" not in parsed.metadata

    def test_user_record_id_populated_centrally_by_process_message(self):
        """The per-record identity issue #1845 needed is now ParsedMessage.record_id,
        populated by MessageProcessor.process_message() from the top-level message_id —
        not by UserMessageHandler writing into metadata."""
        processor = MessageProcessor(MessageParser())
        message_data = {
            "type": "user",
            "content": "Please help me",
            "session_id": "sess-1",
            "timestamp": time.time(),
            "message_id": "msg_user_abc123",
        }
        parsed = processor.process_message(message_data, source="websocket")

        assert parsed.record_id == "msg_user_abc123", (
            "Top-level message_id must be surfaced as record_id so a live-polled user "
            "message carries the same id as its REST-loaded counterpart"
        )

    def test_user_record_id_absent_when_no_top_level_message_id(self):
        """record_id is None when the raw dict carries no top-level message_id."""
        processor = MessageProcessor(MessageParser())
        message_data = {
            "type": "user",
            "content": "Please help me",
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = processor.process_message(message_data, source="websocket")

        assert parsed.record_id is None


class TestIssue1840UsageExtraction:
    """Regression tests for issue #1840: subagent (Task/Agent tool) usage capture.

    `claude_agent_sdk.claude_sdk._convert_sdk_message()` already copies `usage` off
    every raw SDK message dict that has it, including subagent AssistantMessages
    (tagged with `parent_tool_use_id`) — the only gap was that
    AssistantMessageHandler never extracted it into parsed metadata.
    """

    def test_usage_extracted_for_top_level_assistant_message(self):
        handler = AssistantMessageHandler()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 5,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 20,
                "ephemeral_1h_input_tokens": 0,
            },
        }
        message_data = {
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "usage": usage,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata.get("usage") == usage

    def test_usage_extracted_for_subagent_assistant_message(self):
        """A parent_tool_use_id-tagged message extracts usage the same way — no
        subagent-specific parsing path, just the same field the handler already
        has access to."""
        handler = AssistantMessageHandler()
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 3,
                "ephemeral_1h_input_tokens": 7,
            },
        }
        message_data = {
            "type": "assistant",
            "model": "claude-haiku-4-5",
            "parent_tool_use_id": "toolu_subagent_1",
            "usage": usage,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert parsed.metadata.get("parent_tool_use_id") == "toolu_subagent_1"
        assert parsed.metadata.get("usage") == usage
        assert parsed.metadata["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] == 7

    def test_usage_extracted_from_sdk_message_object(self):
        """usage is also read off a real AssistantMessage SDK object, not just the
        top-level dict copy claude_sdk.py makes."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        usage = {"input_tokens": 1, "output_tokens": 1}
        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hi")],
            model="claude-sonnet-4-6",
            usage=usage,
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()
        parsed = handler.parse(message_data)
        assert parsed.metadata.get("usage") == usage

    def test_usage_absent_when_not_present(self):
        handler = AssistantMessageHandler()
        message_data = {
            "type": "assistant",
            "model": "claude-sonnet-4-6",
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        parsed = handler.parse(message_data)
        assert "usage" not in parsed.metadata


class TestIssue1958RecordAndTurnIdentity:
    """Regression tests for issue #1958: record_id and turn_id are distinct, first-class
    ParsedMessage fields, populated once and centrally by
    MessageProcessor.process_message() for every message on every path (live, replay,
    storage-read) — not something each downstream consumer re-derives by digging through
    dicts under a same-named, ambiguous key.
    """

    def test_record_id_and_turn_id_both_populated_for_assistant_message(self):
        """A live assistant message with a distinct per-record id and per-turn id gets
        both surfaced correctly, and they are NOT equal to each other."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hello")],
            model="claude-3-5-sonnet-20241022",
            message_id="anthropic-turn-1",
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
            "message_id": "frame-uuid-1",
        }
        processor = MessageProcessor(MessageParser())
        parsed = processor.process_message(message_data, source="websocket")

        assert parsed.record_id == "frame-uuid-1"
        assert parsed.turn_id == "anthropic-turn-1"
        assert parsed.record_id != parsed.turn_id

    def test_turn_id_none_for_user_message(self):
        """turn_id is an assistant-only concept — a user message never has one, even
        though it does have a record_id."""
        processor = MessageProcessor(MessageParser())
        message_data = {
            "type": "user",
            "content": "hello",
            "session_id": "sess-1",
            "timestamp": time.time(),
            "message_id": "frame-uuid-user-1",
        }
        parsed = processor.process_message(message_data, source="websocket")

        assert parsed.record_id == "frame-uuid-user-1"
        assert parsed.turn_id is None

    def test_turn_id_legacy_fallback_via_process_message_from_storage(self):
        """Issue #1958 backward-compat requirement: a stored dict shaped exactly like
        pre-#1958 history (turn id only under the old metadata["message_id"] key, no
        metadata["turn_id"]) must still resolve turn_id correctly via
        process_message(source="storage") — no migration step required."""
        processor = MessageProcessor(MessageParser())
        message_data = {
            "type": "assistant",
            "content": "Pre-#1958 stored response",
            "metadata": {
                "message_id": "legacy-turn-id",
                "model": "claude-3-5-sonnet-20241022",
            },
            "session_id": "sess-1",
            "timestamp": time.time(),
            "message_id": "frame-uuid-legacy",
        }
        parsed = processor.process_message(message_data, source="storage")

        assert parsed.turn_id == "legacy-turn-id"
        assert parsed.record_id == "frame-uuid-legacy"

    def test_record_id_none_when_message_data_not_a_dict(self):
        """process_message() defensively handles a non-dict message_data (e.g. an SDK
        object passed directly) without raising — record_id is simply None."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hi")],
            model="claude-3-5-sonnet-20241022",
        )
        processor = MessageProcessor(MessageParser())
        parsed = processor.process_message(sdk_msg, source="websocket")

        assert parsed.record_id is None


class TestIssue1967CombinedTextAndToolUse:
    """Regression tests for issue #1967: an assistant turn with both narration text
    and a tool call in the same SDK message silently dropped tool_uses and turn_id.

    Root cause: ClaudeSDK._convert_sdk_message() always derives a top-level
    "content" string whenever any TextBlock is present, alongside the full
    "sdk_message" object. AssistantMessageHandler checked that derived "content"
    string first, so it never reached the branch that iterates sdk_msg.content to
    extract tool_uses and turn_id. Fix: check for a real AssistantMessage SDK object
    first, and only fall back to the derived "content" string / legacy nested format
    when no such object is present.
    """

    def _combined_sdk_message_data(self, extra_blocks=None):
        """Build message_data shaped exactly like ClaudeSDK._convert_sdk_message()
        produces for a combined text+tool_use frame: a derived top-level "content"
        string AND the full "sdk_message" object are both present."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock, ToolUseBlock

        blocks = [
            TextBlock(text="Let me check that file."),
            ToolUseBlock(id="tool-1", name="Read", input={"file_path": "/tmp/x.py"}),
        ]
        if extra_blocks:
            blocks.extend(extra_blocks)

        sdk_msg = AssistantMessage(
            content=blocks,
            model="claude-3-5-sonnet-20241022",
            message_id="msg_combined_1",
            uuid="some-per-event-uuid",
        )
        return {
            "type": "assistant",
            "content": "Let me check that file.",  # derived by _convert_sdk_message()
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }

    def test_combined_text_and_tool_use_both_captured(self):
        """A live combined text+tool_use frame must capture text, tool_uses, and turn_id
        all at once — this exact shape (competing top-level "content" string plus
        "sdk_message") had zero prior coverage and was the reproduction of the bug."""
        handler = AssistantMessageHandler()
        message_data = self._combined_sdk_message_data()

        parsed = handler.parse(message_data)

        assert "Let me check that file." in parsed.content
        assert len(parsed.metadata["tool_uses"]) == 1
        assert parsed.metadata["tool_uses"][0]["id"] == "tool-1"
        assert parsed.metadata["tool_uses"][0]["name"] == "Read"
        assert parsed.metadata["has_tool_uses"] is True
        assert parsed.metadata["turn_id"] == "msg_combined_1"

    def test_text_only_assistant_message_still_works(self):
        """Regression guard: a text-only AssistantMessage (no competing top-level
        "content" string set) still captures text and turn_id, with empty tool_uses."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        sdk_msg = AssistantMessage(
            content=[TextBlock(text="just narration")],
            model="claude-3-5-sonnet-20241022",
            message_id="msg_text_only",
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()
        parsed = handler.parse(message_data)

        assert "just narration" in parsed.content
        assert parsed.metadata["tool_uses"] == []
        assert parsed.metadata["turn_id"] == "msg_text_only"

    def test_tool_use_only_assistant_message_still_works(self):
        """Regression guard: a tool-use-only AssistantMessage (no TextBlock, so no
        derived top-level "content" string is set) still captures tool_uses/turn_id,
        with the default content fallback preserved."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import ToolUseBlock

        sdk_msg = AssistantMessage(
            content=[ToolUseBlock(id="tool-2", name="Bash", input={"command": "ls"})],
            model="claude-3-5-sonnet-20241022",
            message_id="msg_tool_only",
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()
        parsed = handler.parse(message_data)

        assert parsed.content == "Assistant response"
        assert len(parsed.metadata["tool_uses"]) == 1
        assert parsed.metadata["tool_uses"][0]["id"] == "tool-2"
        assert parsed.metadata["turn_id"] == "msg_tool_only"

    def test_multiple_tool_uses_combined_with_text(self):
        """Multiple ToolUseBlocks combined with text in one frame: all tool calls
        must be captured, not just the first."""
        from claude_agent_sdk.types import ToolUseBlock

        handler = AssistantMessageHandler()
        message_data = self._combined_sdk_message_data(
            extra_blocks=[ToolUseBlock(id="tool-3", name="Grep", input={"pattern": "foo"})]
        )

        parsed = handler.parse(message_data)

        tool_ids = {tu["id"] for tu in parsed.metadata["tool_uses"]}
        assert tool_ids == {"tool-1", "tool-3"}

    def test_thinking_text_and_tool_use_combined(self):
        """ThinkingBlock + TextBlock + ToolUseBlock combined in one frame: thinking,
        text, and tool_use must all be captured simultaneously."""
        from claude_agent_sdk.types import ThinkingBlock

        handler = AssistantMessageHandler()
        message_data = self._combined_sdk_message_data(
            extra_blocks=[ThinkingBlock(thinking="pondering...", signature="sig-1")]
        )

        parsed = handler.parse(message_data)

        assert "Let me check that file." in parsed.content
        assert len(parsed.metadata["tool_uses"]) == 1
        assert parsed.metadata["has_thinking"] is True
        assert "pondering..." in parsed.metadata["thinking_content"]

    def test_unrecognized_block_type_logs_warning_but_others_still_captured(self, caplog):
        """An unrecognized content block type mixed into content must produce a
        logger.warning, while the other blocks in the same frame are still captured."""
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock, ToolUseBlock

        class UnknownBlock:
            """Stand-in for a future SDK content block type this handler doesn't know about."""

        sdk_msg = AssistantMessage(
            content=[
                TextBlock(text="narration"),
                UnknownBlock(),
                ToolUseBlock(id="tool-4", name="Read", input={}),
            ],
            model="claude-3-5-sonnet-20241022",
            message_id="msg_unknown_block",
            uuid="some-per-event-uuid",
        )
        message_data = {
            "type": "assistant",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": time.time(),
        }
        handler = AssistantMessageHandler()

        with caplog.at_level("WARNING"):
            parsed = handler.parse(message_data)

        assert "UnknownBlock" in caplog.text
        assert "narration" in parsed.content
        assert len(parsed.metadata["tool_uses"]) == 1
        assert parsed.metadata["tool_uses"][0]["id"] == "tool-4"

    def test_live_replay_round_trip_preserves_tool_uses_and_turn_id(self):
        """Issue #1958-style live/replay divergence check: a combined text+tool_use
        parsed message must survive a prepare_for_storage() -> process_message(...,
        source="storage") round trip with tool_uses and turn_id intact."""
        processor = MessageProcessor(MessageParser())
        message_data = self._combined_sdk_message_data()
        message_data["message_id"] = "frame-uuid-combined"

        parsed = processor.process_message(message_data, source="websocket")
        assert parsed.metadata["tool_uses"], "sanity check: live parse captured tool_uses"
        assert parsed.turn_id == "msg_combined_1"

        stored = processor.prepare_for_storage(parsed)
        stored["message_id"] = "frame-uuid-combined"

        reparsed = processor.process_message(stored, source="storage")

        assert reparsed.metadata["tool_uses"] == parsed.metadata["tool_uses"]
        assert reparsed.turn_id == "msg_combined_1"
