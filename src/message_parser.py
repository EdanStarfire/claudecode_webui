"""Extensible message parser for Claude Code SDK streaming messages."""

import ast
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from .logging_config import get_logger

# Get specialized logger for parser debugging
parser_logger = get_logger('parser', category='PARSER')
# Keep standard logger for errors
logger = logging.getLogger(__name__)




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

    # Permission handling
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"

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
    session_id: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class MessageHandler(ABC):
    """Abstract base class for SDK message type handlers."""

    @abstractmethod
    def can_handle(self, message_data: dict[str, Any]) -> bool:
        """Check if this handler can process the message."""
        pass

    @abstractmethod
    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        """Parse the message data into structured format."""
        pass

    @abstractmethod
    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from message, preserving raw SDK data."""
        pass

    def _standardize_metadata_fields(self, metadata: dict[str, Any], message_data: dict[str, Any]) -> dict[str, Any]:
        """Standardize metadata fields."""
        return metadata


class SystemMessageHandler(MessageHandler):
    """Handler for SDK system messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check if it's an SDK SystemMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], SystemMessage)
        # Fallback to type field check
        return message_data.get("type") == "system"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from system message."""
        extracted = {
            "type": "system",
            "content": "",
            "metadata": {
                "subtype": "unknown",
                "session_id": message_data.get("session_id"),
                "working_directory": None,
                "permissions": None,
                "tools": [],
                "model": None,
                "system_prompt": None,
                "error_details": None,
                "has_tool_uses": False,
                "has_thinking": False,
                "has_tool_results": False,
                "has_permission_requests": False,
                "has_permission_responses": False,
                "is_error": False
            }
        }

        # Extract from SDK object if available
        if "sdk_message" in message_data and isinstance(message_data["sdk_message"], SystemMessage):
            sdk_msg = message_data["sdk_message"]
            extracted["content"] = sdk_msg.content if hasattr(sdk_msg, 'content') else "System message"

            # Check for subtype in multiple locations (same logic as dictionary path)
            subtype = (
                message_data.get("subtype") or
                (message_data.get("metadata") or {}).get("subtype") or
                "init"
            )
            extracted["metadata"]["subtype"] = subtype

            # Extract init data if available (for Session Info feature)
            if hasattr(sdk_msg, 'data') and sdk_msg.data:
                extracted["metadata"]["init_data"] = sdk_msg.data
        else:
            # Extract from dictionary data
            # Look for subtype in multiple locations for robustness
            subtype = (
                # First: check nested metadata
                (message_data.get("metadata") or {}).get("subtype") or
                # Second: check root level
                message_data.get("subtype") or
                # Default for system messages should be "init" to match SDK behavior
                "init"
            )

            # Use provided content or generate default
            provided_content = message_data.get("content")
            if provided_content:
                extracted["content"] = provided_content
            else:
                extracted["content"] = f"System {subtype}: {message_data.get('session_id', 'unknown')}"
            extracted["metadata"]["subtype"] = subtype
            extracted["metadata"]["working_directory"] = message_data.get("cwd")
            extracted["metadata"]["permissions"] = message_data.get("permissionMode")
            extracted["metadata"]["tools"] = message_data.get("tools", [])
            extracted["metadata"]["model"] = message_data.get("model")
            extracted["metadata"]["system_prompt"] = message_data.get("system_prompt")
            extracted["metadata"]["is_error"] = message_data.get("is_error", False)
            if extracted["metadata"]["is_error"]:
                extracted["metadata"]["error_details"] = message_data.get("error_details")

        return extracted

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.SYSTEM,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class AssistantMessageHandler(MessageHandler):
    """Handler for SDK assistant messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check if it's an SDK AssistantMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], AssistantMessage)
        # Fallback to type field check
        return message_data.get("type") == "assistant"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from assistant message."""
        extracted = {
            "type": "assistant",
            "content": "",
            "metadata": {
                "tool_uses": [],
                "tool_results": [],
                "thinking_content": "",
                "thinking_blocks": [],
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False,
                "model": message_data.get("model"),
                "session_id": message_data.get("session_id")
            }
        }

        text_parts = []
        thinking_parts = []
        thinking_blocks = []
        tool_uses = []

        # Check for direct content field first (most common case)
        direct_content = message_data.get("content", "")
        if isinstance(direct_content, str) and direct_content.strip():
            text_parts.append(direct_content)
        else:
            # Handle SDK message object
            if "sdk_message" in message_data and isinstance(message_data["sdk_message"], AssistantMessage):
                sdk_msg = message_data["sdk_message"]
                if hasattr(sdk_msg, 'content'):
                    for block in sdk_msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                        elif isinstance(block, ThinkingBlock):
                            thinking_content = block.thinking
                            thinking_parts.append(thinking_content)
                            thinking_blocks.append({
                                "content": thinking_content,
                                "timestamp": message_data.get("timestamp", time.time())
                            })
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append({
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                                "timestamp": message_data.get("timestamp", time.time())
                            })

            # Handle nested message structure (legacy)
            else:
                self._extract_from_legacy_format(message_data, text_parts)

        # Extract additional legacy format data
        message = message_data.get("message", {})
        if message:
            extracted["metadata"]["model"] = message.get("model") or extracted["metadata"]["model"]
            extracted["metadata"]["role"] = message.get("role")

        # Populate extracted data
        extracted["content"] = " ".join(text_parts) if text_parts else "Assistant response"
        extracted["metadata"]["thinking_content"] = " ".join(thinking_parts) if thinking_parts else ""
        extracted["metadata"]["thinking_blocks"] = thinking_blocks
        extracted["metadata"]["tool_uses"] = tool_uses
        extracted["metadata"]["has_thinking"] = len(thinking_blocks) > 0
        extracted["metadata"]["has_tool_uses"] = len(tool_uses) > 0

        return extracted


    def _parse_structured_content_string(self, content_str: str, message_data: dict[str, Any]) -> list[tuple[str, Any]]:
        """Parse structured content string into typed blocks without regex."""
        blocks = []

        # Try to parse as Python literal first (most reliable for SDK output)
        try:
            # Handle case where content is a Python representation of objects
            if content_str.startswith('[') and content_str.endswith(']'):
                # This looks like a list representation - try to parse it more carefully
                parsed_content = self._parse_sdk_object_list(content_str)
                return parsed_content
        except Exception:
            pass

        # Fallback: Try to extract known patterns using safer parsing
        try:
            # Look for ThinkingBlock content
            if 'ThinkingBlock' in content_str:
                thinking_content = self._extract_thinking_content_safely(content_str)
                if thinking_content:
                    blocks.append(("thinking", thinking_content))

            # Look for TextBlock content
            if 'TextBlock' in content_str:
                text_content = self._extract_text_content_safely(content_str)
                if text_content:
                    blocks.append(("text", text_content))

            # Look for ToolUseBlock content
            if 'ToolUseBlock' in content_str:
                tool_data = self._extract_tool_use_safely(content_str)
                if tool_data:
                    blocks.append(("tool_use", tool_data))

        except Exception as e:
            logger.warning(f"Failed to parse structured content string: {e}")
            # Last resort: treat as plain text
            blocks.append(("text", content_str))

        return blocks

    def _parse_sdk_object_list(self, content_str: str) -> list[tuple[str, Any]]:
        """Parse SDK object list representation more safely."""
        blocks = []

        # Split on block boundaries more carefully
        # Find all block patterns
        block_patterns = [
            (r'TextBlock\(text=(.*?)\)', "text"),
            (r'ThinkingBlock\(thinking=(.*?), signature=.*?\)', "thinking"),
            (r'ToolUseBlock\(id=(.*?), name=(.*?), input=(.*?)\)', "tool_use")
        ]

        for pattern, block_type in block_patterns:
            matches = re.finditer(pattern, content_str, re.DOTALL)
            for match in matches:
                try:
                    if block_type == "text":
                        text_val = match.group(1)
                        if text_val.startswith("'") and text_val.endswith("'"):
                            text_val = text_val[1:-1]  # Remove quotes
                        # Decode escape sequences
                        text_val = text_val.replace('\\n', '\n').replace("\\'", "'").replace('\\"', '"')
                        blocks.append((block_type, text_val))

                    elif block_type == "thinking":
                        thinking_val = match.group(1)
                        if thinking_val.startswith("'") and thinking_val.endswith("'"):
                            thinking_val = thinking_val[1:-1]  # Remove quotes
                        # Decode escape sequences
                        thinking_val = thinking_val.replace('\\n', '\n').replace("\\'", "'").replace('\\"', '"')
                        blocks.append((block_type, thinking_val))

                    elif block_type == "tool_use":
                        tool_id = match.group(1).strip("'\"")
                        tool_name = match.group(2).strip("'\"")
                        tool_input_str = match.group(3)

                        # Parse tool input more safely
                        try:
                            tool_input = ast.literal_eval(tool_input_str)
                        except:
                            try:
                                tool_input = json.loads(tool_input_str.replace("'", '"'))
                            except:
                                tool_input = {"raw": tool_input_str}

                        blocks.append((block_type, {
                            "id": tool_id,
                            "name": tool_name,
                            "input": tool_input
                        }))

                except Exception as e:
                    logger.warning(f"Failed to parse {block_type} block: {e}")
                    continue

        return blocks

    def _extract_thinking_content_safely(self, content_str: str) -> str | None:
        """Safely extract thinking content without fragile regex."""
        try:
            # Look for ThinkingBlock pattern and extract content more robustly
            start_marker = "ThinkingBlock(thinking='"
            end_marker = "', signature="

            start_idx = content_str.find(start_marker)
            if start_idx == -1:
                return None

            content_start = start_idx + len(start_marker)
            end_idx = content_str.find(end_marker, content_start)
            if end_idx == -1:
                return None

            thinking_text = content_str[content_start:end_idx]

            # Decode escape sequences
            thinking_text = thinking_text.replace('\\\\n', '\n').replace('\\n', '\n')
            thinking_text = thinking_text.replace('\\\\t', '\t').replace('\\t', '\t')
            thinking_text = thinking_text.replace("\\'", "'").replace('\\"', '"')

            return thinking_text

        except Exception as e:
            logger.warning(f"Failed to extract thinking content safely: {e}")
            return None

    def _extract_text_content_safely(self, content_str: str) -> str | None:
        """Safely extract text content without fragile regex."""
        try:
            start_marker = "TextBlock(text='"
            end_marker = "')"

            start_idx = content_str.find(start_marker)
            if start_idx == -1:
                return None

            content_start = start_idx + len(start_marker)
            end_idx = content_str.find(end_marker, content_start)
            if end_idx == -1:
                return None

            text_content = content_str[content_start:end_idx]

            # Decode escape sequences
            text_content = text_content.replace('\\n', '\n').replace("\\'", "'").replace('\\"', '"')

            return text_content

        except Exception as e:
            logger.warning(f"Failed to extract text content safely: {e}")
            return None

    def _extract_tool_use_safely(self, content_str: str) -> dict[str, Any] | None:
        """Safely extract tool use information without fragile regex."""
        try:
            start_marker = "ToolUseBlock(id='"
            start_idx = content_str.find(start_marker)
            if start_idx == -1:
                return None

            # Find the end of this ToolUseBlock
            bracket_count = 0
            found_start = False
            end_idx = start_idx

            for i, char in enumerate(content_str[start_idx:], start_idx):
                if char == '(':
                    bracket_count += 1
                    found_start = True
                elif char == ')':
                    bracket_count -= 1
                    if found_start and bracket_count == 0:
                        end_idx = i + 1
                        break

            tool_block = content_str[start_idx:end_idx]

            # Extract components more safely
            # Format: ToolUseBlock(id='...', name='...', input=...)
            parts = tool_block.split("', ", 2)
            if len(parts) < 3:
                return None

            # Extract ID
            id_part = parts[0]
            tool_id = id_part.split("id='", 1)[1] if "id='" in id_part else ""

            # Extract name
            name_part = parts[1]
            tool_name = name_part.split("name='", 1)[1] if "name='" in name_part else ""

            # Extract input (more complex)
            input_part = parts[2]
            if input_part.startswith("input=") and input_part.endswith(")"):
                input_str = input_part[6:-1]  # Remove "input=" and final ")"

                # Try to parse as Python literal
                try:
                    tool_input = ast.literal_eval(input_str)
                except:
                    try:
                        # Try as JSON
                        json_str = input_str.replace("'", '"')
                        tool_input = json.loads(json_str)
                    except:
                        # Fallback
                        tool_input = {"raw": input_str}
            else:
                tool_input = {}

            return {
                "id": tool_id,
                "name": tool_name,
                "input": tool_input
            }

        except Exception as e:
            logger.warning(f"Failed to extract tool use safely: {e}")
            return None


    def _extract_from_legacy_format(self, message_data: dict[str, Any], text_parts: list[str]):
        """Extract data from legacy nested message format."""
        message = message_data.get("message", {})
        content_parts = message.get("content", [])
        if isinstance(content_parts, list):
            text_parts.extend([part.get("text", "") for part in content_parts if part.get("type") == "text"])
        elif isinstance(content_parts, str):
            text_parts.append(content_parts)

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.ASSISTANT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class UserMessageHandler(MessageHandler):
    """Handler for SDK user messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check if it's an SDK UserMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], UserMessage)
        # Fallback to type field check
        return message_data.get("type") == "user"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from user message."""
        extracted = {
            "type": "user",
            "content": "",
            "metadata": {
                "tool_uses": [],
                "tool_results": [],
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False,
                "role": None,
                "session_id": message_data.get("session_id")
            }
        }

        text_parts = []
        tool_results = []
        tool_uses = []

        # Handle SDK message object (live messages from claude_sdk.py)
        if "sdk_message" in message_data and isinstance(message_data["sdk_message"], UserMessage):
            sdk_msg = message_data["sdk_message"]

            # Use pre-extracted content if available (claude_sdk already handled string vs list extraction)
            if "content" in message_data and isinstance(message_data.get("content"), str):
                text_parts.append(message_data["content"])

            # Extract tool results and tool uses from SDK blocks
            if hasattr(sdk_msg, 'content') and isinstance(sdk_msg.content, list):
                for block in sdk_msg.content:
                    if isinstance(block, ToolResultBlock):
                        # Normalize content to always be a string
                        content = block.content
                        if isinstance(content, list):
                            # Handle structured content arrays (e.g., from Task tool)
                            # Format: [{"type": "text", "text": "..."}, ...]
                            text_parts_content = []
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_parts_content.append(item.get("text", ""))
                            content = "\n".join(text_parts_content) if text_parts_content else str(content)
                        elif not isinstance(content, str):
                            # Fallback for any other non-string content
                            content = str(content)

                        tool_results.append({
                            "tool_use_id": block.tool_use_id,
                            "content": content,
                            "is_error": block.is_error if hasattr(block, 'is_error') else False,
                            "timestamp": message_data.get("timestamp", time.time())
                        })
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                            "timestamp": message_data.get("timestamp", time.time())
                        })

        # Handle clean dict format (stored messages without SDK object)
        elif "content" in message_data and isinstance(message_data.get("content"), str):
            self._extract_from_dict_format(message_data, text_parts, tool_results, tool_uses)

        # Handle other dict-based formats
        else:
            self._extract_from_dict_format(message_data, text_parts, tool_results, tool_uses)

        # Extract additional legacy format data
        message = message_data.get("message", {})
        if message:
            extracted["metadata"]["role"] = message.get("role")

        # Populate extracted data
        content = " ".join(text_parts) if text_parts else ""
        if tool_results and not content:
            content = f"Tool results: {len(tool_results)} results"

        # Detect and extract local command responses (slash commands like /context, /cost, /todos)
        if content and '<local-command-stdout>' in content:
            # Extract content from inside the tags
            match = re.search(r'<local-command-stdout>(.*?)</local-command-stdout>', content, re.DOTALL)
            if match:
                extracted_content = match.group(1).strip()
                extracted["content"] = extracted_content
                # Mark as local command response for special frontend handling
                extracted["metadata"]["is_local_command_response"] = True
                extracted["metadata"]["subtype"] = "local_command_response"
                parser_logger.debug(f"Detected local command response, extracted content length: {len(extracted_content)}")
            else:
                # Fallback: just set the flag but keep original content
                extracted["content"] = content
                extracted["metadata"]["is_local_command_response"] = True
                extracted["metadata"]["subtype"] = "local_command_response"
        else:
            extracted["content"] = content

        extracted["metadata"]["tool_results"] = tool_results
        extracted["metadata"]["tool_uses"] = tool_uses
        extracted["metadata"]["has_tool_results"] = len(tool_results) > 0
        extracted["metadata"]["has_tool_uses"] = len(tool_uses) > 0

        return extracted


    def _extract_from_dict_format(self, message_data: dict[str, Any], text_parts: list[str],
                                tool_results: list[dict], tool_uses: list[dict]):
        """Extract data from legacy dictionary format."""
        # First check for direct content field (for simple user messages)
        direct_content = message_data.get("content", "")
        if isinstance(direct_content, str) and direct_content:
            text_parts.append(direct_content)
        else:
            # Check nested message structure
            message = message_data.get("message", {})
            content_parts = message.get("content", [])

            if isinstance(content_parts, list):
                for part in content_parts:
                    if part.get("type") == "tool_result":
                        tool_results.append({
                            "tool_use_id": part.get("tool_use_id"),
                            "content": part.get("content", ""),
                            "is_error": part.get("is_error", False),
                            "timestamp": message_data.get("timestamp", time.time())
                        })
                    elif part.get("type") == "tool_use":
                        tool_uses.append({
                            "id": part.get("id"),
                            "name": part.get("name"),
                            "input": part.get("input", {}),
                            "timestamp": message_data.get("timestamp", time.time())
                        })
                    elif part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
            elif isinstance(content_parts, str):
                text_parts.append(content_parts)

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.USER,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class ResultMessageHandler(MessageHandler):
    """Handler for SDK result/completion messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check if it's an SDK ResultMessage object
        if "sdk_message" in message_data:
            return isinstance(message_data["sdk_message"], ResultMessage)
        # Fallback to type field check
        return message_data.get("type") == "result"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from result message."""
        subtype = message_data.get("subtype", "unknown")
        is_error = message_data.get("is_error", False)

        extracted = {
            "type": "result",
            "content": message_data.get("result", f"Conversation {subtype}"),
            "metadata": {
                "subtype": subtype,
                "is_error": is_error,
                "error_type": message_data.get("error_type") if is_error else None,
                "error_code": message_data.get("error_code") if is_error else None,
                "stack_trace": message_data.get("stack_trace") if is_error else None,
                "session_id": message_data.get("session_id"),
                "duration_ms": message_data.get("duration_ms"),
                "duration_api_ms": message_data.get("duration_api_ms"),
                "num_turns": message_data.get("num_turns"),
                "total_cost_usd": message_data.get("total_cost_usd"),
                "usage": message_data.get("usage", {}),
                "permission_denials": message_data.get("permission_denials", []),
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

        return extracted

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)
        is_error = business_data["metadata"]["is_error"]

        return ParsedMessage(
            type=MessageType.RESULT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            error_message=business_data["content"] if is_error else None,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class ThinkingBlockHandler(MessageHandler):
    """Handler for thinking block content."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check for SDK ThinkingBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ThinkingBlock) for block in sdk_msg.content)
        return message_data.get("type") == "thinking"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from thinking block message."""
        content = ""
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ThinkingBlock):
                        content = block.thinking
                        break
        else:
            content = message_data.get("content", message_data.get("text", ""))

        return {
            "type": "thinking",
            "content": content,
            "metadata": {
                "thinking_content": content,
                "session_id": message_data.get("session_id"),
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": True,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.THINKING,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class ToolUseHandler(MessageHandler):
    """Handler for tool use messages (if SDK supports them)."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check for SDK ToolUseBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ToolUseBlock) for block in sdk_msg.content)
        return message_data.get("type") in ["tool_use", "tool_call"]

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from tool use message."""
        tool_name = "unknown"
        tool_id = ""
        tool_input = {}

        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        tool_id = block.id
                        tool_input = block.input
                        break
        else:
            tool_name = message_data.get("tool_name", message_data.get("name", "unknown"))
            tool_id = message_data.get("id", message_data.get("tool_call_id", ""))
            tool_input = message_data.get("input", message_data.get("parameters", {}))

        return {
            "type": "tool_use",
            "content": f"Using tool: {tool_name}",
            "metadata": {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "tool_input": tool_input,
                "session_id": message_data.get("session_id"),
                "has_tool_uses": True,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.TOOL_USE,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class ToolResultHandler(MessageHandler):
    """Handler for tool result messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        # Check for SDK ToolResultBlock
        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                return any(isinstance(block, ToolResultBlock) for block in sdk_msg.content)
        return message_data.get("type") == "tool_result"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from tool result message."""
        tool_use_id = ""
        content = ""
        is_error = False

        if "sdk_message" in message_data:
            sdk_msg = message_data["sdk_message"]
            if hasattr(sdk_msg, 'content'):
                for block in sdk_msg.content:
                    if isinstance(block, ToolResultBlock):
                        tool_use_id = block.tool_use_id
                        content = block.content
                        is_error = getattr(block, 'is_error', False)
                        break
        else:
            tool_use_id = message_data.get("tool_use_id", "")
            content = message_data.get("content", "")
            is_error = message_data.get("is_error", False)

        return {
            "type": "tool_result",
            "content": content,
            "metadata": {
                "tool_use_id": tool_use_id,
                "is_error": is_error,
                "session_id": message_data.get("session_id"),
                "has_tool_uses": False,
                "has_tool_results": True,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)
        is_error = business_data["metadata"]["is_error"]

        return ParsedMessage(
            type=MessageType.TOOL_RESULT,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            error_message=business_data["content"] if is_error else None,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class ErrorHandler(MessageHandler):
    """Handler for error messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        return message_data.get("type") in ["error", "exception", "warning"]

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from error message."""
        error_type = message_data.get("type", "error")
        content = message_data.get("message", message_data.get("error", ""))

        return {
            "type": error_type,
            "content": content,
            "metadata": {
                "error_type": error_type,
                "error_code": message_data.get("code"),
                "stack_trace": message_data.get("stack_trace"),
                "session_id": message_data.get("session_id"),
                "is_error": True,
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)
        error_type = business_data["metadata"]["error_type"]
        message_type = MessageType.ERROR if error_type != "warning" else MessageType.WARNING

        return ParsedMessage(
            type=message_type,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            error_message=business_data["content"],
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class PermissionRequestHandler(MessageHandler):
    """Handler for permission request messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        return message_data.get("type") == "permission_request"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from permission request message."""
        tool_name = message_data.get("tool_name", "unknown")
        suggestions = message_data.get("suggestions", [])

        extracted = {
            "type": "permission_request",
            "content": message_data.get("content", f"Permission requested for tool: {tool_name}"),
            "metadata": {
                "tool_name": tool_name,
                "input_params": message_data.get("input_params", {}),
                "request_id": message_data.get("request_id"),
                "session_id": message_data.get("session_id"),
                "suggestions": suggestions,
                "has_suggestions": len(suggestions) > 0,
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": True,
                "has_permission_responses": False
            }
        }

        return extracted

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.PERMISSION_REQUEST,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class PermissionResponseHandler(MessageHandler):
    """Handler for permission response messages."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        return message_data.get("type") == "permission_response"

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from permission response message."""
        decision = message_data.get("decision", "unknown")
        tool_name = message_data.get("tool_name", "unknown")
        applied_updates = message_data.get("applied_updates", [])

        # Extract update types for quick reference
        applied_update_types = [u.get('type') for u in applied_updates if u.get('type')]

        extracted = {
            "type": "permission_response",
            "content": message_data.get("content", f"Permission {decision} for tool: {tool_name}"),
            "metadata": {
                "request_id": message_data.get("request_id"),
                "decision": decision,
                "reasoning": message_data.get("reasoning"),
                "tool_name": tool_name,
                "response_time_ms": message_data.get("response_time_ms"),
                "session_id": message_data.get("session_id"),
                "applied_updates": applied_updates,
                "applied_update_types": applied_update_types,
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": True
            }
        }

        return extracted

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.PERMISSION_RESPONSE,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class UnknownMessageHandler(MessageHandler):
    """Handler for unknown message types - always handles as fallback."""

    def can_handle(self, message_data: dict[str, Any]) -> bool:
        return True  # Always handles as fallback

    def _extract_business_data(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Extract business-relevant data from unknown message."""
        message_type = message_data.get("type", "unknown")
        content = str(message_data)

        return {
            "type": "unknown",
            "content": content,
            "metadata": {
                "original_type": message_type,
                "unknown_format": True,
                "parse_error": False,
                "session_id": message_data.get("session_id"),
                "has_tool_uses": False,
                "has_tool_results": False,
                "has_thinking": False,
                "has_permission_requests": False,
                "has_permission_responses": False
            }
        }

    def parse(self, message_data: dict[str, Any]) -> ParsedMessage:
        # Extract all business data upfront
        business_data = self._extract_business_data(message_data)

        return ParsedMessage(
            type=MessageType.UNKNOWN,
            timestamp=message_data.get("timestamp", time.time()),
            session_id=message_data.get("session_id"),
            content=business_data["content"],
            raw_data=message_data,
            metadata=self._standardize_metadata_fields(business_data["metadata"], message_data)
        )


class MessageParser:
    """
    Extensible parser for Claude Code SDK messages.

    Maintains a registry of handlers and provides graceful fallback
    for unknown message types.
    """

    def __init__(self):
        """Initialize the message parser with default handlers."""
        self.handlers: list[MessageHandler] = []
        self.stats = {
            "total_parsed": 0,
            "unknown_types": 0,
            "parse_errors": 0,
            "type_counts": {}
        }
        self.unknown_types: set[str] = set()

        # Register default handlers
        self._register_default_handlers()

        parser_logger.debug("MessageParser initialized with default handlers")

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

            # Permission handlers
            PermissionRequestHandler(),
            PermissionResponseHandler(),

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

        parser_logger.debug(f"Registered handler: {handler.__class__.__name__}")

    def parse_message(self, message_data: dict[str, Any]) -> ParsedMessage:
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

                    parser_logger.debug(f"Parsed message: {parsed.type.value}")
                    return parsed

            # Should never reach here due to UnknownMessageHandler fallback
            logger.error("No handler found for message (this shouldn't happen)")
            return self._create_error_message(message_data, "No handler found")

        except Exception as e:
            logger.error(f"Error parsing message: {e}")
            self.stats["parse_errors"] += 1
            return self._create_error_message(message_data, str(e))

    def _create_error_message(self, message_data: dict[str, Any], error_msg: str) -> ParsedMessage:
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

    def get_stats(self) -> dict[str, Any]:
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
        parser_logger.debug("MessageParser stats reset")

    def get_unknown_types(self) -> list[str]:
        """Get list of unknown message types encountered."""
        return list(self.unknown_types)


class MessageProcessor:
    """Unified message processor that handles both SDK objects and stored JSON consistently."""

    def __init__(self, message_parser: MessageParser):
        """Initialize with a MessageParser instance."""
        self.parser = message_parser
        self.logger = parser_logger  # Use specialized parser logger

    def process_message(self, message_data: dict[str, Any], source: str = "sdk") -> ParsedMessage:
        """
        Process message from any source (SDK, storage) consistently.

        Args:
            message_data: Raw message data (SDK object or stored JSON)
            source: Source of the message ("sdk", "storage", "websocket")

        Returns:
            ParsedMessage: Fully processed message with metadata
        """
        try:
            self.logger.debug(f"Processing message from source: {source}")

            # Use the existing MessageParser to handle the message
            parsed_message = self.parser.parse_message(message_data)

            # Add source information to metadata
            if not hasattr(parsed_message, 'metadata') or parsed_message.metadata is None:
                parsed_message.metadata = {}
            parsed_message.metadata['source'] = source
            parsed_message.metadata['processed_at'] = time.time()

            self.logger.debug(f"Successfully processed {parsed_message.type.value} message from {source}")
            return parsed_message

        except Exception as e:
            self.logger.error(f"Error processing message from {source}: {e}")
            # Return error message instead of raising
            return ParsedMessage(
                type=MessageType.ERROR,
                timestamp=time.time(),
                content=f"Processing error from {source}: {str(e)}",
                error_message=str(e),
                metadata={
                    "source": source,
                    "processing_error": True,
                    "original_data": message_data
                }
            )

    def prepare_for_storage(self, parsed_message: ParsedMessage) -> dict[str, Any]:
        """
        Prepare message for storage with all metadata.

        Args:
            parsed_message: Parsed message to prepare for storage

        Returns:
            Dict containing all data needed for storage
        """
        try:
            storage_data = {
                "type": parsed_message.type.value,
                "content": parsed_message.content,
                "timestamp": parsed_message.timestamp
            }

            # Add metadata with serialization safety check
            if parsed_message.metadata:
                serializable_metadata = {}
                for key, value in parsed_message.metadata.items():
                    # Test serializability
                    try:
                        json.dumps(value)
                        serializable_metadata[key] = value
                    except (TypeError, ValueError):
                        if key == 'sdk_message':
                            # For SDK message objects, convert to string representation
                            serializable_metadata[key] = str(value)
                        else:
                            # For other non-serializable data, convert to string
                            self.logger.warning(f"Converting non-serializable metadata field '{key}' to string")
                            serializable_metadata[key] = str(value)

                storage_data["metadata"] = serializable_metadata

            # Add optional fields if present
            if parsed_message.session_id:
                storage_data["session_id"] = parsed_message.session_id


            if parsed_message.error_message:
                storage_data["error_message"] = parsed_message.error_message

            self.logger.debug(f"Prepared {parsed_message.type.value} message for storage")
            return storage_data

        except Exception as e:
            self.logger.error(f"Error preparing message for storage: {e}")
            # Return minimal storage format
            return {
                "type": "error",
                "content": f"Storage preparation error: {str(e)}",
                "timestamp": time.time(),
                "metadata": {"storage_error": True}
            }

    def prepare_for_websocket(self, parsed_message: ParsedMessage) -> dict[str, Any]:
        """
        Prepare message for WebSocket transmission.

        Args:
            parsed_message: Parsed message to prepare for WebSocket

        Returns:
            Dict containing serializable data for WebSocket
        """
        try:
            websocket_data = {
                "type": parsed_message.type.value,
                "content": parsed_message.content,
                "timestamp": parsed_message.timestamp
            }

            # Add metadata if available, excluding non-serializable fields
            if parsed_message.metadata:
                serializable_metadata = {}
                for key, value in parsed_message.metadata.items():
                    # Skip SDK message objects (not serializable for WebSocket)
                    if key == 'sdk_message':
                        continue

                    # Test serializability
                    try:
                        json.dumps(value)
                        serializable_metadata[key] = value
                    except (TypeError, ValueError):
                        # Convert non-serializable to string
                        serializable_metadata[key] = str(value)

                if serializable_metadata:
                    websocket_data["metadata"] = serializable_metadata

            # Add optional fields
            if parsed_message.session_id:
                websocket_data["session_id"] = parsed_message.session_id

            # Maintain backward compatibility with subtype at root level
            if parsed_message.metadata and 'subtype' in parsed_message.metadata:
                websocket_data["subtype"] = parsed_message.metadata['subtype']

            self.logger.debug(f"Prepared {parsed_message.type.value} message for WebSocket")
            return websocket_data

        except Exception as e:
            self.logger.error(f"Error preparing message for WebSocket: {e}")
            # Return minimal WebSocket format
            return {
                "type": "error",
                "content": f"WebSocket preparation error: {str(e)}",
                "timestamp": time.time()
            }

    def process_batch(self, messages: list[dict[str, Any]], source: str = "storage") -> list[ParsedMessage]:
        """
        Process multiple messages efficiently.

        Args:
            messages: List of raw message data
            source: Source of the messages

        Returns:
            List of ParsedMessage objects
        """
        processed_messages = []

        for i, message_data in enumerate(messages):
            try:
                parsed = self.process_message(message_data, source)
                processed_messages.append(parsed)
            except Exception as e:
                self.logger.warning(f"Failed to process message {i} from {source}: {e}")
                # Add error message for failed processing
                error_message = ParsedMessage(
                    type=MessageType.ERROR,
                    timestamp=time.time(),
                    content=f"Batch processing error for message {i}: {str(e)}",
                    error_message=str(e),
                    metadata={
                        "source": source,
                        "batch_index": i,
                        "batch_error": True
                    }
                )
                processed_messages.append(error_message)

        self.logger.debug(f"Processed {len(processed_messages)} messages from {source}")
        return processed_messages
