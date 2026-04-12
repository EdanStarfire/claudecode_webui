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
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .models.permission_mode import PermissionMode
from .session_config import SessionConfig

# Get specialized logger for session manager actions
session_logger = get_logger('session_manager', category='SESSION_MANAGER')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


# Valid model identifiers (current API aliases)
VALID_MODELS = {
    "opus",
    "sonnet",
    "haiku",
    "opusplan",
}

# Default model for new sessions
DEFAULT_MODEL = "sonnet"


def slugify_name(name: str) -> str:
    """Convert a display name to a slug for MCP tool compatibility.

    "Database Optimizer" -> "database-optimizer"
    "frontend-dev" -> "frontend-dev"  (already slugified, no change)
    """
    slug = name.lower()
    slug = slug.replace("_", "-").replace(" ", "-")
    slug = re.sub(r'[^a-z0-9-]', '', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    return slug


class SessionState(Enum):
    """Session lifecycle states"""
    CREATED = "created"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    ERROR = "error"


# Named SessionState groupings — update these when adding new states
STOPPED_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.PAUSED,
    SessionState.TERMINATED, SessionState.ERROR,
})
STARTABLE_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.PAUSED, SessionState.TERMINATED,
})
RUNNABLE_STATES: frozenset[SessionState] = frozenset({
    SessionState.ACTIVE, SessionState.STARTING,
})
AUTO_START_STATES: frozenset[SessionState] = frozenset({
    SessionState.CREATED, SessionState.TERMINATED,
})


@dataclass
class SessionInfo:
    """Session metadata and state information (all sessions are minions - issue #349)"""
    session_id: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    working_directory: str | None = None
    additional_directories: list[str] | None = None  # Extra dirs agent can access (issue #630)
    current_permission_mode: str = "acceptEdits"
    initial_permission_mode: str | None = None
    system_prompt: str | None = None
    override_system_prompt: bool = False  # If True, use custom prompt only (no Claude Code preset)
    allowed_tools: list[str] = None
    disallowed_tools: list[str] = None  # Tools explicitly denied (issue #461)
    model: str | None = None
    error_message: str | None = None
    claude_code_session_id: str | None = None
    is_processing: bool = False
    name: str | None = None
    sdk_generated_name: str | None = None  # AI-generated title from SDK (issue #904)
    slug: str | None = None  # Slugified name for MCP tool lookups (issue #546)
    order: int | None = None
    project_id: str | None = None

    # Multi-agent fields (universal Legion capabilities - issue #313)
    # Note: is_minion field removed in issue #349 - all sessions are minions
    role: str | None = None  # Minion role description
    is_overseer: bool = False  # True if has spawned children
    overseer_level: int = 0  # 0=user-created, 1=child, 2=grandchild
    parent_overseer_id: str | None = None  # None if user-created
    child_minion_ids: list[str] = None  # Child minion session IDs
    capabilities: list[str] = None  # Capability tags for discovery
    expertise_score: float = 0.5  # Expertise level (0.0-1.0, default 0.5 for MVP)
    can_spawn_minions: bool = True  # If False, Legion MCP tools not attached (leaf minion)

    # Latest message tracking (issue #291) - for hierarchy view visibility
    latest_message: str | None = None  # Last user/assistant/system message (truncated to 200 chars)
    latest_message_type: str | None = None  # "user", "assistant", "system"
    latest_message_time: datetime | None = None  # When the message was received

    # Sandbox mode support (issue #319)
    sandbox_enabled: bool = False  # If True, enable OS-level sandboxing via SDK
    sandbox_config: dict | None = None  # Optional SandboxSettings config (auto_allow_bash, excluded_commands, etc.)

    # Settings sources (issue #36) - which settings files to load permissions from
    setting_sources: list[str] | None = None  # Default: ["user", "project", "local"]

    # Message queue configuration (issue #500)
    queue_config: dict | None = None  # {max_queue_size, min_wait_seconds, min_idle_seconds, default_reset_session}
    queue_paused: bool = False  # If True, queue processor skips delivery

    # CLI path override (issue #489) - custom executable for tool execution (e.g., Docker launcher)
    cli_path: str | None = None

    # Docker session isolation (issue #496)
    docker_enabled: bool = False
    docker_image: str | None = None  # Custom image name (default: claude-code:local)
    docker_extra_mounts: list[str] | None = None  # Additional volume mount specs
    docker_home_directory: str | None = None  # Home directory inside container (for custom images)
    # Issue #1049: Proxy mode (sidecar model)
    docker_proxy_container: str | None = None
    docker_proxy_ca_cert: str | None = None

    # Thinking and effort configuration (issue #540)
    thinking_mode: str | None = None  # "adaptive", "enabled", "disabled", or None (SDK default)
    thinking_budget_tokens: int | None = None  # Token budget when thinking_mode="enabled" (min 1024)
    effort: str | None = None  # "low", "medium", "high", or None (SDK default)

    # Ephemeral session support (issue #578)
    is_ephemeral: bool = False  # True for temporary sessions created by ephemeral schedules

    # History distillation toggle (issue #710, renamed #736)
    history_distillation_enabled: bool = True  # When False, skip distillation and history references

    # Auto-memory mode (issue #709, replaces #708 disable_auto_memory boolean)
    auto_memory_mode: str = "claude"  # "claude" | "session" | "disabled"
    # Custom directory for auto-memory when mode is "claude" (issue #906)
    auto_memory_directory: str | None = None

    # Skill creating toggle (issue #749)
    skill_creating_enabled: bool = False

    # MCP server configuration (issue #676)
    mcp_server_ids: list[str] | None = None  # Global MCP config IDs attached to this session
    enable_claudeai_mcp_servers: bool = True  # Toggle ENABLE_CLAUDEAI_MCP_SERVERS env var
    strict_mcp_config: bool = False  # Pass --strict-mcp-config to disable local .mcp.json
    bare_mode: bool = False  # Pass --bare to skip hooks, LSP, plugin sync, skill walks
    env_scrub_enabled: bool = False  # Issue #957: Strip credentials from subprocess envs

    def __post_init__(self):
        if self.additional_directories is None:
            self.additional_directories = []
        if self.allowed_tools is None:
            self.allowed_tools = ["bash", "edit", "read"]
        if self.disallowed_tools is None:
            self.disallowed_tools = []
        if self.child_minion_ids is None:
            self.child_minion_ids = []
        if self.capabilities is None:
            self.capabilities = []
        if self.docker_extra_mounts is None:
            self.docker_extra_mounts = []
        if self.mcp_server_ids is None:
            self.mcp_server_ids = []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['state'] = self.state.value
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        # Convert latest_message_time if present (issue #291)
        if self.latest_message_time:
            data['latest_message_time'] = self.latest_message_time.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionInfo':
        """Create from dictionary loaded from JSON"""
        data['state'] = SessionState(data['state'])
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        # Convert latest_message_time if present (issue #291)
        if 'latest_message_time' in data and data['latest_message_time']:
            data['latest_message_time'] = datetime.fromisoformat(data['latest_message_time'])
        # Issue #349: Silently ignore deprecated is_minion field from old data
        data.pop('is_minion', None)
        # Issue #546: Derive slug from name if missing (backward compat)
        if 'slug' not in data or not data.get('slug'):
            name = data.get('name')
            if name:
                data['slug'] = slugify_name(name)
        # Backward-compat defaults for fields added after initial schema
        data.setdefault('additional_directories', None)
        data.setdefault('override_system_prompt', False)
        data.setdefault('disallowed_tools', None)
        data.setdefault('expertise_score', 0.5)
        data.setdefault('can_spawn_minions', True)
        data.setdefault('overseer_level', 0)
        data.setdefault('latest_message', None)
        data.setdefault('latest_message_type', None)
        data.setdefault('latest_message_time', None)
        data.setdefault('sandbox_enabled', False)
        data.setdefault('sandbox_config', None)
        data.setdefault('setting_sources', None)
        data.setdefault('queue_config', None)
        data.setdefault('queue_paused', False)
        data.setdefault('cli_path', None)
        data.setdefault('docker_enabled', False)
        data.setdefault('docker_image', None)
        data.setdefault('docker_extra_mounts', None)
        data.setdefault('docker_home_directory', None)
        data.setdefault('docker_proxy_container', None)
        data.setdefault('docker_proxy_ca_cert', None)
        data.setdefault('thinking_mode', None)
        data.setdefault('thinking_budget_tokens', None)
        data.setdefault('effort', None)
        if data.get('effort') == 'max':
            data['effort'] = 'high'
        data.setdefault('is_ephemeral', False)
        # Migrate knowledge_management_enabled → history_distillation_enabled (issue #736)
        if 'knowledge_management_enabled' in data and 'history_distillation_enabled' not in data:
            data['history_distillation_enabled'] = data.pop('knowledge_management_enabled')
        else:
            data.pop('knowledge_management_enabled', None)
            data.setdefault('history_distillation_enabled', True)
        # Migrate legacy disable_auto_memory boolean to auto_memory_mode enum (issue #709)
        if 'disable_auto_memory' in data and 'auto_memory_mode' not in data:
            data['auto_memory_mode'] = "disabled" if data.pop('disable_auto_memory') else "claude"
        else:
            data.pop('disable_auto_memory', None)
            data.setdefault('auto_memory_mode', 'claude')
        data.setdefault('auto_memory_directory', None)
        data.setdefault('sdk_generated_name', None)
        data.setdefault('skill_creating_enabled', False)
        data.setdefault('mcp_server_ids', None)
        data.setdefault('enable_claudeai_mcp_servers', True)
        data.setdefault('strict_mcp_config', False)
        data.setdefault('bare_mode', False)
        data.setdefault('env_scrub_enabled', False)
        return cls(**data)


class SessionManager:
    """Manages Claude Code session lifecycle and persistence"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path("data")
        self.sessions_dir = self.data_dir / "sessions"
        self._active_sessions: dict[str, SessionInfo] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._state_change_callbacks: list[Callable] = []

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
                            with open(state_file) as f:
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

                            if session_info.state in RUNNABLE_STATES:
                                session_info.state = SessionState.CREATED
                                session_info.updated_at = datetime.now(UTC)
                                state_changed = True
                                session_logger.info(f"Reset session {session_info.session_id} from {original_state.value} to {session_info.state.value} on startup")

                            # Reset PAUSED sessions to TERMINATED (orphaned permission requests)
                            # PAUSED state means session was waiting for permission response
                            if session_info.state == SessionState.PAUSED:
                                session_info.state = SessionState.TERMINATED
                                session_info.updated_at = datetime.now(UTC)
                                state_changed = True
                                session_logger.info(f"Reset session {session_info.session_id} from PAUSED to TERMINATED on startup (orphaned permission request)")

                            # Reset processing state since no SDKs are running on startup
                            if session_info.is_processing:
                                session_info.is_processing = False
                                session_info.updated_at = datetime.now(UTC)
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
        config: SessionConfig,
        name: str | None = None,
        order: int | None = None,
        project_id: str | None = None,
        # Multi-agent fields (universal Legion - issue #313, #349)
        role: str | None = None,
        capabilities: list[str] = None,
        parent_overseer_id: str | None = None,
        overseer_level: int = 0,
        can_spawn_minions: bool = True,  # If False, no MCP spawn tools attached
    ) -> None:
        """Create a new session (all sessions are minions - issue #349)

        Args:
            session_id: Unique session identifier
            config: Bundled configuration toggles (permission, tools, model, etc.)
            name: Display name (defaults to timestamp)
            order: Display order (defaults to 0)
            project_id: Parent project ID
            role: Minion role description
            capabilities: Capability tags for discovery
            parent_overseer_id: Parent minion session ID (None if user-created)
            overseer_level: Hierarchy depth (0=user-created)
            can_spawn_minions: If False, no MCP spawn tools attached
        """
        # Validate session_id is not reserved
        from src.models.legion_models import RESERVED_MINION_IDS
        if session_id in RESERVED_MINION_IDS:
            raise ValueError(f"Cannot create session with reserved ID: {session_id}")

        now = datetime.now(UTC)

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
            working_directory=config.working_directory,
            additional_directories=config.additional_directories if config.additional_directories is not None else [],
            current_permission_mode=config.permission_mode,
            initial_permission_mode=config.permission_mode,
            system_prompt=config.system_prompt,
            override_system_prompt=config.override_system_prompt,
            allowed_tools=config.allowed_tools if config.allowed_tools is not None else [],
            disallowed_tools=config.disallowed_tools if config.disallowed_tools is not None else [],
            model=config.model,
            name=name,
            slug=slugify_name(name) if name else None,
            order=order,
            project_id=project_id,
            # Multi-agent fields (universal Legion - issue #313, #349)
            role=role,
            capabilities=capabilities if capabilities is not None else [],
            parent_overseer_id=parent_overseer_id,
            overseer_level=overseer_level,
            can_spawn_minions=can_spawn_minions,
            # Sandbox mode (issue #319)
            sandbox_enabled=config.sandbox_enabled,
            sandbox_config=config.sandbox_config,
            # Settings sources (issue #36)
            setting_sources=config.setting_sources,
            # CLI path override (issue #489)
            cli_path=config.cli_path,
            # Docker session isolation (issue #496)
            docker_enabled=config.docker_enabled,
            docker_image=config.docker_image,
            docker_extra_mounts=config.docker_extra_mounts if config.docker_extra_mounts is not None else [],
            docker_home_directory=config.docker_home_directory,
            # Issue #1049: Proxy mode (sidecar model)
            docker_proxy_container=config.docker_proxy_container,
            docker_proxy_ca_cert=config.docker_proxy_ca_cert,
            # Thinking and effort configuration (issue #540)
            thinking_mode=config.thinking_mode,
            thinking_budget_tokens=config.thinking_budget_tokens,
            effort=config.effort,
            history_distillation_enabled=config.history_distillation_enabled,
            auto_memory_mode=config.auto_memory_mode,
            auto_memory_directory=config.auto_memory_directory,
            skill_creating_enabled=config.skill_creating_enabled,
            # MCP server configuration (issue #676)
            mcp_server_ids=config.mcp_server_ids if config.mcp_server_ids is not None else [],
            enable_claudeai_mcp_servers=config.enable_claudeai_mcp_servers,
            strict_mcp_config=config.strict_mcp_config,
            bare_mode=config.bare_mode,
            env_scrub_enabled=config.env_scrub_enabled,
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

                if session.state not in STARTABLE_STATES:
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
        """Pause an active session (used internally for permission prompt waiting)"""
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

    async def update_session_state(self, session_id: str, new_state: SessionState, error_message: str | None = None) -> bool:
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

                # Issue #1029: Early-return if value unchanged. The reactive approach
                # fires update_processing_state(True) on every assistant message
                # (every streaming chunk). Without this guard, each token triggers
                # a full JSON file write + callback dispatch.
                if session.is_processing == is_processing:
                    return True

                session.is_processing = is_processing
                session.updated_at = datetime.now(UTC)
                await self._persist_processing_state_only(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} processing state to {is_processing}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} processing state: {e}")
                return False

    async def get_session_info(self, session_id: str) -> SessionInfo | None:
        """Get session information"""
        return self._active_sessions.get(session_id)

    async def list_sessions(self) -> list[SessionInfo]:
        """List all sessions sorted by order"""
        sessions = list(self._active_sessions.values())

        # Sort by order (None/missing order gets high value), then by created_at as fallback
        def sort_key(session: SessionInfo):
            order = session.order if session.order is not None else 999999
            return (order, session.created_at)

        return sorted(sessions, key=sort_key)

    async def get_sessions_by_ids(self, session_ids: list[str]) -> list[SessionInfo]:
        """Get sessions by IDs in the order provided"""
        sessions = []
        for session_id in session_ids:
            session = self._active_sessions.get(session_id)
            if session:
                sessions.append(session)
            else:
                session_logger.warning(f"Session {session_id} not found in get_sessions_by_ids")
        return sessions

    async def get_session_directory(self, session_id: str) -> Path | None:
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
        error_message: str | None = None
    ):
        """Update session state and persist changes"""
        session = self._active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.state = new_state
        session.updated_at = datetime.now(UTC)
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

    async def _persist_processing_state_only(self, session_id: str):
        """Persist only is_processing and updated_at to state.json (targeted write).

        Reads the existing file, patches two fields, and writes back.
        Falls back to full write if the file doesn't exist yet.
        """
        try:
            session = self._active_sessions[session_id]
            state_file = self.sessions_dir / session_id / "state.json"

            if not state_file.exists():
                # No existing file — fall back to full write
                await self._persist_session_state(session_id)
                return

            with open(state_file) as f:
                data = json.load(f)

            data['is_processing'] = session.is_processing
            data['updated_at'] = session.updated_at.isoformat()

            with open(state_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to persist processing state for {session_id}: {e}")
            raise

    async def update_claude_code_session_id(self, session_id: str, claude_code_session_id: str | None):
        """Update the Claude Code session ID for a session (including setting to None for reset)"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    raise ValueError(f"Session {session_id} not found")

                session.claude_code_session_id = claude_code_session_id
                session.updated_at = datetime.now(UTC)
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
                session.slug = slugify_name(name.strip())
                session.updated_at = datetime.now(UTC)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} name to '{name}'")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} name: {e}")
                return False

    async def update_sdk_generated_name(self, session_id: str, sdk_name: str) -> bool:
        """Update SDK-generated session title (issue #904)"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    return False
                if session.sdk_generated_name == sdk_name:
                    return True  # No change needed — idempotent success
                session.sdk_generated_name = sdk_name
                session.updated_at = datetime.now(UTC)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} sdk_generated_name to '{sdk_name}'")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} sdk_generated_name: {e}")
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
                if mode not in PermissionMode._value2member_map_:
                    logger.error(f"Invalid permission mode: {mode}")
                    return False

                session.current_permission_mode = mode
                session.updated_at = datetime.now(UTC)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} permission mode to '{mode}'")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} permission mode: {e}")
                return False

    async def update_additional_directories(self, session_id: str, dirs_to_add: list[str]) -> bool:
        """Add directories to session's additional_directories list (deduplicated).

        Issue #630: Persist addDirectories permission suggestions to session configuration.
        """
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                existing = set(session.additional_directories or [])
                added = []
                for d in dirs_to_add:
                    d = d.strip()
                    if d and d not in existing:
                        existing.add(d)
                        added.append(d)

                if added:
                    session.additional_directories = list(existing)
                    session.updated_at = datetime.now(UTC)
                    await self._persist_session_state(session_id)
                    session_logger.info(
                        f"Added {len(added)} directories to session {session_id}: {added}"
                    )
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id} additional_directories: {e}")
                return False

    async def update_allowed_tools(self, session_id: str, tools_to_add: list[str]) -> bool:
        """Add tools to session's allowed_tools list (case-sensitive, deduplicated).

        Issue #433: Persist permission approvals to session configuration.
        """
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                existing = set(session.allowed_tools or [])
                new_tools = [t for t in tools_to_add if t not in existing]
                if not new_tools:
                    return True  # Already present

                session.allowed_tools = list(existing | set(new_tools))
                session.updated_at = datetime.now(UTC)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(
                    f"Added {len(new_tools)} tools to session {session_id} "
                    f"allowed_tools: {new_tools}"
                )
                return True
            except Exception as e:
                logger.error(
                    f"Failed to update allowed_tools for session {session_id}: {e}"
                )
                return False

    async def update_latest_message(
        self,
        session_id: str,
        content: str,
        message_type: str,
        timestamp: datetime
    ) -> bool:
        """Update latest message tracking for a session (issue #291)"""
        async with self._get_session_lock(session_id):
            try:
                session = self._active_sessions.get(session_id)
                if not session:
                    logger.error(f"Session {session_id} not found")
                    return False

                # Update fields
                session.latest_message = content
                session.latest_message_type = message_type
                session.latest_message_time = timestamp
                session.updated_at = datetime.now(UTC)

                # Persist to disk
                await self._persist_session_state(session_id)

                # Trigger state change callback
                await self._notify_state_change_callbacks(session_id, session.state)

                session_logger.debug(f"Updated latest message for session {session_id}: {message_type}")
                return True
            except Exception as e:
                logger.error(f"Failed to update latest message for session {session_id}: {e}")
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
                session.updated_at = datetime.now(UTC)
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

                session.updated_at = datetime.now(UTC)
                await self._persist_session_state(session_id)
                await self._notify_state_change_callbacks(session_id, session.state)
                session_logger.info(f"Updated session {session_id} fields: {list(kwargs.keys())}")
                return True
            except Exception as e:
                logger.error(f"Failed to update session {session_id}: {e}")
                return False

    async def reorder_sessions(self, session_ids: list[str]) -> bool:
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
