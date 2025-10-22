"""
OverseerController - Minion lifecycle management for Legion multi-agent system.

Responsibilities:
- Create minions (user-initiated and minion-spawned)
- Dispose minions (parent authority enforcement)
- Track horde hierarchy
- Coordinate memory transfer on disposal
- Manage minion state transitions
"""

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional
from datetime import datetime

from src.models.legion_models import Horde

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

        # Track hordes by horde_id
        self.hordes: Dict[str, Horde] = {}

    async def create_minion_for_user(
        self,
        legion_id: str,
        name: str,
        role: str = "",
        initialization_context: str = "",
        capabilities: Optional[List[str]] = None
    ) -> str:
        """
        Create a minion for the user (root overseer).

        Args:
            legion_id: ID of the legion (project) this minion belongs to
            name: Minion name (must be unique within legion)
            role: Optional role description (e.g., "Code Expert")
            initialization_context: Instructions/context for the minion
            capabilities: List of capability keywords for discovery

        Returns:
            str: The created minion's session_id

        Raises:
            ValueError: If name is not unique or legion doesn't exist or is at capacity
        """
        # Validate legion exists and is multi-agent
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if not project:
            raise ValueError(f"Legion {legion_id} not found")
        if not project.is_multi_agent:
            raise ValueError(f"Project {legion_id} is not a legion (is_multi_agent=False)")

        # Check minion limit (max 20 concurrent minions per legion)
        existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
        legion_minions = [s for s in existing_sessions if s.is_minion and self._belongs_to_legion(s, legion_id)]

        if len(legion_minions) >= (project.max_concurrent_minions or 20):
            raise ValueError(f"Legion {legion_id} has reached maximum concurrent minions ({project.max_concurrent_minions or 20})")

        # Validate name uniqueness within legion
        if any(m.name == name for m in legion_minions):
            raise ValueError(f"Minion name '{name}' already exists in this legion")

        # Generate session ID for the minion
        minion_id = str(uuid.uuid4())

        # Create session via SessionCoordinator (which will auto-detect minion from parent project)
        await self.system.session_coordinator.create_session(
            session_id=minion_id,
            project_id=legion_id,
            name=name,
            permission_mode="default",  # Default permission mode for minions
            # Minion-specific fields
            role=role,
            initialization_context=initialization_context,
            capabilities=capabilities or []
        )

        # Register capabilities in capability registry
        if capabilities:
            for capability in capabilities:
                if capability not in self.system.legion_coordinator.capability_registry:
                    self.system.legion_coordinator.capability_registry[capability] = []
                self.system.legion_coordinator.capability_registry[capability].append(minion_id)

        # Create or update horde
        await self._ensure_horde_for_minion(legion_id, minion_id, name)

        return minion_id

    def _belongs_to_legion(self, session_info, legion_id: str) -> bool:
        """Check if a session belongs to a legion by checking working directory."""
        project = self.system.session_coordinator.project_manager.projects.get(legion_id)
        if not project:
            return False
        return session_info.working_directory == project.working_directory

    async def _ensure_horde_for_minion(self, legion_id: str, minion_id: str, minion_name: str):
        """
        Create horde if this is the first minion, otherwise add to existing horde.

        Args:
            legion_id: Legion ID
            minion_id: Minion session ID
            minion_name: Minion name
        """
        # Check if legion already has a horde
        existing_horde = None
        for horde in self.hordes.values():
            if horde.legion_id == legion_id:
                existing_horde = horde
                break

        if existing_horde:
            # Add minion to existing horde
            if minion_id not in existing_horde.all_minion_ids:
                existing_horde.all_minion_ids.append(minion_id)
                existing_horde.updated_at = datetime.now()
                await self._persist_horde(existing_horde)
        else:
            # Create new horde with this minion as root overseer
            horde_id = str(uuid.uuid4())
            horde = Horde(
                horde_id=horde_id,
                legion_id=legion_id,
                name=f"{minion_name}'s Horde",
                root_overseer_id=minion_id,
                all_minion_ids=[minion_id],
                created_by="user"
            )
            self.hordes[horde_id] = horde
            await self._persist_horde(horde)

    async def _persist_horde(self, horde: Horde):
        """
        Persist horde to disk.

        Args:
            horde: Horde instance to persist
        """
        # Get data directory from session coordinator
        data_dir = self.system.session_coordinator.data_dir
        hordes_dir = data_dir / "hordes"
        hordes_dir.mkdir(exist_ok=True)

        horde_file = hordes_dir / f"{horde.horde_id}.json"

        import json
        with open(horde_file, 'w') as f:
            json.dump(horde.to_dict(), f, indent=2)

    # TODO: Implement in Phase 5
    # - spawn_minion() - for autonomous spawning by minions
    # - dispose_minion() - for cleanup with memory transfer
    # - terminate_minion() - for forced termination
