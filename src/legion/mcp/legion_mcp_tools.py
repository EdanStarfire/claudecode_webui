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
            return await self._handle_join_channel(args)

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

        # Map 'info' to internal 'guide' enum value for backward compatibility
        comm_type_mapping = {
            "task": "task",
            "question": "question",
            "report": "report",
            "info": "guide"  # Map user-facing 'info' to internal 'guide'
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

        Args:
            args: {"channel_name": str, "content": str, "comm_type": str}

        Returns:
            Tool result with content array
        """
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"send_comm_to_channel not yet implemented (would send to #{args.get('channel_name')})"
            }],
            "is_error": True
        }

    async def _handle_spawn_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle spawn_minion tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"spawn_minion not yet implemented (would create {args.get('name')})"
            }],
            "is_error": True
        }

    async def _handle_dispose_minion(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle dispose_minion tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"dispose_minion not yet implemented (would dispose {args.get('minion_name')})"
            }],
            "is_error": True
        }

    async def _handle_search_capability(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle search_capability tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"search_capability not yet implemented (would search for '{args.get('capability')}')"
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
        """Handle join_channel tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"join_channel not yet implemented (would join #{args.get('channel_name')})"
            }],
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
