"""Claude Code SDK integration and session management."""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, AsyncIterator
from enum import Enum
from dataclasses import dataclass, asdict

from .logging_config import get_logger

logger = get_logger(__name__)


class SessionState(Enum):
    """Claude Code SDK session states."""
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass
class SessionInfo:
    """Information about a Claude Code SDK session."""
    session_id: str
    working_directory: str
    state: SessionState = SessionState.CREATED
    start_time: Optional[float] = None
    last_activity: Optional[float] = None
    message_count: int = 0
    error_message: Optional[str] = None


class ClaudeSDK:
    """
    Manages Claude Code SDK integration and streaming conversations.

    Handles session lifecycle, SDK configuration, and message streaming.
    """

    def __init__(
        self,
        session_id: str,
        working_directory: str,
        message_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        error_callback: Optional[Callable[[str, Exception], None]] = None,
        initial_prompt: Optional[str] = None
    ):
        """
        Initialize Claude Code SDK wrapper.

        Args:
            session_id: Unique session identifier
            working_directory: Directory where Claude Code should run
            message_callback: Called when new messages are received
            error_callback: Called when errors occur
            initial_prompt: Initial prompt for the conversation
        """
        self.session_id = session_id
        self.working_directory = Path(working_directory)
        self.message_callback = message_callback
        self.error_callback = error_callback
        self.initial_prompt = initial_prompt

        self.info = SessionInfo(session_id=session_id, working_directory=str(self.working_directory))

        # SDK will be imported dynamically to avoid dependency issues during development
        self._sdk_query = None
        self._sdk_options = None

        # Control
        self._current_iterator: Optional[AsyncIterator] = None
        self._shutdown_event = asyncio.Event()

        logger.info(f"Initialized Claude SDK wrapper for session {session_id}")

    async def start(self) -> bool:
        """
        Start the Claude Code SDK session.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting Claude Code SDK session in {self.working_directory}")
            self.info.state = SessionState.STARTING
            self.info.start_time = time.time()

            # Verify working directory exists
            if not self.working_directory.exists():
                raise FileNotFoundError(f"Working directory does not exist: {self.working_directory}")

            # Import SDK (required)
            from claude_code_sdk import query, ClaudeCodeOptions
            self._sdk_query = query
            self._sdk_options = ClaudeCodeOptions
            logger.info("Claude Code SDK imported successfully")

            # Configure SDK options
            options = self._sdk_options(
                cwd=str(self.working_directory),
                permission_mode="acceptEdits",  # For testing - should be configurable
            )

            self.info.state = SessionState.RUNNING
            logger.info(f"Claude Code SDK session started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Claude Code SDK session: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                self.error_callback("startup_failed", e)
            return False

    async def send_message(self, message: str) -> bool:
        """
        Send a message to the Claude Code SDK.

        Args:
            message: Message text to send

        Returns:
            True if sent successfully, False otherwise
        """
        if not self._is_ready_for_input():
            logger.warning(f"Session not ready for input, state: {self.info.state}")
            return False

        try:
            logger.debug(f"Sending message: {message[:100]}...")
            self.info.state = SessionState.PROCESSING

            # Run SDK conversation
            await self._run_sdk_conversation(message)

            self.info.message_count += 1
            self.info.last_activity = time.time()
            logger.info(f"Sent message #{self.info.message_count}")
            return True

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                self.error_callback("message_send_failed", e)
            return False

    async def _run_sdk_conversation(self, message: str) -> None:
        """Run actual SDK conversation."""
        try:
            options = self._sdk_options(
                cwd=str(self.working_directory),
                permission_mode="acceptEdits",
            )

            # Create async iterator for streaming messages
            async for sdk_message in self._sdk_query(prompt=message, options=options):
                if self._shutdown_event.is_set():
                    break

                # Convert SDK message to our format
                converted_message = self._convert_sdk_message(sdk_message)
                self.info.last_activity = time.time()

                # Call message callback
                if self.message_callback:
                    self.message_callback(converted_message)

                logger.debug(f"Processed SDK message: {converted_message.get('type', 'unknown')}")

            self.info.state = SessionState.COMPLETED

        except Exception as e:
            logger.error(f"Error in SDK conversation: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                self.error_callback("sdk_conversation_failed", e)

    def _convert_sdk_message(self, sdk_message: Any) -> Dict[str, Any]:
        """Convert SDK message to a serializable format while preserving type information."""
        try:
            # Import SDK classes for type checking
            from claude_code_sdk import UserMessage, AssistantMessage, SystemMessage, ResultMessage

            # Handle dict-like objects (for backward compatibility)
            if isinstance(sdk_message, dict):
                message_dict = {
                    "type": sdk_message.get("type", "unknown"),
                    "sdk_message": sdk_message,
                    "timestamp": time.time(),
                    "session_id": self.session_id
                }
                # Copy all fields from dict
                message_dict.update(sdk_message)
                return message_dict

            # Handle SDK message objects
            message_type = "unknown"
            if isinstance(sdk_message, UserMessage):
                message_type = "user"
            elif isinstance(sdk_message, AssistantMessage):
                message_type = "assistant"
            elif isinstance(sdk_message, SystemMessage):
                message_type = "system"
            elif isinstance(sdk_message, ResultMessage):
                message_type = "result"

            # Create message dict with type and original SDK message
            message_dict = {
                "type": message_type,
                "sdk_message": sdk_message,  # Keep original SDK message object
                "timestamp": time.time(),
                "session_id": self.session_id
            }

            # Add content for serialization if needed
            if hasattr(sdk_message, 'content'):
                # Handle content which can be string or list of blocks
                content = sdk_message.content
                if isinstance(content, str):
                    message_dict["content"] = content
                elif isinstance(content, list):
                    # Extract text from content blocks
                    text_parts = []
                    for block in content:
                        if hasattr(block, 'text'):
                            text_parts.append(block.text)
                    message_dict["content"] = " ".join(text_parts) if text_parts else ""

            # Copy other common attributes from SDK message
            for attr in ['message', 'data', 'subtype', 'error', 'usage', 'model']:
                if hasattr(sdk_message, attr):
                    message_dict[attr] = getattr(sdk_message, attr)

            # For unknown objects, add string representation as content
            if message_type == "unknown":
                message_dict["content"] = str(sdk_message)

            return message_dict

        except Exception as e:
            logger.warning(f"Failed to convert SDK message: {e}")
            # For completely unknown objects, convert to string representation
            content = str(sdk_message) if not isinstance(sdk_message, dict) else ""
            return {
                "type": "unknown",
                "sdk_message": sdk_message,
                "timestamp": time.time(),
                "session_id": self.session_id,
                "content": content,
                "conversion_error": str(e)
            }

    def _is_ready_for_input(self) -> bool:
        """Check if the session is ready to accept input."""
        return self.info.state in [SessionState.RUNNING]

    async def terminate(self, timeout: float = 5.0) -> bool:
        """
        Terminate the Claude Code SDK session gracefully.

        Args:
            timeout: Seconds to wait for graceful shutdown

        Returns:
            True if terminated successfully, False otherwise
        """
        logger.info(f"Terminating Claude Code SDK session {self.session_id}")

        self.info.state = SessionState.TERMINATED
        self._shutdown_event.set()

        try:
            # For SDK sessions, we just need to stop the iterator
            if self._current_iterator:
                # The async iterator should stop when _shutdown_event is set
                pass

            logger.info("Claude Code SDK session terminated successfully")
            return True

        except Exception as e:
            logger.error(f"Error terminating SDK session: {e}")
            return False

    def get_info(self) -> Dict[str, Any]:
        """Get current session information."""
        info_dict = asdict(self.info)
        # Convert enum to string value for JSON serialization
        info_dict["state"] = self.info.state.value
        return info_dict

    def is_running(self) -> bool:
        """Check if the session is currently running."""
        return self.info.state in [SessionState.RUNNING, SessionState.PROCESSING]