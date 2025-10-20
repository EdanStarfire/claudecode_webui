"""
OverseerController - Minion lifecycle management for Legion multi-agent system.

Responsibilities:
- Create minions (user-initiated and minion-spawned)
- Dispose minions (parent authority enforcement)
- Track horde hierarchy
- Coordinate memory transfer on disposal
- Manage minion state transitions
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class OverseerController:
    """Manages minion lifecycle and horde hierarchy."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize OverseerController with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

    # TODO: Implement lifecycle management methods in Phase 2
    # - create_minion_for_user()
    # - spawn_minion()
    # - dispose_minion()
    # - terminate_minion()
    # - _create_horde()
    # - _update_horde()
