"""Claude Code SDK integration and session management."""

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, Union
from enum import Enum
from dataclasses import dataclass, asdict

from .logging_config import get_logger
from .data_storage import DataStorageManager

# Import SDK components
try:
    from claude_code_sdk import (
        ClaudeSDKClient,
        ClaudeCodeOptions,
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
        AssistantMessage,
        UserMessage,
        SystemMessage,
        ResultMessage,
        TextBlock
    )
except ImportError:
    # Fallback for development/testing environments
    ClaudeSDKClient = None
    ClaudeCodeOptions = None
    PermissionResultAllow = None
    PermissionResultDeny = None
    ToolPermissionContext = None
    AssistantMessage = None
    UserMessage = None
    SystemMessage = None
    ResultMessage = None
    TextBlock = None

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
        session_manager: Optional[Any] = None,
        message_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        error_callback: Optional[Callable[[str, Exception], None]] = None,
        permission_callback: Optional[Callable[[str, Dict[str, Any]], Union[bool, Dict[str, Any]]]] = None,
        permissions: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        tools: List[str] = None,
        model: Optional[str] = None,
        resume_session_id: Optional[str] = None
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
        self.session_manager = session_manager
        self.message_callback = message_callback
        self.error_callback = error_callback
        self.permission_callback = permission_callback
        self.permissions = permissions
        self.system_prompt = system_prompt
        self.tools = tools if tools is not None else []
        self.model = model
        self.resume_session_id = resume_session_id

        self.info = SessionInfo(session_id=session_id, working_directory=str(self.working_directory))

        # New SDK client pattern
        self._sdk_client: Optional[ClaudeSDKClient] = None
        self._sdk_options: Optional[ClaudeCodeOptions] = None

        # Interactive conversation support
        self._message_queue = asyncio.Queue()
        self._conversation_task: Optional[asyncio.Task] = None

        # Control
        self._shutdown_event = asyncio.Event()

        # Claude Code's actual session ID (captured from init message)
        self._claude_code_session_id: Optional[str] = None

        logger.info(f"Initialized enhanced Claude SDK wrapper for session {session_id}")

    async def start(self) -> bool:
        """
        Start the Claude Code SDK session with new ClaudeSDKClient pattern.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting Claude Code SDK session with ClaudeSDKClient in {self.working_directory}")
            self.info.state = SessionState.STARTING
            self.info.start_time = time.time()

            # Verify working directory exists
            if not self.working_directory.exists():
                raise FileNotFoundError(f"Working directory does not exist: {self.working_directory}")

            # Check SDK components are available
            if not ClaudeSDKClient or not ClaudeCodeOptions:
                raise ImportError("Claude Code SDK components not available")

            # Check for existing Claude Code session ID if this is a resume operation
            if self.resume_session_id is not None and self.session_manager:
                session_info = await self.session_manager.get_session_info(self.session_id)
                if session_info and session_info.claude_code_session_id:
                    logger.info(f"Using stored Claude Code session ID for resume: {session_info.claude_code_session_id}")
                    # Temporarily override resume_session_id with actual Claude Code ID
                    original_resume_id = self.resume_session_id
                    self.resume_session_id = session_info.claude_code_session_id
                    self._sdk_options = self._get_sdk_options()
                    # Restore original for tracking purposes
                    self.resume_session_id = original_resume_id
                else:
                    logger.warning(f"No stored Claude Code session ID found for WebUI session {self.session_id}, using WebUI ID for resume")
                    self._sdk_options = self._get_sdk_options()
            else:
                # Configure SDK options normally for new sessions
                self._sdk_options = self._get_sdk_options()
            logger.info("Claude Code SDK options configured")

            # Start message processing task
            self._conversation_task = asyncio.create_task(self._message_processing_loop())

            self.info.state = SessionState.RUNNING
            logger.info(f"Claude Code SDK session started successfully")
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

    async def _message_processing_loop(self):
        """
        Main message processing loop using the new ClaudeSDKClient pattern.
        """
        logger.info("Starting message processing loop")
        try:
            # Create SDK client with context manager
            async with ClaudeSDKClient(self._sdk_options) as client:
                self._sdk_client = client
                logger.info("ClaudeSDKClient initialized successfully")

                while not self._shutdown_event.is_set():
                    try:
                        # Wait for a message from the queue
                        message_data = await asyncio.wait_for(
                            self._message_queue.get(),
                            timeout=1.0
                        )

                        if message_data and message_data.get("content"):
                            content = message_data["content"]
                            logger.debug(f"Processing message: {content[:100]}...")

                            # Store user message if storage available
                            if self.storage_manager:
                                await self.storage_manager.append_message({
                                    "type": "user",
                                    "content": content,
                                    "session_id": self.session_id
                                })

                            self.info.message_count += 1
                            self.info.last_activity = time.time()

                            # Send message to Claude using the persistent session pattern
                            await client.query(content)

                            # Process all responses for this query (maintains session continuity)
                            async for response_message in client.receive_response():
                                if self._shutdown_event.is_set():
                                    break
                                await self._process_sdk_message(response_message)

                            # Session continues for next message - no re-initialization needed

                        self._message_queue.task_done()

                    except asyncio.TimeoutError:
                        # No message in queue, continue loop
                        continue
                    except asyncio.CancelledError:
                        logger.info("Message processing loop cancelled")
                        break
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        if self.error_callback:
                            await self._safe_callback(self.error_callback, "message_processing_error", e)

        except Exception as e:
            logger.error(f"Fatal error in message processing loop: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_processing_loop_error", e)
        finally:
            self._sdk_client = None
            logger.info("Message processing loop ended")


    def _get_sdk_options(self):
        """Configure SDK options with correct parameter names for new ClaudeSDKClient pattern."""

        # Create callback function with new PermissionResult return types
        async def can_use_tool_wrapper(
            tool_name: str,
            input_params: Dict[str, Any],
            context: ToolPermissionContext
        ) -> Union[PermissionResultAllow, PermissionResultDeny]:
            return await self._can_use_tool_callback(tool_name, input_params, context)

        options_kwargs = {
            "cwd": str(self.working_directory),
            "permission_mode": self.permissions,
            "system_prompt": self.system_prompt,
            "allowed_tools": self.tools,
        }

        # Only add can_use_tool callback if permission callback is provided and SDK classes are available
        if self.permission_callback and PermissionResultAllow and PermissionResultDeny:
            options_kwargs["can_use_tool"] = can_use_tool_wrapper

        if self.model is not None:
            options_kwargs["model"] = self.model

        if self.resume_session_id is not None:
            options_kwargs["resume"] = self.resume_session_id
            logger.info(f"Setting resume parameter to: {self.resume_session_id}")

        logger.info(f"ClaudeCodeOptions: {options_kwargs}")
        return ClaudeCodeOptions(**options_kwargs)

    async def _process_sdk_message(self, sdk_message: Any):
        """Process a single message from the SDK stream."""
        try:
            # Capture Claude Code's actual session ID from init message
            if hasattr(sdk_message, 'subtype') and sdk_message.subtype == 'init':
                session_id = getattr(sdk_message, 'data', {}).get('session_id') if hasattr(sdk_message, 'data') else None
                if session_id:
                    # Only store if this is a different session ID (prevent duplicates)
                    if not hasattr(self, '_claude_code_session_id') or self._claude_code_session_id != session_id:
                        self._claude_code_session_id = session_id
                        logger.info(f"Captured Claude Code session ID: {session_id} for WebUI session: {self.session_id}")

                        # Always store the latest Claude Code session ID for cumulative sessions
                        if self.session_manager:
                            await self.session_manager.update_claude_code_session_id(self.session_id, session_id)
                            if self.resume_session_id is None:
                                logger.info(f"Stored new Claude Code session ID: {session_id}")
                            else:
                                logger.info(f"Resume created new cumulative session {session_id} (was attempting to resume {self.resume_session_id})")
                                logger.info(f"Updated stored session ID to latest: {session_id}")

            converted_message = self._convert_sdk_message(sdk_message)
            self.info.last_activity = time.time()

            if self.storage_manager:
                await self._store_sdk_message(converted_message)

            if self.message_callback:
                await self._safe_callback(self.message_callback, converted_message)

            logger.debug(f"Processed SDK message: {converted_message.get('type', 'unknown')}")

        except Exception as e:
            logger.error(f"Failed to process SDK message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "sdk_message_processing_failed", e)
    
    async def _store_sdk_message(self, converted_message: Dict[str, Any]):
        """Store the SDK message in a serializable format."""
        storage_message = {
            "type": converted_message.get("type", "unknown"),
            "content": converted_message.get("content", ""),
            "session_id": converted_message.get("session_id"),
            "timestamp": converted_message.get("timestamp"),
            "sdk_message_type": converted_message.get("sdk_message").__class__.__name__
            if converted_message.get("sdk_message")
            else None,
        }
        for key, value in converted_message.items():
            if key not in ["sdk_message"] and isinstance(
                value, (str, int, float, bool, type(None))
            ):
                storage_message[key] = value
        await self.storage_manager.append_message(storage_message)

    async def _can_use_tool_callback(
        self,
        tool_name: str,
        input_params: Dict[str, Any],
        context: ToolPermissionContext
    ) -> Union[PermissionResultAllow, PermissionResultDeny]:
        """
        Callback to decide if a tool can be used using new PermissionResult types.
        Delegates the decision to an external callback if provided.

        Returns:
            PermissionResultAllow or PermissionResultDeny object
        """
        logger.info(f"=======================================")
        logger.info(f"PERMISSION CALLBACK TRIGGERED!")
        logger.info(f"Tool: {tool_name}")
        logger.info(f"Input: {input_params}")
        logger.info(f"Context: {context}")
        logger.info(f"=======================================")

        if self.permission_callback:
            logger.info(f"Delegating permission check for tool '{tool_name}' to external callback.")
            try:
                # Call the callback - can now await since SDK callback is async
                if asyncio.iscoroutinefunction(self.permission_callback):
                    decision = await self.permission_callback(tool_name, input_params)
                else:
                    decision = self.permission_callback(tool_name, input_params)

                # Convert different response formats to new PermissionResult objects
                if isinstance(decision, bool):
                    if decision:
                        logger.info(f"   ✅ Tool '{tool_name}' approved by callback")
                        return PermissionResultAllow(updated_input=input_params)
                    else:
                        logger.info(f"   ❌ Tool '{tool_name}' denied by callback")
                        return PermissionResultDeny(message=f"Tool '{tool_name}' denied by permission callback")
                elif isinstance(decision, dict):
                    # Handle old format dict responses
                    behavior = decision.get("behavior", "deny")
                    if behavior == "allow":
                        updated_input = decision.get("updated_input", input_params)
                        return PermissionResultAllow(updated_input=updated_input)
                    else:
                        message = decision.get("message", "Tool denied by callback")
                        return PermissionResultDeny(message=message)
                elif isinstance(decision, (PermissionResultAllow, PermissionResultDeny)):
                    # Already in correct format
                    return decision
                else:
                    logger.warning(f"Unexpected permission callback return type: {type(decision)}")
                    return PermissionResultDeny(message="Invalid callback response")

            except Exception as e:
                logger.error(f"Error in external permission_callback: {e}")
                return PermissionResultDeny(message=f"Permission callback error: {str(e)}")

        logger.warning(f"No permission_callback provided. Denying tool use: '{tool_name}'")
        return PermissionResultDeny(message="No permission callback configured")

    

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

            # Handle SDK message objects using imported classes
            message_type = "unknown"
            if UserMessage and isinstance(sdk_message, UserMessage):
                message_type = "user"
            elif AssistantMessage and isinstance(sdk_message, AssistantMessage):
                message_type = "assistant"
            elif SystemMessage and isinstance(sdk_message, SystemMessage):
                message_type = "system"
            elif ResultMessage and isinstance(sdk_message, ResultMessage):
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
                        elif TextBlock and isinstance(block, TextBlock):
                            text_parts.append(block.text)
                    message_dict["content"] = " ".join(text_parts) if text_parts else ""

            # Copy other common attributes from SDK message
            for attr in ['message', 'data', 'subtype', 'error', 'usage', 'model', 'duration_ms', 'total_cost_usd']:
                if hasattr(sdk_message, attr):
                    value = getattr(sdk_message, attr)
                    # Only add serializable values
                    if isinstance(value, (str, int, float, bool, type(None))):
                        message_dict[attr] = value
                    elif isinstance(value, (dict, list)):
                        # For dict/list, try to include if they appear to contain serializable data
                        try:
                            # Test if it's JSON serializable
                            import json
                            json.dumps(value)
                            message_dict[attr] = value
                        except (TypeError, ValueError):
                            # If not serializable, skip it
                            pass

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
        Terminate the Claude Code SDK session gracefully with new ClaudeSDKClient pattern.

        Args:
            timeout: Seconds to wait for graceful shutdown

        Returns:
            True if terminated successfully, False otherwise
        """
        logger.info(f"Terminating Claude Code SDK session {self.session_id}")

        self.info.state = SessionState.TERMINATED
        self._shutdown_event.set()

        try:
            # Cancel message processing task
            if self._conversation_task and not self._conversation_task.done():
                self._conversation_task.cancel()
                try:
                    await asyncio.wait_for(self._conversation_task, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("Message processing task did not terminate within timeout")
                except asyncio.CancelledError:
                    logger.debug("Message processing task cancelled successfully")

            # SDK client will be cleaned up by context manager in the task

            # Clear message queue
            while not self._message_queue.empty():
                try:
                    self._message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Cleanup storage
            if self.storage_manager:
                await self.storage_manager.cleanup()

            logger.info("Claude Code SDK session terminated successfully")
            return True

        except Exception as e:
            logger.error(f"Error terminating SDK session: {e}")
            return False

    def get_queue_size(self) -> int:
        """Get current message queue size"""
        return self._message_queue.qsize()

    

    def get_info(self) -> Dict[str, Any]:
        """Get current session information."""
        info_dict = asdict(self.info)
        # Convert enum to string value for JSON serialization
        info_dict["state"] = self.info.state.value
        return info_dict

    def is_running(self) -> bool:
        """Check if the session is currently running."""
        return self.info.state in [SessionState.RUNNING, SessionState.PROCESSING]

