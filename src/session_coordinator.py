"""
Session Coordinator for Claude Code WebUI

Integrates session management, data storage, and SDK interaction
into a unified system for managing Claude Code sessions.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Union
import logging

from .session_manager import SessionManager, SessionState, SessionInfo
from .data_storage import DataStorageManager
from .claude_sdk import ClaudeSDK
from .message_parser import MessageParser

logger = logging.getLogger(__name__)


class SessionCoordinator:
    """
    Coordinates Claude Code sessions with integrated storage and SDK management.

    This is the main orchestrator that ties together:
    - Session lifecycle management
    - Persistent data storage
    - Claude Code SDK integration
    - Message processing and parsing
    - Real-time communication pipeline
    """

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path("data")
        self.session_manager = SessionManager(self.data_dir)
        self.message_parser = MessageParser()

        # Active SDK instances
        self._active_sdks: Dict[str, ClaudeSDK] = {}
        self._storage_managers: Dict[str, DataStorageManager] = {}

        # Event callbacks
        self._message_callbacks: Dict[str, List[Callable]] = {}
        self._error_callbacks: Dict[str, List[Callable]] = {}
        self._state_change_callbacks: List[Callable] = []

    async def initialize(self):
        """Initialize the session coordinator"""
        try:
            await self.session_manager.initialize()

            # Register callback to receive session manager state changes
            self.session_manager.add_state_change_callback(self._on_session_manager_state_change)

            # Initialize storage managers for all existing sessions
            await self._initialize_existing_session_storage()

            logger.info("Session coordinator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize session coordinator: {e}")
            raise

    async def _initialize_existing_session_storage(self):
        """Initialize storage managers for all existing sessions"""
        try:
            # Get all existing sessions
            sessions = await self.session_manager.list_sessions()
            logger.info(f"Initializing storage managers for {len(sessions)} existing sessions")

            for session in sessions:
                session_id = session.session_id
                if session_id not in self._storage_managers:
                    # Create storage manager for this session
                    from .data_storage import DataStorageManager
                    session_dir = await self.session_manager.get_session_directory(session_id)
                    storage_manager = DataStorageManager(session_dir)
                    await storage_manager.initialize()
                    self._storage_managers[session_id] = storage_manager
                    logger.info(f"Initialized storage manager for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to initialize storage managers for existing sessions: {e}")
            # Don't raise - this shouldn't block coordinator initialization

    async def create_session(
        self,
        working_directory: str,
        permissions: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        tools: List[str] = None,
        model: Optional[str] = None,
        permission_callback: Optional[Callable[[str, Dict[str, Any]], Union[bool, Dict[str, Any]]]] = None,
    ) -> str:
        """Create a new Claude Code session with integrated components"""
        try:
            # Create session through session manager
            session_id = await self.session_manager.create_session(
                working_directory=working_directory,
                permissions=permissions,
                system_prompt=system_prompt,
                tools=tools,
                model=model
            )

            # Initialize storage manager for this session
            session_dir = await self.session_manager.get_session_directory(session_id)
            storage_manager = DataStorageManager(session_dir)
            await storage_manager.initialize()
            self._storage_managers[session_id] = storage_manager

            # Create SDK instance
            sdk = ClaudeSDK(
                session_id=session_id,
                working_directory=working_directory,
                storage_manager=storage_manager,
                session_manager=self.session_manager,
                message_callback=self._create_message_callback(session_id),
                error_callback=self._create_error_callback(session_id),
                permission_callback=permission_callback,
                permissions=permissions,
                system_prompt=system_prompt,
                tools=tools,
                model=model
            )
            self._active_sdks[session_id] = sdk

            # Initialize callback lists
            self._message_callbacks[session_id] = []
            self._error_callbacks[session_id] = []

            logger.info(f"Created integrated session {session_id}")
            await self._notify_state_change(session_id, SessionState.CREATED)

            return session_id

        except Exception as e:
            logger.error(f"Failed to create integrated session: {e}")
            raise

    async def start_session(self, session_id: str) -> bool:
        """Start a session with SDK integration"""
        try:
            # Start session through session manager
            if not await self.session_manager.start_session(session_id):
                return False

            # Check if SDK exists and is running
            sdk = self._active_sdks.get(session_id)
            if sdk and sdk.is_running():
                logger.info(f"Session {session_id} is already running, skipping start")
                return True

            sdk_was_created = False
            if not sdk:
                sdk_was_created = True
                logger.info(f"Recreating SDK for existing session {session_id}")

                # Get session info to recreate SDK with same parameters
                session_info = await self.session_manager.get_session_info(session_id)
                if not session_info:
                    logger.error(f"No session info found for {session_id}")
                    return False

                # Create storage manager
                from .data_storage import DataStorageManager
                session_dir = await self.session_manager.get_session_directory(session_id)
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()
                self._storage_managers[session_id] = storage_manager

                # Check if we have a valid Claude Code session ID to resume
                resume_sdk_session = None
                if session_info.claude_code_session_id:
                    logger.info(f"Resuming session with Claude Code session ID: {session_info.claude_code_session_id}")
                    resume_sdk_session = session_id  # Use WebUI session ID as resume identifier
                else:
                    logger.info(f"No Claude Code session ID found - starting fresh session for {session_id}")

                # Recreate SDK instance with session parameters
                sdk = ClaudeSDK(
                    session_id=session_id,
                    working_directory=session_info.working_directory,
                    storage_manager=storage_manager,
                    session_manager=self.session_manager,
                    message_callback=self._create_message_callback(session_id),
                    error_callback=self._create_error_callback(session_id),
                    permission_callback=None,  # Use defaults for existing sessions
                    permissions=session_info.permissions,
                    system_prompt=session_info.system_prompt,
                    tools=session_info.tools,
                    model=session_info.model,
                    resume_session_id=resume_sdk_session  # Only resume if we have a Claude Code session ID
                )
                self._active_sdks[session_id] = sdk

                # Initialize callback lists if not exists (preserve existing callbacks)
                if session_id not in self._message_callbacks:
                    self._message_callbacks[session_id] = []
                if session_id not in self._error_callbacks:
                    self._error_callbacks[session_id] = []

            if not await sdk.start():
                logger.error(f"Failed to start SDK for session {session_id}")

                # Get the error message from the SDK
                raw_error_message = getattr(sdk.info, 'error_message', 'Unknown error occurred while starting Claude Code')

                # Extract user-friendly error message
                error_message = self._extract_claude_cli_error(raw_error_message)

                # Update session state to ERROR and reset processing state
                try:
                    await self.session_manager.update_session_state(session_id, SessionState.ERROR, error_message)
                    # Also ensure processing state is reset when going to error state
                    await self.session_manager.update_processing_state(session_id, False)
                    await self._notify_state_change(session_id, SessionState.ERROR)
                    logger.info(f"Updated session {session_id} state to ERROR and reset processing state")
                except Exception as state_error:
                    logger.error(f"Failed to update session state to ERROR: {state_error}")

                # Send system message explaining the failure
                await self._send_session_failure_message(session_id, error_message)

                # Clean up the failed SDK
                if session_id in self._active_sdks:
                    del self._active_sdks[session_id]
                    logger.info(f"Cleaned up failed SDK for session {session_id}")

                return False

            # Send system message for SDK client launch/resume
            if sdk_was_created:  # Only if we actually created/resumed the SDK
                await self._send_client_launched_message(session_id)

            logger.info(f"Started integrated session {session_id}")
            await self._notify_state_change(session_id, SessionState.ACTIVE)
            return True

        except Exception as e:
            logger.error(f"Failed to start integrated session {session_id}: {e}")
            return False

    async def pause_session(self, session_id: str) -> bool:
        """Pause a session"""
        try:
            success = await self.session_manager.pause_session(session_id)
            if success:
                await self._notify_state_change(session_id, SessionState.PAUSED)
            return success
        except Exception as e:
            logger.error(f"Failed to pause session {session_id}: {e}")
            return False

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a session and cleanup resources"""
        try:
            # Terminate SDK first
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.terminate()
                del self._active_sdks[session_id]

            # Terminate session through manager
            success = await self.session_manager.terminate_session(session_id)

            # Cleanup storage and callbacks
            if session_id in self._storage_managers:
                del self._storage_managers[session_id]
            if session_id in self._message_callbacks:
                del self._message_callbacks[session_id]
            if session_id in self._error_callbacks:
                del self._error_callbacks[session_id]

            if success:
                await self._notify_state_change(session_id, SessionState.TERMINATED)

            logger.info(f"Terminated integrated session {session_id}")
            return success

        except Exception as e:
            logger.error(f"Failed to terminate integrated session {session_id}: {e}")
            return False

    async def send_message(self, session_id: str, message: str) -> bool:
        """Send a message through the integrated pipeline"""
        try:
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.error(f"No SDK found for session {session_id}")
                return False

            # Mark session as processing before sending message
            await self.session_manager.update_processing_state(session_id, True)

            # Send message through SDK (will be queued and processed)
            result = await sdk.send_message(message)

            # If message sending failed, reset processing state
            if not result:
                await self.session_manager.update_processing_state(session_id, False)

            return result

        except Exception as e:
            logger.error(f"Failed to send message to session {session_id}: {e}")
            # Reset processing state on error
            try:
                await self.session_manager.update_processing_state(session_id, False)
            except Exception:
                pass  # Don't fail on state update error
            return False

    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive session information"""
        try:
            # Get session info from manager
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                return None

            # Get SDK status
            sdk_info = {}
            sdk = self._active_sdks.get(session_id)
            if sdk:
                sdk_info = sdk.get_info()
                sdk_info.update({
                    "queue_size": sdk.get_queue_size(),
                })

            # Get storage stats
            storage_info = {}
            storage = self._storage_managers.get(session_id)
            if storage:
                storage_info = {
                    "message_count": await storage.get_message_count(),
                    "corruption_check": await storage.detect_corruption()
                }

            return {
                "session": session_info.to_dict(),
                "sdk": sdk_info,
                "storage": storage_info
            }

        except Exception as e:
            logger.error(f"Failed to get session info for {session_id}: {e}")
            return None

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions with their current status"""
        try:
            sessions = await self.session_manager.list_sessions()
            return [session.to_dict() for session in sessions]
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []

    async def get_session_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get messages from a session with pagination metadata"""
        try:
            storage = self._storage_managers.get(session_id)
            if not storage:
                logger.error(f"No storage manager found for session {session_id}")
                return {
                    "messages": [],
                    "total_count": 0,
                    "limit": limit or 50,
                    "offset": offset,
                    "has_more": False
                }

            # Get messages and total count
            messages = await storage.read_messages(limit=limit, offset=offset)
            total_count = await storage.get_message_count()

            # Calculate pagination metadata
            actual_limit = limit or 50
            has_more = (offset + len(messages)) < total_count

            return {
                "messages": messages,
                "total_count": total_count,
                "limit": actual_limit,
                "offset": offset,
                "has_more": has_more
            }

        except Exception as e:
            logger.error(f"Failed to get messages for session {session_id}: {e}")
            return {
                "messages": [],
                "total_count": 0,
                "limit": limit or 50,
                "offset": offset,
                "has_more": False
            }

    def add_message_callback(self, session_id: str, callback: Callable):
        """Add callback for session messages"""
        if session_id not in self._message_callbacks:
            self._message_callbacks[session_id] = []
        self._message_callbacks[session_id].append(callback)
        logger.info(f"Added message callback for session {session_id}, total callbacks: {len(self._message_callbacks[session_id])}")

    def add_error_callback(self, session_id: str, callback: Callable):
        """Add callback for session errors"""
        if session_id not in self._error_callbacks:
            self._error_callbacks[session_id] = []
        self._error_callbacks[session_id].append(callback)

    def add_state_change_callback(self, callback: Callable):
        """Add callback for session state changes"""
        self._state_change_callbacks.append(callback)

    def _create_message_callback(self, session_id: str) -> Callable:
        """Create message callback for a session"""
        async def callback(message_data: Dict[str, Any]):
            try:
                # Parse message using message parser
                parsed_message = self.message_parser.parse_message(message_data)

                # Check if this message indicates processing completion
                if parsed_message.type.value == 'result':
                    # Message processing completed - reset processing state
                    try:
                        await self.session_manager.update_processing_state(session_id, False)
                        logger.info(f"Reset processing state for session {session_id} after result message")
                    except Exception as e:
                        logger.error(f"Failed to reset processing state for session {session_id}: {e}")

                # Call registered callbacks
                callbacks = self._message_callbacks.get(session_id, [])
                logger.info(f"Processing message for session {session_id}, found {len(callbacks)} callbacks")
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(session_id, parsed_message)
                        else:
                            cb(session_id, parsed_message)
                    except Exception as e:
                        logger.error(f"Error in message callback: {e}")

            except Exception as e:
                logger.error(f"Error processing message callback for {session_id}: {e}")

        return callback

    def _create_error_callback(self, session_id: str) -> Callable:
        """Create error callback for a session"""
        async def callback(error_type: str, error: Exception):
            try:
                error_data = {
                    "session_id": session_id,
                    "error_type": error_type,
                    "error": str(error),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

                logger.error(f"SDK error in session {session_id}: {error_type} - {error}")

                # Reset processing state on any error
                try:
                    await self.session_manager.update_processing_state(session_id, False)
                    logger.info(f"Reset processing state for session {session_id} after error: {error_type}")
                except Exception as e:
                    logger.error(f"Failed to reset processing state for session {session_id}: {e}")

                # Handle critical errors that require session state updates
                if error_type in ["startup_failed", "message_processing_loop_error", "immediate_cli_failure"]:
                    logger.info(f"Handling critical SDK error: {error_type}")

                    # Extract user-friendly error message
                    user_error_message = self._extract_claude_cli_error(str(error))

                    # Update session state to ERROR and reset processing state
                    try:
                        await self.session_manager.update_session_state(session_id, SessionState.ERROR, user_error_message)
                        # Also ensure processing state is reset when going to error state
                        await self.session_manager.update_processing_state(session_id, False)
                        await self._notify_state_change(session_id, SessionState.ERROR)
                        logger.info(f"Updated session {session_id} state to ERROR and reset processing state due to SDK error")
                    except Exception as state_error:
                        logger.error(f"Failed to update session state to ERROR: {state_error}")

                    # Send system message explaining the runtime failure
                    await self._send_session_failure_message(session_id, user_error_message)

                    # Clean up the failed SDK
                    if session_id in self._active_sdks:
                        del self._active_sdks[session_id]
                        logger.info(f"Cleaned up failed SDK for session {session_id}")

                # Call registered callbacks
                callbacks = self._error_callbacks.get(session_id, [])
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(session_id, error_data)
                        else:
                            cb(session_id, error_data)
                    except Exception as e:
                        logger.error(f"Error in error callback: {e}")

            except Exception as e:
                logger.error(f"Error processing error callback for {session_id}: {e}")

        return callback

    async def _send_client_launched_message(self, session_id: str):
        """Send a system message indicating the Claude SDK client was launched/resumed"""
        try:
            from datetime import datetime, timezone

            # Create system message for client launch
            message_data = {
                "type": "system",
                "subtype": "client_launched",
                "content": "Claude Code client launched",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sdk_message_type": "SystemMessage"
            }

            # Store message in storage
            storage = self._storage_managers.get(session_id)
            if storage:
                await storage.append_message(message_data)

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            logger.info(f"Sent client launched message for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to send client launched message for {session_id}: {e}")

    async def _send_session_failure_message(self, session_id: str, error_message: str):
        """Send a system message indicating the session failed to start"""
        try:
            from datetime import datetime, timezone
            # Create system message for session failure
            message_data = {
                "type": "system",
                "subtype": "session_failed",
                "content": f"Session failed to start: {error_message}",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sdk_message_type": "SystemMessage",
                "error_details": error_message
            }

            # Store message in storage
            storage = self._storage_managers.get(session_id)
            if storage:
                await storage.append_message(message_data)

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            logger.info(f"Sent session failure message for session {session_id}: {error_message}")
        except Exception as e:
            logger.error(f"Failed to send session failure message for {session_id}: {e}")

    def _extract_claude_cli_error(self, error_message: str) -> str:
        """Extract and format user-friendly error messages from Claude CLI output"""
        try:
            error_str = str(error_message).strip()

            # Common Claude CLI error patterns and their user-friendly versions
            error_patterns = {
                "Command failed with exit code 1": "Claude Code command failed",
                "Fatal error in message reader": "Claude Code CLI failed during startup",
                "not a valid UUID": "Invalid session ID format",
                "--resume requires a valid session ID": "Session resume failed - invalid session ID",
                "Check stderr output for details": "See error details above",
                "Claude Code command failed - see details above": "Claude Code CLI failed - check error details"
            }

            # Check for known patterns and provide clearer descriptions
            for pattern, friendly_msg in error_patterns.items():
                if pattern in error_str:
                    # If it's a UUID error, try to extract the actual UUID from the message
                    if "not a valid UUID" in error_str and "Provided value" in error_str:
                        # Try to extract the invalid UUID value
                        import re
                        uuid_match = re.search(r'Provided value "([^"]+)"', error_str)
                        if uuid_match:
                            invalid_uuid = uuid_match.group(1)
                            return f"Invalid session ID format: '{invalid_uuid}' is not a valid UUID"

                    # For resume errors, provide more context
                    if "--resume requires a valid session ID" in error_str:
                        return "Session resume failed: Invalid or missing Claude Code session ID. This may indicate the session was corrupted or manually modified."

                    return friendly_msg

            # If no patterns match, return the original message but clean it up
            cleaned_msg = error_str.replace("Error output: Check stderr output for details", "").strip()
            if cleaned_msg:
                return cleaned_msg
            else:
                return "Unknown Claude Code error occurred"

        except Exception as e:
            logger.warning(f"Failed to extract CLI error message: {e}")
            return str(error_message)

    async def _notify_state_change(self, session_id: str, new_state: SessionState):
        """Notify registered callbacks about state changes"""
        try:
            state_data = {
                "session_id": session_id,
                "new_state": new_state.value,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            for callback in self._state_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(state_data)
                    else:
                        callback(state_data)
                except Exception as e:
                    logger.error(f"Error in state change callback: {e}")

        except Exception as e:
            logger.error(f"Error notifying state change for {session_id}: {e}")

    async def _on_session_manager_state_change(self, session_id: str, new_state: SessionState):
        """Handle state changes from session manager"""
        logger.info(f"Received state change from session manager: {session_id} -> {new_state.value}")
        await self._notify_state_change(session_id, new_state)

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            # Terminate all active sessions
            session_ids = list(self._active_sdks.keys())
            for session_id in session_ids:
                await self.terminate_session(session_id)

            logger.info("Session coordinator cleanup completed")

        except Exception as e:
            logger.error(f"Error during session coordinator cleanup: {e}")