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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .claude_sdk import ClaudeSDK
from .data_storage import DataStorageManager
from .logging_config import get_logger
from .message_parser import MessageParser, MessageProcessor
from .project_manager import ProjectInfo, ProjectManager
from .session_manager import SessionInfo, SessionManager, SessionState
from .timestamp_utils import get_unix_timestamp
from src.legion.minion_system_prompts import get_legion_guide_only

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

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path("data")
        self.session_manager = SessionManager(self.data_dir)
        self.project_manager = ProjectManager(self.data_dir)
        self.message_parser = MessageParser()
        self.message_processor = MessageProcessor(self.message_parser)

        # Template manager for minion templates
        from src.template_manager import TemplateManager
        self.template_manager = TemplateManager(self.data_dir)

        # Active SDK instances
        self._active_sdks: Dict[str, ClaudeSDK] = {}
        self._storage_managers: Dict[str, DataStorageManager] = {}

        # Event callbacks
        self._message_callbacks: Dict[str, List[Callable]] = {}
        self._error_callbacks: Dict[str, List[Callable]] = {}
        self._state_change_callbacks: List[Callable] = []

        # Track recent tool uses for ExitPlanMode detection
        self._recent_tool_uses: Dict[str, Dict[str, str]] = {}  # session_id -> {tool_use_id: tool_name}

        # Track applied permission updates for state management
        self._permission_updates: Dict[str, List[dict]] = {}  # session_id -> list of applied updates

        # Track ExitPlanMode that had setMode suggestions applied (to prevent auto-reset)
        self._exitplanmode_with_setmode: Dict[str, bool] = {}  # session_id -> bool

        # Initialize Legion multi-agent system
        from src.legion_system import LegionSystem
        # Create a dummy storage manager for now (legion will create its own per-legion storage)
        dummy_storage = DataStorageManager(self.data_dir / "legion_temp")
        self.legion_system = LegionSystem(
            session_coordinator=self,
            data_storage_manager=dummy_storage,
            template_manager=self.template_manager
        )

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

            # Load channels for all legions (if LegionSystem is initialized)
            if hasattr(self, 'legion_system') and self.legion_system is not None:
                await self.legion_system.legion_coordinator._load_all_channels()
                coord_logger.info("Loaded channels for all legions")

            # Register callback to receive session manager state changes
            self.session_manager.add_state_change_callback(self._on_session_manager_state_change)

            # Initialize storage managers for all existing sessions
            await self._initialize_existing_session_storage()

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

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        permission_mode: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        override_system_prompt: bool = False,
        tools: List[str] = None,
        model: Optional[str] = None,
        name: Optional[str] = None,
        permission_callback: Optional[Callable[[str, Dict[str, Any]], Union[bool, Dict[str, Any]]]] = None,
        # Minion-specific fields
        role: Optional[str] = None,
        capabilities: List[str] = None,
        # Hierarchy fields (Phase 5)
        parent_overseer_id: Optional[str] = None,
        overseer_level: int = 0,
        horde_id: Optional[str] = None,
    ) -> str:
        """Create a new Claude Code session with integrated components (within a project)"""
        try:
            # Get project to determine working directory and multi-agent status
            project = await self.project_manager.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")

            working_directory = project.working_directory

            # Calculate order based on existing sessions in project
            order = len(project.session_ids)

            # Detect if this session should be a minion (parent project is a legion)
            is_minion = project.is_multi_agent

            # Create session through session manager
            # Store raw system_prompt (guide will be prepended later in start_session)
            await self.session_manager.create_session(
                session_id=session_id,
                working_directory=working_directory,
                permission_mode=permission_mode,
                system_prompt=system_prompt,
                override_system_prompt=override_system_prompt,
                tools=tools,
                model=model,
                name=name,
                order=order,
                project_id=project_id,
                # Minion fields (auto-populated if parent is legion)
                is_minion=is_minion,
                role=role,
                capabilities=capabilities,
                # Hierarchy fields (Phase 5)
                parent_overseer_id=parent_overseer_id,
                overseer_level=overseer_level,
                horde_id=horde_id
            )

            # Add session to project
            await self.project_manager.add_session_to_project(project_id, session_id)

            # Initialize storage manager for this session
            session_dir = await self.session_manager.get_session_directory(session_id)
            storage_manager = DataStorageManager(session_dir)
            await storage_manager.initialize()
            self._storage_managers[session_id] = storage_manager

            # Attach MCP tools for minion sessions (multi-agent)
            mcp_servers = None
            legion_tools = []
            coord_logger.debug(f"MCP attachment check for session {session_id}: is_minion={is_minion}, legion_system={self.legion_system is not None}, mcp_tools={self.legion_system.mcp_tools if self.legion_system else None}")
            if is_minion and self.legion_system and self.legion_system.mcp_tools:
                # Create session-specific MCP server (injects session_id into tool calls)
                mcp_server = self.legion_system.mcp_tools.create_mcp_server_for_session(session_id)
                coord_logger.debug(f"Created session-specific MCP server for {session_id}: {mcp_server}")
                if mcp_server:
                    # SDK expects dict mapping server name to config, not a list
                    mcp_servers = {"legion": mcp_server}
                    # Allow all legion MCP tools (reduces command-line length)
                    legion_tools = ["mcp__legion"]
                    coord_logger.info(f"Attaching Legion MCP tools to minion session {session_id}: {legion_tools}")
                else:
                    coord_logger.warning(f"MCP server creation failed for minion session {session_id}")
            else:
                if not is_minion:
                    coord_logger.debug(f"Session {session_id} is not a minion - skipping MCP tools")
                elif not self.legion_system:
                    coord_logger.warning(f"Legion system is None for session {session_id}")
                elif not self.legion_system.mcp_tools:
                    coord_logger.warning(f"Legion system mcp_tools is None for session {session_id}")

            # Merge Legion tools with any provided tools
            all_tools = tools if tools else []
            all_tools = list(set(all_tools + legion_tools))  # Deduplicate

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
                model=model,
                mcp_servers=mcp_servers
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

    async def start_session(self, session_id: str, permission_callback: Optional[Callable[[str, Dict[str, Any]], Union[bool, Dict[str, Any]]]] = None) -> bool:
        """Start a session with SDK integration"""
        try:
            # Start session through session manager
            if not await self.session_manager.start_session(session_id):
                return False

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

            # Attach MCP tools for minion sessions (multi-agent)
            mcp_servers = None
            legion_tools = []
            if session_info.is_minion and self.legion_system and self.legion_system.mcp_tools:
                # Create session-specific MCP server (injects session_id into tool calls)
                mcp_server = self.legion_system.mcp_tools.create_mcp_server_for_session(session_id)
                if mcp_server:
                    # SDK expects dict mapping server name to config, not a list
                    mcp_servers = {"legion": mcp_server}
                    # Allow all legion MCP tools (reduces command-line length)
                    legion_tools = ["mcp__legion"]
                    coord_logger.info(f"Attaching Legion MCP tools to minion session {session_id} (on start): {legion_tools}")

            # Merge Legion tools with session's stored tools
            all_tools = session_info.tools if session_info.tools else []
            all_tools = list(set(all_tools + legion_tools))  # Deduplicate

            # Build minion system prompt by prepending legion guide to stored system_prompt
            if session_info.is_minion:
                legion_guide = get_legion_guide_only()
                if session_info.system_prompt:
                    minion_system_prompt = f"{legion_guide}\n\n---\n\n{session_info.system_prompt}"
                else:
                    minion_system_prompt = legion_guide
                coord_logger.info(f"Built minion system prompt for start (guide + context): {len(minion_system_prompt)} chars")
            else:
                minion_system_prompt = session_info.system_prompt

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
                model=session_info.model,
                resume_session_id=resume_sdk_session,  # Only resume if we have a Claude Code session ID
                mcp_servers=mcp_servers
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

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and cleanup all resources (with cascading deletion for child minions)"""
        try:
            # Step 0: If this is a parent overseer, recursively delete all children first (cascading)
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info and session_info.is_minion and session_info.is_overseer and session_info.child_minion_ids:
                child_ids = list(session_info.child_minion_ids)  # Make a copy
                coord_logger.info(f"Parent overseer {session_id} has {len(child_ids)} children - cascading deletion")

                for child_id in child_ids:
                    coord_logger.info(f"Cascading: deleting child minion {child_id} of parent {session_id}")
                    # Recursively delete child (which may have its own children)
                    await self.delete_session(child_id)

                coord_logger.info(f"Cascading deletion complete for parent {session_id} - all {len(child_ids)} children deleted")

            # Step 1: Find and remove session from its project
            project = await self._find_project_for_session(session_id)
            project_was_deleted = False
            deleted_project_id = None

            if project:
                removal_success, project_was_deleted = await self.project_manager.remove_session_from_project(project.project_id, session_id)

                if removal_success:
                    if project_was_deleted:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id} - project was empty and has been deleted")
                        deleted_project_id = project.project_id
                    else:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id}")
                else:
                    logger.warning(f"Failed to remove session {session_id} from project {project.project_id}")

            # Step 1.5: If this is a minion with a parent overseer, remove from parent's child_minion_ids
            # (session_info was already fetched in Step 0)
            if not session_info:
                session_info = await self.session_manager.get_session_info(session_id)

            if session_info and session_info.is_minion and session_info.parent_overseer_id:
                parent_id = session_info.parent_overseer_id
                parent_info = await self.session_manager.get_session_info(parent_id)

                if parent_info and session_id in parent_info.child_minion_ids:
                    parent_info.child_minion_ids.remove(session_id)
                    parent_info.updated_at = datetime.now(timezone.utc)
                    await self.session_manager._persist_session_state(parent_id)
                    coord_logger.info(f"Removed minion {session_id} from parent overseer {parent_id}'s child_minion_ids")
                elif parent_info:
                    coord_logger.warning(f"Minion {session_id} not found in parent overseer {parent_id}'s child_minion_ids")
                else:
                    coord_logger.warning(f"Parent overseer {parent_id} not found for minion {session_id}")

            # Step 2: Terminate the SDK if it's running
            sdk = self._active_sdks.get(session_id)
            if sdk:
                # logger.info(f"Terminating SDK for session {session_id} before deletion")
                await sdk.terminate()
                del self._active_sdks[session_id]
                # Give SDK time to fully close
                await asyncio.sleep(0.2)

            # Step 3: Clean up storage manager and ensure all files are closed
            if session_id in self._storage_managers:
                storage_manager = self._storage_managers[session_id]
                # logger.info(f"Cleaning up storage manager for session {session_id}")
                await storage_manager.cleanup()
                del self._storage_managers[session_id]
                # Give storage manager time to close all file handles
                await asyncio.sleep(0.2)

            # Step 4: Clean up callbacks
            if session_id in self._message_callbacks:
                del self._message_callbacks[session_id]
            if session_id in self._error_callbacks:
                del self._error_callbacks[session_id]

            # Step 5: Force multiple garbage collections to ensure all handles are released
            gc.collect()
            await asyncio.sleep(0.1)
            gc.collect()
            await asyncio.sleep(0.1)

            # Step 6: Additional Windows-specific cleanup
            if os.name == 'nt':  # Windows
                # logger.info(f"Performing Windows-specific cleanup for session {session_id}")
                # Force close any remaining handles that might be held by the system
                gc.collect()
                await asyncio.sleep(0.3)

            # Step 7: Delete through session manager (this removes from active sessions and deletes files)
            # logger.info(f"Deleting session files for session {session_id}")
            success = await self.session_manager.delete_session(session_id)

            if success:
                coord_logger.info(f"Session {session_id} deleted")
                # Notify about session deletion (using a special state change)
                await self._notify_state_change(session_id, "deleted")

            return success

        except Exception as e:
            logger.error(f"Failed to delete integrated session {session_id}: {e}")
            return False

    async def _find_project_for_session(self, session_id: str) -> Optional[ProjectInfo]:
        """Find the project that contains a given session"""
        projects = await self.project_manager.list_projects()
        for project in projects:
            if session_id in project.session_ids:
                return project
        return None

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

    async def restart_session(self, session_id: str, permission_callback: Optional[Callable] = None) -> bool:
        """
        Restart a session by disconnecting SDK and resuming with same session ID.

        This is useful for unsticking the agent without losing conversation history.
        """
        try:
            coord_logger.info(f"Restarting session {session_id}")

            # Get current SDK
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK for session {session_id}, cannot restart")
                return False

            # Disconnect SDK gracefully
            disconnect_result = await sdk.disconnect()
            if not disconnect_result:
                logger.warning(f"SDK disconnect returned False for session {session_id}")

            # Update session state to TERMINATED after disconnect
            await self.session_manager.update_session_state(session_id, SessionState.TERMINATED)

            # Wait a moment for cleanup
            await asyncio.sleep(0.5)

            # Remove old SDK reference
            del self._active_sdks[session_id]

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

    async def reset_session(self, session_id: str, permission_callback: Optional[Callable] = None) -> bool:
        """
        Reset a session by clearing all messages and starting fresh.

        Keeps session settings (permission mode, tools, etc.) but clears conversation history.
        """
        try:
            coord_logger.info(f"Resetting session {session_id}")

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
            if storage:
                await storage.clear_messages()
                coord_logger.info(f"Cleared message history for session {session_id}")

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

    async def send_message(self, session_id: str, message: str) -> bool:
        """Send a message to a session"""
        try:
            # Check if SDK exists and is active
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id}")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found")
                return False

            # Only allow sending messages to active sessions
            if session_info.state not in [SessionState.ACTIVE]:
                logger.warning(f"Session {session_id} not active (state: {session_info.state})")
                return False

            # Update processing state before sending message
            await self.session_manager.update_processing_state(session_id, True)

            # Send message via SDK
            result = await sdk.send_message(message)
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
            raw_messages = await storage.read_messages(limit=limit, offset=offset)
            total_count = await storage.get_message_count()

            # Convert stored messages to WebSocket format (skip re-processing if already processed)
            parsed_messages = []
            for raw_message in raw_messages:
                try:
                    # Check if message is already fully processed (has metadata)
                    if isinstance(raw_message.get("metadata"), dict) and raw_message.get("type") and raw_message.get("content") is not None:
                        # Message is already processed, prepare for WebSocket
                        metadata = raw_message["metadata"].copy()

                        # For init messages: extract init_data from raw_sdk_message string if present
                        if metadata.get("subtype") == "init" and "raw_sdk_message" in metadata and "init_data" not in metadata:
                            import re
                            raw_sdk_str = metadata.get("raw_sdk_message", "")
                            if isinstance(raw_sdk_str, str) and "data=" in raw_sdk_str:
                                # Extract the data dict from the string representation
                                try:
                                    # Match: data={'key': 'value', ...}
                                    data_match = re.search(r"data=(\{[^}]+(?:\{[^}]*\}[^}]*)*\})", raw_sdk_str)
                                    if data_match:
                                        import ast
                                        data_str = data_match.group(1)
                                        init_data = ast.literal_eval(data_str)
                                        metadata["init_data"] = init_data
                                        logger.debug(f"Extracted init_data from historical raw_sdk_message")
                                except Exception as parse_error:
                                    logger.warning(f"Failed to parse init_data from raw_sdk_message: {parse_error}")

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

                        parsed_messages.append(websocket_data)
                    else:
                        # Message needs processing - run through MessageProcessor
                        processed_message = self.message_processor.process_message(raw_message, source="storage")
                        websocket_data = self.message_processor.prepare_for_websocket(processed_message)
                        parsed_messages.append(websocket_data)
                except Exception as e:
                    logger.warning(f"Failed to prepare historical message for WebSocket: {e}")
                    # Fallback to basic format
                    fallback_data = {
                        "type": processed_message.type.value,
                        "content": processed_message.content,
                        "timestamp": processed_message.timestamp
                    }
                    if processed_message.session_id:
                        fallback_data["session_id"] = processed_message.session_id
                    parsed_messages.append(fallback_data)

            # Calculate pagination metadata
            actual_limit = limit or 50
            has_more = (offset + len(parsed_messages)) < total_count

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

    async def _store_processed_message(self, session_id: str, message_data: Dict[str, Any]):
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
        async def callback(message_data: Dict[str, Any]):
            try:
                # Process message using unified MessageProcessor
                parsed_message = self.message_processor.process_message(message_data, source="sdk")

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
                    project.updated_at = datetime.now(timezone.utc)
                    await self.project_manager._persist_project_state(project_id)

                    if len(valid_session_ids) == 0:
                        coord_logger.info(f"Project {project_id} ({project.name}) now has no sessions but will remain for new minions")

            # Also validate parent-child minion relationships (overseer child_minion_ids)
            sessions = await self.session_manager.list_sessions()
            child_cleanup_count = 0

            for session in sessions:
                if session.is_minion and session.is_overseer and session.child_minion_ids:
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
                        session.updated_at = datetime.now(timezone.utc)
                        await self.session_manager._persist_session_state(session.session_id)
                        coord_logger.info(f"Removed {len(children_before) - len(valid_children)} orphaned child(ren) from overseer {session.session_id}")

            if cleanup_count > 0 or child_cleanup_count > 0:
                coord_logger.info(f"Startup cleanup complete: removed {cleanup_count} orphaned project session(s), {child_cleanup_count} orphaned child minion(s)")
            else:
                coord_logger.info("Startup validation complete: no orphaned data found")

        except Exception as e:
            logger.error(f"Error during startup project validation: {e}")
            # Don't raise - this shouldn't block startup

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