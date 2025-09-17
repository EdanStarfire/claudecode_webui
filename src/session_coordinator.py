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
            logger.info("Session coordinator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize session coordinator: {e}")
            raise

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

            if not sdk:
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

                # Recreate SDK instance with session parameters and resume
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
                    resume_session_id=session_id  # Resume the existing session
                )
                self._active_sdks[session_id] = sdk

                # Initialize callback lists if not exists (preserve existing callbacks)
                if session_id not in self._message_callbacks:
                    self._message_callbacks[session_id] = []
                if session_id not in self._error_callbacks:
                    self._error_callbacks[session_id] = []

            if not await sdk.start():
                logger.error(f"Failed to start SDK for session {session_id}")
                return False

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

            # Send message through SDK (will be queued and processed)
            return await sdk.send_message(message)

        except Exception as e:
            logger.error(f"Failed to send message to session {session_id}: {e}")
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
    ) -> List[Dict[str, Any]]:
        """Get messages from a session"""
        try:
            storage = self._storage_managers.get(session_id)
            if not storage:
                logger.error(f"No storage manager found for session {session_id}")
                return []

            return await storage.read_messages(limit=limit, offset=offset)

        except Exception as e:
            logger.error(f"Failed to get messages for session {session_id}: {e}")
            return []

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