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

        # Create MCP server with all Legion tools
        self.mcp_server = self._create_mcp_server() if (tool and create_sdk_mcp_server) else None

    def _create_mcp_server(self):
        """
        Create MCP server with all Legion tools using SDK patterns.

        Returns:
            MCP server instance with all tools registered
        """
        # Define all tools with @tool decorator
        # Tools will be named: mcp__legion__send_comm, mcp__legion__spawn_minion, etc.

        @tool(
            "send_comm",
            "Send a communication (message) to another minion by name. "
            "Use this for direct collaboration, asking questions, delegating tasks, "
            "or reporting findings. You can reference other minions in your message "
            "using #minion-name syntax (e.g., 'Check with #DatabaseArchitect about schema').",
            {
                "to_minion_name": str,  # Exact name of target minion (case-sensitive)
                "content": str,          # Message content with optional #tags
                "comm_type": str         # One of: task, question, report, guide
            }
        )
        async def send_comm_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Send communication to another minion."""
            return await self._handle_send_comm(args)

        @tool(
            "send_comm_to_channel",
            "Broadcast a message to all members of a channel. All channel members will "
            "receive your message. Use for group coordination, status updates, or questions "
            "to the team. Reference specific minions using #minion-name syntax.",
            {
                "channel_name": str,  # Name of channel to broadcast to
                "content": str,       # Message to broadcast with optional #tags
                "comm_type": str      # One of: task, question, report, guide (optional)
            }
        )
        async def send_comm_to_channel_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Broadcast communication to channel."""
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
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": f"send_comm not yet implemented (would send to {args.get('to_minion_name')})"
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
        """Handle list_minions tool call."""
        # TODO: Implement in Phase 2
        return {
            "content": [{
                "type": "text",
                "text": "list_minions not yet implemented"
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
