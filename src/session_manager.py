"""
Session Management for Claude Code WebUI

Handles session lifecycle, state persistence, and coordination between
SDK interactions and web interface.
"""

import asyncio
import gc
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .logging_config import get_logger

# Get specialized logger for session manager actions
session_logger = get_logger('session_manager', category='SESSION_MANAGER')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Session lifecycle states"""
    CREATED = "created"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class SessionInfo:
    """Session metadata and state information (also serves as Minion when is_minion=True)"""
    session_id: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    working_directory: Optional[str] = None
    current_permission_mode: str = "acceptEdits"
    initial_permission_mode: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: List[str] = None
    model: Optional[str] = None
    error_message: Optional[str] = None
    claude_code_session_id: Optional[str] = None
    is_processing: bool = False
    name: Optional[str] = None
    order: Optional[int] = None
    project_id: Optional[str] = None

    # Minion-specific fields (only used when is_minion=True)
    is_minion: bool = False  # True if this is a minion in a legion
    role: Optional[str] = None  # Minion role description
    is_overseer: bool = False  # True if has spawned children
    overseer_level: int = 0  # 0=user-created, 1=child, 2=grandchild
    parent_overseer_id: Optional[str] = None  # None if user-created
    child_minion_ids: List[str] = None  # Child minion session IDs
    horde_id: Optional[str] = None  # Which horde this minion belongs to
    channel_ids: List[str] = None  # Communication channels
    capabilities: List[str] = None  # Capability tags for discovery
    initialization_context: Optional[str] = None  # Custom system prompt for minion

    def __post_init__(self):
        if self.tools is None:
            self.tools = ["bash", "edit", "read"]
        if self.child_minion_ids is None:
            self.child_minion_ids = []
        if self.channel_ids is None:
            self.channel_ids = []
        if self.capabilities is None:
            self.capabilities = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['state'] = self.state.value
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionInfo':
        """Create from dictionary loaded from JSON"""
        data['state'] = SessionState(data['state'])
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        # Migration: Add minion fields if missing (backward compatibility)
        if 'is_minion' not in data:
            data['is_minion'] = False
        if 'child_minion_ids' not in data:
            data['child_minion_ids'] = []
        if 'channel_ids' not in data:
            data['channel_ids'] = []
        if 'capabilities' not in data:
            data['capabilities'] = []
        if 'is_overseer' not in data:
            data['is_overseer'] = False
        if 'overseer_level' not in data:
            data['overseer_level'] = 0
        return cls(**data)


class SessionManager:
    """Manages Claude Code session lifecycle and persistence"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path("data")
        self.sessions_dir = self.data_dir / "sessions"
        self._active_sessions: Dict[str, SessionInfo] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._state_change_callbacks: List[Callable] = []

    async def initialize(self):
        """Initialize session manager and load existing sessions"""
        try:
            self.data_dir.mkdir(exist_ok=True)
            self.sessions_dir.mkdir(exist_ok=True)
            await self._load_existing_sessions()
            session_logger.info(f"SessionManager initialized with {len(self._active_sessions)} existing sessions")
        except Exception as e:
            logger.error(f"Failed to initialize SessionManager: {e}")
            raise

    async def _load_existing_sessions(self):
        """Load existing session state from filesystem"""
        try:
            for session_dir in self.sessions_dir.iterdir():
                if session_dir.is_dir():
                    state_file = session_dir / "state.json"
                    if state_file.exists():
                        try:
                            with open(state_file, 'r') as f:
                                data = json.load(f)

                            # Migration: Add initial_permission_mode if missing
                            if 'initial_permission_mode' not in data:
                                data['initial_permission_mode'] = data.get('current_permission_mode', 'acceptEdits')

                            session_info = SessionInfo.from_dict(data)

                            # Reset active/starting sessions to created state on startup
                            # since there are no SDK instances running for them
                            original_state = session_info.state
                            original_processing = session_info.is_processing
                            state_changed = False

                            if session_info.state in [SessionState.ACTIVE, SessionState.STARTING]:
                                session_info.state = SessionState.CREATED
                                session_info.updated_at = datetime.now(timezone.utc)
                                state_changed = True
                                session_logger.info(f"Reset session {session_info.session_id} from {original_state.value} to {session_info.state.value} on startup")

                            # Reset processing state since no SDKs are running on startup
                            if session_info.is_processing:
                                session_info.is_processing = False
                                session_info.updated_at = datetime.now(timezone.utc)
                                state_changed = True
                                session_logger.info(f"Reset processing state for session {session_info.session_id} from {original_processing} to False on startup")

                            self._active_sessions[session_info.session_id] = session_info

                            # Save the updated state if it was modified
                            if state_changed:
                                await self._persist_session_state(session_info.session_id)
                            self._session_locks[session_info.session_id] = asyncio.Lock()
                            session_logger.debug(f"Loaded session {session_info.session_id} with state {session_info.state}")
                        except Exception as e:
                            logger.error(f"Failed to load session from {session_dir}: {e}")
        except Exception as e:
            logger.error(f"Error loading existing sessions: {e}")

    async def create_session(
        self,
        session_id: str,
        working_directory: Optional[str] = None,
        permission_mode: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        tools: List[str] = None,
        model: Optional[str] = None,
        name: Optional[str] = None,
        order: Optional[int] = None,
        project_id: Optional[str] = None,
        # Minion-specific fields
        is_minion: bool = False,
        role: Optional[str] = None,
        capabilities: List[str] = None,
        initialization_context: Optional[str] = None,
        # Hierarchy fields (Phase 5)
        parent_overseer_id: Optional[str] = None,
        overseer_level: int = 0,
        horde_id: Optional[str] = None
    ) -> None:
        """Create a new session with provided ID (or minion if is_minion=True)"""
        now = datetime.now(timezone.utc)

        # Generate default name if not provided
        if name is None:
            name = now.strftime("%Y-%m-%d %H:%M:%S")

        # Order is now managed by ProjectManager (sessions ordered within projects)
        if order is None:
            order = 0

        session_info = SessionInfo(
            session_id=session_id,
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            working_directory=working_directory,
            current_permission_mode=permission_mode,
            initial_permission_mode=permission_mode,
            system_prompt=system_prompt,
            tools=tools if tools is not None else [],
            model=model,
            name=name,
            order=order,
            project_id=project_id,
            # Minion-specific fields
            is_minion=is_minion,
            role=role,
            capabilities=capabilities if capabilities is not None else [],
            initialization_context=initialization_context,
            # Hierarchy fields (Phase 5)
            parent_overseer_id=parent_overseer_id,
            overseer_level=overseer_level,
            horde_id=horde_id
        )

        try:
            # Create session directory structure
            session_dir = self.sessions_dir / session_id
            session_dir.mkdir(exist_ok=True)

            # Store session info
            self._active_sessions[session_id] = session_info
            self._session_locks[session_id] = asyncio.Lock()

            await self._persist_session_state(session_id)

            session_logger.info(f"Created session {session_id}")

        except Exception as e:
            logger.error(f"Failed to create session {session_id}: {e}")
            # Cleanup on failure
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]
            if session_id in self._session_locks:
                del self._session_locks[session_id]
            raise

    async def start_session(self, session_id: str) -> bool:
        """Start a session (transition to ACTIVE state)"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                if session.state not in [SessionState.CREATED, SessionState.PAUSED, SessionState.TERMINATED]:
                    session_logger.warning(f"Cannot start session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.STARTING)

                # Session will remain in STARTING state until SDK is ready
                # SDK will call mark_session_active() when context manager is initialized
                session_logger.info(f"Session {session_id} moved to STARTING state - waiting for SDK initialization")
                return True

            except Exception as e:
                logger.error(f"Failed to start session {session_id}: {e}")
                await self._update_session_state(session_id, SessionState.ERROR, str(e))
                return False

    async def mark_session_active(self, session_id: str) -> bool:
        """Mark session as active when SDK is ready"""
        try:
            async with self._get_session_lock(session_id):
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                if session.state != SessionState.STARTING:
                    session_logger.warning(f"Cannot activate session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.ACTIVE)
                session_logger.info(f"Session {session_id} marked as ACTIVE - SDK ready")
                return True

        except Exception as e:
            logger.error(f"Failed to mark session {session_id} as active: {e}")
            return False

    async def pause_session(self, session_id: str) -> bool:
        """Pause an active session"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                if session.state != SessionState.ACTIVE:
                    session_logger.warning(f"Cannot pause session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.PAUSED)
                session_logger.info(f"Paused session {session_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to pause session {session_id}: {e}")
                return False

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a session and cleanup resources"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    session_logger.warning(f"Session {session_id} not found for termination")
                    return False

                if session.state == SessionState.TERMINATED:
                    session_logger.info(f"Session {session_id} already terminated")
                    return True

                await self._update_session_state(session_id, SessionState.TERMINATING)

                # Perform cleanup tasks
                await asyncio.sleep(0.1)  # Placeholder for cleanup logic

                await self._update_session_state(session_id, SessionState.TERMINATED)
                session_logger.info(f"Terminated session {session_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to terminate session {session_id}: {e}")
                await self._update_session_state(session_id, SessionState.ERROR, str(e))
                return False

    async def update_session_state(self, session_id: str, new_state: SessionState, error_message: Optional[str] = None) -> bool:
        """Update session state with optional error message"""
        async with self._get_session_lock(session_id):
            try:
                await self._update_session_state(session_id, new_state, error_message)
                session_logger.info(f"Updated session {session_id} state to {new_state.value}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} state to {new_state.value}: {e}")
                return False

    async def update_processing_state(self, session_id: str, is_processing: bool) -> bool:
        """Update session processing state"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                session.is_processing = is_processing
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} processing state to {is_processing}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} processing state: {e}")
                return False

    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Get session information"""
        return self._active_sessions.get(session_id)

    async def list_sessions(self) -> List[SessionInfo]:
        """List all sessions sorted by order"""
        sessions = list(self._active_sessions.values())

        # Sort by order (None/missing order gets high value), then by created_at as fallback
        def sort_key(session: SessionInfo):
            order = session.order if session.order is not None else 999999
            return (order, session.created_at)

        return sorted(sessions, key=sort_key)

    async def get_sessions_by_ids(self, session_ids: List[str]) -> List[SessionInfo]:
        """Get sessions by IDs in the order provided"""
        sessions = []
        for session_id in session_ids:
            session = self._active_sessions.get(session_id)
            if session:
                sessions.append(session)
            else:
                session_logger.warning(f"Session {session_id} not found in get_sessions_by_ids")
        return sessions

    async def get_session_directory(self, session_id: str) -> Optional[Path]:
        """Get the data directory path for a session"""
        if session_id not in self._active_sessions:
            return None
        return self.sessions_dir / session_id

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create lock for session"""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _update_session_state(
        self,
        session_id: str,
        new_state: SessionState,
        error_message: Optional[str] = None
    ):
        """Update session state and persist changes"""
        session = self._active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.state = new_state
        session.updated_at = datetime.now(timezone.utc)
        if error_message:
            session.error_message = error_message

        await self._persist_session_state(session_id)
        await self._notify_state_change_callbacks(session_id, new_state)

    async def _persist_session_state(self, session_id: str):
        """Persist session state to filesystem"""
        try:
            session = self._active_sessions[session_id]
            session_dir = self.sessions_dir / session_id
            session_dir.mkdir(exist_ok=True)

            state_file = session_dir / "state.json"
            with open(state_file, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)

        except Exception as e:
            logger.error(f"Failed to persist session state for {session_id}: {e}")
            raise

    async def update_claude_code_session_id(self, session_id: str, claude_code_session_id: Optional[str]):
        """Update the Claude Code session ID for a session (including setting to None for reset)"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    raise ValueError(f"Session {session_id} not found")

                session.claude_code_session_id = claude_code_session_id
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                session_logger.info(f"Updated Claude Code session ID for {session_id}: {claude_code_session_id}")

            except Exception as e:
                logger.error(f"Failed to update Claude Code session ID for {session_id}: {e}")
                raise

    async def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                session.name = name.strip()
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} name to '{name}'")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} name: {e}")
                return False

    async def update_permission_mode(self, session_id: str, mode: str) -> bool:
        """Update session permission mode"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                # Validate mode
                valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
                if mode not in valid_modes:
                    logger.error(f"Invalid permission mode: {mode}")
                    return False

                session.current_permission_mode = mode
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} permission mode to '{mode}'")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} permission mode: {e}")
                return False

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its data"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                # First try to delete session directory and all contents
                session_dir = self.sessions_dir / session_id
                if session_dir.exists():
                    try:
                        shutil.rmtree(session_dir)
                        session_logger.info(f"Deleted session directory: {session_dir}")
                    except Exception as e:
                        session_logger.warning(f"Standard deletion failed for {session_dir}: {e}")

                        # Try Windows-specific fallback deletion
                        if os.name == 'nt':  # Windows
                            try:
                                session_logger.info(f"Attempting Windows-specific deletion for {session_dir}")

                                # Method 1: Try to remove directory after ensuring we're not in it
                                # Force garbage collection to clear any directory references
                                gc.collect()
                                time.sleep(0.5)

                                # Use Windows rmdir command as fallback
                                result = subprocess.run(
                                    ['rmdir', '/s', '/q', str(session_dir)],
                                    shell=True,
                                    capture_output=True,
                                    text=True
                                )

                                if result.returncode == 0:
                                    session_logger.info(f"Successfully deleted directory using Windows rmdir: {session_dir}")
                                else:
                                    logger.error(f"Windows rmdir failed: {result.stderr}")
                                    # Don't proceed with memory cleanup if file deletion failed
                                    return False

                            except Exception as fallback_e:
                                logger.error(f"Windows fallback deletion also failed for {session_dir}: {fallback_e}")
                                # Don't proceed with memory cleanup if file deletion failed
                                return False
                        else:
                            # Not Windows, re-raise original error
                            logger.error(f"Failed to delete session directory {session_dir}: {e}")
                            return False

                # Only remove from memory after successful file deletion
                del self._active_sessions[session_id]

                # Remove session lock
                if session_id in self._session_locks:
                    del self._session_locks[session_id]

                session_logger.info(f"Successfully deleted session {session_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                return False

    async def update_session_order(self, session_id: str, order: int) -> bool:
        """Update session order"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                session.order = order
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} order to {order}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} order: {e}")
                return False

    async def update_session(self, session_id: str, **kwargs) -> bool:
        """
        Update session fields dynamically (Phase 5 - for hierarchy management).

        Supported fields:
        - is_overseer (bool)
        - child_minion_ids (List[str])
        - horde_id (str)
        - Any other SessionInfo field

        Example:
            await session_manager.update_session(
                session_id,
                is_overseer=True,
                child_minion_ids=["child1", "child2"]
            )
        """
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                # Update fields
                for key, value in kwargs.items():
                    if hasattr(session, key):
                        setattr(session, key, value)
                    else:
                        logger.warning(f"Session field '{key}' does not exist, skipping")

                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} fields: {list(kwargs.keys())}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id}: {e}")
                return False

    async def reorder_sessions(self, session_ids: List[str]) -> bool:
        """Reorder sessions by assigning sequential order values (within project context)"""
        try:
            # Filter sessions to only those that exist
            valid_session_ids = []
            for session_id in session_ids:
                session = self._active_sessions.get(session_id)
                if session:
                    valid_session_ids.append(session_id)
                else:
                    session_logger.warning(f"Session {session_id} not found during reorder")

            if not valid_session_ids:
                logger.error("No valid sessions found for reordering")
                return False

            # Update order values sequentially
            tasks = []
            for i, session_id in enumerate(valid_session_ids):
                tasks.append(self.update_session_order(session_id, i))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check if all updates succeeded
            success_count = sum(1 for result in results if result is True)
            if success_count == len(valid_session_ids):
                session_logger.info(f"Successfully reordered {success_count} sessions")
                return True
            else:
                logger.error(f"Reorder partially failed: {success_count}/{len(valid_session_ids)} sessions updated")
                return False

        except Exception as e:
            logger.error(f"Failed to reorder sessions: {e}")
            return False

    def add_state_change_callback(self, callback: Callable):
        """Add callback for session state changes"""
        self._state_change_callbacks.append(callback)

    async def _notify_state_change_callbacks(self, session_id: str, new_state: SessionState):
        """Notify registered callbacks about state changes"""
        for callback in self._state_change_callbacks:
            try:
                await callback(session_id, new_state)
            except Exception as e:
                logger.error(f"Error in state change callback: {e}")