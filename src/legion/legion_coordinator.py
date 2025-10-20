"""
LegionCoordinator - Top-level orchestrator for Legion multi-agent system.

Responsibilities:
- Create/delete legions
- Track all hordes, channels, minions in legion
- Coordinate emergency halt/resume
- Provide fleet status
- Maintain central capability registry (MVP approach)
"""

from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class LegionCoordinator:
    """Top-level orchestrator for legion lifecycle and fleet management."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionCoordinator with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # In-memory state (will be persisted to disk)
        self.legions: Dict = {}  # Dict[str, LegionInfo]
        self.minions: Dict = {}  # Dict[str, MinionInfo]
        self.hordes: Dict = {}   # Dict[str, Horde]
        self.channels: Dict = {} # Dict[str, Channel]

        # Central capability registry (MVP approach)
        # Format: {minion_id: [capability1, capability2, ...]}
        self.capability_registry: Dict[str, List[str]] = {}

    # TODO: Implement legion management methods in Phase 2
    # - create_legion()
    # - delete_legion()
    # - emergency_halt_all()
    # - resume_all()
    # - get_fleet_status()
    # - register_capability()
    # - search_capability_registry()
