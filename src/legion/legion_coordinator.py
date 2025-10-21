"""
LegionCoordinator - Top-level orchestrator for Legion multi-agent system.

Responsibilities:
- Create/delete legions
- Track all hordes, channels, minions in legion
- Coordinate emergency halt/resume
- Provide fleet status
- Maintain central capability registry (MVP approach)
"""

import uuid
import json
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from src.legion_system import LegionSystem
    from src.models.legion_models import MinionInfo, LegionInfo


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

    async def get_minion_info(self, minion_id: str) -> Optional['MinionInfo']:
        """
        Get minion info by ID.

        Args:
            minion_id: Minion UUID

        Returns:
            MinionInfo if found, None otherwise
        """
        return self.minions.get(minion_id)

    async def get_minion_by_name(self, name: str) -> Optional['MinionInfo']:
        """
        Get minion by name (case-sensitive).

        Args:
            name: Minion name

        Returns:
            MinionInfo if found, None otherwise
        """
        for minion in self.minions.values():
            if minion.name == name:
                return minion
        return None

    async def create_legion(
        self,
        name: str,
        working_directory: str,
        max_concurrent_minions: int = 20
    ) -> 'LegionInfo':
        """
        Create a new legion.

        Args:
            name: Legion name
            working_directory: Absolute path to working directory
            max_concurrent_minions: Max number of concurrent minions

        Returns:
            LegionInfo for created legion
        """
        from src.models.legion_models import LegionInfo

        legion_id = str(uuid.uuid4())

        # Create LegionInfo
        legion = LegionInfo(
            legion_id=legion_id,
            name=name,
            working_directory=working_directory,
            max_concurrent_minions=max_concurrent_minions
        )

        # Store in memory
        self.legions[legion_id] = legion

        # Create directory structure
        await self._create_legion_directories(legion_id)

        # Persist to disk
        await self._persist_legion(legion)

        return legion

    async def get_legion(self, legion_id: str) -> Optional['LegionInfo']:
        """
        Get legion by ID.

        Args:
            legion_id: Legion UUID

        Returns:
            LegionInfo if found, None otherwise
        """
        return self.legions.get(legion_id)

    async def list_legions(self) -> List['LegionInfo']:
        """
        List all legions.

        Returns:
            List of all LegionInfo objects
        """
        return list(self.legions.values())

    async def _create_legion_directories(self, legion_id: str) -> None:
        """
        Create directory structure for legion.

        Creates:
        - data/legions/{legion_id}/
        - data/legions/{legion_id}/minions/
        - data/legions/{legion_id}/channels/
        - data/legions/{legion_id}/hordes/

        Args:
            legion_id: Legion UUID
        """
        base_path = Path("data") / "legions" / legion_id

        # Create subdirectories
        (base_path / "minions").mkdir(parents=True, exist_ok=True)
        (base_path / "channels").mkdir(parents=True, exist_ok=True)
        (base_path / "hordes").mkdir(parents=True, exist_ok=True)

    async def _persist_legion(self, legion: 'LegionInfo') -> None:
        """
        Persist legion state to disk.

        Args:
            legion: LegionInfo to persist
        """
        legion_path = Path("data") / "legions" / legion.legion_id
        state_file = legion_path / "state.json"

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(legion.to_dict(), f, indent=2)

    # TODO: Implement additional legion management methods in later phases
    # - delete_legion()
    # - emergency_halt_all()
    # - resume_all()
    # - get_fleet_status()
    # - register_capability()
    # - search_capability_registry()
