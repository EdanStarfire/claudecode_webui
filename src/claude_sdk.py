"""Claude Code SDK integration and session management."""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, AsyncIterator
from enum import Enum
from dataclasses import dataclass, asdict
from queue import Queue

from .logging_config import get_logger
from .data_storage import DataStorageManager

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
    Enhanced Claude Code SDK wrapper with interactive conversation support.

    Handles session lifecycle, SDK configuration, message streaming,
    message queuing, and persistent storage integration.
    """

    def __init__(
        self,
        session_id: str,
        working_directory: str,
        storage_manager: Optional[DataStorageManager] = None,
        message_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        error_callback: Optional[Callable[[str, Exception], None]] = None,
        permission_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        permissions: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        tools: List[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialize enhanced Claude Code SDK wrapper.

        Args:
            session_id: Unique session identifier
            working_directory: Directory where Claude Code should run
            storage_manager: Data storage manager for persistence
            message_callback: Called when new messages are received
            error_callback: Called when errors occur
            permission_callback: Called to check for tool permissions
            permissions: SDK permission mode
            system_prompt: Custom system prompt
            tools: List of allowed tools
            model: Model to use
        """
        self.session_id = session_id
        self.working_directory = Path(working_directory)
        self.storage_manager = storage_manager
        self.message_callback = message_callback
        self.error_callback = error_callback
        self.permission_callback = permission_callback
        self.permissions = permissions
        self.system_prompt = system_prompt
        self.tools = tools or ["bash", "edit", "read"]
        self.model = model

        self.info = SessionInfo(session_id=session_id, working_directory=str(self.working_directory))

        # SDK will be imported dynamically to avoid dependency issues during development
        self._sdk_query = None
        self._sdk_options = None

        # Interactive conversation support
        self._message_queue = asyncio.Queue()
        self._conversation_task: Optional[asyncio.Task] = None

        # Control
        self._current_iterator: Optional[AsyncIterator] = None
        self._shutdown_event = asyncio.Event()

        logger.info(f"Initialized enhanced Claude SDK wrapper for session {session_id}")

    async def start(self) -> bool:
        """
        Start the Claude Code SDK session with interactive conversation support.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting enhanced Claude Code SDK session in {self.working_directory}")
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

            # Start conversation task for message queue processing
            self._conversation_task = asyncio.create_task(self._conversation_loop())

            self.info.state = SessionState.RUNNING
            logger.info(f"Enhanced Claude Code SDK session started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Claude Code SDK session: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "startup_failed", e)
            return False

    async def send_message(self, message: str) -> bool:
        """
        Queue a message to send to the Claude Code SDK.

        Args:
            message: Message text to send

        Returns:
            True if queued successfully, False otherwise
        """
        if not self._is_ready_for_input():
            logger.warning(f"Session not ready for input, state: {self.info.state}")
            return False

        try:
            logger.debug(f"Queuing message: {message[:100]}...")

            # Add to message queue
            await self._message_queue.put({
                "type": "user_message",
                "content": message,
                "timestamp": time.time()
            })

            logger.info(f"Message queued successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_queue_failed", e)
            return False

    async def _conversation_loop(self):
        """Main conversation loop that processes queued messages"""
        logger.info("Starting conversation loop")

        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for message with timeout to allow shutdown checking
                    message_data = await asyncio.wait_for(
                        self._message_queue.get(),
                        timeout=1.0
                    )

                    if message_data["type"] == "user_message":
                        await self._process_user_message(message_data["content"])

                except asyncio.TimeoutError:
                    # Normal timeout, continue loop
                    continue

                except Exception as e:
                    logger.error(f"Error in conversation loop: {e}")
                    if self.error_callback:
                        await self._safe_callback(self.error_callback, "conversation_loop_error", e)
                    await asyncio.sleep(1.0)  # Prevent tight error loop

        except Exception as e:
            logger.error(f"Fatal error in conversation loop: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)

        logger.info("Conversation loop ended")

    async def _process_user_message(self, message: str):
        """Process a single user message through the SDK"""
        if self._processing_message:
            logger.warning("Already processing a message, skipping")
            return

        self._processing_message = True
        try:
            logger.debug(f"Processing user message: {message[:100]}...")
            self.info.state = SessionState.PROCESSING

            # Store user message if storage available
            if self.storage_manager:
                await self.storage_manager.append_message({
                    "type": "user",
                    "content": message,
                    "session_id": self.session_id
                })

            # Run SDK conversation
            await self._run_sdk_conversation(message)

            self.info.message_count += 1
            self.info.last_activity = time.time()
            self.info.state = SessionState.RUNNING

            logger.info(f"Processed message #{self.info.message_count}")

        except Exception as e:
            logger.error(f"Failed to process user message: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_processing_failed", e)
        finally:
            self._processing_message = False

    async def _can_use_tool_callback(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """
        Callback to decide if a tool can be used.
        Delegates the decision to an external callback if provided.
        """
        if self.permission_callback:
            logger.info(f"Delegating permission check for tool '{tool_name}' to external callback.")
            try:
                # Await the callback if it's a coroutine function
                if asyncio.iscoroutinefunction(self.permission_callback):
                    return await self.permission_callback(tool_name, tool_input)
                else:
                    return self.permission_callback(tool_name, tool_input)
            except Exception as e:
                logger.error(f"Error in external permission_callback: {e}")
                return False # Deny on error

        logger.warning(f"No permission_callback provided. Denying tool use: '{tool_name}'")
        return False

    async def _run_sdk_conversation(self, message: str) -> None:
        """Run SDK conversation with enhanced configuration and storage integration."""
        try:
            # Configure SDK options with correct parameter names
            options_kwargs = {
                "cwd": str(self.working_directory),
                "permission_mode": self.permissions,
                "system_prompt": self.system_prompt,
                "allowed_tools": self.tools,
                "can_use_tool": self._can_use_tool_callback,
            }

            # Only add model if specified (let SDK use default otherwise)
            if self.model is not None:
                options_kwargs["model"] = self.model

            options = self._sdk_options(**options_kwargs)

            # To enable streaming mode for the can_use_tool callback, the prompt
            # must be an async iterable yielding a dict, not a string.
            async def prompt_generator():
                yield {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": message
                    }
                }

            # Create async iterator for streaming messages
            self._current_iterator = self._sdk_query(prompt=prompt_generator(), options=options)

            async for sdk_message in self._current_iterator:
                if self._shutdown_event.is_set():
                    logger.debug("Shutdown requested, breaking from SDK conversation")
                    break

                # Convert SDK message to our format
                converted_message = self._convert_sdk_message(sdk_message)
                self.info.last_activity = time.time()

                # Store message if storage available
                if self.storage_manager:
                    # Create JSON-serializable version for storage
                    storage_message = {
                        "type": converted_message.get("type", "unknown"),
                        "content": converted_message.get("content", ""),
                        "session_id": converted_message.get("session_id"),
                        "timestamp": converted_message.get("timestamp"),
                        "sdk_message_type": converted_message.get("sdk_message").__class__.__name__ if converted_message.get("sdk_message") else None
                    }
                    # Add any other serializable fields
                    for key, value in converted_message.items():
                        if key not in ["sdk_message"] and isinstance(value, (str, int, float, bool, type(None))):
                            storage_message[key] = value

                    await self.storage_manager.append_message(storage_message)

                # Call message callback
                if self.message_callback:
                    await self._safe_callback(self.message_callback, converted_message)

                logger.debug(f"Processed SDK message: {converted_message.get('type', 'unknown')}")

        except Exception as e:
            logger.error(f"Error in SDK conversation: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)

            # Store error message
            if self.storage_manager:
                await self.storage_manager.append_message({
                    "type": "error",
                    "content": f"SDK conversation failed: {str(e)}",
                    "session_id": self.session_id,
                    "error": True,
                    "timestamp": time.time()
                })

            if self.error_callback:
                await self._safe_callback(self.error_callback, "sdk_conversation_failed", e)
        finally:
            self._current_iterator = None

    async def _safe_callback(self, callback: Callable, *args, **kwargs):
        """Safely execute callback with error handling"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args, **kwargs)
            else:
                callback(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in callback execution: {e}")

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
        Terminate the enhanced Claude Code SDK session gracefully.

        Args:
            timeout: Seconds to wait for graceful shutdown

        Returns:
            True if terminated successfully, False otherwise
        """
        logger.info(f"Terminating enhanced Claude Code SDK session {self.session_id}")

        self.info.state = SessionState.TERMINATED
        self._shutdown_event.set()

        try:
            # Cancel conversation task
            if self._conversation_task and not self._conversation_task.done():
                self._conversation_task.cancel()
                try:
                    await asyncio.wait_for(self._conversation_task, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("Conversation task did not terminate within timeout")
                except asyncio.CancelledError:
                    logger.debug("Conversation task cancelled successfully")

            # Clear message queue
            while not self._message_queue.empty():
                try:
                    self._message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Cleanup storage
            if self.storage_manager:
                await self.storage_manager.cleanup()

            logger.info("Enhanced Claude Code SDK session terminated successfully")
            return True

        except Exception as e:
            logger.error(f"Error terminating enhanced SDK session: {e}")
            return False

    def get_queue_size(self) -> int:
        """Get current message queue size"""
        return self._message_queue.qsize()

    def is_processing(self) -> bool:
        """Check if currently processing a message"""
        return self._processing_message

    def get_info(self) -> Dict[str, Any]:
        """Get current session information."""
        info_dict = asdict(self.info)
        # Convert enum to string value for JSON serialization
        info_dict["state"] = self.info.state.value
        return info_dict

    def is_running(self) -> bool:
        """Check if the session is currently running."""
        return self.info.state in [SessionState.RUNNING, SessionState.PROCESSING]