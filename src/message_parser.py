"""Extensible message parser for Claude Code SDK streaming messages."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set, Type, Callable
from enum import Enum

from claude_code_sdk import (
    UserMessage, AssistantMessage, SystemMessage, ResultMessage,
    TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock
)

from .logging_config import get_logger

logger = get_logger(__name__)


class MessageType(Enum):
    """Known Claude Code SDK message types."""
    # Core SDK message types
    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"
    RESULT = "result"

    # Tool execution (if SDK supports it)
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"

    # Content blocks
    THINKING = "thinking"

    # Status and state
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STATUS_UPDATE = "status_update"
    PROCESSING = "processing"

    # Errors
    ERROR = "error"
    WARNING = "warning"
    EXCEPTION = "exception"

    # Unknown/extensible
    UNKNOWN = "unknown"


@dataclass
class ParsedMessage:
    """Structured representation of a parsed SDK message."""
    type: MessageType
    timestamp: float
    session_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None


class MessageHandler(ABC):
    """Abstract base class for SDK message type handlers."""

    @abstractmethod
    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        """Check if this handler can process the message."""
        pass

    @abstractmethod
    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        """Parse the message data into structured format."""
        pass


class SystemMessageHandler(MessageHandler):
    """Handler for SDK system messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check if it's an SDK SystemMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], SystemMessage)
        # Fallback to type field check
        return message_data.get("type") == "system"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data and isinstance(message_data["sdk_message"], SystemMessage):
            sdk_msg = message_data["sdk_message"]
            content = sdk_msg.content if hasattr(sdk_msg, 'content') else "System message"

            return ParsedMessage(
                type=MessageType.SYSTEM,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=content,
                raw_data=message_data,
                metadata={
                    "sdk_message": sdk_msg,
                    "session_id": message_data.get("session_id"),
                    "subtype": message_data.get("subtype", "unknown")
                }
            )

        # Fallback to dict-based parsing
        subtype = message_data.get("subtype", "unknown")
        return ParsedMessage(
            type=MessageType.SYSTEM,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=f"System {subtype}: {message_data.get('session_id', 'unknown')}",
            raw_data=message_data,
            metadata={
                "subtype": subtype,
                "session_id": message_data.get("session_id"),
                "cwd": message_data.get("cwd"),
                "tools": message_data.get("tools", []),
                "model": message_data.get("model"),
                "permission_mode": message_data.get("permissionMode")
            }
        )


class AssistantMessageHandler(MessageHandler):
    """Handler for SDK assistant messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check if it's an SDK AssistantMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], AssistantMessage)
        # Fallback to type field check
        return message_data.get("type") == "assistant"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data and isinstance(message_data["sdk_message"], AssistantMessage):
            sdk_msg = message_data["sdk_message"]
            text_parts = []
            thinking_parts = []
            tool_uses = []

            # Extract content from different block types
            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        thinking_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })

            content = " ".join(text_parts) if text_parts else "Assistant response"
            if thinking_parts:
                content = f"[Thinking: {' '.join(thinking_parts)}] {content}"

            return ParsedMessage(
                type=MessageType.ASSISTANT,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=content,
                raw_data=message_data,
                metadata={
                    "sdk_message": sdk_msg,
                    "model": sdk_msg.model if hasattr(sdk_msg, 'model') else None,
                    "session_id": message_data.get("session_id"),
                    "has_thinking": len(thinking_parts) > 0,
                    "thinking_content": thinking_parts,
                    "tool_uses": tool_uses,
                    "has_tool_uses": len(tool_uses) > 0
                }
            )

        # Fallback to dict-based parsing
        message = message_data.get("message", {})
        content_parts = message.get("content", [])

        # Extract text content
        text_content = ""
        if isinstance(content_parts, list):
            text_parts = [part.get("text", "") for part in content_parts if part.get("type") == "text"]
            text_content = " ".join(text_parts)
        elif isinstance(content_parts, str):
            text_content = content_parts

        return ParsedMessage(
            type=MessageType.ASSISTANT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=text_content,
            raw_data=message_data,
            metadata={
                "message_id": message.get("id"),
                "model": message.get("model"),
                "role": message.get("role"),
                "session_id": message_data.get("session_id"),
                "usage": message.get("usage", {}),
                "stop_reason": message.get("stop_reason")
            }
        )


class UserMessageHandler(MessageHandler):
    """Handler for SDK user messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check if it's an SDK UserMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], UserMessage)
        # Fallback to type field check
        return message_data.get("type") == "user"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data and isinstance(message_data["sdk_message"], UserMessage):
            sdk_msg = message_data["sdk_message"]
            text_parts = []
            tool_results = []
            tool_uses = []

            # Extract content from different block types
            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolResultBlock):
                        tool_results.append({
                            "tool_use_id": block.tool_use_id,
                            "content": block.content,
                            "is_error": block.is_error if hasattr(block, 'is_error') else False
                        })
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })

            content = " ".join(text_parts) if text_parts else ""
            if tool_results and not content:
                content = f"Tool results: {len(tool_results)} results"

            return ParsedMessage(
                type=MessageType.USER,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=content,
                raw_data=message_data,
                metadata={
                    "sdk_message": sdk_msg,
                    "session_id": message_data.get("session_id"),
                    "tool_results": tool_results,
                    "tool_uses": tool_uses,
                    "has_tool_results": len(tool_results) > 0,
                    "has_tool_uses": len(tool_uses) > 0
                }
            )

        # Fallback to dict-based parsing
        message = message_data.get("message", {})
        content_parts = message.get("content", [])

        # Check if this contains tool results and tool uses
        tool_results = []
        tool_uses = []
        text_content = ""

        if isinstance(content_parts, list):
            for part in content_parts:
                if part.get("type") == "tool_result":
                    tool_results.append({
                        "tool_use_id": part.get("tool_use_id"),
                        "content": part.get("content", "")
                    })
                elif part.get("type") == "tool_use":
                    tool_uses.append({
                        "id": part.get("id"),
                        "name": part.get("name"),
                        "input": part.get("input", {})
                    })
                elif part.get("type") == "text":
                    text_content += part.get("text", "")
        elif isinstance(content_parts, str):
            text_content = content_parts

        return ParsedMessage(
            type=MessageType.USER,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=text_content or (f"Tool results: {len(tool_results)} results" if tool_results else ""),
            raw_data=message_data,
            metadata={
                "role": message.get("role"),
                "tool_results": tool_results,
                "tool_uses": tool_uses,
                "session_id": message_data.get("session_id"),
                "has_tool_results": len(tool_results) > 0,
                "has_tool_uses": len(tool_uses) > 0
            }
        )


class ResultMessageHandler(MessageHandler):
    """Handler for SDK result/completion messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check if it's an SDK ResultMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], ResultMessage)
        # Fallback to type field check
        return message_data.get("type") == "result"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        subtype = message_data.get("subtype", "unknown")
        is_error = message_data.get("is_error", False)

        return ParsedMessage(
            type=MessageType.RESULT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=message_data.get("result", f"Conversation {subtype}"),
            raw_data=message_data,
            error_message=message_data.get("result") if is_error else None,
            metadata={
                "subtype": subtype,
                "is_error": is_error,
                "session_id": message_data.get("session_id"),
                "duration_ms": message_data.get("duration_ms"),
                "duration_api_ms": message_data.get("duration_api_ms"),
                "num_turns": message_data.get("num_turns"),
                "total_cost_usd": message_data.get("total_cost_usd"),
                "usage": message_data.get("usage", {}),
                "permission_denials": message_data.get("permission_denials", [])
            }
        )


class ThinkingBlockHandler(MessageHandler):
    """Handler for thinking block content."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check for SDK ThinkingBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ThinkingBlock) for block in sdk_msg.content)
        return message_data.get("type") == "thinking"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            thinking_content = ""

            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ThinkingBlock):
                        thinking_content = block.text
                        break

            return ParsedMessage(
                type=MessageType.THINKING,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=thinking_content,
                raw_data=message_data,
                metadata={
                    "sdk_message": sdk_msg,
                    "thinking_content": thinking_content,
                    "session_id": message_data.get("session_id")
                }
            )

        # Fallback to dict-based parsing
        content = message_data.get("content", message_data.get("text", ""))
        return ParsedMessage(
            type=MessageType.THINKING,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=content,
            raw_data=message_data,
            metadata={
                "thinking_content": content
            }
        )


class ToolUseHandler(MessageHandler):
    """Handler for tool use messages (if SDK supports them)."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check for SDK ToolUseBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ToolUseBlock) for block in sdk_msg.content)
        return message_data.get("type") in ["tool_use", "tool_call"]

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            tool_name = "unknown"
            tool_id = ""
            tool_input = {}

            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_id = block.id
                        tool_input = block.input
                        break

            return ParsedMessage(
                type=MessageType.TOOL_USE,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=f"Using tool: {tool_name}",
                raw_data=message_data,
                metadata={
                    "sdk_message": sdk_msg,
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "tool_input": tool_input,
                    "session_id": message_data.get("session_id")
                }
            )

        # Fallback to dict-based parsing
        tool_name = message_data.get("tool_name", message_data.get("name", "unknown"))
        return ParsedMessage(
            type=MessageType.TOOL_USE,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=f"Using tool: {tool_name}",
            raw_data=message_data,
            metadata={
                "tool_name": tool_name,
                "tool_input": message_data.get("input", message_data.get("parameters")),
                "tool_id": message_data.get("id", message_data.get("tool_call_id"))
            }
        )


class ToolResultHandler(MessageHandler):
    """Handler for tool result messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        # Check for SDK ToolResultBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ToolResultBlock) for block in sdk_msg.content)
        return message_data.get("type") == "tool_result"

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        # Handle SDK message object
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            tool_use_id = ""
            content = ""
            is_error = False

            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ToolResultBlock):
                        tool_use_id = block.tool_use_id
                        content = block.content
                        is_error = getattr(block, 'is_error', False)
                        break

            return ParsedMessage(
                type=MessageType.TOOL_RESULT,
                timestamp=message_data.get("timestamp", time.time()),
                session_id=message_data.get("session_id"),
                content=content,
                raw_data=message_data,
                error_message=content if is_error else None,
                metadata={
                    "sdk_message": sdk_msg,
                    "tool_use_id": tool_use_id,
                    "is_error": is_error,
                    "session_id": message_data.get("session_id")
                }
            )

        # Fallback to dict-based parsing
        content = message_data.get("content", "")
        is_error = message_data.get("is_error", False)

        return ParsedMessage(
            type=MessageType.TOOL_RESULT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=content,
            raw_data=message_data,
            error_message=content if is_error else None,
            metadata={
                "tool_use_id": message_data.get("tool_use_id"),
                "is_error": is_error
            }
        )


class ErrorHandler(MessageHandler):
    """Handler for error messages."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        return message_data.get("type") in ["error", "exception", "warning"]

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        error_type = message_data.get("type", "error")
        message_type = MessageType.ERROR if error_type != "warning" else MessageType.WARNING

        return ParsedMessage(
            type=message_type,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=message_data.get("message", message_data.get("error", "")),
            raw_data=message_data,
            error_message=message_data.get("message", message_data.get("error", "")),
            metadata={
                "error_type": error_type,
                "error_code": message_data.get("code"),
                "stack_trace": message_data.get("stack_trace")
            }
        )


class UnknownMessageHandler(MessageHandler):
    """Handler for unknown message types - always handles as fallback."""

    def can_handle(self, message_data: Dict[str, Any]) -> bool:
        return True  # Always handles as fallback

    def parse(self, message_data: Dict[str, Any]) -> ParsedMessage:
        message_type = message_data.get("type", "unknown")
        return ParsedMessage(
            type=MessageType.UNKNOWN,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=str(message_data),
            raw_data=message_data,
            metadata={
                "original_type": message_type,
                "unknown_format": True
            }
        )


class MessageParser:
    """
    Extensible parser for Claude Code SDK messages.

    Maintains a registry of handlers and provides graceful fallback
    for unknown message types.
    """

    def __init__(self):
        """Initialize the message parser with default handlers."""
        self.handlers: List[MessageHandler] = []
        self.stats = {
            "total_parsed": 0,
            "unknown_types": 0,
            "parse_errors": 0,
            "type_counts": {}
        }
        self.unknown_types: Set[str] = set()

        # Register default handlers
        self._register_default_handlers()

        logger.info("MessageParser initialized with default handlers")

    def _register_default_handlers(self) -> None:
        """Register the default set of message handlers."""
        default_handlers = [
            # SDK specific handlers
            SystemMessageHandler(),
            AssistantMessageHandler(),
            UserMessageHandler(),
            ResultMessageHandler(),

            # Content block handlers
            ThinkingBlockHandler(),
            ToolUseHandler(),
            ToolResultHandler(),

            # Generic handlers
            ErrorHandler(),
            UnknownMessageHandler()  # Always last as fallback
        ]

        for handler in default_handlers:
            self.register_handler(handler)

    def register_handler(self, handler: MessageHandler) -> None:
        """
        Register a new message handler.

        Args:
            handler: Handler instance to register
        """
        # Insert before the UnknownMessageHandler (which should always be last)
        if self.handlers and isinstance(self.handlers[-1], UnknownMessageHandler):
            self.handlers.insert(-1, handler)
        else:
            self.handlers.append(handler)

        logger.debug(f"Registered handler: {handler.__class__.__name__}")

    def parse_message(self, message_data: Dict[str, Any]) -> ParsedMessage:
        """
        Parse a message using the appropriate handler.

        Args:
            message_data: Raw message data from Claude Code SDK

        Returns:
            Parsed message with structured data
        """
        self.stats["total_parsed"] += 1

        try:
            # Find the first handler that can process this message
            for handler in self.handlers:
                if handler.can_handle(message_data):
                    parsed = handler.parse(message_data)

                    # Update statistics
                    type_name = parsed.type.value
                    self.stats["type_counts"][type_name] = self.stats["type_counts"].get(type_name, 0) + 1

                    # Track unknown types for analysis
                    if parsed.type == MessageType.UNKNOWN:
                        original_type = message_data.get("type", "no_type_field")
                        self.unknown_types.add(original_type)
                        self.stats["unknown_types"] += 1
                        logger.warning(f"Unknown message type: {original_type}")

                    logger.debug(f"Parsed message: {parsed.type.value}")
                    return parsed

            # Should never reach here due to UnknownMessageHandler fallback
            logger.error("No handler found for message (this shouldn't happen)")
            return self._create_error_message(message_data, "No handler found")

        except Exception as e:
            logger.error(f"Error parsing message: {e}")
            self.stats["parse_errors"] += 1
            return self._create_error_message(message_data, str(e))

    def _create_error_message(self, message_data: Dict[str, Any], error_msg: str) -> ParsedMessage:
        """Create an error message for parsing failures."""
        # Handle None or invalid message_data
        safe_message_data = message_data if message_data is not None else {}

        return ParsedMessage(
            type=MessageType.ERROR,
            timestamp=time.time(),
            session_id=safe_message_data.get("session_id") if isinstance(safe_message_data, dict) else None,
            content=f"Parse error: {error_msg}",
            raw_data=safe_message_data,
            error_message=error_msg,
            metadata={
                "parse_error": True,
                "original_data": safe_message_data
            }
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get parsing statistics."""
        return {
            **self.stats,
            "unknown_types_seen": list(self.unknown_types),
            "handler_count": len(self.handlers)
        }

    def reset_stats(self) -> None:
        """Reset parsing statistics."""
        self.stats = {
            "total_parsed": 0,
            "unknown_types": 0,
            "parse_errors": 0,
            "type_counts": {}
        }
        self.unknown_types.clear()
        logger.info("MessageParser stats reset")

    def get_unknown_types(self) -> List[str]:
        """Get list of unknown message types encountered."""
        return list(self.unknown_types)