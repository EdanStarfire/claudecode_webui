"""
CommRouter - Communication routing for Legion multi-agent system.

Responsibilities:
- Convert Comm → Message for SDK injection
- Convert SDK Message → Comm for routing
- Route Comms to minions, channels, or user
- Handle interrupt priorities (Halt, Pivot)
- Persist Comms to multiple locations
- Parse #minion-name and #channel-name tags for explicit references
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class CommRouter:
    """Routes communications between minions, channels, and user."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize CommRouter with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

    # TODO: Implement routing methods in Phase 2
    # - route_comm()
    # - _send_to_minion()
    # - _broadcast_to_channel()
    # - _send_to_user()
    # - _extract_tags()
    # - sdk_message_to_comm()
