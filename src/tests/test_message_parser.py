"""Tests for SDK message parser."""

import time

import pytest

from ..message_parser import (
    AssistantMessageHandler,
    ErrorHandler,
    MessageParser,
    MessageType,
    ParsedMessage,
    ResultMessageHandler,
    SystemMessageHandler,
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
        from src.message_parser import ThinkingBlockHandler

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
        from src.message_parser import ToolUseHandler

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
        from src.message_parser import ToolResultHandler

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
        from src.message_parser import ToolResultHandler

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
