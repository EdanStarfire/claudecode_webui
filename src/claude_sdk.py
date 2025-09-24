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
        logger.info(f"DEBUG: ClaudeSDK initialized with permission_callback: {permission_callback is not None}")
        if permission_callback:
            logger.info(f"DEBUG: Permission callback type: {type(permission_callback)}")
        else:
            logger.warning("DEBUG: No permission callback provided to ClaudeSDK!")
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

        logger.info(f"Initialized enhanced Claude SDK wrapper for session {session_id}")

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

            logger.debug(f"SDK error detection handler set up for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to set up SDK error detection: {e}")

    def _cleanup_sdk_error_detection(self):
        """Clean up SDK error detection handler."""
        try:
            if self._sdk_error_handler:
                sdk_logger = logging.getLogger('claude_code_sdk._internal.query')
                sdk_logger.removeHandler(self._sdk_error_handler)
                self._sdk_error_handler = None
                logger.debug(f"SDK error detection handler cleaned up for session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to clean up SDK error detection: {e}")

    async def start(self) -> bool:
        """
        Start the Claude Code SDK session with new ClaudeSDKClient pattern.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info(f"Starting Claude Code SDK session with ClaudeSDKClient in {self.working_directory}")

            # Reset shutdown event to ensure clean start (fixes resumed session disconnect loops)
            self._shutdown_event.clear()

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

            # Keep session in STARTING state until context manager is ready
            # State will be changed to RUNNING in _message_processing_loop when SDK is ready
            logger.info(f"Claude Code SDK session task started - waiting for context manager initialization")
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

    async def interrupt_session(self) -> bool:
        """
        Interrupt the current Claude Code SDK session.

        This method attempts to gracefully interrupt any ongoing SDK operations
        by calling the interrupt() method on the active SDK client.

        Returns:
            True if interrupt was sent successfully, False otherwise
        """
        try:
            logger.info(f"DEBUG: INTERRUPT REQUESTED for session {self.session_id}")

            # Check if we have an active SDK client
            if not self._sdk_client:
                logger.warning(f"DEBUG: No active SDK client for session {self.session_id} - cannot interrupt")
                return False

            # Check if we're in a state that can be interrupted
            if self.info.state not in [SessionState.RUNNING, SessionState.PROCESSING]:
                logger.warning(f"DEBUG: Session {self.session_id} not in interruptible state: {self.info.state}")
                return False

            logger.info(f"DEBUG: Session {self.session_id} state check passed: {self.info.state}")
            logger.info(f"DEBUG: SDK client exists: {bool(self._sdk_client)}")

            # Add interrupt request to message queue to be processed by the message loop
            logger.info(f"DEBUG: Adding interrupt request to message queue for session {self.session_id}")
            await self._message_queue.put({
                "type": "interrupt_request",
                "timestamp": time.time()
            })

            logger.info(f"DEBUG: INTERRUPT REQUEST QUEUED for session {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to interrupt session {self.session_id}: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "interrupt_failed", e)
            return False

    async def _message_processing_loop(self):
        """
        Main message processing loop using the new ClaudeSDKClient pattern.
        """
        loop_start_time = time.time()
        logger.info(f"Starting message processing loop for session {self.session_id}")

        try:
            async with ClaudeSDKClient(self._sdk_options) as client:
                self._sdk_client = client

                # Update health monitoring state
                self._session_health_checks["context_manager_active"] = True
                self._session_health_checks["client_object_valid"] = True
                self._session_health_checks["session_start_time"] = time.time()

                # NOW the SDK is truly ready - change session state to RUNNING
                self.info.state = SessionState.RUNNING
                logger.info(f"Session {self.session_id} state changed to RUNNING")

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
                            logger.info(f"Processing user message: {content[:100]}...")

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
                                            logger.info(f"DEBUG: INTERRUPT RECEIVED while processing messages for session {self.session_id}")
                                            logger.info(f"DEBUG: Calling client.interrupt() for session {self.session_id}")
                                            await client.interrupt()
                                            logger.info(f"DEBUG: INTERRUPT SENT successfully to SDK for session {self.session_id}")

                                            # Note: Interrupt message storage and notification is now handled by session coordinator

                                            # Notify through callback
                                            if self.message_callback:
                                                await self._safe_callback(self.message_callback, self.session_id, {
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
                            logger.warning(f"DEBUG: RECEIVED INTERRUPT REQUEST outside of message processing for session {self.session_id}")
                            logger.warning(f"DEBUG: This should not happen - interrupts should be handled during active message processing")
                            # Just mark as done since interrupt should be handled during active message processing
                            pass

                        elif message_data and message_data.get("content"):
                            # Fallback for older format (backwards compatibility)
                            content = message_data["content"]
                            logger.info(f"Processing legacy message format: {content[:100]}...")

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
                        logger.info(f"Message processing loop cancelled")
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
            logger.error(f"[SDK_LIFECYCLE] FATAL ERROR in message processing loop at {fatal_error_time}: {e}")
            logger.error(f"[SDK_LIFECYCLE] Exception type: {type(e)}")
            logger.error(f"[SDK_LIFECYCLE] Exception details: {e}")
            logger.error(f"[SDK_LIFECYCLE] Session state before error: {self.info.state}")


            # Comprehensive fatal error tracking
            import traceback
            logger.error(f"[ERROR_TRACKING] FATAL ERROR - Full exception traceback:")
            for line in traceback.format_exception(type(e), e, e.__traceback__):
                logger.error(f"[ERROR_TRACKING] {line.rstrip()}")

            logger.error(f"[ERROR_TRACKING] Fatal error context:")
            logger.error(f"[ERROR_TRACKING] - Session ID: {self.session_id}")
            logger.error(f"[ERROR_TRACKING] - Working directory: {self.working_directory}")
            logger.error(f"[ERROR_TRACKING] - Session state: {self.info.state}")
            logger.error(f"[ERROR_TRACKING] - Message count: {self.info.message_count}")
            logger.error(f"[ERROR_TRACKING] - Queue size: {self._message_queue.qsize()}")
            logger.error(f"[ERROR_TRACKING] - Shutdown event: {self._shutdown_event.is_set()}")
            logger.error(f"[ERROR_TRACKING] - Context manager was active: {self._session_health_checks.get('context_manager_active', False)}")
            logger.error(f"[ERROR_TRACKING] - Total queries sent: {self._session_health_checks.get('total_queries_sent', 0)}")
            logger.error(f"[ERROR_TRACKING] - Total responses received: {self._session_health_checks.get('total_responses_received', 0)}")

            # Update health monitoring
            self._session_health_checks["context_manager_active"] = False
            self._session_health_checks["client_object_valid"] = False

            # Final health check for fatal error
            try:
                self._log_session_health(None, "fatal_error")
            except Exception as health_check_error:
                logger.error(f"[ERROR_TRACKING] Health check failed during fatal error handling: {health_check_error}")

            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "message_processing_loop_error", e)
        finally:
            cleanup_time = time.time()
            self._sdk_client = None
            logger.info(f"[SDK_LIFECYCLE] Message processing loop cleanup at {cleanup_time}")
            logger.info(f"[SDK_LIFECYCLE] Total loop runtime: {cleanup_time - loop_start_time:.3f}s")
            logger.info(f"[SDK_LIFECYCLE] Message processing loop ENDED")


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
        logger.info(f"DEBUG: Callback registration check:")
        logger.info(f"DEBUG: - permission_callback exists: {self.permission_callback is not None}")
        logger.info(f"DEBUG: - PermissionResultAllow available: {PermissionResultAllow is not None}")
        logger.info(f"DEBUG: - PermissionResultDeny available: {PermissionResultDeny is not None}")

        if self.permission_callback and PermissionResultAllow and PermissionResultDeny:
            options_kwargs["can_use_tool"] = can_use_tool_wrapper
            logger.info("DEBUG: ✅ Permission callback registered with SDK!")
        else:
            logger.warning("DEBUG: ❌ Permission callback NOT registered - missing requirements")

        if self.model is not None:
            options_kwargs["model"] = self.model

        if self.resume_session_id is not None:
            options_kwargs["resume"] = self.resume_session_id
            logger.info(f"Setting resume parameter to: {self.resume_session_id}")

        logger.info(f"DEBUG: Final SDK options keys: {list(options_kwargs.keys())}")
        logger.info(f"DEBUG: can_use_tool included: {'can_use_tool' in options_kwargs}")
        logger.info(f"ClaudeCodeOptions: {options_kwargs}")
        return ClaudeCodeOptions(**options_kwargs)

    async def _process_sdk_message(self, sdk_message: Any):
        """Process a single message from the SDK stream."""
        try:
            # Check for fatal error messages that indicate immediate CLI failures
            if hasattr(sdk_message, 'type') and sdk_message.type == 'error':
                error_content = str(sdk_message.content) if hasattr(sdk_message, 'content') else str(sdk_message)
                if "Fatal error in message reader" in error_content:
                    logger.error(f"[FATAL_ERROR_DETECTION] Immediate CLI failure detected: {error_content}")

                    # Extract the underlying error details
                    fatal_error = error_content
                    if "Command failed with exit code" in error_content:
                        # This is the immediate CLI failure we want to surface
                        logger.info(f"[FATAL_ERROR_DETECTION] CLI command failure: {error_content}")
                        fatal_error = "Claude Code command failed - see details above"

                    # Trigger immediate error callback to update session state
                    if self.error_callback:
                        logger.info(f"[FATAL_ERROR_DETECTION] Triggering immediate error callback")
                        await self._safe_callback(self.error_callback, "immediate_cli_failure", Exception(fatal_error))

                    return  # Don't process this error message further

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

            # Debug log raw SDK response structure
            logger.debug(f"Raw SDK response: {sdk_message=}")

            if self.storage_manager:
                await self._store_sdk_message(converted_message, sdk_message)

            if self.message_callback:
                await self._safe_callback(self.message_callback, converted_message)

            logger.debug(f"Processed SDK message: {converted_message.get('type', 'unknown')}")

        except Exception as e:
            logger.error(f"Failed to process SDK message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "sdk_message_processing_failed", e)
    
    async def _store_sdk_message(self, converted_message: Dict[str, Any], raw_sdk_message: Any = None):
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

        # Add raw SDK response for debugging
        if raw_sdk_message is not None:
            try:
                import json
                # Capture all non-private attributes
                raw_data = {}
                for attr in dir(raw_sdk_message):
                    if not attr.startswith('_') and not callable(getattr(raw_sdk_message, attr, None)):
                        try:
                            value = getattr(raw_sdk_message, attr)
                            json.dumps(value)  # Test if serializable
                            raw_data[attr] = value
                        except (TypeError, ValueError):
                            raw_data[attr] = str(value)
                storage_message["raw_sdk_response"] = json.dumps(raw_data)
            except Exception as e:
                # Final fallback
                storage_message["raw_sdk_response"] = json.dumps(str(raw_sdk_message))

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

            # Cleanup SDK error detection handler
            self._cleanup_sdk_error_detection()

            logger.info("Claude Code SDK session terminated successfully")
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

        logger.info(f"[SESSION_HEALTH] Health check #{health_status['check_number']} for session {self.session_id} ({context})")
        logger.info(f"[SESSION_HEALTH] Overall health: {health_status['overall_health']}")
        logger.info(f"[SESSION_HEALTH] Session state: {health_status['session_state']}")
        logger.info(f"[SESSION_HEALTH] Context manager active: {health_status['context_manager_active']}")
        logger.info(f"[SESSION_HEALTH] Client object valid: {health_status['client_object_valid']}")
        logger.info(f"[SESSION_HEALTH] SDK client reference valid: {health_status['sdk_client_reference_valid']}")
        logger.info(f"[SESSION_HEALTH] Shutdown event set: {health_status['shutdown_event_set']}")
        logger.info(f"[SESSION_HEALTH] Message queue size: {health_status['message_queue_size']}")
        logger.info(f"[SESSION_HEALTH] Total queries sent: {health_status['total_queries_sent']}")
        logger.info(f"[SESSION_HEALTH] Total responses received: {health_status['total_responses_received']}")
        logger.info(f"[SESSION_HEALTH] Session uptime: {health_status['session_uptime']:.3f}s" if health_status['session_uptime'] else "[SESSION_HEALTH] Session uptime: not available")
        logger.info(f"[SESSION_HEALTH] Time since last activity: {health_status['time_since_last_activity']:.3f}s" if health_status['time_since_last_activity'] else "[SESSION_HEALTH] Time since last activity: not available")

        if health_status['consecutive_failures'] > 0:
            logger.warning(f"[SESSION_HEALTH] Consecutive health check failures: {health_status['consecutive_failures']}")

        return health_status

