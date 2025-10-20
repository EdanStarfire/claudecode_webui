"""
LegionMCPTools - MCP tool definitions for Legion multi-agent capabilities.

This module provides MCP tools that minions can use for:
- Communication (send_comm, send_comm_to_channel)
- Lifecycle (spawn_minion, dispose_minion)
- Discovery (search_capability, list_minions, get_minion_info)
- Channels (join_channel, create_channel, list_channels)

All minion SDK sessions receive these tools on creation.
"""

from typing import TYPE_CHECKING, List, Dict, Any

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class LegionMCPTools:
    """
    MCP tools for Legion multi-agent capabilities.
    Single instance shared across all minion SDK sessions.
    """

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionMCPTools with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Tool definitions will be created in Phase 3
        self.tools: List = []

    # TODO: Implement in Phase 3
    # - _create_tool_definitions()
    # - _handle_send_comm()
    # - _handle_send_comm_to_channel()
    # - _handle_spawn_minion()
    # - _handle_dispose_minion()
    # - _handle_search_capability()
    # - _handle_list_minions()
    # - _handle_get_minion_info()
    # - _handle_join_channel()
    # - _handle_create_channel()
    # - _handle_list_channels()
