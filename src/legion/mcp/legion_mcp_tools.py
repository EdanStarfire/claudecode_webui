"""
LegionMCPTools - MCP tool definitions for Legion multi-agent capabilities.

This module provides MCP tools that minions can use for:
- Communication (send_comm, send_comm_to_channel)
- Lifecycle (spawn_minion, dispose_minion)
- Discovery (search_capability, list_minions, get_minion_info)
- Channels (join_channel, create_channel, list_channels)

All minion SDK sessions receive these tools on creation via MCP server.

Implementation uses Claude Agent SDK's @tool decorator and create_sdk_mcp_server.
Tools are exposed to minions with names like: mcp__legion__send_comm
"""

from typing import TYPE_CHECKING, Dict, Any

try:
    from claude_agent_sdk import tool, create_sdk_mcp_server
except ImportError:
    # For testing environments without SDK
    tool = None
    create_sdk_mcp_server = None

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class LegionMCPTools:
    """
    MCP tools for Legion multi-agent capabilities.
    Creates an MCP server with tools for minion collaboration.
    """

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionMCPTools with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Note: No longer creating a shared MCP server here
        # Each minion session gets its own session-specific server
        # via create_mcp_server_for_session()

    def create_mcp_server_for_session(self, session_id: str):
        """
        Create session-specific MCP server with all Legion tools.

        Args:
            session_id: The session ID to inject into tool calls

        Returns:
            MCP server instance with all tools registered
        """
        # Define all tools with @tool decorator
        # Tools will be named: mcp__legion__send_comm, mcp__legion__spawn_minion, etc.

        @tool(
            "send_comm",
            "Send a communication (message) to another minion by name. "
            "Proactively use when needing to respond to or ask questions of another #minion. "
            "Use this for direct collaboration, asking questions, delegating tasks, "
            "or sharing information. You can reference other minions in your message "
            "using #minion-name syntax (e.g., 'Check with #DatabaseArchitect about schema'). "
            "\n\nValid comm_type values: 'task', 'question', 'report', 'info'"
            "\n\nProvide BOTH summary and content:"
            "\n- summary: Brief one-line description (~50 chars, shown collapsed in timeline)"
            "\n- content: Full detailed message (shown when expanded, supports markdown)",
            {
                "to_minion_name": str,  # Exact name of target minion (case-sensitive)
                "summary": str,          # Brief one-line description (~50 chars)
                "content": str,          # Full detailed message (supports markdown)
                "comm_type": str         # One of: task, question, report, info
            }
        )
        async def send_comm_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Send communication to another minion."""
            # Inject session context
            args["_from_minion_id"] = session_id
            return await self._handle_send_comm(args)

        @tool(
            "send_comm_to_channel",
            "Broadcast a message to all members of a channel. All channel members will "
            "receive your message. Use for group coordination, status updates, or questions "
            "to the team. Reference specific minions using #minion-name syntax. "
            "\n\nValid comm_type values: 'task', 'question', 'report', 'info'"
            "\n\nProvide BOTH summary and content:"
            "\n- summary: Brief one-line description (~50 chars, shown collapsed in timeline)"
            "\n- content: Full detailed message (shown when expanded, supports markdown)",
            {
                "channel_name": str,  # Name of channel to broadcast to
                "summary": str,       # Brief one-line description (~50 chars)
                "content": str,       # Full detailed message (supports markdown)
                "comm_type": str      # One of: task, question, report, info (optional)
            }
        )
        async def send_comm_to_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Broadcast communication to channel."""
            # Inject session context
            args["_from_minion_id"] = session_id
            return await self._handle_send_comm_to_channel(args)

        @tool(
            "spawn_minion",
            "Create a new child minion to delegate specialized work. You become the overseer "
            "of this minion and can later dispose of it when done. The child minion will be a "
            "full Claude agent with access to tools.",
            {
                "name": str,                    # Unique name for new minion
                "role": str,                    # Human-readable role description
                "initialization_context": str,  # System prompt defining expertise
                "channels": list                # List of channel names to join (optional)
            }
        )
        async def spawn_minion_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Spawn a new child minion."""
            # Inject session context (parent overseer ID)
            args["_parent_overseer_id"] = session_id
            return await self._handle_spawn_minion(args)

        @tool(
            "dispose_minion",
            "Terminate a child minion you created when their task is complete. You can only "
            "dispose minions you spawned (your children). Their knowledge will be transferred "
            "to you before termination.",
            {
                "minion_name": str  # Name of child minion to dispose
            }
        )
        async def dispose_minion_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Dispose of a child minion."""
            # Inject session context (parent overseer ID)
            args["_parent_overseer_id"] = session_id
            return await self._handle_dispose_minion(args)

        @tool(
            "search_capability",
            "Find minions with a specific capability by searching the central capability registry. "
            "Returns ranked list of matching minions sorted by expertise scores. Use keywords like "
            "'database', 'oauth', 'authentication', 'payment_processing', etc.",
            {
                "capability": str  # Capability keyword to search for
            }
        )
        async def search_capability_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Search for minions by capability."""
            return await self._handle_search_capability(args)

        @tool(
            "list_minions",
            "Get a list of all active minions in the legion with their names, roles, and "
            "current states. Useful for understanding who's available to collaborate with.",
            {}  # No parameters required
        )
        async def list_minions_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """List all active minions."""
            return await self._handle_list_minions(args)

        @tool(
            "get_minion_info",
            "Get detailed information about a specific minion, including their role, capabilities, "
            "current task, parent/children, and channels.",
            {
                "minion_name": str  # Name of minion to query
            }
        )
        async def get_minion_info_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Get detailed minion information."""
            return await self._handle_get_minion_info(args)

        @tool(
            "join_channel",
            "Join an existing channel to collaborate with its members. You'll receive all "
            "future broadcasts to this channel.",
            {
                "channel_name": str  # Name of channel to join
            }
        )
        async def join_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Join a channel."""
            args["_from_minion_id"] = session_id
            return await self._handle_join_channel(args)

        @tool(
            "leave_channel",
            "Leave a channel you previously joined. You'll no longer receive broadcasts "
            "to this channel. Useful when your work related to that channel is complete.",
            {
                "channel_name": str  # Name of channel to leave
            }
        )
        async def leave_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Leave a channel."""
            args["_from_minion_id"] = session_id
            return await self._handle_leave_channel(args)

        @tool(
            "add_minion_to_channel",
            "Add one of your child minions to a channel (overseers only). You can only add "
            "minions you directly spawned (your children), not grandchildren or unrelated minions. "
            "Useful for organizing your team into specialized communication groups.",
            {
                "minion_name": str,   # Name of your child minion to add
                "channel_name": str   # Name of channel to add them to
            }
        )
        async def add_minion_to_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Add child minion to channel."""
            args["_parent_overseer_id"] = session_id
            return await self._handle_add_minion_to_channel(args)

        @tool(
            "remove_minion_from_channel",
            "Remove one of your child minions from a channel (overseers only). You can only remove "
            "minions you directly spawned (your children), not grandchildren or unrelated minions.",
            {
                "minion_name": str,   # Name of your child minion to remove
                "channel_name": str   # Name of channel to remove them from
            }
        )
        async def remove_minion_from_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Remove child minion from channel."""
            args["_parent_overseer_id"] = session_id
            return await self._handle_remove_minion_from_channel(args)

        @tool(
            "create_channel",
            "Create a new channel for group communication. You'll be automatically added as a member. "
            "Use channels to coordinate with multiple minions on a shared task.",
            {
                "name": str,              # Unique name for channel
                "description": str,       # Purpose and description
                "purpose": str,           # One of: coordination, planning, research, scene
                "initial_members": list   # List of minion names (optional)
            }
        )
        async def create_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Create a new channel."""
            return await self._handle_create_channel(args)

        @tool(
            "list_channels",
            "Get list of all channels in the legion with their names, descriptions, and member counts.",
            {}  # No parameters required
        )
        async def list_channels_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """List all channels."""
            return await self._handle_list_channels(args)

        # Create and return MCP server with all tools
        return create_sdk_mcp_server(
            name="legion",
            version="1.0.0",
            tools=[
                send_comm_tool,
                send_comm_to_channel_tool,
                spawn_minion_tool,
                dispose_minion_tool,
                search_capability_tool,
                list_minions_tool,
                get_minion_info_tool,
                join_channel_tool,
                leave_channel_tool,
                add_minion_to_channel_tool,
                remove_minion_from_channel_tool,
                create_channel_tool,
                list_channels_tool
            ]
        )

    # Tool Handler Methods - Implementation in Phase 2
    # These are called by the @tool decorated functions above

    async def _handle_send_comm(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle send_comm tool call.

        Args:
            args: {"to_minion_name": str, "content": str, "comm_type": str}

        Returns:
            Tool result with content array
        """
        import uuid
        from src.models.legion_models import Comm, CommType, InterruptPriority

        # Get current minion context (from session_id in SDK context)
        # For now, we'll need to pass this through - placeholder
        from_minion_id = args.get("_from_minion_id")  # Will be injected by SDK wrapper

        if not from_minion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine sender minion ID"
                }],
                "is_error": True
            }

        # Validate comm_type
        comm_type_str = args.get("comm_type", "task").lower()
        valid_comm_types = ["task", "question", "report", "info"]

        # Map user-facing types to CommType enum values
        comm_type_mapping = {
            "task": "task",
            "question": "question",
            "report": "report",
            "info": "info"  # Maps directly to CommType.INFO
        }

        if comm_type_str not in valid_comm_types:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Invalid comm_type '{comm_type_str}'. Valid values are: {', '.join(valid_comm_types)}"
                }],
                "is_error": True
            }

        # Get the internal enum value
        internal_comm_type = comm_type_mapping[comm_type_str]

        # Look up target minion by name
        to_minion_name = args.get("to_minion_name")

        # Check if sending to the special "user" minion
        from src.models.legion_models import USER_MINION_ID
        sending_to_user = (to_minion_name == "user")

        if sending_to_user:
            to_minion_id = None  # User doesn't have a minion_id, only use to_user flag
        else:
            to_minion = await self.system.legion_coordinator.get_minion_by_name(to_minion_name)
            if not to_minion:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: Minion '{to_minion_name}' not found"
                    }],
                    "is_error": True
                }
            to_minion_id = to_minion.session_id  # session_id IS the minion_id

        # Extract summary and content with fallback
        content = args.get("content", "")
        summary = args.get("summary", "")

        # Fallback: If summary is empty, auto-generate from first 50 chars of content
        if not summary and content:
            summary = content[:50] + ("..." if len(content) > 50 else "")

        # Look up sender minion name (for historical display)
        from_minion_name = None
        from_minion_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if from_minion_session:
            from_minion_name = from_minion_session.name

        # Look up recipient minion name if applicable (for historical display)
        to_minion_name_captured = None
        if to_minion_id:
            to_minion_session = await self.system.session_coordinator.session_manager.get_session_info(to_minion_id)
            if to_minion_session:
                to_minion_name_captured = to_minion_session.name

        # Create Comm
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=from_minion_id,
            from_user=False,
            from_minion_name=from_minion_name,
            to_minion_id=to_minion_id,
            to_user=sending_to_user,
            to_minion_name=to_minion_name_captured,
            summary=summary,
            content=content,
            comm_type=CommType(internal_comm_type),
            interrupt_priority=InterruptPriority.ROUTINE,
            visible_to_user=True
        )

        # Route the comm
        try:
            success = await self.system.comm_router.route_comm(comm)
            if success:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Message sent to {to_minion_name}"
                    }],
                    "is_error": False
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to send message to {to_minion_name}"
                    }],
                    "is_error": True
                }
        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error sending message: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_send_comm_to_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle send_comm_to_channel tool call.

        Broadcasts a message to all members of a channel. Sender must be a member
        of the channel. Sender does not receive their own message back.

        Args:
            args: {
                "_from_minion_id": str,  # Injected by tool wrapper
                "channel_name": str,     # Channel name to send to
                "content": str,          # Message content
                "comm_type": str,        # Optional: "task", "question", "report", "info"
                "summary": str           # Optional: Brief summary
            }

        Returns:
            Tool result with content array
        """
        import uuid
        from src.models.legion_models import Comm, CommType, InterruptPriority

        # Get current minion context (from session_id in SDK context)
        from_minion_id = args.get("_from_minion_id")

        if not from_minion_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine sender minion ID"
                }],
                "is_error": True
            }

        # Validate comm_type
        comm_type_str = args.get("comm_type", "info").lower()
        valid_comm_types = ["task", "question", "report", "info"]

        comm_type_mapping = {
            "task": "task",
            "question": "question",
            "report": "report",
            "info": "info"
        }

        if comm_type_str not in valid_comm_types:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Invalid comm_type '{comm_type_str}'. Valid values are: {', '.join(valid_comm_types)}"
                }],
                "is_error": True
            }

        internal_comm_type = comm_type_mapping[comm_type_str]

        # Look up channel by name
        channel_name = args.get("channel_name")
        if not channel_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'channel_name' parameter is required"
                }],
                "is_error": True
            }

        # Get sender's legion to find channel
        sender_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if not sender_session:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to find sender session"
                }],
                "is_error": True
            }

        legion_id = sender_session.project_id

        # Find channel by name in this legion
        channels = await self.system.channel_manager.list_channels(legion_id)
        target_channel = None
        for channel in channels:
            if channel.name == channel_name:
                target_channel = channel
                break

        if not target_channel:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Channel '{channel_name}' not found"
                }],
                "is_error": True
            }

        # Validate sender is a member of the channel
        if from_minion_id not in target_channel.member_minion_ids:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: You are not a member of channel '{channel_name}'"
                }],
                "is_error": True
            }

        # Extract summary and content with fallback
        content = args.get("content", "")
        summary = args.get("summary", "")

        # Fallback: If summary is empty, auto-generate from first 50 chars of content
        if not summary and content:
            summary = content[:50] + ("..." if len(content) > 50 else "")

        # Look up sender minion name (for historical display)
        from_minion_name = sender_session.name

        # Create Comm for channel broadcast
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=from_minion_id,
            from_user=False,
            from_minion_name=from_minion_name,
            to_channel_id=target_channel.channel_id,
            to_channel_name=target_channel.name,  # Capture channel name
            to_user=False,
            summary=summary,
            content=content,
            comm_type=CommType(internal_comm_type),
            interrupt_priority=InterruptPriority.ROUTINE,
            visible_to_user=True
        )

        # Route the comm (will broadcast to all members except sender)
        try:
            success = await self.system.comm_router.route_comm(comm)
            if success:
                # Calculate recipient count (all members except sender)
                recipient_count = len([m for m in target_channel.member_minion_ids if m != from_minion_id])
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Message broadcast to channel '{channel_name}' ({recipient_count} recipient{'s' if recipient_count != 1 else ''})"
                    }],
                    "is_error": False
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Failed to broadcast message to channel '{channel_name}'"
                    }],
                    "is_error": True
                }
        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error broadcasting to channel: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_spawn_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle spawn_minion tool call from a minion.

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper
                "name": str,
                "role": str,
                "initialization_context": str,
                "capabilities": List[str],  # Optional
                "channels": List[str]  # Optional
            }

        Returns:
            Tool result with success/error
        """
        from src.logging_config import get_logger

        coord_logger = get_logger(__name__, "COORDINATOR")

        parent_overseer_id = args.get("_parent_overseer_id")
        if not parent_overseer_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine parent overseer ID"
                }],
                "is_error": True
            }

        # Extract parameters
        name = args.get("name", "").strip()
        role = args.get("role", "").strip()
        initialization_context = args.get("initialization_context", "").strip()
        capabilities = args.get("capabilities", [])
        channels = args.get("channels", [])

        # Validate required fields
        if not name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'name' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        if not role:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'role' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        if not initialization_context:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'initialization_context' parameter is required and cannot be empty. Provide clear instructions for what this minion should do."
                }],
                "is_error": True
            }

        # Attempt to spawn child minion
        try:
            # Map initialization_context to system_prompt (initialization_context is semantic UI term)
            child_minion_id = await self.system.overseer_controller.spawn_minion(
                parent_overseer_id=parent_overseer_id,
                name=name,
                role=role,
                system_prompt=initialization_context,
                capabilities=capabilities,
                channels=channels
            )

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ Successfully spawned minion '{name}' with role '{role}'.\n\n"
                        f"Minion ID: {child_minion_id}\n"
                        f"The child minion is now active and ready to receive comms. "
                        f"You can communicate with them using send_comm(to_minion_name='{name}', ...)."
                    )
                }],
                "is_error": False
            }

        except ValueError as e:
            # Handle validation errors (duplicate name, capacity, etc.)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Cannot spawn minion: {str(e)}"
                }],
                "is_error": True
            }

        except PermissionError as e:
            # Handle permission errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Permission denied: {str(e)}"
                }],
                "is_error": True
            }

        except Exception as e:
            # Catch-all for unexpected errors
            coord_logger.error(f"Unexpected error in spawn_minion: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to spawn minion due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_dispose_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle dispose_minion tool call from a minion.

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper (session_id)
                "minion_name": str
            }

        Returns:
            Tool result with success/error
        """
        from src.logging_config import get_logger

        coord_logger = get_logger(__name__, "COORDINATOR")

        parent_overseer_id = args.get("_parent_overseer_id")  # This is actually the CALLER's session_id
        if not parent_overseer_id:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: Unable to determine caller ID"
                }],
                "is_error": True
            }

        # Extract parameters
        minion_name = args.get("minion_name", "").strip()

        # Validate required field
        if not minion_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: 'minion_name' parameter is required and cannot be empty"
                }],
                "is_error": True
            }

        # Attempt to dispose child minion
        try:
            result = await self.system.overseer_controller.dispose_minion(
                parent_overseer_id=parent_overseer_id,
                child_minion_name=minion_name
            )

            descendants_msg = ""
            if result["descendants_count"] > 0:
                descendants_msg = f"\n\n⚠️  Also disposed {result['descendants_count']} descendant minion(s) (children of {minion_name})."

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"✅ Successfully disposed of minion '{minion_name}'."
                        f"{descendants_msg}\n\n"
                        f"Their knowledge has been preserved and will be available to you."
                    )
                }],
                "is_error": False
            }

        except ValueError as e:
            # Handle not found or validation errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Cannot dispose minion: {str(e)}"
                }],
                "is_error": True
            }

        except PermissionError as e:
            # Handle permission errors (not your child)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Permission denied: {str(e)}"
                }],
                "is_error": True
            }

        except Exception as e:
            # Catch-all for unexpected errors
            coord_logger.error(f"Unexpected error in dispose_minion: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to dispose minion due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_search_capability(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle search_capability tool call.

        Searches the central capability registry for minions with matching capabilities.
        Returns formatted text list of ranked results.

        Args:
            args: Tool arguments with 'capability' keyword

        Returns:
            Tool result with formatted minion list or error message
        """
        try:
            # Get search keyword
            keyword = args.get("capability", "").strip()

            if not keyword:
                return {
                    "content": [{
                        "type": "text",
                        "text": "❌ Error: capability parameter is required and cannot be empty"
                    }],
                    "is_error": True
                }

            # Search the capability registry
            try:
                results = await self.system.legion_coordinator.search_capability_registry(
                    keyword=keyword
                )
            except ValueError as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Search error: {str(e)}"
                    }],
                    "is_error": True
                }

            # Handle empty results
            if not results:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"No minions found with capability matching '{keyword}'"
                    }],
                    "is_error": False
                }

            # Format results as text list
            result_lines = [f"**Minions with capability '{keyword}'** (ranked by expertise):\n"]

            for minion_id, expertise_score, capability_matched in results:
                # Get minion details
                minion = await self.system.legion_coordinator.get_minion_info(minion_id)
                if not minion:
                    continue  # Skip if minion no longer exists

                name = minion.name or minion_id[:8]
                role = minion.role or "No role specified"
                state = minion.state.value if hasattr(minion.state, 'value') else str(minion.state)

                # Format expertise score as percentage
                expertise_pct = int(expertise_score * 100)

                result_lines.append(
                    f"\n• **{name}** (ID: {minion_id[:8]}...)\n"
                    f"  - Role: {role}\n"
                    f"  - State: {state}\n"
                    f"  - Expertise: {expertise_pct}% ({expertise_score:.2f})\n"
                    f"  - Matched capability: {capability_matched}"
                )

            return {
                "content": [{
                    "type": "text",
                    "text": "".join(result_lines)
                }],
                "is_error": False
            }

        except Exception as e:
            # Catch-all for unexpected errors
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Failed to search capabilities due to unexpected error: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_list_minions(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle list_minions tool call.

        Lists all active minions in the legion with their names, roles, and states.
        Always includes the special "user" minion for sending comms to the user.

        Returns:
            Tool result with formatted minion list
        """
        try:
            from src.models.legion_models import USER_MINION_ID

            # Get all sessions from SessionManager
            session_manager = self.system.session_coordinator.session_manager
            all_sessions = await session_manager.list_sessions()

            # Filter to only minions
            minions = [s for s in all_sessions if s.is_minion]

            # Format minion list
            minion_lines = []

            # Always include the special "user" minion first
            minion_lines.append(
                f"• **user** (ID: {USER_MINION_ID})\n"
                f"  - Role: Human operator\n"
                f"  - State: active\n"
                f"  - Capabilities: Receives reports and can send tasks"
            )

            # Add other minions
            for minion in minions:
                name = minion.name or minion.session_id[:8]
                role = minion.role or "No role specified"
                state = minion.state.value if hasattr(minion.state, 'value') else str(minion.state)
                capabilities = ", ".join(minion.capabilities) if minion.capabilities else "None"

                minion_lines.append(
                    f"• **{name}** (ID: {minion.session_id})\n"
                    f"  - Role: {role}\n"
                    f"  - State: {state}\n"
                    f"  - Capabilities: {capabilities}"
                )

            total_count = len(minions) + 1  # +1 for user
            result_text = f"**Active Minions ({total_count}):**\n\n" + "\n\n".join(minion_lines)

            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }],
                "is_error": False
            }

        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error listing minions: {str(e)}"
                }],
                "is_error": True
            }

    async def _handle_get_minion_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_minion_info tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"get_minion_info not yet implemented (would query {args.get('minion_name')})"
            }],
            "is_error": True
        }

    async def _handle_join_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle join_channel tool call.

        Minion joins a channel by name. Idempotent - joining an already-joined
        channel returns success.

        Args:
            args: {
                "_from_minion_id": str,  # Injected by tool wrapper
                "channel_name": str
            }

        Returns:
            Tool result with success/error message
        """
        from_minion_id = args.get("_from_minion_id")
        if not from_minion_id:
            return {
                "content": [{"type": "text", "text": "Error: Unable to determine minion ID"}],
                "is_error": True
            }

        channel_name = args.get("channel_name", "").strip()
        if not channel_name:
            return {
                "content": [{"type": "text", "text": "Error: 'channel_name' parameter is required"}],
                "is_error": True
            }

        # Get minion's legion
        minion_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if not minion_session:
            return {
                "content": [{"type": "text", "text": "Error: Unable to find minion session"}],
                "is_error": True
            }

        legion_id = minion_session.project_id

        # Find channel by name
        channel = await self.system.legion_coordinator.get_channel_by_name(legion_id, channel_name)
        if not channel:
            return {
                "content": [{"type": "text", "text": f"Error: Channel '{channel_name}' not found"}],
                "is_error": True
            }

        # Check if already a member (for clear messaging)
        already_member = from_minion_id in channel.member_minion_ids

        # Add to channel (idempotent)
        try:
            await self.system.channel_manager.add_member(channel.channel_id, from_minion_id)

            # Log system comm
            await self._log_membership_change_comm(
                legion_id=legion_id,
                channel=channel,
                minion_id=from_minion_id,
                minion_name=minion_session.name,
                action="joined",
                actor_name=minion_session.name
            )

            if already_member:
                return {
                    "content": [{"type": "text", "text": f"Already a member of channel '{channel_name}'"}],
                    "is_error": False
                }
            else:
                member_count = len(channel.member_minion_ids)
                return {
                    "content": [{"type": "text", "text": f"Joined channel '{channel_name}' ({member_count} member{'s' if member_count != 1 else ''})"}],
                    "is_error": False
                }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error joining channel: {str(e)}"}],
                "is_error": True
            }

    async def _handle_leave_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle leave_channel tool call.

        Minion leaves a channel by name. Idempotent - leaving an already-left
        channel returns success.

        Args:
            args: {
                "_from_minion_id": str,  # Injected by tool wrapper
                "channel_name": str
            }

        Returns:
            Tool result with success/error message
        """
        from_minion_id = args.get("_from_minion_id")
        if not from_minion_id:
            return {
                "content": [{"type": "text", "text": "Error: Unable to determine minion ID"}],
                "is_error": True
            }

        channel_name = args.get("channel_name", "").strip()
        if not channel_name:
            return {
                "content": [{"type": "text", "text": "Error: 'channel_name' parameter is required"}],
                "is_error": True
            }

        # Get minion's legion
        minion_session = await self.system.session_coordinator.session_manager.get_session_info(from_minion_id)
        if not minion_session:
            return {
                "content": [{"type": "text", "text": "Error: Unable to find minion session"}],
                "is_error": True
            }

        legion_id = minion_session.project_id

        # Find channel by name
        channel = await self.system.legion_coordinator.get_channel_by_name(legion_id, channel_name)
        if not channel:
            return {
                "content": [{"type": "text", "text": f"Error: Channel '{channel_name}' not found"}],
                "is_error": True
            }

        # Check if was a member (for comm logging)
        was_member = from_minion_id in channel.member_minion_ids

        # Remove from channel (idempotent)
        try:
            await self.system.channel_manager.remove_member(channel.channel_id, from_minion_id)

            # Log system comm only if was actually a member
            if was_member:
                await self._log_membership_change_comm(
                    legion_id=legion_id,
                    channel=channel,
                    minion_id=from_minion_id,
                    minion_name=minion_session.name,
                    action="left",
                    actor_name=minion_session.name
                )

            return {
                "content": [{"type": "text", "text": f"Left channel '{channel_name}'"}],
                "is_error": False
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error leaving channel: {str(e)}"}],
                "is_error": True
            }

    async def _handle_add_minion_to_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle add_minion_to_channel tool call (overseer only).

        Overseer adds a direct child minion to a channel. Can only manage
        direct children (not grandchildren or unrelated minions).

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper
                "minion_name": str,
                "channel_name": str
            }

        Returns:
            Tool result with success/error message
        """
        overseer_id = args.get("_parent_overseer_id")
        if not overseer_id:
            return {
                "content": [{"type": "text", "text": "Error: Unable to determine overseer ID"}],
                "is_error": True
            }

        minion_name = args.get("minion_name", "").strip()
        channel_name = args.get("channel_name", "").strip()

        if not minion_name:
            return {
                "content": [{"type": "text", "text": "Error: 'minion_name' parameter is required"}],
                "is_error": True
            }

        if not channel_name:
            return {
                "content": [{"type": "text", "text": "Error: 'channel_name' parameter is required"}],
                "is_error": True
            }

        # Get overseer's legion
        overseer_session = await self.system.session_coordinator.session_manager.get_session_info(overseer_id)
        if not overseer_session:
            return {
                "content": [{"type": "text", "text": "Error: Unable to find overseer session"}],
                "is_error": True
            }

        legion_id = overseer_session.project_id

        # Look up target minion by name
        target_minion = await self.system.legion_coordinator.get_minion_by_name(minion_name)
        if not target_minion:
            return {
                "content": [{"type": "text", "text": f"Error: Minion '{minion_name}' not found"}],
                "is_error": True
            }

        target_minion_id = target_minion.session_id

        # Validate overseer owns this minion (direct child only)
        if target_minion.parent_overseer_id != overseer_id:
            return {
                "content": [{"type": "text", "text": f"Error: Not authorized - '{minion_name}' is not your direct child"}],
                "is_error": True
            }

        # Find channel by name
        channel = await self.system.legion_coordinator.get_channel_by_name(legion_id, channel_name)
        if not channel:
            return {
                "content": [{"type": "text", "text": f"Error: Channel '{channel_name}' not found"}],
                "is_error": True
            }

        # Check if already a member (for clear messaging)
        already_member = target_minion_id in channel.member_minion_ids

        # Add to channel (idempotent)
        try:
            await self.system.channel_manager.add_member(channel.channel_id, target_minion_id)

            # Log system comm
            await self._log_membership_change_comm(
                legion_id=legion_id,
                channel=channel,
                minion_id=target_minion_id,
                minion_name=minion_name,
                action="added to",
                actor_name=overseer_session.name
            )

            if already_member:
                return {
                    "content": [{"type": "text", "text": f"'{minion_name}' is already a member of channel '{channel_name}'"}],
                    "is_error": False
                }
            else:
                return {
                    "content": [{"type": "text", "text": f"Added '{minion_name}' to channel '{channel_name}'"}],
                    "is_error": False
                }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error adding minion to channel: {str(e)}"}],
                "is_error": True
            }

    async def _handle_remove_minion_from_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle remove_minion_from_channel tool call (overseer only).

        Overseer removes a direct child minion from a channel. Can only manage
        direct children (not grandchildren or unrelated minions).

        Args:
            args: {
                "_parent_overseer_id": str,  # Injected by tool wrapper
                "minion_name": str,
                "channel_name": str
            }

        Returns:
            Tool result with success/error message
        """
        overseer_id = args.get("_parent_overseer_id")
        if not overseer_id:
            return {
                "content": [{"type": "text", "text": "Error: Unable to determine overseer ID"}],
                "is_error": True
            }

        minion_name = args.get("minion_name", "").strip()
        channel_name = args.get("channel_name", "").strip()

        if not minion_name:
            return {
                "content": [{"type": "text", "text": "Error: 'minion_name' parameter is required"}],
                "is_error": True
            }

        if not channel_name:
            return {
                "content": [{"type": "text", "text": "Error: 'channel_name' parameter is required"}],
                "is_error": True
            }

        # Get overseer's legion
        overseer_session = await self.system.session_coordinator.session_manager.get_session_info(overseer_id)
        if not overseer_session:
            return {
                "content": [{"type": "text", "text": "Error: Unable to find overseer session"}],
                "is_error": True
            }

        legion_id = overseer_session.project_id

        # Look up target minion by name
        target_minion = await self.system.legion_coordinator.get_minion_by_name(minion_name)
        if not target_minion:
            return {
                "content": [{"type": "text", "text": f"Error: Minion '{minion_name}' not found"}],
                "is_error": True
            }

        target_minion_id = target_minion.session_id

        # Validate overseer owns this minion (direct child only)
        if target_minion.parent_overseer_id != overseer_id:
            return {
                "content": [{"type": "text", "text": f"Error: Not authorized - '{minion_name}' is not your direct child"}],
                "is_error": True
            }

        # Find channel by name
        channel = await self.system.legion_coordinator.get_channel_by_name(legion_id, channel_name)
        if not channel:
            return {
                "content": [{"type": "text", "text": f"Error: Channel '{channel_name}' not found"}],
                "is_error": True
            }

        # Check if was a member (for comm logging)
        was_member = target_minion_id in channel.member_minion_ids

        # Remove from channel (idempotent)
        try:
            await self.system.channel_manager.remove_member(channel.channel_id, target_minion_id)

            # Log system comm only if was actually a member
            if was_member:
                await self._log_membership_change_comm(
                    legion_id=legion_id,
                    channel=channel,
                    minion_id=target_minion_id,
                    minion_name=minion_name,
                    action="removed from",
                    actor_name=overseer_session.name
                )

            return {
                "content": [{"type": "text", "text": f"Removed '{minion_name}' from channel '{channel_name}'"}],
                "is_error": False
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error removing minion from channel: {str(e)}"}],
                "is_error": True
            }

    async def _handle_create_channel(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle create_channel tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"create_channel not yet implemented (would create #{args.get('name')})"
            }],
            "is_error": True
        }

    async def _handle_list_channels(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle list_channels tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": "list_channels not yet implemented"
            }],
            "is_error": True
        }

    async def _log_membership_change_comm(
        self,
        legion_id: str,
        channel: 'Channel',
        minion_id: str,
        minion_name: str,
        action: str,
        actor_name: str
    ) -> None:
        """
        Log a membership change as a SYSTEM comm.

        Args:
            legion_id: Legion UUID
            channel: Channel instance
            minion_id: ID of minion being added/removed
            minion_name: Name of minion being added/removed
            action: "joined", "left", "added to", or "removed from"
            actor_name: Name of the actor (minion or overseer)
        """
        import uuid
        from src.models.legion_models import Comm, CommType, InterruptPriority

        # Format message based on action
        if action in ["joined", "left"]:
            # Self-action by minion
            summary = f"{minion_name} {action} #{channel.name}"
            content = f"Minion **{minion_name}** {action} channel **#{channel.name}**"
        else:
            # Overseer action
            summary = f"{actor_name} {action} {minion_name} to #{channel.name}"
            content = f"Overseer **{actor_name}** {action} minion **{minion_name}** {'to' if 'added' in action else 'from'} channel **#{channel.name}**"

        # Create SYSTEM comm
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=None,  # System-generated
            from_user=False,
            summary=summary,
            content=content,
            comm_type=CommType.SYSTEM,
            interrupt_priority=InterruptPriority.ROUTINE,
            visible_to_user=True,
            to_channel_id=channel.channel_id,
            to_channel_name=channel.name
        )

        # Route comm (logs to timeline and channel history)
        await self.system.comm_router.route_comm(comm)
