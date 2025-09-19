"""
Session Management for Claude Code WebUI

Handles session lifecycle, state persistence, and coordination between
SDK interactions and web interface.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Any, List, Callable
from dataclasses import dataclass, asdict
import logging

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
    """Session metadata and state information"""
    session_id: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    working_directory: Optional[str] = None
    permissions: str = "acceptEdits"
    system_prompt: Optional[str] = None
    tools: List[str] = None
    model: Optional[str] = None
    error_message: Optional[str] = None
    claude_code_session_id: Optional[str] = None
    is_processing: bool = False

    def __post_init__(self):
        if self.tools is None:
            self.tools = ["bash", "edit", "read"]

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
            logger.info(f"SessionManager initialized with {len(self._active_sessions)} existing sessions")
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
                                logger.info(f"Reset session {session_info.session_id} from {original_state.value} to {session_info.state.value} on startup")

                            # Reset processing state since no SDKs are running on startup
                            if session_info.is_processing:
                                session_info.is_processing = False
                                session_info.updated_at = datetime.now(timezone.utc)
                                state_changed = True
                                logger.info(f"Reset processing state for session {session_info.session_id} from {original_processing} to False on startup")

                            self._active_sessions[session_info.session_id] = session_info

                            # Save the updated state if it was modified
                            if state_changed:
                                await self._persist_session_state(session_info.session_id)
                            self._session_locks[session_info.session_id] = asyncio.Lock()
                            logger.debug(f"Loaded session {session_info.session_id} with state {session_info.state}")
                        except Exception as e:
                            logger.error(f"Failed to load session from {session_dir}: {e}")
        except Exception as e:
            logger.error(f"Error loading existing sessions: {e}")

    async def create_session(
        self,
        working_directory: Optional[str] = None,
        permissions: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        tools: List[str] = None,
        model: Optional[str] = None
    ) -> str:
        """Create a new session with unique ID"""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        session_info = SessionInfo(
            session_id=session_id,
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            working_directory=working_directory,
            permissions=permissions,
            system_prompt=system_prompt,
            tools=tools if tools is not None else [],
            model=model
        )

        try:
            # Create session directory structure
            session_dir = self.sessions_dir / session_id
            session_dir.mkdir(exist_ok=True)

            # Store session info
            self._active_sessions[session_id] = session_info
            self._session_locks[session_id] = asyncio.Lock()

            await self._persist_session_state(session_id)

            logger.info(f"Created session {session_id}")
            return session_id

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
                    logger.warning(f"Cannot start session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.STARTING)

                # Session will remain in STARTING state until SDK is ready
                # SDK will call mark_session_active() when context manager is initialized
                logger.info(f"Session {session_id} moved to STARTING state - waiting for SDK initialization")
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
                    logger.warning(f"Cannot activate session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.ACTIVE)
                logger.info(f"Session {session_id} marked as ACTIVE - SDK ready")
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
                    logger.warning(f"Cannot pause session {session_id} in state {session.state}")
                    return False

                await self._update_session_state(session_id, SessionState.PAUSED)
                logger.info(f"Paused session {session_id}")
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
                    logger.warning(f"Session {session_id} not found for termination")
                    return False

                if session.state == SessionState.TERMINATED:
                    logger.info(f"Session {session_id} already terminated")
                    return True

                await self._update_session_state(session_id, SessionState.TERMINATING)

                # Perform cleanup tasks
                await asyncio.sleep(0.1)  # Placeholder for cleanup logic

                await self._update_session_state(session_id, SessionState.TERMINATED)
                logger.info(f"Terminated session {session_id}")
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
                logger.info(f"Updated session {session_id} state to {new_state.value}")
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
                logger.info(f"Updated session {session_id} processing state to {is_processing}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} processing state: {e}")
                return False

    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Get session information"""
        return self._active_sessions.get(session_id)

    async def list_sessions(self) -> List[SessionInfo]:
        """List all sessions"""
        return list(self._active_sessions.values())

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

    async def update_claude_code_session_id(self, session_id: str, claude_code_session_id: str):
        """Update the Claude Code session ID for a session"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    raise ValueError(f"Session {session_id} not found")

                session.claude_code_session_id = claude_code_session_id
                session.updated_at = datetime.now(timezone.utc)
                await self._persist_session_state(session_id)
                logger.info(f"Updated Claude Code session ID for {session_id}: {claude_code_session_id}")

            except Exception as e:
                logger.error(f"Failed to update Claude Code session ID for {session_id}: {e}")
                raise

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