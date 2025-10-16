"""Claude Code SDK integration and session management."""

import asyncio
import time
import logging
import contextlib
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, Union
from enum import Enum
from dataclasses import dataclass, asdict

from .logging_config import get_logger
from .data_storage import DataStorageManager
from .message_parser import MessageProcessor, MessageParser

# Import SDK components
try:
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
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
    ClaudeAgentOptions = None
    PermissionResultAllow = None
    PermissionResultDeny = None
    ToolPermissionContext = None
    AssistantMessage = None
    UserMessage = None
    SystemMessage = None
    ResultMessage = None
    TextBlock = None

# Get specialized loggers for SDK debugging
sdk_logger = get_logger('sdk_debug', category='SDK')
perm_logger = get_logger('sdk_debug', category='PERMISSIONS')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Claude Code SDK session states."""
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class SDKErrorDetectionHandler(logging.Handler):
    """Custom log handler to detect immediate SDK CLI failures."""

    def __init__(self, session_id: str, error_callback: Optional[Callable] = None):
        super().__init__()
        self.session_id = session_id
        self.error_callback = error_callback
        self.logger = get_logger(__name__)

    def emit(self, record):
        """Handle log records from claude_code_sdk._internal.query."""
        try:
            if (record.levelno >= logging.ERROR and
                "Fatal error in message reader" in record.getMessage()):
                self.logger.error(f"[SDK_LOG_DETECTION] Immediate CLI failure detected for session {self.session_id}: {record.getMessage()}")

                if self.error_callback:
                    # Schedule the error callback to run in the event loop
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._trigger_error_callback(record.getMessage()))
                    else:
                        asyncio.run(self._trigger_error_callback(record.getMessage()))
        except Exception as e:
            # Prevent logging errors from breaking the handler
            pass

    async def _trigger_error_callback(self, error_message: str):
        """Trigger the error callback asynchronously."""
        try:
            if self.error_callback:
                if asyncio.iscoroutinefunction(self.error_callback):
                    await self.error_callback("immediate_cli_failure", Exception(error_message))
                else:
                    self.error_callback("immediate_cli_failure", Exception(error_message))
        except Exception as e:
            self.logger.error(f"Error in SDK error callback: {e}")


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
        sdk_logger.debug(f"ClaudeSDK initialized with permission_callback: {permission_callback is not None}")
        if permission_callback:
            sdk_logger.debug(f"Permission callback type: {type(permission_callback)}")
        else:
            logger.warning("No permission callback provided to ClaudeSDK!")
        self.current_permission_mode = permissions
        self.system_prompt = system_prompt
        self.tools = tools if tools is not None else []
        self.model = model
        self.resume_session_id = resume_session_id

        self.info = SessionInfo(session_id=session_id, working_directory=str(self.working_directory))

        # New SDK client pattern
        self._sdk_client: Optional[ClaudeSDKClient] = None
        self._sdk_options: Optional[ClaudeAgentOptions] = None

        # Interactive conversation support
        self._message_queue = asyncio.Queue()
        self._conversation_task: Optional[asyncio.Task] = None

        # Control
        self._shutdown_event = asyncio.Event()

        # Claude Code's actual session ID (captured from init message)
        self._claude_code_session_id: Optional[str] = None

        # Initialize MessageProcessor for unified message handling
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Session health monitoring
        self._session_health_checks = {
            "context_manager_active": False,
            "client_object_valid": False,
            "last_health_check": None,
            "health_check_count": 0,
            "consecutive_health_failures": 0,
            "session_start_time": None,
            "last_successful_query": None,
            "last_successful_response": None,
            "total_queries_sent": 0,
            "total_responses_received": 0
        }

        # Set up SDK error detection handler to capture immediate CLI failures
        self._sdk_error_handler = None
        self._setup_sdk_error_detection()

        sdk_logger.info(f"Initialized enhanced Claude SDK wrapper for session {session_id}")

    def _setup_sdk_error_detection(self):
        """Set up SDK error detection handler to capture immediate CLI failures."""
        try:
            # Get the SDK logger that emits the "Fatal error in message reader" error
            sdk_logger = logging.getLogger('claude_code_sdk._internal.query')

            # Create our error detection handler
            self._sdk_error_handler = SDKErrorDetectionHandler(
                session_id=self.session_id,
                error_callback=self.error_callback
            )

            # Add our handler to the SDK logger
            sdk_logger.addHandler(self._sdk_error_handler)

            sdk_logger.debug(f"SDK error detection handler set up for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to set up SDK error detection: {e}")

    def _cleanup_sdk_error_detection(self):
        """Clean up SDK error detection handler."""
        try:
            if self._sdk_error_handler:
                sdk_logger = logging.getLogger('claude_code_sdk._internal.query')
                sdk_logger.removeHandler(self._sdk_error_handler)
                self._sdk_error_handler = None
                sdk_logger.debug(f"SDK error detection handler cleaned up for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to clean up SDK error detection: {e}")

    async def start(self) -> bool:
        """
        Start the Claude Code SDK session with new ClaudeSDKClient pattern.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            sdk_logger.info(f"Starting Claude Code SDK session with ClaudeSDKClient in {self.working_directory}")

            # Reset shutdown event to ensure clean start (fixes resumed session disconnect loops)
            self._shutdown_event.clear()

            self.info.state = SessionState.STARTING
            self.info.start_time = time.time()

            # Verify working directory exists
            if not self.working_directory.exists():
                raise FileNotFoundError(f"Working directory does not exist: {self.working_directory}")

            # Check SDK components are available
            if not ClaudeSDKClient or not ClaudeAgentOptions:
                raise ImportError("Claude Agent SDK components not available")

            # Check for existing Claude Code session ID if this is a resume operation
            if self.resume_session_id is not None and self.session_manager:
                session_info = await self.session_manager.get_session_info(self.session_id)
                if session_info and session_info.claude_code_session_id:
                    sdk_logger.info(f"Using stored Claude Code session ID for resume: {session_info.claude_code_session_id}")
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
            sdk_logger.info("Claude Code SDK options configured")

            # Start message processing task
            self._conversation_task = asyncio.create_task(self._message_processing_loop())

            # Keep session in STARTING state until context manager is ready
            # State will be changed to RUNNING in _message_processing_loop when SDK is ready
            sdk_logger.info(f"Claude Code SDK session task started - waiting for context manager initialization")
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
            sdk_logger.debug(f"Queuing message: {message[:100]}...")

            # Add to message queue
            await self._message_queue.put({
                "type": "user_message",
                "content": message,
                "timestamp": time.time()
            })

            sdk_logger.debug(f"Message queued successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_queue_failed", e)
            return False

    async def interrupt_session(self) -> bool:
        """
        Interrupt the current Claude Code SDK session.

        This method attempts to gracefully interrupt any ongoing SDK operations
        by calling the interrupt() method on the active SDK client.

        Returns:
            True if interrupt was sent successfully, False otherwise
        """
        try:
            sdk_logger.info(f"INTERRUPT REQUESTED for session {self.session_id}")

            # Check if we have an active SDK client
            if not self._sdk_client:
                sdk_logger.debug(f"No active SDK client for session {self.session_id} - cannot interrupt")
                return False

            # Check if we're in a state that can be interrupted
            if self.info.state not in [SessionState.RUNNING, SessionState.PROCESSING]:
                sdk_logger.debug(f"Session {self.session_id} not in interruptible state: {self.info.state}")
                return False

            sdk_logger.debug(f"Session {self.session_id} state check passed: {self.info.state}")
            sdk_logger.debug(f"SDK client exists: {bool(self._sdk_client)}")

            # Add interrupt request to message queue to be processed by the message loop
            sdk_logger.debug(f"Adding interrupt request to message queue for session {self.session_id}")
            await self._message_queue.put({
                "type": "interrupt_request",
                "timestamp": time.time()
            })

            sdk_logger.info(f"INTERRUPT REQUEST QUEUED for session {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to interrupt session {self.session_id}: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "interrupt_failed", e)
            return False

    async def set_permission_mode(self, mode: str) -> bool:
        """
        Set the permission mode for the current session.

        Args:
            mode: Permission mode ("default", "acceptEdits", "plan", "bypassPermissions")

        Returns:
            True if mode was set successfully, False otherwise
        """
        try:
            perm_logger.info(f"Setting permission mode to '{mode}' for session {self.session_id}")

            # Validate mode
            valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
            if mode not in valid_modes:
                logger.error(f"Invalid permission mode: {mode}")
                return False

            # Check if we have an active SDK client
            if not self._sdk_client:
                logger.warning(f"No active SDK client for session {self.session_id} - cannot set permission mode")
                return False

            # Check if we're in a valid state
            if self.info.state not in [SessionState.RUNNING, SessionState.PROCESSING]:
                logger.warning(f"Session {self.session_id} not in valid state for permission mode change: {self.info.state}")
                return False

            # Call SDK's set_permission_mode method
            await self._sdk_client.set_permission_mode(mode)

            # Update local permissions tracking
            self.current_permission_mode = mode

            perm_logger.info(f"Successfully set permission mode to '{mode}' for session {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set permission mode for session {self.session_id}: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "set_permission_mode_failed", e)
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from the Claude SDK session gracefully.

        This triggers the context manager's __aexit__ which properly closes
        the SDK client connection. The session can be resumed later.

        Returns:
            True if disconnected successfully, False otherwise
        """
        try:
            sdk_logger.info(f"Disconnecting SDK session {self.session_id}")

            # Set shutdown event to exit message processing loop
            self._shutdown_event.set()

            # Wait for conversation task to complete (which will trigger context manager cleanup)
            if self._conversation_task and not self._conversation_task.done():
                try:
                    await asyncio.wait_for(self._conversation_task, timeout=5.0)
                    sdk_logger.info(f"Conversation task completed for session {self.session_id}")
                except asyncio.TimeoutError:
                    sdk_logger.warning(f"Conversation task did not complete within timeout for {self.session_id}")
                    self._conversation_task.cancel()

            # Context manager cleanup happens automatically when task exits
            sdk_logger.info(f"SDK session {self.session_id} disconnected successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to disconnect SDK session {self.session_id}: {e}")
            return False

    async def _message_processing_loop(self):
        """
        Main message processing loop using the new ClaudeSDKClient pattern.
        """
        loop_start_time = time.time()
        sdk_logger.info(f"Starting message processing loop for session {self.session_id}")

        try:
            async with ClaudeSDKClient(self._sdk_options) as client:
                self._sdk_client = client

                # Update health monitoring state
                self._session_health_checks["context_manager_active"] = True
                self._session_health_checks["client_object_valid"] = True
                self._session_health_checks["session_start_time"] = time.time()

                # NOW the SDK is truly ready - change session state to RUNNING
                self.info.state = SessionState.RUNNING
                sdk_logger.info(f"Session {self.session_id} state changed to RUNNING")

                # Also notify session manager that SDK is ready
                if self.session_manager:
                    await self.session_manager.mark_session_active(self.session_id)

                while not self._shutdown_event.is_set():
                    try:
                        message_data = await asyncio.wait_for(
                            self._message_queue.get(),
                            timeout=1.0
                        )

                        # Handle different message types
                        message_type = message_data.get("type", "unknown") if message_data else "unknown"

                        if message_type == "user_message" and message_data.get("content"):
                            content = message_data["content"]
                            sdk_logger.debug(f"Processing user message: {content[:100]}...")

                            # Store user message if storage available
                            if self.storage_manager:
                                await self.storage_manager.append_message({
                                    "type": "user",
                                    "content": content,
                                    "session_id": self.session_id
                                })

                            self.info.message_count += 1
                            self.info.last_activity = time.time()

                            await client.query(content)
                            self._session_health_checks["total_queries_sent"] += 1
                            self._session_health_checks["last_successful_query"] = time.time()

                            # Process all responses for this query using background task pattern (like GitHub example)
                            async def consume_messages():
                                async for response_message in client.receive_response():
                                    if self._shutdown_event.is_set():
                                        break
                                    await self._process_sdk_message(response_message)
                                    self._session_health_checks["total_responses_received"] += 1

                            # Start message consumption task
                            consume_task = asyncio.create_task(consume_messages())

                            # Monitor for interrupt requests while consuming messages
                            try:
                                while not consume_task.done():
                                    try:
                                        # Check for interrupt requests with short timeout
                                        interrupt_check = await asyncio.wait_for(
                                            self._message_queue.get(),
                                            timeout=0.1
                                        )

                                        if interrupt_check and interrupt_check.get("type") == "interrupt_request":
                                            sdk_logger.info(f"INTERRUPT RECEIVED while processing messages for session {self.session_id}")
                                            sdk_logger.debug(f"Calling client.interrupt() for session {self.session_id}")
                                            await client.interrupt()
                                            sdk_logger.info(f"INTERRUPT SENT successfully to SDK for session {self.session_id}")

                                            # Note: Interrupt message storage and notification is now handled by session coordinator

                                            # Notify through callback
                                            if self.message_callback:
                                                await self._safe_callback(self.message_callback, {
                                                    "type": "system",
                                                    "content": "Session interrupted successfully",
                                                    "subtype": "interrupt_success",
                                                    "session_id": self.session_id,
                                                    "timestamp": time.time()
                                                })

                                            # Mark task as done and break monitoring loop
                                            self._message_queue.task_done()
                                            break
                                        else:
                                            # Put non-interrupt message back for later processing
                                            await self._message_queue.put(interrupt_check)
                                            self._message_queue.task_done()

                                    except asyncio.TimeoutError:
                                        # No messages in queue, continue monitoring
                                        continue

                            except Exception as monitor_error:
                                logger.error(f"Error in interrupt monitoring: {monitor_error}")

                            # Wait for message consumption to complete
                            with contextlib.suppress(asyncio.CancelledError):
                                await consume_task

                            self._session_health_checks["last_successful_response"] = time.time()

                        elif message_type == "interrupt_request":
                            # This case should not happen anymore since we handle interrupts during message processing
                            sdk_logger.debug(f"RECEIVED INTERRUPT REQUEST outside of message processing for session {self.session_id}")
                            sdk_logger.debug(f"This should not happen - interrupts should be handled during active message processing")
                            # Just mark as done since interrupt should be handled during active message processing
                            pass

                        elif message_data and message_data.get("content"):
                            # Fallback for older format (backwards compatibility)
                            content = message_data["content"]
                            sdk_logger.debug(f"Processing legacy message format: {content[:100]}...")

                            # Store user message if storage available
                            if self.storage_manager:
                                await self.storage_manager.append_message({
                                    "type": "user",
                                    "content": content,
                                    "session_id": self.session_id
                                })

                            self.info.message_count += 1
                            self.info.last_activity = time.time()

                            await client.query(content)
                            self._session_health_checks["total_queries_sent"] += 1
                            self._session_health_checks["last_successful_query"] = time.time()

                            # Process all responses for this query (maintains session continuity)
                            async for response_message in client.receive_response():
                                if self._shutdown_event.is_set():
                                    break
                                await self._process_sdk_message(response_message)
                                self._session_health_checks["total_responses_received"] += 1

                            self._session_health_checks["last_successful_response"] = time.time()


                        self._message_queue.task_done()

                    except asyncio.TimeoutError:
                        # No message in queue, continue loop
                        continue
                    except asyncio.CancelledError:
                        sdk_logger.info(f"Message processing loop cancelled")
                        break
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        if self.error_callback:
                            await self._safe_callback(self.error_callback, "message_processing_error", e)

            # Update health monitoring state
            self._session_health_checks["context_manager_active"] = False
            self._session_health_checks["client_object_valid"] = False

        except Exception as e:
            fatal_error_time = time.time()
            logger.error(f"FATAL ERROR in message processing loop at {fatal_error_time}: {e}")
            logger.error(f"Exception type: {type(e)}")
            logger.error(f"Exception details: {e}")
            logger.error(f"Session state before error: {self.info.state}")


            # Comprehensive fatal error tracking
            import traceback
            logger.error(f"FATAL ERROR - Full exception traceback:")
            for line in traceback.format_exception(type(e), e, e.__traceback__):
                logger.error(f"{line.rstrip()}")

            logger.error(f"Fatal error context:")
            logger.error(f"- Session ID: {self.session_id}")
            logger.error(f"- Working directory: {self.working_directory}")
            logger.error(f"- Session state: {self.info.state}")
            logger.error(f"- Message count: {self.info.message_count}")
            logger.error(f"- Queue size: {self._message_queue.qsize()}")
            logger.error(f"- Shutdown event: {self._shutdown_event.is_set()}")
            logger.error(f"- Context manager was active: {self._session_health_checks.get('context_manager_active', False)}")
            logger.error(f"- Total queries sent: {self._session_health_checks.get('total_queries_sent', 0)}")
            logger.error(f"- Total responses received: {self._session_health_checks.get('total_responses_received', 0)}")

            # Update health monitoring
            self._session_health_checks["context_manager_active"] = False
            self._session_health_checks["client_object_valid"] = False

            # Final health check for fatal error
            try:
                self._log_session_health(None, "fatal_error")
            except Exception as health_check_error:
                logger.error(f"Health check failed during fatal error handling: {health_check_error}")

            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_processing_loop_error", e)
        finally:
            cleanup_time = time.time()
            self._sdk_client = None
            sdk_logger.info(f"Message processing loop cleanup at {cleanup_time}")
            sdk_logger.info(f"Total loop runtime: {cleanup_time - loop_start_time:.3f}s")
            sdk_logger.info(f"Message processing loop ENDED")


    def _get_sdk_options(self):
        """Configure SDK options with correct parameter names for new ClaudeSDKClient pattern."""

        # Create callback function with new PermissionResult return types
        async def can_use_tool_wrapper(
            tool_name: str,
            input_params: Dict[str, Any],
            context: ToolPermissionContext
        ) -> Union[PermissionResultAllow, PermissionResultDeny]:
            return await self._can_use_tool_callback(tool_name, input_params, context)

        # Configure system prompt: use Claude Code preset if no custom prompt provided
        system_prompt_config = self.system_prompt
        if system_prompt_config is None:
            # Use Claude Code preset system prompt by default
            system_prompt_config = {
                "type": "preset",
                "preset": "claude_code"
            }

        options_kwargs = {
            "cwd": str(self.working_directory),
            "permission_mode": self.current_permission_mode,
            "system_prompt": system_prompt_config,
            "allowed_tools": self.tools,
            # Restore default settings sources behavior (load from user, project, and local)
            "setting_sources": ["user", "project", "local"]
        }

        # Only add can_use_tool callback if permission callback is provided and SDK classes are available
        perm_logger.debug(f"Callback registration check:")
        perm_logger.debug(f"- permission_callback exists: {self.permission_callback is not None}")
        perm_logger.debug(f"- PermissionResultAllow available: {PermissionResultAllow is not None}")
        perm_logger.debug(f"- PermissionResultDeny available: {PermissionResultDeny is not None}")

        if self.permission_callback and PermissionResultAllow and PermissionResultDeny:
            options_kwargs["can_use_tool"] = can_use_tool_wrapper
            perm_logger.info("Permission callback registered with SDK")
        else:
            logger.warning("Permission callback NOT registered - missing requirements")

        if self.model is not None:
            options_kwargs["model"] = self.model

        if self.resume_session_id is not None:
            options_kwargs["resume"] = self.resume_session_id
            sdk_logger.debug(f"Setting resume parameter to: {self.resume_session_id}")

        sdk_logger.debug(f"Final SDK options keys: {list(options_kwargs.keys())}")
        sdk_logger.debug(f"can_use_tool included: {'can_use_tool' in options_kwargs}")
        sdk_logger.debug(f"ClaudeAgentOptions: {options_kwargs}")
        return ClaudeAgentOptions(**options_kwargs)

    async def _process_sdk_message(self, sdk_message: Any):
        """Process a single message from the SDK stream."""
        try:
            # Check for fatal error messages that indicate immediate CLI failures
            if hasattr(sdk_message, 'type') and sdk_message.type == 'error':
                error_content = str(sdk_message.content) if hasattr(sdk_message, 'content') else str(sdk_message)
                if "Fatal error in message reader" in error_content:
                    logger.error(f"Immediate CLI failure detected: {error_content}")

                    # Extract the underlying error details
                    fatal_error = error_content
                    if "Command failed with exit code" in error_content:
                        # This is the immediate CLI failure we want to surface
                        logger.error(f"CLI command failure: {error_content}")
                        fatal_error = "Claude Code command failed - see details above"

                    # Trigger immediate error callback to update session state
                    if self.error_callback:
                        sdk_logger.debug(f"Triggering immediate error callback")
                        await self._safe_callback(self.error_callback, "immediate_cli_failure", Exception(fatal_error))

                    return  # Don't process this error message further

            # Capture Claude Code's actual session ID from init message
            if hasattr(sdk_message, 'subtype') and sdk_message.subtype == 'init':
                session_id = getattr(sdk_message, 'data', {}).get('session_id') if hasattr(sdk_message, 'data') else None
                if session_id:
                    # Only store if this is a different session ID (prevent duplicates)
                    if not hasattr(self, '_claude_code_session_id') or self._claude_code_session_id != session_id:
                        self._claude_code_session_id = session_id
                        sdk_logger.info(f"Captured Claude Code session ID: {session_id} for WebUI session: {self.session_id}")

                        # Always store the latest Claude Code session ID for cumulative sessions
                        if self.session_manager:
                            await self.session_manager.update_claude_code_session_id(self.session_id, session_id)
                            if self.resume_session_id is None:
                                sdk_logger.info(f"Stored new Claude Code session ID: {session_id}")
                            else:
                                sdk_logger.info(f"Resume created new cumulative session {session_id} (was attempting to resume {self.resume_session_id})")
                                sdk_logger.info(f"Updated stored session ID to latest: {session_id}")

            converted_message = self._convert_sdk_message(sdk_message)
            self.info.last_activity = time.time()

            # Debug log raw SDK response structure
            sdk_logger.debug(f"Raw SDK response: {sdk_message=}")

            if self.storage_manager:
                await self._store_sdk_message(converted_message, sdk_message)

            if self.message_callback:
                await self._safe_callback(self.message_callback, converted_message)

            sdk_logger.debug(f"Processed SDK message: {converted_message.get('type', 'unknown')}")

        except Exception as e:
            logger.error(f"Failed to process SDK message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "sdk_message_processing_failed", e)
    
    async def _store_sdk_message(self, converted_message: Dict[str, Any], raw_sdk_message: Any = None):
        """Store the SDK message using unified MessageProcessor for consistent format."""
        try:
            # Process the message through MessageProcessor to get standardized metadata
            parsed_message = self._message_processor.process_message(converted_message, source="sdk")

            # Prepare for storage using MessageProcessor
            storage_data = self._message_processor.prepare_for_storage(parsed_message)

            # Ensure raw SDK data is preserved for debugging
            raw_sdk_data = self._capture_raw_sdk_data(raw_sdk_message or converted_message.get("sdk_message"))
            if raw_sdk_data:
                # Store only the standardized field name
                storage_data["raw_sdk_message"] = raw_sdk_data

            # Add SDK-specific metadata
            if converted_message.get("sdk_message"):
                storage_data["sdk_message_type"] = converted_message.get("sdk_message").__class__.__name__

            sdk_logger.debug(f"Storing processed SDK message: {storage_data.get('type', 'unknown')}")
            await self.storage_manager.append_message(storage_data)

        except Exception as e:
            logger.error(f"Failed to store SDK message through MessageProcessor: {e}")
            # Fallback to original storage format if processing fails
            storage_message = {
                "type": converted_message.get("type", "unknown"),
                "content": converted_message.get("content", ""),
                "session_id": converted_message.get("session_id"),
                "timestamp": converted_message.get("timestamp"),
                "error": f"MessageProcessor storage failed: {str(e)}"
            }
            await self.storage_manager.append_message(storage_message)

    def _capture_raw_sdk_data(self, sdk_message: Any) -> Optional[str]:
        """Capture raw SDK message data in a standardized, serializable format."""
        if sdk_message is None:
            return None

        try:
            import json

            # For SDK objects, capture all non-private attributes comprehensively
            raw_data = {}

            # Add type information
            raw_data["__class__"] = sdk_message.__class__.__name__
            raw_data["__module__"] = getattr(sdk_message.__class__, "__module__", "unknown")

            # Capture all accessible attributes
            for attr in dir(sdk_message):
                if not attr.startswith('_') and not callable(getattr(sdk_message, attr, None)):
                    try:
                        value = getattr(sdk_message, attr)
                        # Try to serialize the value
                        json.dumps(value)
                        raw_data[attr] = value
                    except (TypeError, ValueError):
                        # If not directly serializable, convert to string representation
                        raw_data[attr] = str(value)

            # Store as JSON string
            return json.dumps(raw_data)

        except Exception as e:
            logger.warning(f"Failed to capture raw SDK data: {e}")
            # Ultimate fallback - string representation
            try:
                return json.dumps({"__fallback__": str(sdk_message)})
            except Exception:
                return json.dumps({"__fallback__": "failed to serialize"})

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
        perm_logger.info(f"=======================================")
        perm_logger.info(f"Permission callback triggered")
        perm_logger.info(f"Tool: {tool_name}")
        perm_logger.info(f"Input: {input_params}")
        perm_logger.info(f"Context: {context}")
        perm_logger.info(f"=======================================")

        if self.permission_callback:
            perm_logger.debug(f"Delegating permission check for tool '{tool_name}' to external callback")
            try:
                # Call the callback with context - can now await since SDK callback is async
                if asyncio.iscoroutinefunction(self.permission_callback):
                    decision = await self.permission_callback(tool_name, input_params, context)
                else:
                    decision = self.permission_callback(tool_name, input_params, context)

                # Convert different response formats to new PermissionResult objects
                if isinstance(decision, bool):
                    if decision:
                        perm_logger.info(f"Tool '{tool_name}' approved by callback")
                        return PermissionResultAllow(updated_input=input_params)
                    else:
                        perm_logger.info(f"Tool '{tool_name}' denied by callback")
                        return PermissionResultDeny(message=f"Tool '{tool_name}' denied by permission callback")
                elif isinstance(decision, dict):
                    # Handle dict responses with optional updated_permissions
                    behavior = decision.get("behavior", "deny")
                    if behavior == "allow":
                        updated_input = decision.get("updated_input", input_params)
                        updated_permissions = decision.get("updated_permissions")
                        return PermissionResultAllow(
                            updated_input=updated_input,
                            updated_permissions=updated_permissions
                        )
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

        perm_logger.debug(f"No permission_callback provided. Denying tool use: '{tool_name}'")
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
        sdk_logger.info(f"Terminating Claude Code SDK session {self.session_id}")

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
                    sdk_logger.debug("Message processing task cancelled successfully")

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

            # Cleanup SDK error detection handler
            self._cleanup_sdk_error_detection()

            sdk_logger.info("Claude Code SDK session terminated successfully")
            return True

        except Exception as e:
            logger.error(f"Error terminating SDK session: {e}")
            # Cleanup SDK error detection handler even on error
            self._cleanup_sdk_error_detection()
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

    def _perform_session_health_check(self, client: Optional[ClaudeSDKClient] = None) -> Dict[str, Any]:
        """
        Perform comprehensive session health check.

        Returns:
            Dictionary containing health check results
        """
        health_check_time = time.time()
        self._session_health_checks["health_check_count"] += 1
        self._session_health_checks["last_health_check"] = health_check_time

        health_status = {
            "check_time": health_check_time,
            "check_number": self._session_health_checks["health_check_count"],
            "session_id": self.session_id,
            "session_state": self.info.state.value,
            "context_manager_active": self._session_health_checks["context_manager_active"],
            "client_object_valid": client is not None,
            "sdk_client_reference_valid": self._sdk_client is not None,
            "shutdown_event_set": self._shutdown_event.is_set(),
            "message_queue_size": self._message_queue.qsize(),
            "message_count": self.info.message_count,
            "last_activity": self.info.last_activity,
            "total_queries_sent": self._session_health_checks["total_queries_sent"],
            "total_responses_received": self._session_health_checks["total_responses_received"],
            "session_uptime": None,
            "time_since_last_activity": None
        }

        # Calculate timing metrics
        if self._session_health_checks["session_start_time"]:
            health_status["session_uptime"] = health_check_time - self._session_health_checks["session_start_time"]

        if self.info.last_activity:
            health_status["time_since_last_activity"] = health_check_time - self.info.last_activity

        # Assess overall health
        is_healthy = (
            health_status["context_manager_active"] and
            health_status["client_object_valid"] and
            not health_status["shutdown_event_set"] and
            self.info.state in [SessionState.RUNNING, SessionState.PROCESSING]
        )

        health_status["overall_health"] = "healthy" if is_healthy else "unhealthy"

        if is_healthy:
            self._session_health_checks["consecutive_health_failures"] = 0
        else:
            self._session_health_checks["consecutive_health_failures"] += 1

        health_status["consecutive_failures"] = self._session_health_checks["consecutive_health_failures"]

        return health_status

    def _log_session_health(self, client: Optional[ClaudeSDKClient] = None, context: str = "general"):
        """Log detailed session health information."""
        health_status = self._perform_session_health_check(client)

        sdk_logger.debug(f"Health check #{health_status['check_number']} for session {self.session_id} ({context})")
        sdk_logger.debug(f"Overall health: {health_status['overall_health']}")
        sdk_logger.debug(f"Session state: {health_status['session_state']}")
        sdk_logger.debug(f"Context manager active: {health_status['context_manager_active']}")
        sdk_logger.debug(f"Client object valid: {health_status['client_object_valid']}")
        sdk_logger.debug(f"SDK client reference valid: {health_status['sdk_client_reference_valid']}")
        sdk_logger.debug(f"Shutdown event set: {health_status['shutdown_event_set']}")
        sdk_logger.debug(f"Message queue size: {health_status['message_queue_size']}")
        sdk_logger.debug(f"Total queries sent: {health_status['total_queries_sent']}")
        sdk_logger.debug(f"Total responses received: {health_status['total_responses_received']}")
        sdk_logger.debug(f"Session uptime: {health_status['session_uptime']:.3f}s" if health_status['session_uptime'] else "Session uptime: not available")
        sdk_logger.debug(f"Time since last activity: {health_status['time_since_last_activity']:.3f}s" if health_status['time_since_last_activity'] else "Time since last activity: not available")

        if health_status['consecutive_failures'] > 0:
            logger.warning(f"Consecutive health check failures: {health_status['consecutive_failures']}")

        return health_status

