"""
Session Coordinator for Claude Code WebUI

Integrates session management, data storage, and SDK interaction
into a unified system for managing Claude Code sessions.
"""

import asyncio
import gc
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.legion.minion_system_prompts import get_legion_guide_only

from .claude_sdk import ClaudeSDK
from .data_storage import DataStorageManager
from .logging_config import get_logger
from .message_parser import MessageParser, MessageProcessor
from .models.messages import (
    DisplayProjection,
    PermissionInfo,
    ToolCall,
    ToolDisplayInfo,
    ToolState,
    legacy_to_stored,
)
from .project_manager import ProjectInfo, ProjectManager
from .queue_manager import QueueManager
from .queue_processor import QueueProcessor
from .session_manager import SessionManager, SessionState
from .timestamp_utils import get_unix_timestamp

# Get specialized logger for coordinator actions
coord_logger = get_logger('coordinator', category='COORDINATOR')
# Keep standard logger for errors
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

    def __init__(self, data_dir: Path = None, experimental: bool = False):
        self.data_dir = data_dir or Path("data")
        self.experimental = experimental
        self.session_manager = SessionManager(self.data_dir)
        self.project_manager = ProjectManager(self.data_dir)
        self.message_parser = MessageParser()
        self.message_processor = MessageProcessor(self.message_parser)

        # Template manager for minion templates
        from src.template_manager import TemplateManager
        self.template_manager = TemplateManager(self.data_dir)

        # Active SDK instances
        self._active_sdks: dict[str, ClaudeSDK] = {}
        self._storage_managers: dict[str, DataStorageManager] = {}

        # Event callbacks
        self._message_callbacks: dict[str, list[Callable]] = {}
        self._error_callbacks: dict[str, list[Callable]] = {}
        self._state_change_callbacks: list[Callable] = []

        # Track recent tool uses for ExitPlanMode detection
        self._recent_tool_uses: dict[str, dict[str, str]] = {}  # session_id -> {tool_use_id: tool_name}

        # Track applied permission updates for state management
        self._permission_updates: dict[str, list[dict]] = {}  # session_id -> list of applied updates

        # Display projections per session (Issue #310)
        # Tracks tool lifecycle state and computes display metadata for frontend
        self._display_projections: dict[str, DisplayProjection] = {}

        # Track ExitPlanMode that had setMode suggestions applied (to prevent auto-reset)
        self._exitplanmode_with_setmode: dict[str, bool] = {}  # session_id -> bool

        # Issue #324: Active tool calls per session for unified ToolCall lifecycle
        # Maps session_id -> {tool_use_id: ToolCall}
        self._active_tool_calls: dict[str, dict[str, ToolCall]] = {}

        # Issue #403: Uploaded file paths per session for auto-approve Read permissions
        # Maps session_id -> set of absolute file paths
        self._uploaded_file_paths: dict[str, set[str]] = {}

        # Permission callback factory (injected by web_server)
        # Allows legion components to create permission callbacks on-demand
        self._permission_callback_factory: Callable[[str], Callable] | None = None

        # Message callback registrar (injected by web_server)
        # Allows legion components to register WebSocket message callbacks
        self._message_callback_registrar: Callable[[str], None] | None = None

        # Initialize Legion multi-agent system
        from src.legion_system import LegionSystem
        # Create a dummy storage manager for now (legion will create its own per-legion storage)
        dummy_storage = DataStorageManager(self.data_dir / "legion_temp")
        self.legion_system = LegionSystem(
            session_coordinator=self,
            data_storage_manager=dummy_storage,
            template_manager=self.template_manager
        )

        # Issue #500: Message queue system
        self.queue_manager = QueueManager()
        self.queue_processor = QueueProcessor(self)

        # Issue #404: Resource MCP tools for displaying resources in task panel
        # Callback for broadcasting resource_registered events will be set by web_server
        from src.mcp.resource_mcp_tools import ResourceMCPTools
        self._resource_broadcast_callback: Callable[[str, dict], None] | None = None
        self.resource_mcp_tools = ResourceMCPTools(
            session_coordinator=self,
            broadcast_callback=self._broadcast_resource_registered
        )
        # Backward compatibility alias
        self.image_viewer_mcp_tools = self.resource_mcp_tools

    async def initialize(self):
        """Initialize the session coordinator"""
        try:
            await self.session_manager.initialize()
            await self.project_manager.initialize()

            # Load templates from disk
            await self.template_manager.load_templates()
            # Create default templates if none exist
            await self.template_manager.create_default_templates()
            coord_logger.info("Loaded minion templates")

            # Rebuild capability registry from persisted session data (if LegionSystem is initialized)
            if hasattr(self, 'legion_system') and self.legion_system is not None:
                await self.legion_system.legion_coordinator.rebuild_capability_registry()

            # Register callback to receive session manager state changes
            self.session_manager.add_state_change_callback(self._on_session_manager_state_change)

            # Initialize storage managers for all existing sessions
            await self._initialize_existing_session_storage()

            # Issue #500: Load message queues for all existing sessions
            await self._initialize_queues()

            # Validate and cleanup orphaned project/session references (issue #63)
            await self._validate_and_cleanup_projects()

            coord_logger.info("Session coordinator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize session coordinator: {e}")
            raise

    async def _initialize_existing_session_storage(self):
        """Initialize storage managers for all existing sessions"""
        try:
            # Get all existing sessions
            sessions = await self.session_manager.list_sessions()
            # logger.info(f"Initializing storage managers for {len(sessions)} existing sessions")

            for session in sessions:
                session_id = session.session_id
                if session_id not in self._storage_managers:
                    # Create storage manager for this session
                    session_dir = await self.session_manager.get_session_directory(session_id)
                    storage_manager = DataStorageManager(session_dir)
                    await storage_manager.initialize()
                    self._storage_managers[session_id] = storage_manager
                    # logger.info(f"Initialized storage manager for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to initialize storage managers for existing sessions: {e}")
            # Don't raise - this shouldn't block coordinator initialization

    async def _initialize_queues(self):
        """Load message queues for all existing sessions on startup."""
        try:
            sessions = await self.session_manager.list_sessions()
            for session in sessions:
                session_id = session.session_id
                session_dir = await self.session_manager.get_session_directory(session_id)
                if session_dir:
                    await self.queue_manager.load_queue(session_id, session_dir)
                    # Auto-start processor if there are pending items
                    if self.queue_manager.get_pending_count(session_id) > 0:
                        queue_paused = getattr(session, 'queue_paused', False)
                        if not queue_paused:
                            self.queue_processor.ensure_running(session_id)
        except Exception as e:
            logger.error(f"Failed to initialize queues: {e}")

    # =========================================================================
    # Message Queue Operations (Issue #500)
    # =========================================================================

    async def enqueue_message(
        self,
        session_id: str,
        content: str,
        reset_session: bool | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Enqueue a message for a session. Returns the queue item dict."""
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")

        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            raise ValueError(f"Session directory not found for {session_id}")

        queue_config = getattr(session_info, 'queue_config', None) or {}
        max_queue_size = queue_config.get("max_queue_size", 100)

        # Use session default if not specified
        if reset_session is None:
            reset_session = queue_config.get("default_reset_session", True)

        item = await self.queue_manager.enqueue(
            session_id=session_id,
            session_dir=session_dir,
            content=content,
            reset_session=reset_session,
            metadata=metadata,
            max_queue_size=max_queue_size,
        )

        # Start processor if not paused
        queue_paused = getattr(session_info, 'queue_paused', False)
        if not queue_paused:
            self.queue_processor.ensure_running(session_id)

        return item.to_dict()

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Cancel a pending queue item. Returns the item dict or None."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.queue_manager.cancel(session_id, session_dir, queue_id)
        return item.to_dict() if item else None

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Re-queue a sent/failed item at the front. Returns the new item dict."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.queue_manager.requeue(session_id, session_dir, queue_id)
        if item:
            session_info = await self.session_manager.get_session_info(session_id)
            queue_paused = getattr(session_info, 'queue_paused', False) if session_info else False
            if not queue_paused:
                self.queue_processor.ensure_running(session_id)
        return item.to_dict() if item else None

    async def clear_queue(self, session_id: str) -> int:
        """Clear all pending queue items. Returns count of cancelled items."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self.queue_manager.clear_pending(session_id, session_dir)

    async def get_queue(self, session_id: str) -> list[dict]:
        """Get all queue items for a session."""
        return [item.to_dict() for item in self.queue_manager.get_queue(session_id)]

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        """Pause or resume the queue for a session."""
        success = await self.session_manager.update_session(
            session_id, queue_paused=paused
        )
        if success and not paused:
            # Resume: start processor if there are pending items
            if self.queue_manager.get_pending_count(session_id) > 0:
                self.queue_processor.ensure_running(session_id)
        return success

    async def update_queue_config(self, session_id: str, config: dict) -> bool:
        """Update queue configuration for a session."""
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return False
        current = getattr(session_info, 'queue_config', None) or {}
        current.update(config)
        return await self.session_manager.update_session(
            session_id, queue_config=current
        )

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        permission_mode: str = "acceptEdits",
        system_prompt: str | None = None,
        override_system_prompt: bool = False,
        allowed_tools: list[str] = None,
        disallowed_tools: list[str] = None,
        model: str | None = None,
        name: str | None = None,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        working_directory: str | None = None,  # Custom working directory (defaults to project directory)
        # Multi-agent fields (universal Legion - issue #313, #349)
        role: str | None = None,
        capabilities: list[str] = None,
        parent_overseer_id: str | None = None,
        overseer_level: int = 0,
        can_spawn_minions: bool = True,  # If False, no MCP spawn tools attached
        # Sandbox mode (issue #319)
        sandbox_enabled: bool = False,
        sandbox_config: dict | None = None,
        # Settings sources (issue #36)
        setting_sources: list[str] | None = None,
    ) -> str:
        """Create a new Claude Code session with integrated components (within a project)"""
        try:
            # Get project to determine working directory and multi-agent status
            project = await self.project_manager.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")

            # Use custom working directory if provided, otherwise use project directory
            working_directory = working_directory or project.working_directory

            # Calculate order based on existing sessions in project
            order = len(project.session_ids)

            # Issue #349: All sessions are minions (is_minion field removed)

            # Create session through session manager
            # Store raw system_prompt (guide will be prepended later in start_session)
            await self.session_manager.create_session(
                session_id=session_id,
                working_directory=working_directory,
                permission_mode=permission_mode,
                system_prompt=system_prompt,
                override_system_prompt=override_system_prompt,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                model=model,
                name=name,
                order=order,
                project_id=project_id,
                # Multi-agent fields (universal Legion - issue #313, #349)
                role=role or "assistant",
                capabilities=capabilities,
                parent_overseer_id=parent_overseer_id,
                overseer_level=overseer_level,
                can_spawn_minions=can_spawn_minions,
                # Sandbox mode (issue #319)
                sandbox_enabled=sandbox_enabled,
                sandbox_config=sandbox_config,
                # Settings sources (issue #36)
                setting_sources=setting_sources
            )

            # Add session to project
            await self.project_manager.add_session_to_project(project_id, session_id)

            # Initialize storage manager for this session
            session_dir = await self.session_manager.get_session_directory(session_id)
            storage_manager = DataStorageManager(session_dir)
            await storage_manager.initialize()
            self._storage_managers[session_id] = storage_manager

            # Issue #313: Attach MCP tools based on can_spawn_minions flag (universal Legion)
            # All sessions can have Legion MCP tools if can_spawn_minions=True
            mcp_servers = {}
            mcp_tools_list = []
            coord_logger.debug(f"MCP attachment check for session {session_id}: can_spawn_minions={can_spawn_minions}, legion_system={self.legion_system is not None}")
            if can_spawn_minions and self.legion_system and self.legion_system.mcp_tools:
                # Create session-specific MCP server (injects session_id into tool calls)
                mcp_server = self.legion_system.mcp_tools.create_mcp_server_for_session(session_id)
                coord_logger.debug(f"Created session-specific MCP server for {session_id}: {mcp_server}")
                if mcp_server:
                    # SDK expects dict mapping server name to config, not a list
                    mcp_servers["legion"] = mcp_server
                    # Allow all legion MCP tools (reduces command-line length)
                    mcp_tools_list.append("mcp__legion")
                    coord_logger.info(f"Attaching Legion MCP tools to session {session_id}")
                else:
                    coord_logger.warning(f"MCP server creation failed for session {session_id}")
            else:
                if not can_spawn_minions:
                    coord_logger.debug(f"Session {session_id} has can_spawn_minions=False - skipping MCP spawn tools")
                elif not self.legion_system:
                    coord_logger.warning(f"Legion system is None for session {session_id}")
                elif not self.legion_system.mcp_tools:
                    coord_logger.warning(f"Legion system mcp_tools is None for session {session_id}")

            # Issue #404: Attach resource MCP tools to all sessions
            if self.resource_mcp_tools:
                resource_mcp_server = self.resource_mcp_tools.create_mcp_server_for_session(session_id)
                if resource_mcp_server:
                    mcp_servers["resources"] = resource_mcp_server
                    mcp_tools_list.append("mcp__resources")
                    coord_logger.info(f"Attaching Resource MCP tools to session {session_id}")

            # Merge MCP tools with any provided allowed_tools
            all_tools = allowed_tools if allowed_tools else []
            all_tools = list(set(all_tools + mcp_tools_list))  # Deduplicate

            # Escape special characters in system_prompt for subprocess command-line safety
            # CRITICAL: Newlines break subprocess argument parsing on Windows
            escaped_system_prompt = system_prompt
            if system_prompt:
                # Replace literal newlines with escaped newlines for command-line
                escaped_system_prompt = system_prompt.replace('\n', '\\n').replace('\r', '\\r')
                # Also escape other problematic characters
                escaped_system_prompt = escaped_system_prompt.replace('"', '\\"').replace('$', '\\$')
                coord_logger.info(f"Escaped system prompt in create: {len(escaped_system_prompt)} chars (original: {len(system_prompt)})")

            # Create SDK instance
            sdk = ClaudeSDK(
                session_id=session_id,
                working_directory=working_directory,
                storage_manager=storage_manager,
                session_manager=self.session_manager,
                message_callback=self._create_message_callback(session_id),
                error_callback=self._create_error_callback(session_id),
                permission_callback=permission_callback,
                permissions=permission_mode,
                system_prompt=escaped_system_prompt,
                override_system_prompt=override_system_prompt,
                tools=all_tools,
                disallowed_tools=disallowed_tools,
                model=model,
                mcp_servers=mcp_servers if mcp_servers else None,
                sandbox_enabled=sandbox_enabled,
                sandbox_config=sandbox_config,
                experimental=self.experimental
            )
            self._active_sdks[session_id] = sdk

            # Initialize callback lists
            self._message_callbacks[session_id] = []
            self._error_callbacks[session_id] = []

            coord_logger.info(f"Session {session_id} created in project {project_id}")
            await self._notify_state_change(session_id, SessionState.CREATED)

            return session_id

        except Exception as e:
            logger.error(f"Failed to create integrated session: {e}")
            raise

    async def get_session_storage(self, session_id: str):
        """Get storage manager for a session"""
        return self._storage_managers.get(session_id)

    async def get_session_images(self, session_id: str) -> list[dict]:
        """
        Get all image metadata for a session.

        Issue #404: Used by REST endpoint to list session images.

        Args:
            session_id: Session ID

        Returns:
            List of image metadata dicts
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            # Try to get from session directory
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.read_images()

        return []

    async def get_session_image_file(self, session_id: str, image_id: str) -> bytes | None:
        """
        Get raw image bytes for a specific image.

        Issue #404: Used by REST endpoint to serve image files.

        Args:
            session_id: Session ID
            image_id: Image ID

        Returns:
            Raw image bytes or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.get_image_file(image_id)

        return None

    async def get_session_resources(self, session_id: str) -> list[dict]:
        """
        Get all resource metadata for a session.

        Issue #404: Used by REST endpoint to list session resources.

        Args:
            session_id: Session ID

        Returns:
            List of resource metadata dicts
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            # Try to get from session directory
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.read_resources()

        return []

    async def get_session_resource_file(self, session_id: str, resource_id: str) -> bytes | None:
        """
        Get raw file bytes for a specific resource.

        Issue #404: Used by REST endpoint to serve resource files.

        Args:
            session_id: Session ID
            resource_id: Resource ID

        Returns:
            Raw file bytes or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.get_resource_file(resource_id)

        return None

    async def remove_session_resource(self, session_id: str, resource_id: str) -> bool:
        """
        Soft-remove a resource from the session display.

        Issue #423: Appends a removal marker to resources.jsonl so the resource
        is hidden from the gallery. The .bin file is NOT deleted.

        Args:
            session_id: Session ID
            resource_id: Resource ID to remove from display

        Returns:
            True if removed successfully, False otherwise
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.remove_resource_from_display(resource_id)

        return False

    def set_permission_callback_factory(self, factory: Callable[[str], Callable]) -> None:
        """
        Set the permission callback factory for creating callbacks on demand.

        This factory is used by legion components (overseer_controller, comm_router)
        to create permission callbacks for spawned minions.

        Args:
            factory: Function that takes session_id and returns permission_callback
        """
        self._permission_callback_factory = factory
        coord_logger.info("Permission callback factory registered in SessionCoordinator")

        # Propagate to legion_system if it exists
        if self.legion_system:
            self.legion_system.permission_callback_factory = factory
            coord_logger.info("Permission callback factory propagated to LegionSystem")

    def set_message_callback_registrar(self, registrar: Callable[[str], None]) -> None:
        """
        Set the message callback registrar for registering WebSocket callbacks.

        This registrar is used by legion components (overseer_controller, comm_router)
        to register WebSocket message callbacks for spawned minions.

        Args:
            registrar: Function that takes session_id and registers message callback
        """
        self._message_callback_registrar = registrar
        coord_logger.info("Message callback registrar registered in SessionCoordinator")

        # Propagate to legion_system if it exists
        if self.legion_system:
            self.legion_system.message_callback_registrar = registrar
            coord_logger.info("Message callback registrar propagated to LegionSystem")

    def set_resource_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """
        Set the callback for broadcasting resource_registered events to WebSocket.

        Issue #404: Resource MCP tool integration.

        Args:
            callback: Async function(session_id, resource_metadata) for WebSocket broadcast
        """
        self._resource_broadcast_callback = callback
        coord_logger.info("Resource broadcast callback registered in SessionCoordinator")

    # Backward compatibility alias
    def set_image_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Deprecated: Use set_resource_broadcast_callback instead."""
        self.set_resource_broadcast_callback(callback)

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict) -> None:
        """
        Broadcast resource_registered event via the injected callback.

        Issue #404: Called by ResourceMCPTools when a resource is registered.

        Args:
            session_id: Session that registered the resource
            resource_metadata: Resource metadata dict (resource_id, title, is_image, etc.)
        """
        if self._resource_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._resource_broadcast_callback):
                    await self._resource_broadcast_callback(session_id, resource_metadata)
                else:
                    self._resource_broadcast_callback(session_id, resource_metadata)
            except Exception as e:
                logger.error(f"Failed to broadcast resource_registered for {session_id}: {e}")

    async def start_session(self, session_id: str, permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None) -> bool:
        """Start a session with SDK integration"""
        try:
            # Start session through session manager
            if not await self.session_manager.start_session(session_id):
                return False

            # Defensively reset processing state on session start
            await self.session_manager.update_processing_state(session_id, False)

            # Check if SDK exists and is running
            sdk = self._active_sdks.get(session_id)
            if sdk and sdk.is_running():
                # logger.info(f"Session {session_id} is already running, skipping start - NO CLIENT_LAUNCHED MESSAGE")
                return True

            # We need to create or recreate the SDK in either case:
            # - No SDK exists: Create new SDK
            # - SDK exists but not running: Recreate SDK to restart it
            sdk_was_created = True

            # if not sdk:
            #     logger.info(f"No SDK found for session {session_id} - CREATING NEW SDK")
            # else:
            #     logger.info(f"SDK exists but not running for session {session_id} - RECREATING SDK")

            # Get session info to create/recreate SDK with same parameters
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.error(f"No session info found for {session_id}")
                return False

            # Issue #349: is_minion field removed - all sessions are minions
            # Ensure role is set for sessions without one
            if not session_info.role:
                session_info.role = "assistant"
                await self.session_manager._persist_session_state(session_id)
                logger.info(f"Set default role for session {session_id}: {session_info.role}")

            # Create storage manager
            session_dir = await self.session_manager.get_session_directory(session_id)
            storage_manager = DataStorageManager(session_dir)
            await storage_manager.initialize()
            self._storage_managers[session_id] = storage_manager

            # Check if we have a valid Claude Code session ID to resume
            resume_sdk_session = None
            # if session_info.claude_code_session_id:
            #     logger.info(f"Resuming session with Claude Code session ID: {session_info.claude_code_session_id}")
            #     resume_sdk_session = session_id  # Use WebUI session ID as resume identifier
            # else:
            #     logger.info(f"No Claude Code session ID found - starting fresh session for {session_id}")
            if session_info.claude_code_session_id:
                resume_sdk_session = session_id  # Use WebUI session ID as resume identifier

            # Issue #313: Attach MCP tools based on can_spawn_minions flag (universal Legion)
            # All sessions with can_spawn_minions=True get Legion MCP tools
            mcp_servers = {}
            mcp_tools_list = []
            if session_info.can_spawn_minions and self.legion_system and self.legion_system.mcp_tools:
                # Create session-specific MCP server (injects session_id into tool calls)
                mcp_server = self.legion_system.mcp_tools.create_mcp_server_for_session(session_id)
                if mcp_server:
                    # SDK expects dict mapping server name to config, not a list
                    mcp_servers["legion"] = mcp_server
                    # Allow all legion MCP tools (reduces command-line length)
                    mcp_tools_list.append("mcp__legion")
                    coord_logger.info(f"Attaching Legion MCP tools to session {session_id} (can_spawn_minions=True)")

            # Issue #404: Attach resource MCP tools to all sessions
            if self.resource_mcp_tools:
                resource_mcp_server = self.resource_mcp_tools.create_mcp_server_for_session(session_id)
                if resource_mcp_server:
                    mcp_servers["resources"] = resource_mcp_server
                    mcp_tools_list.append("mcp__resources")
                    coord_logger.info(f"Attaching Resource MCP tools to session {session_id}")

            # Merge MCP tools with session's stored allowed_tools
            all_tools = session_info.allowed_tools if session_info.allowed_tools else []
            all_tools = list(set(all_tools + mcp_tools_list))  # Deduplicate

            # Issue #349: All sessions are minions - always prepend legion guide
            legion_guide = get_legion_guide_only()
            if session_info.system_prompt:
                minion_system_prompt = f"{legion_guide}\n\n---\n\n{session_info.system_prompt}"
            else:
                minion_system_prompt = legion_guide
            coord_logger.info(f"Built minion system prompt for start (guide + context): {len(minion_system_prompt)} chars")

            # Escape special characters in system_prompt for subprocess command-line safety
            # CRITICAL: Newlines break subprocess argument parsing on Windows
            if minion_system_prompt:
                # Replace literal newlines with escaped newlines for command-line
                escaped_prompt = minion_system_prompt.replace('\n', '\\n').replace('\r', '\\r')
                # Also escape other problematic characters
                escaped_prompt = escaped_prompt.replace('"', '\\"').replace('$', '\\$')
                coord_logger.info(f"Escaped system prompt: {len(escaped_prompt)} chars (original: {len(minion_system_prompt)})")
                minion_system_prompt = escaped_prompt

            # Create/recreate SDK instance with session parameters
            # system_prompt is used for both regular sessions and minions (SDK appends to Claude Code preset unless override is set)
            sdk = ClaudeSDK(
                session_id=session_id,
                working_directory=session_info.working_directory,
                storage_manager=storage_manager,
                session_manager=self.session_manager,
                message_callback=self._create_message_callback(session_id),
                error_callback=self._create_error_callback(session_id),
                permission_callback=permission_callback,  # Use provided permission callback for resumed sessions
                permissions=session_info.current_permission_mode,
                system_prompt=minion_system_prompt,
                override_system_prompt=session_info.override_system_prompt,
                tools=all_tools,
                disallowed_tools=session_info.disallowed_tools,
                model=session_info.model,
                resume_session_id=resume_sdk_session,  # Only resume if we have a Claude Code session ID
                mcp_servers=mcp_servers if mcp_servers else None,
                sandbox_enabled=session_info.sandbox_enabled,
                sandbox_config=session_info.sandbox_config,
                setting_sources=session_info.setting_sources,  # Issue #36
                experimental=self.experimental
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
                    # logger.info(f"Updated session {session_id} state to ERROR and reset processing state")
                except Exception as state_error:
                    logger.error(f"Failed to update session state to ERROR: {state_error}")

                # Send system message explaining the failure
                await self._send_session_failure_message(session_id, error_message)

                # Clean up the failed SDK
                if session_id in self._active_sdks:
                    del self._active_sdks[session_id]
                    # logger.info(f"Cleaned up failed SDK for session {session_id}")

                return False

            # Send system message for SDK client launch/resume
            # logger.info(f"Session {session_id}: sdk_was_created = {sdk_was_created}")
            if sdk_was_created:  # Only if we actually created/resumed the SDK
                # logger.info(f"CALLING _send_client_launched_message for session {session_id}")
                await self._send_client_launched_message(session_id)
            # else:
            #     logger.info(f"NOT calling _send_client_launched_message for session {session_id} because sdk_was_created = False")

            coord_logger.info(f"Session {session_id} started")
            await self._notify_state_change(session_id, SessionState.ACTIVE)
            return True

        except Exception as e:
            logger.error(f"Failed to start integrated session {session_id}: {e}")

            # Extract user-friendly error message
            error_message = self._extract_claude_cli_error(str(e))

            # Update session state to ERROR and reset processing state
            try:
                await self.session_manager.update_session_state(session_id, SessionState.ERROR, error_message)
                await self.session_manager.update_processing_state(session_id, False)
                await self._notify_state_change(session_id, SessionState.ERROR)
                coord_logger.info(f"Updated session {session_id} state to ERROR after exception")
            except Exception as state_error:
                logger.error(f"Failed to update session state to ERROR: {state_error}")

            # Send system message explaining the failure
            await self._send_session_failure_message(session_id, error_message)

            # Clean up any partially created SDK
            if session_id in self._active_sdks:
                try:
                    sdk = self._active_sdks[session_id]
                    if sdk:
                        await sdk.terminate()
                except Exception:
                    pass  # Best effort cleanup
                del self._active_sdks[session_id]
                coord_logger.info(f"Cleaned up SDK for session {session_id} after exception")

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
            # Issue #500: Stop queue processor before termination
            self.queue_processor.stop(session_id)

            # Reset processing state before termination
            await self.session_manager.update_processing_state(session_id, False)

            # Issue #310: Mark active tools as orphaned before termination
            self._mark_tools_orphaned(session_id)

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
            # Issue #310: Cleanup display projection
            if session_id in self._display_projections:
                del self._display_projections[session_id]

            if success:
                await self._notify_state_change(session_id, SessionState.TERMINATED)

            coord_logger.info(f"Session {session_id} stopped")
            return success

        except Exception as e:
            logger.error(f"Failed to terminate integrated session {session_id}: {e}")
            return False

    async def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name"""
        try:
            success = await self.session_manager.update_session_name(session_id, name)
            if success:
                # Notify about state change to trigger UI updates
                session_info = await self.session_manager.get_session_info(session_id)
                if session_info:
                    await self._notify_state_change(session_id, session_info.state)
            coord_logger.info(f"Updated session {session_id} name to '{name}'")
            return success
        except Exception as e:
            logger.error(f"Failed to update session {session_id} name: {e}")
            return False

    async def delete_session(self, session_id: str, archive_reason: str = "user_deleted") -> dict:
        """
        Delete a session and cleanup all resources (with cascading deletion for child minions).

        Args:
            session_id: ID of session to delete
            archive_reason: Reason for archival (default: "user_deleted", use "parent_initiated" for dispose_minion)

        Returns a dict with:
            - success: bool indicating if deletion succeeded
            - deleted_session_ids: list of all session IDs deleted (including cascaded children)
        """
        deleted_ids = []

        try:
            # Step 0: If this session has children, recursively delete all children first (cascading)
            # Check child_minion_ids regardless of is_minion/is_overseer flags - any session with
            # children should cascade the deletion
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info and session_info.child_minion_ids:
                child_ids = list(session_info.child_minion_ids)  # Make a copy
                coord_logger.info(f"Session {session_id} has {len(child_ids)} children - cascading deletion")

                for child_id in child_ids:
                    coord_logger.info(f"Cascading: deleting child minion {child_id} of parent {session_id}")
                    # Recursively delete child (which may have its own children)
                    # Pass archive_reason through to children
                    result = await self.delete_session(child_id, archive_reason=archive_reason)
                    if result.get("success"):
                        deleted_ids.extend(result.get("deleted_session_ids", []))

                coord_logger.info(f"Cascading deletion complete for {session_id} - all {len(child_ids)} children deleted")

            # Step 1: Find and remove session from its project
            project = await self._find_project_for_session(session_id)
            project_was_deleted = False

            if project:
                removal_success, project_was_deleted = await self.project_manager.remove_session_from_project(project.project_id, session_id)

                if removal_success:
                    if project_was_deleted:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id} - project was empty and has been deleted")
                    else:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id}")
                else:
                    logger.warning(f"Failed to remove session {session_id} from project {project.project_id}")

            # Step 1.5: If this is a minion with a parent overseer, remove from parent's child_minion_ids
            # (session_info was already fetched in Step 0)
            if not session_info:
                session_info = await self.session_manager.get_session_info(session_id)

            # Issue #349: All sessions are minions - check parent relationship directly
            if session_info and session_info.parent_overseer_id:
                parent_id = session_info.parent_overseer_id
                parent_info = await self.session_manager.get_session_info(parent_id)

                if parent_info and session_id in parent_info.child_minion_ids:
                    parent_info.child_minion_ids.remove(session_id)
                    parent_info.updated_at = datetime.now(UTC)
                    await self.session_manager._persist_session_state(parent_id)
                    coord_logger.info(f"Removed minion {session_id} from parent overseer {parent_id}'s child_minion_ids")
                elif parent_info:
                    coord_logger.warning(f"Minion {session_id} not found in parent overseer {parent_id}'s child_minion_ids")
                else:
                    coord_logger.warning(f"Parent overseer {parent_id} not found for minion {session_id}")

            # Step 1.6: Clean up capability registry if session has capabilities (issue #349: all sessions are minions)
            if session_info and session_info.capabilities:
                # Clean up capability registry
                if project and self.legion_system:
                    self.legion_system.legion_coordinator.unregister_minion_capabilities(session_id)
                    coord_logger.info(f"Cleaned up {len(session_info.capabilities)} capabilities from registry for minion {session_id}")

            # Step 1.65: Terminate SDK and update state BEFORE archive (Issue #236)
            # This ensures archive captures final "terminated" state (same pattern as dispose_minion)
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.terminate()
                del self._active_sdks[session_id]
                await asyncio.sleep(0.2)  # Give SDK time to fully close

            # Update session state to terminated (so archive captures correct final_state)
            if session_info and session_info.state != SessionState.TERMINATED:
                await self.session_manager.terminate_session(session_id)
                coord_logger.info(f"Terminated session {session_id} before archive/deletion")

            # Step 1.7: Archive session before deletion (Issue #236)
            # Archive any session in a project before deletion
            if session_info and project and self.legion_system:
                try:
                    # Get parent info for archive metadata
                    parent_name = None
                    if session_info.parent_overseer_id:
                        parent_info = await self.session_manager.get_session_info(session_info.parent_overseer_id)
                        parent_name = parent_info.name if parent_info else None

                    archive_result = await self.legion_system.archive_manager.archive_minion(
                        minion_id=session_id,
                        reason=archive_reason,
                        parent_overseer_id=session_info.parent_overseer_id,
                        parent_overseer_name=parent_name,
                        descendants_count=len(deleted_ids),  # Children already deleted in cascade
                        will_be_deleted=True  # This is a hard delete
                    )
                    if archive_result.success:
                        coord_logger.info(f"Archived session {session_id} to {archive_result.archive_path} before deletion")
                    else:
                        coord_logger.warning(f"Failed to archive session {session_id}: {archive_result.error_message}")
                except Exception as e:
                    coord_logger.error(f"Error archiving session {session_id} before deletion: {e}")

            # Step 1.8: Legion-specific cleanup (issue #349: all sessions are minions)
            if session_info and project and self.legion_system:
                legion_id = project.project_id
                coord_logger.info(f"Starting Legion-specific cleanup for minion {session_id} in legion {legion_id}")

                # 1.8a: Delete minion directory in legions/{legion_id}/minions/{minion_id}/
                minion_dir = self.data_dir / "legions" / legion_id / "minions" / session_id
                if minion_dir.exists():
                    try:
                        import shutil
                        shutil.rmtree(minion_dir)
                        coord_logger.info(f"Deleted Legion minion directory: {minion_dir}")
                    except Exception as e:
                        coord_logger.error(f"Failed to delete Legion minion directory {minion_dir}: {e}")
                else:
                    coord_logger.debug(f"Legion minion directory does not exist (already cleaned): {minion_dir}")

            # Step 2: Clean up storage manager and ensure all files are closed
            if session_id in self._storage_managers:
                storage_manager = self._storage_managers[session_id]
                # logger.info(f"Cleaning up storage manager for session {session_id}")
                await storage_manager.cleanup()
                del self._storage_managers[session_id]
                # Give storage manager time to close all file handles
                await asyncio.sleep(0.2)

            # Step 3: Clean up callbacks
            if session_id in self._message_callbacks:
                del self._message_callbacks[session_id]
            if session_id in self._error_callbacks:
                del self._error_callbacks[session_id]

            # Step 4: Force multiple garbage collections to ensure all handles are released
            gc.collect()
            await asyncio.sleep(0.1)
            gc.collect()
            await asyncio.sleep(0.1)

            # Step 5: Additional Windows-specific cleanup
            if os.name == 'nt':  # Windows
                # logger.info(f"Performing Windows-specific cleanup for session {session_id}")
                # Force close any remaining handles that might be held by the system
                gc.collect()
                await asyncio.sleep(0.3)

            # Step 6: Delete through session manager (this removes from active sessions and deletes files)
            # logger.info(f"Deleting session files for session {session_id}")
            success = await self.session_manager.delete_session(session_id)

            if success:
                coord_logger.info(f"Session {session_id} deleted")
                # Notify about session deletion (using a special state change)
                await self._notify_state_change(session_id, "deleted")
                # Add this session to the deleted list
                deleted_ids.append(session_id)

            return {"success": success, "deleted_session_ids": deleted_ids}

        except Exception as e:
            logger.error(f"Failed to delete integrated session {session_id}: {e}")
            return {"success": False, "deleted_session_ids": deleted_ids}

    async def _find_project_for_session(self, session_id: str) -> ProjectInfo | None:
        """Find the project that contains a given session"""
        projects = await self.project_manager.list_projects()
        for project in projects:
            if session_id in project.session_ids:
                return project
        return None

    async def get_descendants(self, session_id: str) -> list[dict]:
        """
        Get all descendant sessions (children, grandchildren, etc.) of a session.

        Returns a list of session info dicts for all descendants, or empty list
        if the session has no children or doesn't exist.
        """
        descendants = []

        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return descendants

        # Check if session has any children - any session with child_minion_ids
        # can have descendants, regardless of is_minion/is_overseer flags
        if not session_info.child_minion_ids:
            return descendants

        # Process each child
        for child_id in session_info.child_minion_ids:
            child_info = await self.session_manager.get_session_info(child_id)
            if child_info:
                descendants.append({
                    "session_id": child_id,
                    "name": child_info.name,
                    "role": child_info.role,
                    "state": child_info.state.value if hasattr(child_info.state, 'value') else str(child_info.state),
                    "parent_id": session_id
                })
                # Recursively get grandchildren
                grandchildren = await self.get_descendants(child_id)
                descendants.extend(grandchildren)

        return descendants

    async def count_descendants(self, session_id: str) -> int:
        """
        Count total number of descendants (children, grandchildren, etc.).

        Returns count of all descendant sessions.
        """
        descendants = await self.get_descendants(session_id)
        return len(descendants)

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
            # The SDK should echo the user message back through the stream
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

    async def interrupt_session(self, session_id: str) -> bool:
        """Interrupt the current Claude Code SDK session"""
        try:
            coord_logger.info(f"Interrupt requested for session {session_id}")

            # Check if SDK exists and is active
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot interrupt")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found - cannot interrupt")
                return False

            # Only allow interrupt for active/processing sessions
            if session_info.state not in [SessionState.ACTIVE] and not session_info.is_processing:
                logger.warning(f"Session {session_id} not in interruptible state (state: {session_info.state}, processing: {session_info.is_processing})")
                return False

            # Update processing state to indicate interrupting
            # Note: We don't have an "INTERRUPTING" state, so we'll just leave it processing until interrupt completes
            # logger.info(f"Attempting to interrupt session {session_id}")

            # Call SDK interrupt method
            result = await sdk.interrupt_session()

            if result:
                coord_logger.info(f"Session {session_id} interrupted")

                # Issue #310: Mark active tools as orphaned on interrupt
                self._mark_tools_orphaned(session_id)

                # Send interrupt system message via callback system (following client_launched pattern)
                await self._send_interrupt_message(session_id)

                # Note: Processing state will be reset by the SDK's interrupt handling in the message processing loop
            else:
                logger.warning(f"Failed to initiate interrupt for session {session_id}")
                # Reset processing state if interrupt initiation failed
                await self.session_manager.update_processing_state(session_id, False)

            return result

        except Exception as e:
            logger.error(f"Failed to interrupt session {session_id}: {e}")
            return False

    async def set_permission_mode(self, session_id: str, mode: str) -> bool:
        """Set the permission mode for a session"""
        try:
            coord_logger.info(f"Setting permission mode to '{mode}' for session {session_id}")

            # Validate mode
            valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
            if mode not in valid_modes:
                logger.error(f"Invalid permission mode: {mode}")
                return False

            # Check if SDK exists and is active
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot set permission mode")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found - cannot set permission mode")
                return False

            # Only allow permission mode change for active sessions
            if session_info.state not in [SessionState.ACTIVE]:
                logger.warning(f"Session {session_id} not in active state (state: {session_info.state})")
                return False

            # Call SDK set_permission_mode method
            sdk_result = await sdk.set_permission_mode(mode)

            if sdk_result:
                # Update session manager's tracking of current permission mode
                await self.session_manager.update_permission_mode(session_id, mode)
                coord_logger.info(f"Permission mode set to '{mode}' for session {session_id}")
            else:
                logger.warning(f"Failed to set permission mode for session {session_id}")

            return sdk_result

        except Exception as e:
            logger.error(f"Failed to set permission mode for session {session_id}: {e}")
            return False

    async def restart_session(self, session_id: str, permission_callback: Callable | None = None) -> bool:
        """
        Restart a session by disconnecting SDK and resuming with same session ID.

        This is useful for unsticking the agent without losing conversation history.
        """
        try:
            coord_logger.info(f"Restarting session {session_id}")

            # Get current SDK if it exists
            sdk = self._active_sdks.get(session_id)
            if sdk:
                # Disconnect existing SDK gracefully
                coord_logger.debug(f"Disconnecting existing SDK for session {session_id}")
                disconnect_result = await sdk.disconnect()
                if not disconnect_result:
                    logger.warning(f"SDK disconnect returned False for session {session_id}")

                # Update session state to TERMINATED after disconnect
                await self.session_manager.update_session_state(session_id, SessionState.TERMINATED)

                # Wait a moment for cleanup
                await asyncio.sleep(0.5)

                # Remove old SDK reference
                del self._active_sdks[session_id]
            else:
                # No active SDK - restart will act like a fresh start
                coord_logger.debug(f"No existing SDK for session {session_id}, will create new one")
                # Ensure session state is TERMINATED before starting
                await self.session_manager.update_session_state(session_id, SessionState.TERMINATED)

            # Start session again (will automatically resume using claude_code_session_id)
            success = await self.start_session(session_id, permission_callback)

            if success:
                coord_logger.info(f"Session {session_id} restarted successfully")
            else:
                logger.error(f"Failed to restart session {session_id}")

            return success

        except Exception as e:
            logger.error(f"Failed to restart session {session_id}: {e}")
            return False

    async def reset_session(self, session_id: str, permission_callback: Callable | None = None) -> bool:
        """
        Reset a session by clearing all messages and starting fresh.

        Keeps session settings (permission mode, tools, etc.) but clears conversation history.
        Queue: pending items preserved, in-flight items marked failed.
        """
        try:
            coord_logger.info(f"Resetting session {session_id}")

            # Issue #500: Stop queue processor and mark any in-flight item as failed
            self.queue_processor.stop(session_id)

            # Get current SDK and disconnect
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.disconnect()
                await self.session_manager.update_session_state(session_id, SessionState.TERMINATED)
                await asyncio.sleep(0.5)
                del self._active_sdks[session_id]

            # Clear Claude Code session ID from state (prevents resume)
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info:
                await self.session_manager.update_claude_code_session_id(session_id, None)
                coord_logger.info(f"Cleared Claude Code session ID for {session_id}")

            # Clear message history
            storage = self._storage_managers.get(session_id)
            if not storage:
                # Create storage manager on-demand for inactive sessions
                coord_logger.debug(f"Creating storage manager for inactive session {session_id}")
                session_dir = await self.session_manager.get_session_directory(session_id)
                storage = DataStorageManager(session_dir)
                await storage.initialize()
                self._storage_managers[session_id] = storage

            # Clear messages (storage now guaranteed to exist)
            if storage:
                await storage.clear_messages()
                coord_logger.info(f"Cleared message history for session {session_id}")

            # Issue #310: Reset DisplayProjection state (clears tool tracking)
            self._reset_display_projection(session_id)

            # Start fresh session (will create new Claude Code session)
            success = await self.start_session(session_id, permission_callback)

            if success:
                coord_logger.info(f"Session {session_id} reset successfully")
            else:
                logger.error(f"Failed to reset session {session_id}")

            return success

        except Exception as e:
            logger.error(f"Failed to reset session {session_id}: {e}")
            return False

    async def get_session_info(self, session_id: str) -> dict[str, Any] | None:
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
                    "message_count": await storage.get_message_count()
                }

            return {
                "session": session_info.to_dict(),
                "sdk": sdk_info,
                "storage": storage_info
            }

        except Exception as e:
            logger.error(f"Failed to get session info for {session_id}: {e}")
            return None

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with their current status"""
        try:
            sessions = await self.session_manager.list_sessions()
            return [session.to_dict() for session in sessions]
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []

    def _convert_stored_message_to_websocket(self, stored_msg: dict[str, Any]) -> dict[str, Any] | None:
        """
        Convert new StoredMessage format (_type discriminator) to WebSocket format.

        Issue #310: The new dataclass-based StoredMessage uses _type as discriminator
        and stores message data in the 'data' field. This method converts it to the
        legacy format expected by the frontend.
        """
        try:
            _type = stored_msg.get("_type", "")
            data = stored_msg.get("data", {})
            timestamp = stored_msg.get("timestamp")
            session_id = stored_msg.get("session_id")
            display = stored_msg.get("display")

            # Map _type to legacy type string
            type_mapping = {
                "AssistantMessage": "assistant",
                "UserMessage": "user",
                "SystemMessage": "system",
                "ResultMessage": "result",
                "PermissionRequestMessage": "permission_request",
                "PermissionResponseMessage": "permission_response",
            }
            legacy_type = type_mapping.get(_type, "system")

            # Extract content from data
            content = ""
            if "content" in data:
                raw_content = data["content"]
                if isinstance(raw_content, str):
                    content = raw_content
                elif isinstance(raw_content, list):
                    # Extract text from content blocks
                    # For AssistantMessages, only include actual text - NOT tool_use blocks
                    # Tool uses should be suppressed from content display (handled via metadata)
                    texts = []
                    for block in raw_content:
                        if isinstance(block, dict):
                            if "text" in block:
                                texts.append(block["text"])
                            elif "thinking" in block:
                                texts.append(f"[Thinking: {block['thinking'][:100]}...]")
                            # Explicitly skip tool_use blocks - they go into metadata.tool_uses
                            # and should not contribute to content string
                    content = " ".join(texts) if texts else ""

            # Build metadata
            metadata = {}

            # Extract tool uses from AssistantMessage
            tool_uses = []
            if _type == "AssistantMessage" and isinstance(data.get("content"), list):
                for block in data["content"]:
                    if isinstance(block, dict) and "id" in block and "name" in block:
                        tool_uses.append(block)
            if tool_uses:
                metadata["has_tool_uses"] = True
                metadata["tool_uses"] = tool_uses

            # Extract tool results from UserMessage
            tool_results = []
            if _type == "UserMessage" and isinstance(data.get("content"), list):
                for block in data["content"]:
                    if isinstance(block, dict) and "tool_use_id" in block:
                        tool_results.append(block)
            if tool_results:
                metadata["has_tool_results"] = True
                metadata["tool_results"] = tool_results

            # Extract parent_tool_use_id for Task subagent prompt filtering (Issue #384)
            if _type == "UserMessage" and data.get("parent_tool_use_id"):
                metadata["parent_tool_use_id"] = data["parent_tool_use_id"]

            # Extract thinking blocks
            thinking_blocks = []
            if _type == "AssistantMessage" and isinstance(data.get("content"), list):
                for block in data["content"]:
                    if isinstance(block, dict) and "thinking" in block:
                        thinking_blocks.append({
                            "content": block["thinking"],
                            "timestamp": timestamp,
                        })
            if thinking_blocks:
                metadata["has_thinking"] = True
                metadata["thinking_blocks"] = thinking_blocks

            # Handle SystemMessage subtypes
            if _type == "SystemMessage":
                subtype = data.get("subtype")
                if subtype:
                    metadata["subtype"] = subtype
                # Extract init_data if present
                if data.get("data"):
                    metadata["init_data"] = data["data"]

            # Handle ResultMessage
            if _type == "ResultMessage":
                subtype = data.get("subtype")
                if subtype:
                    metadata["subtype"] = subtype
                # Copy usage data
                for key in ["usage", "duration_ms", "duration_api_ms", "total_cost_usd", "num_turns"]:
                    if key in data:
                        metadata[key] = data[key]

            # Handle PermissionRequestMessage
            if _type == "PermissionRequestMessage":
                metadata["request_id"] = data.get("request_id")
                metadata["tool_name"] = data.get("tool_name")
                metadata["input_params"] = data.get("input_params", {})
                metadata["suggestions"] = data.get("suggestions", [])
                metadata["has_permission_requests"] = True

            # Handle PermissionResponseMessage
            if _type == "PermissionResponseMessage":
                metadata["request_id"] = data.get("request_id")
                metadata["decision"] = data.get("decision")
                metadata["tool_name"] = data.get("tool_name")
                metadata["reasoning"] = data.get("reasoning")
                metadata["applied_updates"] = data.get("applied_updates", [])
                # Include updated_input for AskUserQuestion answers
                if data.get("updated_input"):
                    metadata["updated_input"] = data["updated_input"]

            # Add display metadata if present (Issue #310)
            if display:
                metadata["display"] = display

            # Build websocket message
            websocket_data = {
                "type": legacy_type,
                "content": content,
                "timestamp": timestamp,
                "session_id": session_id,
                "metadata": metadata,
            }

            # Add subtype at root level for backward compatibility
            if metadata.get("subtype"):
                websocket_data["subtype"] = metadata["subtype"]

            return websocket_data

        except Exception as e:
            coord_logger.warning(f"Failed to convert StoredMessage to WebSocket format: {e}")
            return None

    async def get_session_messages(
        self,
        session_id: str,
        limit: int | None = None,
        offset: int = 0
    ) -> dict[str, Any]:
        """Get messages from a session with pagination metadata.

        Issue #491: Generates interleaved tool_call messages alongside regular messages.
        The frontend processes all tool_call messages through handleToolCall() regardless
        of whether they arrived via WebSocket or REST history endpoint.
        """
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
            raw_messages = await storage.read_messages(limit=limit, offset=offset)
            total_count = await storage.get_message_count()

            # Convert stored messages to WebSocket format and generate tool_call messages
            parsed_messages = []

            # Issue #491: Track tool lifecycle state for generating tool_call messages
            # Maps tool_use_id -> ToolCall being reconstructed from history
            active_history_tools: dict[str, ToolCall] = {}

            for raw_message in raw_messages:
                try:
                    websocket_data = None

                    # Issue #310: Handle new StoredMessage format with _type discriminator
                    if raw_message.get("_type"):
                        websocket_data = self._convert_stored_message_to_websocket(raw_message)
                    # Check if message is already fully processed (has metadata)
                    elif isinstance(raw_message.get("metadata"), dict) and raw_message.get("type") and raw_message.get("content") is not None:
                        # Message is already processed, prepare for WebSocket
                        metadata = raw_message["metadata"].copy()

                        websocket_data = {
                            "type": raw_message["type"],
                            "content": raw_message["content"],
                            "timestamp": raw_message.get("timestamp"),
                            "metadata": metadata,
                            "session_id": raw_message.get("session_id")
                        }
                        # Maintain backward compatibility with subtype at root level
                        if metadata.get('subtype'):
                            websocket_data["subtype"] = metadata['subtype']
                    else:
                        # Message needs processing - run through MessageProcessor
                        processed_message = self.message_processor.process_message(raw_message, source="storage")
                        websocket_data = self.message_processor.prepare_for_websocket(processed_message)

                    if not websocket_data:
                        continue

                    # Add the regular message to the response
                    parsed_messages.append(websocket_data)

                    # Issue #491: Generate interleaved tool_call messages from message metadata
                    msg_type = websocket_data.get("type", "")
                    metadata = websocket_data.get("metadata", {})
                    msg_timestamp = websocket_data.get("timestamp")

                    # AssistantMessage with tool_uses → create pending ToolCall messages
                    if metadata.get("has_tool_uses") and metadata.get("tool_uses"):
                        for tool_use in metadata["tool_uses"]:
                            tool_use_id = tool_use.get("id")
                            if not tool_use_id:
                                continue
                            tool_call = ToolCall(
                                tool_use_id=tool_use_id,
                                session_id=session_id,
                                name=tool_use.get("name", ""),
                                input=tool_use.get("input", {}),
                                status=ToolState.PENDING,
                                created_at=msg_timestamp if isinstance(msg_timestamp, (int, float)) else 0.0,
                                display=ToolDisplayInfo(
                                    state=ToolState.PENDING,
                                    visible=True,
                                    collapsed=False,
                                    style="default",
                                ),
                            )
                            active_history_tools[tool_use_id] = tool_call
                            tc_data = tool_call.to_dict()
                            tc_data["type"] = "tool_call"
                            parsed_messages.append(tc_data)

                    # PermissionRequestMessage → update matching ToolCall to awaiting_permission
                    if msg_type == "permission_request" or metadata.get("has_permission_requests"):
                        perm_tool_name = metadata.get("tool_name", "")
                        perm_request_id = metadata.get("request_id", "")
                        perm_suggestions = metadata.get("suggestions", [])

                        # Find matching tool by name+input signature
                        matched_tool = None
                        for tc in active_history_tools.values():
                            if tc.name == perm_tool_name and tc.status == ToolState.PENDING:
                                matched_tool = tc
                                break
                        # Fallback: match by tool name alone if unique pending
                        if not matched_tool:
                            candidates = [
                                tc for tc in active_history_tools.values()
                                if tc.name == perm_tool_name and tc.status in (
                                    ToolState.PENDING, ToolState.AWAITING_PERMISSION
                                )
                            ]
                            if len(candidates) == 1:
                                matched_tool = candidates[0]

                        if matched_tool:
                            matched_tool.status = ToolState.AWAITING_PERMISSION
                            matched_tool.requires_permission = True
                            matched_tool.permission = PermissionInfo(
                                message=websocket_data.get("content", ""),
                                suggestions=perm_suggestions,
                            )
                            if matched_tool.display:
                                matched_tool.display.state = ToolState.AWAITING_PERMISSION
                                matched_tool.display.style = "warning"
                            tc_data = matched_tool.to_dict()
                            tc_data["type"] = "tool_call"
                            tc_data["request_id"] = perm_request_id
                            parsed_messages.append(tc_data)

                    # PermissionResponseMessage → update matching ToolCall with decision
                    if msg_type == "permission_response":
                        perm_decision = metadata.get("decision", "")
                        perm_request_id = metadata.get("request_id", "")
                        perm_tool_name = metadata.get("tool_name", "")
                        updated_input = metadata.get("updated_input")
                        applied_updates = metadata.get("applied_updates", [])

                        # Find matching tool awaiting permission
                        matched_tool = None
                        for tc in active_history_tools.values():
                            if tc.name == perm_tool_name and tc.status == ToolState.AWAITING_PERMISSION:
                                matched_tool = tc
                                break

                        if matched_tool:
                            granted = perm_decision == "allow"
                            matched_tool.permission_granted = granted
                            if granted:
                                matched_tool.status = ToolState.RUNNING
                                if matched_tool.display:
                                    matched_tool.display.state = ToolState.RUNNING
                                    matched_tool.display.style = "default"
                            else:
                                matched_tool.status = ToolState.DENIED
                                if matched_tool.display:
                                    matched_tool.display.state = ToolState.DENIED
                                    matched_tool.display.style = "error"

                            tc_data = matched_tool.to_dict()
                            tc_data["type"] = "tool_call"
                            tc_data["request_id"] = perm_request_id
                            if updated_input:
                                tc_data["updated_input"] = updated_input
                            if applied_updates:
                                tc_data["applied_updates"] = applied_updates
                            parsed_messages.append(tc_data)

                            # Remove denied tools from tracking
                            if not granted:
                                active_history_tools.pop(matched_tool.tool_use_id, None)

                    # UserMessage with tool_results → update matching ToolCall to completed/failed
                    if metadata.get("has_tool_results") and metadata.get("tool_results"):
                        for tool_result in metadata["tool_results"]:
                            tool_use_id = tool_result.get("tool_use_id")
                            if not tool_use_id:
                                continue
                            matched_tool = active_history_tools.pop(tool_use_id, None)
                            if matched_tool:
                                is_error = tool_result.get("is_error", False)
                                result_content = tool_result.get("content", "")
                                if is_error:
                                    matched_tool.status = ToolState.FAILED
                                    matched_tool.error = str(result_content) if result_content else "Tool execution failed"
                                    if matched_tool.display:
                                        matched_tool.display.state = ToolState.FAILED
                                        matched_tool.display.style = "error"
                                else:
                                    matched_tool.status = ToolState.COMPLETED
                                    matched_tool.result = result_content
                                    if matched_tool.display:
                                        matched_tool.display.state = ToolState.COMPLETED
                                        matched_tool.display.style = "success"
                                tc_data = matched_tool.to_dict()
                                tc_data["type"] = "tool_call"
                                parsed_messages.append(tc_data)

                    # SystemMessage client_launched or interrupt → mark unresolved tools as interrupted
                    if msg_type == "system":
                        subtype = metadata.get("subtype", "")
                        if subtype in ("client_launched", "interrupt"):
                            for tool_use_id in list(active_history_tools.keys()):
                                tc = active_history_tools.pop(tool_use_id)
                                tc.status = ToolState.INTERRUPTED
                                if tc.display:
                                    tc.display.state = ToolState.INTERRUPTED
                                    tc.display.style = "orphaned"
                                tc_data = tc.to_dict()
                                tc_data["type"] = "tool_call"
                                parsed_messages.append(tc_data)

                except Exception as e:
                    logger.warning(f"Failed to prepare historical message for WebSocket: {e}")
                    # Fallback to basic format if we have enough info
                    try:
                        msg_type = raw_message.get("type", raw_message.get("_type", "system"))
                        fallback_data = {
                            "type": msg_type,
                            "content": raw_message.get("content", ""),
                            "timestamp": raw_message.get("timestamp")
                        }
                        if raw_message.get("session_id"):
                            fallback_data["session_id"] = raw_message["session_id"]
                        parsed_messages.append(fallback_data)
                    except Exception:
                        pass

            # Issue #491: Mark any remaining unresolved tools as interrupted
            # (session may have been terminated without explicit interrupt/restart message)
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info and session_info.state not in (
                SessionState.ACTIVE, SessionState.PAUSED, SessionState.STARTING
            ):
                for tool_use_id in list(active_history_tools.keys()):
                    tc = active_history_tools.pop(tool_use_id)
                    tc.status = ToolState.INTERRUPTED
                    if tc.display:
                        tc.display.state = ToolState.INTERRUPTED
                        tc.display.style = "orphaned"
                    tc_data = tc.to_dict()
                    tc_data["type"] = "tool_call"
                    parsed_messages.append(tc_data)

            # Calculate pagination metadata
            actual_limit = limit or 50
            has_more = (offset + len(raw_messages)) < total_count

            return {
                "messages": parsed_messages,
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
        # logger.info(f"Added message callback for session {session_id}, total callbacks: {len(self._message_callbacks[session_id])}")

    def add_error_callback(self, session_id: str, callback: Callable):
        """Add callback for session errors"""
        if session_id not in self._error_callbacks:
            self._error_callbacks[session_id] = []
        self._error_callbacks[session_id].append(callback)

    def add_state_change_callback(self, callback: Callable):
        """Add callback for session state changes"""
        self._state_change_callbacks.append(callback)

    def _get_display_projection(self, session_id: str) -> DisplayProjection:
        """
        Get or create DisplayProjection for a session (Issue #310).

        Each session has its own projection instance to track tool lifecycle
        state independently.
        """
        if session_id not in self._display_projections:
            self._display_projections[session_id] = DisplayProjection()
            coord_logger.debug(f"Created DisplayProjection for session {session_id}")
        return self._display_projections[session_id]

    def _reset_display_projection(self, session_id: str) -> None:
        """Reset DisplayProjection for a session (e.g., on session reset)."""
        if session_id in self._display_projections:
            self._display_projections[session_id].reset()
            coord_logger.debug(f"Reset DisplayProjection for session {session_id}")

    def _mark_tools_orphaned(self, session_id: str) -> list[str]:
        """
        Mark all active tools as orphaned for a session (Issue #310).

        Called when a session is interrupted or terminated to mark pending
        tools as abandoned. Returns list of orphaned tool IDs.
        """
        projection = self._display_projections.get(session_id)
        if projection:
            orphaned = projection.mark_tools_orphaned()
            if orphaned:
                coord_logger.info(f"Marked {len(orphaned)} tools as orphaned for session {session_id}")
            return orphaned
        return []

    # ============================================================
    # Issue #324: Unified ToolCall Lifecycle Management
    # ============================================================

    def create_tool_call(
        self,
        session_id: str,
        tool_use_id: str,
        name: str,
        input_params: dict[str, Any],
        requires_permission: bool = False,
    ) -> ToolCall:
        """
        Create a new ToolCall when tool_use is detected (Issue #324).

        Called when an AssistantMessage contains a tool_use block.
        Returns the created ToolCall for WebSocket broadcast.
        """
        import time

        tool_call = ToolCall(
            tool_use_id=tool_use_id,
            session_id=session_id,
            name=name,
            input=input_params,
            status=ToolState.PENDING,
            created_at=time.time(),
            requires_permission=requires_permission,
            display=ToolDisplayInfo(
                state=ToolState.PENDING,
                visible=True,
                collapsed=False,
                style="default",
            ),
        )

        # Track in active tool calls
        if session_id not in self._active_tool_calls:
            self._active_tool_calls[session_id] = {}
        self._active_tool_calls[session_id][tool_use_id] = tool_call

        coord_logger.debug(
            f"Created ToolCall {tool_use_id} for {name} in session {session_id}"
        )
        return tool_call

    def update_tool_call_permission_request(
        self,
        session_id: str,
        tool_use_id: str,
        permission_info: PermissionInfo,
    ) -> ToolCall | None:
        """
        Update ToolCall to awaiting_permission status (Issue #324).

        Called when a permission request is received for a tool.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission request in session {session_id}"
            )
            return None

        tool_call.status = ToolState.AWAITING_PERMISSION
        tool_call.requires_permission = True
        tool_call.permission = permission_info
        if tool_call.display:
            tool_call.display.state = ToolState.AWAITING_PERMISSION
            tool_call.display.style = "warning"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to awaiting_permission in session {session_id}"
        )
        return tool_call

    def update_tool_call_permission_response(
        self,
        session_id: str,
        tool_use_id: str,
        granted: bool,
    ) -> ToolCall | None:
        """
        Update ToolCall after permission response (Issue #324).

        Called when user grants or denies permission.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission response in session {session_id}"
            )
            return None

        tool_call.permission_granted = granted
        tool_call.permission_response_at = time.time()

        if granted:
            tool_call.status = ToolState.RUNNING
            tool_call.started_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.RUNNING
                tool_call.display.style = "default"
        else:
            tool_call.status = ToolState.DENIED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.DENIED
                tool_call.display.style = "error"
            # Remove from active (denied tools are terminal)
            self._remove_active_tool_call(session_id, tool_use_id)

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} permission_granted={granted} in session {session_id}"
        )
        return tool_call

    def update_tool_call_running(
        self,
        session_id: str,
        tool_use_id: str,
    ) -> ToolCall | None:
        """
        Update ToolCall to running status (Issue #324).

        Called when a tool starts executing (for tools that don't require permission).
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            # Tool may not exist yet if tool_use and execution happen quickly
            coord_logger.debug(
                f"ToolCall {tool_use_id} not found for running update in session {session_id}"
            )
            return None

        tool_call.status = ToolState.RUNNING
        tool_call.started_at = time.time()
        if tool_call.display:
            tool_call.display.state = ToolState.RUNNING
            tool_call.display.style = "default"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to running in session {session_id}"
        )
        return tool_call

    def update_tool_call_result(
        self,
        session_id: str,
        tool_use_id: str,
        result: Any,
        is_error: bool = False,
    ) -> ToolCall | None:
        """
        Update ToolCall with result (Issue #324).

        Called when a tool_result is received.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for result in session {session_id}"
            )
            return None

        tool_call.completed_at = time.time()
        tool_call.result = result

        if is_error:
            tool_call.status = ToolState.FAILED
            tool_call.error = str(result) if result else "Tool execution failed"
            if tool_call.display:
                tool_call.display.state = ToolState.FAILED
                tool_call.display.style = "error"
        else:
            tool_call.status = ToolState.COMPLETED
            if tool_call.display:
                tool_call.display.state = ToolState.COMPLETED
                tool_call.display.style = "success"

        # Remove from active (completed/failed tools are terminal)
        self._remove_active_tool_call(session_id, tool_use_id)

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to {'failed' if is_error else 'completed'} in session {session_id}"
        )
        return tool_call

    def mark_session_tools_interrupted(self, session_id: str) -> list[ToolCall]:
        """
        Mark all active tools for a session as interrupted (Issue #324).

        Called when session is terminated or interrupted.
        Returns list of interrupted ToolCalls for WebSocket broadcast.
        """
        import time

        interrupted = []
        session_tools = self._active_tool_calls.get(session_id, {})

        for _tool_use_id, tool_call in list(session_tools.items()):
            tool_call.status = ToolState.INTERRUPTED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.INTERRUPTED
                tool_call.display.style = "orphaned"
            interrupted.append(tool_call)

        # Clear active tools for session
        if session_id in self._active_tool_calls:
            self._active_tool_calls[session_id].clear()

        if interrupted:
            coord_logger.info(
                f"Marked {len(interrupted)} tools as interrupted for session {session_id}"
            )

        return interrupted

    def get_active_tool_calls(self, session_id: str) -> list[ToolCall]:
        """Get all active tool calls for a session."""
        return list(self._active_tool_calls.get(session_id, {}).values())

    def _get_active_tool_call(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        """Get a specific active tool call."""
        session_tools = self._active_tool_calls.get(session_id, {})
        return session_tools.get(tool_use_id)

    def _remove_active_tool_call(self, session_id: str, tool_use_id: str) -> None:
        """Remove a tool call from active tracking."""
        session_tools = self._active_tool_calls.get(session_id, {})
        if tool_use_id in session_tools:
            del session_tools[tool_use_id]

    def find_tool_call_by_signature(
        self,
        session_id: str,
        tool_name: str,
        input_params: dict[str, Any],
    ) -> ToolCall | None:
        """
        Find a tool call by matching its signature (Issue #324).

        Used to correlate permission requests with tool_use when tool_use_id
        is not directly available. Creates a signature from tool name and
        first significant input parameter.
        """
        import hashlib
        import json

        # Create signature similar to DisplayProjection._create_tool_signature
        first_value = ""
        for key, value in input_params.items():
            if value and key not in ("_simulatedSedEdit",):
                if isinstance(value, str):
                    first_value = value[:100]
                else:
                    first_value = json.dumps(value)[:100]
                break

        param_hash = hashlib.md5(first_value.encode()).hexdigest()[:8]
        target_signature = f"{tool_name}:{param_hash}"

        # Search active tools for matching signature
        session_tools = self._active_tool_calls.get(session_id, {})
        for tool_call in session_tools.values():
            # Compute signature for this tool
            tc_first_value = ""
            for key, value in tool_call.input.items():
                if value and key not in ("_simulatedSedEdit",):
                    if isinstance(value, str):
                        tc_first_value = value[:100]
                    else:
                        tc_first_value = json.dumps(value)[:100]
                    break
            tc_hash = hashlib.md5(tc_first_value.encode()).hexdigest()[:8]
            tc_signature = f"{tool_call.name}:{tc_hash}"

            if tc_signature == target_signature and tool_call.status == ToolState.PENDING:
                return tool_call

        return None

    async def _store_processed_message(self, session_id: str, message_data: dict[str, Any]):
        """Store message using unified MessageProcessor for consistent format."""
        try:
            # Process the message through MessageProcessor
            parsed_message = self.message_processor.process_message(message_data, source="system")

            # Prepare for storage using MessageProcessor
            storage_data = self.message_processor.prepare_for_storage(parsed_message)

            # Store in session storage
            storage = self._storage_managers.get(session_id)
            if storage:
                # logger.debug(f"Storing processed system message: {storage_data.get('type', 'unknown')}")
                await storage.append_message(storage_data)
            else:
                logger.warning(f"No storage manager found for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to store processed message for session {session_id}: {e}")
            # Fallback to direct storage
            storage = self._storage_managers.get(session_id)
            if storage:
                await storage.append_message(message_data)

    def _create_message_callback(self, session_id: str) -> Callable:
        """Create message callback for a session using unified MessageProcessor"""
        async def callback(message_data: dict[str, Any]):
            try:
                # Process message using unified MessageProcessor
                parsed_message = self.message_processor.process_message(message_data, source="sdk")

                # Issue #310: Process through DisplayProjection for tool lifecycle tracking
                display_metadata = None
                try:
                    projection = self._get_display_projection(session_id)
                    # Convert to StoredMessage format for projection processing
                    legacy_dict = {
                        'type': parsed_message.type.value,
                        'timestamp': parsed_message.timestamp,
                        'session_id': session_id,
                        'content': parsed_message.content,
                    }
                    if parsed_message.metadata:
                        legacy_dict.update(parsed_message.metadata)
                    stored_msg = legacy_to_stored(legacy_dict)
                    display_metadata = projection.process_message(stored_msg)
                except Exception as proj_error:
                    coord_logger.debug(f"DisplayProjection processing failed: {proj_error}")
                    # Non-fatal - continue without display metadata

                # Track latest meaningful message (issue #291)
                # Only track user, assistant, system messages (not tool_use, tool_result, permission, etc.)
                if parsed_message.type.value in ['user', 'assistant', 'system']:
                    content = parsed_message.content or ""

                    # Skip messages that are just internal SDK placeholders or tool execution artifacts
                    has_tool_results = parsed_message.metadata.get('has_tool_results', False) if parsed_message.metadata else False
                    has_tool_uses = parsed_message.metadata.get('has_tool_uses', False) if parsed_message.metadata else False

                    # Get subtype for system message filtering
                    subtype = parsed_message.metadata.get('subtype') if parsed_message.metadata else None

                    skip_message = (
                        # User messages with only tool_results (no actual user text)
                        (parsed_message.type.value == 'user' and has_tool_results and content.startswith('Tool results:')) or
                        # Assistant messages with only tool_uses (no actual text response)
                        (parsed_message.type.value == 'assistant' and has_tool_uses and content == 'Assistant response') or
                        # System messages with generic placeholder content
                        (parsed_message.type.value == 'system' and content == 'System message') or
                        # System init messages (synced with frontend MessageList.vue filtering)
                        (parsed_message.type.value == 'system' and subtype == 'init') or
                        # System task_notification messages (synced with frontend MessageList.vue filtering)
                        (parsed_message.type.value == 'system' and subtype == 'task_notification')
                    )

                    if not skip_message:
                        # Truncate to 200 chars (stored), will be truncated further in frontend (100 chars displayed)
                        truncated_content = content[:200] if len(content) > 200 else content

                        # Replace newlines with spaces for single-line display
                        truncated_content = truncated_content.replace('\n', ' ').strip()

                        # Update session's latest message tracking
                        if truncated_content:  # Only update if there's actual content
                            await self.session_manager.update_latest_message(
                                session_id,
                                truncated_content,
                                parsed_message.type.value,
                                datetime.now(UTC)
                            )

                # Track tool uses from assistant messages for ExitPlanMode detection
                if parsed_message.type.value == 'assistant' and parsed_message.metadata:
                    tool_uses = parsed_message.metadata.get('tool_uses', [])
                    if session_id not in self._recent_tool_uses:
                        self._recent_tool_uses[session_id] = {}
                    for tool_use in tool_uses:
                        tool_id = tool_use.get('id')
                        tool_name = tool_use.get('name')
                        if tool_id and tool_name:
                            self._recent_tool_uses[session_id][tool_id] = tool_name

                # Check for ExitPlanMode completion in tool results
                if parsed_message.type.value == 'user' and parsed_message.metadata:
                    tool_results = parsed_message.metadata.get('tool_results', [])
                    for tool_result in tool_results:
                        tool_use_id = tool_result.get('tool_use_id')
                        is_error = tool_result.get('is_error', False)

                        # Check if this tool use was ExitPlanMode
                        if tool_use_id and not is_error:
                            tool_uses = self._recent_tool_uses.get(session_id, {})
                            tool_name = tool_uses.get(tool_use_id)

                            if tool_name == 'ExitPlanMode':
                                # ExitPlanMode completed successfully - conditionally reset mode
                                # Check if setMode was applied for this ExitPlanMode
                                skip_reset = self._exitplanmode_with_setmode.get(session_id, False)

                                if skip_reset:
                                    coord_logger.info(f"ExitPlanMode completed for session {session_id} with setMode applied - skipping auto-reset")
                                    # Clean up the flag
                                    del self._exitplanmode_with_setmode[session_id]
                                else:
                                    # No setMode was applied - reset to default if still in plan mode
                                    session_info = await self.session_manager.get_session_info(session_id)
                                    if session_info and session_info.current_permission_mode == 'plan':
                                        # Only reset to default if still in plan mode (suggestion wasn't applied)
                                        await self.session_manager.update_permission_mode(session_id, 'default')
                                        coord_logger.info(f"Permission mode reset to default after ExitPlanMode for session {session_id}")
                                    elif session_info:
                                        # Mode already changed (likely via setMode suggestion) - no reset needed
                                        coord_logger.info(f"ExitPlanMode completed for session {session_id}, mode already changed to {session_info.current_permission_mode}")

                                # Clean up tracked tool use
                                del tool_uses[tool_use_id]

                # Track permission responses with applied updates (for historical/display purposes)
                # Note: The actual mode change is handled immediately in web_server.py when
                # the permission response is built, since the SDK applies it internally
                if parsed_message.type.value == 'permission_response' and parsed_message.metadata:
                    applied_updates = parsed_message.metadata.get('applied_updates', [])
                    tool_name = parsed_message.metadata.get('tool_name')

                    if applied_updates:
                        # Track the applied updates for display/history
                        if session_id not in self._permission_updates:
                            self._permission_updates[session_id] = []
                        self._permission_updates[session_id].extend(applied_updates)
                        coord_logger.debug(f"Tracked {len(applied_updates)} applied permission updates for session {session_id}")

                        # Check if ExitPlanMode had a setMode suggestion applied
                        if tool_name == 'ExitPlanMode':
                            has_setmode = any(update.get('type') == 'setMode' for update in applied_updates)
                            if has_setmode:
                                # Mark this session to skip mode reset for ExitPlanMode
                                self._exitplanmode_with_setmode[session_id] = True
                                coord_logger.info(f"ExitPlanMode for session {session_id} had setMode applied - will skip auto-reset")

                # Check if this message indicates processing completion
                if parsed_message.type.value == 'result':
                    # Message processing completed - reset processing state
                    try:
                        await self.session_manager.update_processing_state(session_id, False)
                        # logger.info(f"Reset processing state for session {session_id} after result message")
                    except Exception as e:
                        logger.error(f"Failed to reset processing state for session {session_id}: {e}")

                # Also reset processing state on interrupt_success
                if parsed_message.type.value == 'system' and parsed_message.metadata.get('subtype') == 'interrupt_success':
                    try:
                        await self.session_manager.update_processing_state(session_id, False)
                        coord_logger.info(f"Reset processing state for session {session_id} after interrupt")
                    except Exception as e:
                        logger.error(f"Failed to reset processing state after interrupt for session {session_id}: {e}")

                # Call registered callbacks with processed message (maintain backward compatibility)
                callbacks = self._message_callbacks.get(session_id, [])
                # logger.info(f"Processing message for session {session_id}, found {len(callbacks)} callbacks")

                # Issue #310: Attach display metadata to parsed message for WebSocket broadcast
                if display_metadata:
                    if parsed_message.metadata is None:
                        parsed_message.metadata = {}
                    parsed_message.metadata['display'] = display_metadata.to_dict()

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
                    "timestamp": get_unix_timestamp()
                }

                logger.error(f"SDK error in session {session_id}: {error_type} - {error}")

                # Reset processing state on any error
                try:
                    await self.session_manager.update_processing_state(session_id, False)
                    # logger.info(f"Reset processing state for session {session_id} after error: {error_type}")
                except Exception as e:
                    logger.error(f"Failed to reset processing state for session {session_id}: {e}")

                # Handle critical errors that require session state updates
                if error_type in ["startup_failed", "message_processing_loop_error", "immediate_cli_failure"]:
                    # logger.info(f"Handling critical SDK error: {error_type}")

                    # Extract user-friendly error message
                    user_error_message = self._extract_claude_cli_error(str(error))

                    # Update session state to ERROR and reset processing state
                    try:
                        await self.session_manager.update_session_state(session_id, SessionState.ERROR, user_error_message)
                        # Also ensure processing state is reset when going to error state
                        await self.session_manager.update_processing_state(session_id, False)
                        await self._notify_state_change(session_id, SessionState.ERROR)
                        # logger.info(f"Updated session {session_id} state to ERROR and reset processing state due to SDK error")
                    except Exception as state_error:
                        logger.error(f"Failed to update session state to ERROR: {state_error}")

                    # Send system message explaining the runtime failure
                    await self._send_session_failure_message(session_id, user_error_message)

                    # Clean up the failed SDK
                    if session_id in self._active_sdks:
                        del self._active_sdks[session_id]
                        # logger.info(f"Cleaned up failed SDK for session {session_id}")

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
            # logger.info(f"DEBUG: _send_client_launched_message called for session {session_id}")

            # Create system message for client launch
            message_data = {
                "type": "system",
                "subtype": "client_launched",
                "content": "Claude Code Launched",
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage"
            }

            # logger.info(f"DEBUG: About to store processed message: {message_data}")

            # Process and store message using unified MessageProcessor
            await self._store_processed_message(session_id, message_data)

            # logger.info(f"DEBUG: Message stored, about to send via callback")

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            # logger.info(f"SUCCESS: Sent client launched message for session {session_id}")

        except Exception as e:
            logger.error(f"ERROR: Failed to send client launched message for {session_id}: {e}")
            import traceback
            logger.error(f"ERROR: Traceback: {traceback.format_exc()}")

    async def _send_session_failure_message(self, session_id: str, error_message: str):
        """Send a system message indicating the session failed to start"""
        try:
            # Create system message for session failure
            message_data = {
                "type": "system",
                "subtype": "session_failed",
                "content": f"Session failed to start: {error_message}",
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage",
                "error_details": error_message
            }

            # Process and store message using unified MessageProcessor
            await self._store_processed_message(session_id, message_data)

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Session failure message sent for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to send session failure message for {session_id}: {e}")

    async def _send_interrupt_message(self, session_id: str):
        """Send interrupt system message via callback system (following client_launched pattern)"""
        try:
            # Create interrupt system message
            message_data = {
                "type": "system",
                "subtype": "interrupt",
                "content": "User Interrupted Processing",
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage"
            }

            # Process and store message using unified MessageProcessor
            await self._store_processed_message(session_id, message_data)

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Interrupt message sent for session {session_id}")

        except Exception as e:
            logger.error(f"Failed to send interrupt message for {session_id}: {e}")

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
                "timestamp": get_unix_timestamp()
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
        # logger.info(f"Received state change from session manager: {session_id} -> {new_state.value}")
        await self._notify_state_change(session_id, new_state)

    async def _validate_and_cleanup_projects(self):
        """
        Validate project/session references and clean up orphaned data.

        This runs on startup to fix any data corruption from previous bugs (like issue #63).
        Removes orphaned session references and deletes empty projects.
        Logs all cleanup actions for debugging.
        """
        try:
            projects = await self.project_manager.list_projects()
            coord_logger.info(f"Validating {len(projects)} projects for orphaned session references")

            cleanup_count = 0

            for project in projects:
                project_id = project.project_id
                session_ids_before = list(project.session_ids)  # Make a copy
                valid_session_ids = []

                # Check each session ID to see if it actually exists
                for session_id in session_ids_before:
                    session_info = await self.session_manager.get_session_info(session_id)
                    if session_info:
                        valid_session_ids.append(session_id)
                    else:
                        coord_logger.warning(f"Found orphaned session reference '{session_id}' in project {project_id} - will be removed")
                        cleanup_count += 1

                # If we found orphaned references, update or delete the project
                if len(valid_session_ids) != len(session_ids_before):
                    orphaned_count = len(session_ids_before) - len(valid_session_ids)

                    # Clean the session_ids list but keep the project (even if empty)
                    coord_logger.info(f"Removing {orphaned_count} orphaned session(s) from project {project_id} ({project.name})")
                    project.session_ids = valid_session_ids
                    project.updated_at = datetime.now(UTC)
                    await self.project_manager._persist_project_state(project_id)

                    if len(valid_session_ids) == 0:
                        coord_logger.info(f"Project {project_id} ({project.name}) now has no sessions but will remain for new minions")

            # Also validate parent-child minion relationships (overseer child_minion_ids)
            sessions = await self.session_manager.list_sessions()
            child_cleanup_count = 0

            for session in sessions:
                # Issue #349: All sessions are minions - just check is_overseer and child_minion_ids
                if session.is_overseer and session.child_minion_ids:
                    # This is a parent overseer - validate its children
                    valid_children = []
                    children_before = list(session.child_minion_ids)

                    for child_id in children_before:
                        child_info = await self.session_manager.get_session_info(child_id)
                        if child_info:
                            valid_children.append(child_id)
                        else:
                            coord_logger.warning(f"Found orphaned child minion reference '{child_id}' in overseer {session.session_id} - will be removed")
                            child_cleanup_count += 1

                    # Update if we found orphaned children
                    if len(valid_children) != len(children_before):
                        session.child_minion_ids = valid_children
                        session.updated_at = datetime.now(UTC)
                        await self.session_manager._persist_session_state(session.session_id)
                        coord_logger.info(f"Removed {len(children_before) - len(valid_children)} orphaned child(ren) from overseer {session.session_id}")

            if cleanup_count > 0 or child_cleanup_count > 0:
                coord_logger.info(f"Startup cleanup complete: removed {cleanup_count} orphaned project session(s), {child_cleanup_count} orphaned child minion(s)")
            else:
                coord_logger.info("Startup validation complete: no orphaned data found")

        except Exception as e:
            logger.error(f"Error during startup project validation: {e}")
            # Don't raise - this shouldn't block startup

    # ==================== FILE UPLOAD TRACKING (Issue #403) ====================

    async def register_uploaded_file(self, session_id: str, file_path: str) -> None:
        """
        Register an uploaded file path for auto-approve Read permissions.

        When a user uploads a file, the path is registered so that subsequent
        Read tool requests for that path can be auto-approved without prompting.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id not in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id] = set()
        self._uploaded_file_paths[session_id].add(file_path)
        coord_logger.debug(f"Registered uploaded file for session {session_id}: {file_path}")

    async def unregister_uploaded_file(self, session_id: str, file_path: str) -> None:
        """
        Unregister an uploaded file path from auto-approve tracking.

        Called when a file is deleted by the user.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id].discard(file_path)
            coord_logger.debug(f"Unregistered uploaded file for session {session_id}: {file_path}")

    def is_uploaded_file(self, session_id: str, file_path: str) -> bool:
        """
        Check if a file path is an uploaded file for a session.

        Used by permission callbacks to auto-approve Read requests for
        user-uploaded files.

        Args:
            session_id: Session ID to check
            file_path: Absolute path to check

        Returns:
            True if the file was uploaded by the user for this session
        """
        return file_path in self._uploaded_file_paths.get(session_id, set())

    def get_uploaded_files(self, session_id: str) -> set[str]:
        """
        Get all uploaded file paths for a session.

        Args:
            session_id: Session ID

        Returns:
            Set of absolute file paths uploaded to this session
        """
        return self._uploaded_file_paths.get(session_id, set()).copy()

    def clear_uploaded_files(self, session_id: str) -> None:
        """
        Clear all uploaded file tracking for a session.

        Called when a session is deleted or reset.

        Args:
            session_id: Session ID to clear
        """
        if session_id in self._uploaded_file_paths:
            del self._uploaded_file_paths[session_id]
            coord_logger.debug(f"Cleared uploaded files tracking for session {session_id}")

    # ==================== RESOURCE GALLERY INTEGRATION (Issue #404) ====================

    async def register_uploaded_resource(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> None:
        """
        Register an uploaded file to the resource gallery.

        When a user uploads a file through the attachment system,
        this automatically adds it to the task panel resource gallery.

        Args:
            session_id: Session ID that owns the resource
            file_path: Absolute path to the uploaded file
            title: Optional title for the resource (defaults to filename)
            description: Optional description
        """
        if not self.resource_mcp_tools:
            coord_logger.warning("Resource MCP tools not available")
            return

        # Build args matching the MCP tool interface
        args = {
            "file_path": file_path,
            "title": title or "",
            "description": description or ""
        }

        # Use the MCP tool's internal handler directly
        result = await self.resource_mcp_tools._handle_register_resource(session_id, args)

        if result.get("is_error"):
            error_text = result.get("content", [{}])[0].get("text", "Unknown error")
            raise ValueError(f"Failed to register resource: {error_text}")

        coord_logger.info(f"Registered uploaded resource to gallery for session {session_id}: {title}")

    # Backward compatibility alias
    async def register_uploaded_image(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> None:
        """Deprecated: Use register_uploaded_resource instead."""
        await self.register_uploaded_resource(session_id, file_path, title, description)

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            # Terminate all active sessions
            session_ids = list(self._active_sdks.keys())
            for session_id in session_ids:
                await self.terminate_session(session_id)

            coord_logger.info("Session coordinator cleanup completed")

        except Exception as e:
            logger.error(f"Error during session coordinator cleanup: {e}")
