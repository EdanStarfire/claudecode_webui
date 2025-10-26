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
        system_prompt: str = "",
        capabilities: Optional[List[str]] = None
    ) -> str:
        """
        Create a minion for the user (root overseer).

        Args:
            legion_id: ID of the legion (project) this minion belongs to
            name: Minion name (must be unique within legion)
            role: Optional role description (e.g., "Code Expert")
            system_prompt: Instructions/context for the minion (appended to Claude Code preset)
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
            system_prompt=system_prompt,
            # Minion-specific fields
            role=role,
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
        project = self.system.session_coordinator.project_manager._active_projects.get(legion_id)
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

    async def spawn_minion(
        self,
        parent_overseer_id: str,
        name: str,
        role: str,
        system_prompt: str,
        capabilities: Optional[List[str]] = None,
        channels: Optional[List[str]] = None
    ) -> str:
        """
        Spawn a child minion autonomously by a parent overseer.

        This is called when a minion uses the spawn_minion MCP tool.

        Args:
            parent_overseer_id: Session ID of parent minion
            name: Unique name for child (provided by LLM)
            role: Role description for child
            system_prompt: System prompt/instructions for child (appended to Claude Code preset)
            capabilities: Capability keywords for discovery
            channels: Channel IDs to join immediately

        Returns:
            str: Child minion's session_id

        Raises:
            ValueError: If parent doesn't exist, name duplicate, or legion at capacity
            PermissionError: If parent is not a valid overseer
        """
        from src.logging_config import get_logger
        from src.models.legion_models import Comm, CommType, InterruptPriority

        coord_logger = get_logger(__name__, "COORDINATOR")

        # 1. Get parent minion session
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        if not parent_session:
            raise ValueError(f"Parent overseer {parent_overseer_id} not found")

        if not parent_session.is_minion:
            raise PermissionError(f"Session {parent_overseer_id} is not a minion")

        # 2. Get legion (parent's project)
        legion_id = parent_session.project_id
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if not project or not project.is_multi_agent:
            raise ValueError(f"Legion {legion_id} not found or not multi-agent")

        # 3. Validate name uniqueness within legion
        existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
        legion_minions = [s for s in existing_sessions if s.is_minion and self._belongs_to_legion(s, legion_id)]

        if any(m.name == name for m in legion_minions):
            raise ValueError(f"Minion name '{name}' already exists in this legion. Choose a different name.")

        # 4. Check minion limit
        if len(legion_minions) >= (project.max_concurrent_minions or 20):
            raise ValueError(f"Legion at maximum capacity ({project.max_concurrent_minions or 20} minions). Cannot spawn more.")

        # 5. Generate child minion ID
        child_minion_id = str(uuid.uuid4())

        # 6. Calculate overseer level (parent + 1)
        overseer_level = (parent_session.overseer_level or 0) + 1

        # 7. Get parent's horde_id (child joins parent's horde)
        parent_horde_id = parent_session.horde_id
        if not parent_horde_id:
            # Parent has no horde yet - create one
            await self._ensure_horde_for_minion(legion_id, parent_overseer_id, parent_session.name)
            parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
            parent_horde_id = parent_session.horde_id

        # 8. Create child session via SessionCoordinator
        await self.system.session_coordinator.create_session(
            session_id=child_minion_id,
            project_id=legion_id,
            name=name,
            permission_mode="default",
            system_prompt=system_prompt,
            # Minion-specific fields
            role=role,
            capabilities=capabilities or [],
            # Hierarchy fields
            parent_overseer_id=parent_overseer_id,
            overseer_level=overseer_level,
            horde_id=parent_horde_id
        )

        # 9. Update parent: mark as overseer, add child to child_minion_ids
        if not parent_session.is_overseer:
            await self.system.session_coordinator.session_manager.update_session(
                parent_overseer_id,
                is_overseer=True
            )

        # Add child to parent's child_minion_ids list
        parent_children = list(parent_session.child_minion_ids or [])
        parent_children.append(child_minion_id)
        await self.system.session_coordinator.session_manager.update_session(
            parent_overseer_id,
            child_minion_ids=parent_children
        )

        # 10. Update horde: add child to all_minion_ids
        horde = self.hordes.get(parent_horde_id)
        if horde:
            if child_minion_id not in horde.all_minion_ids:
                horde.all_minion_ids.append(child_minion_id)
                horde.updated_at = datetime.now()
                await self._persist_horde(horde)

        # 11. Register capabilities in central registry
        if capabilities:
            for capability in capabilities:
                if capability not in self.system.legion_coordinator.capability_registry:
                    self.system.legion_coordinator.capability_registry[capability] = []
                self.system.legion_coordinator.capability_registry[capability].append(child_minion_id)

        # 12. Join channels if specified
        if channels and self.system.channel_manager:
            for channel_id in channels:
                try:
                    await self.system.channel_manager.add_member(channel_id, child_minion_id)
                except Exception as e:
                    # Log but don't fail spawn if channel join fails
                    coord_logger.warning(f"Failed to add {name} to channel {channel_id}: {e}")

        # 13. Send SPAWN notification to user
        spawn_comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=parent_overseer_id,
            from_minion_name=parent_session.name,
            to_user=True,
            summary=f"Spawned {name}",
            content=f"**{parent_session.name}** spawned minion **{name}** ({role})",
            comm_type=CommType.SPAWN,
            interrupt_priority=InterruptPriority.ROUTINE,
            visible_to_user=True
        )
        await self.system.comm_router.route_comm(spawn_comm)

        # 14. Start child session (make it active)
        await self.system.session_coordinator.start_session(child_minion_id)

        # 15. Broadcast project update to UI WebSocket (new session added)
        if self.system.ui_websocket_manager:
            project = await self.system.session_coordinator.project_manager.get_project(legion_id)
            if project:
                await self.system.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })
                coord_logger.debug(f"Broadcasted project_updated for legion {legion_id} after minion spawn")

        coord_logger.info(f"Minion {name} spawned by {parent_session.name} (parent={parent_overseer_id}, child={child_minion_id})")

        return child_minion_id

    async def dispose_minion(
        self,
        parent_overseer_id: str,
        child_minion_name: str
    ) -> Dict[str, any]:
        """
        Dispose of a child minion (terminate with cleanup).

        Args:
            parent_overseer_id: Session ID of parent overseer
            child_minion_name: Name of child to dispose (NOT session_id)

        Returns:
            dict: {"success": bool, "disposed_minion_id": str, "descendants_count": int}

        Raises:
            ValueError: If child not found
            PermissionError: If caller is not parent of child
        """
        from src.logging_config import get_logger
        from src.models.legion_models import Comm, CommType, InterruptPriority

        coord_logger = get_logger(__name__, "COORDINATOR")

        # 1. Get parent session
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        if not parent_session:
            raise ValueError(f"Parent overseer {parent_overseer_id} not found")

        # 2. Find child by name from parent's children
        child_session = None
        child_minion_id = None

        for child_id in (parent_session.child_minion_ids or []):
            session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
            if session and session.name == child_minion_name:
                child_session = session
                child_minion_id = child_id
                break

        if not child_session:
            # Get names of actual children for helpful error message
            child_names = []
            for cid in (parent_session.child_minion_ids or []):
                s = await self.system.session_coordinator.session_manager.get_session_info(cid)
                if s:
                    child_names.append(s.name)

            raise ValueError(
                f"No child minion named '{child_minion_name}' found. "
                f"You can only dispose minions you spawned. "
                f"Your children: {child_names if child_names else 'none'}"
            )

        # 3. Recursively dispose descendants first (depth-first disposal)
        descendants_disposed = 0
        if child_session.child_minion_ids:
            for grandchild_id in list(child_session.child_minion_ids):
                grandchild = await self.system.session_coordinator.session_manager.get_session_info(grandchild_id)
                if grandchild:
                    result = await self.dispose_minion(child_minion_id, grandchild.name)
                    descendants_disposed += result["descendants_count"] + 1

        # 4. Memory distillation (stub for now - Phase 7)
        # await self.system.memory_manager.distill_completion(child_minion_id)

        # 5. Knowledge transfer to parent (stub for now - Phase 7)
        # await self.system.memory_manager.transfer_knowledge(child_minion_id, parent_overseer_id)

        # 6. Terminate SDK session
        await self.system.session_coordinator.terminate_session(child_minion_id)

        # 7. Update parent: remove child from child_minion_ids
        parent_children = list(parent_session.child_minion_ids or [])
        if child_minion_id in parent_children:
            parent_children.remove(child_minion_id)
            await self.system.session_coordinator.session_manager.update_session(
                parent_overseer_id,
                child_minion_ids=parent_children
            )

        # If parent has no more children, mark as no longer overseer
        if not parent_children:
            await self.system.session_coordinator.session_manager.update_session(
                parent_overseer_id,
                is_overseer=False
            )

        # 8. Update horde: remove child from all_minion_ids
        horde_id = child_session.horde_id
        if horde_id:
            horde = self.hordes.get(horde_id)
            if horde and child_minion_id in horde.all_minion_ids:
                horde.all_minion_ids.remove(child_minion_id)
                horde.updated_at = datetime.now()
                await self._persist_horde(horde)

        # 9. Deregister from capability registry
        for capability, minion_ids in self.system.legion_coordinator.capability_registry.items():
            if child_minion_id in minion_ids:
                minion_ids.remove(child_minion_id)

        # 10. Send DISPOSE notification to user
        dispose_comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=parent_overseer_id,
            from_minion_name=parent_session.name,
            to_user=True,
            summary=f"Disposed {child_minion_name}",
            content=f"**{parent_session.name}** disposed of minion **{child_minion_name}**" + (
                f" (and {descendants_disposed} descendants)" if descendants_disposed > 0 else ""
            ),
            comm_type=CommType.DISPOSE,
            interrupt_priority=InterruptPriority.ROUTINE,
            visible_to_user=True
        )
        await self.system.comm_router.route_comm(dispose_comm)

        # 11. Broadcast project update to UI WebSocket (session removed)
        legion_id = parent_session.project_id
        if self.system.ui_websocket_manager:
            project = await self.system.session_coordinator.project_manager.get_project(legion_id)
            if project:
                await self.system.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })
                coord_logger.debug(f"Broadcasted project_updated for legion {legion_id} after minion disposal")

        coord_logger.info(f"Minion {child_minion_name} disposed by {parent_session.name} (disposed_id={child_minion_id}, descendants={descendants_disposed})")

        return {
            "success": True,
            "disposed_minion_id": child_minion_id,
            "disposed_minion_name": child_minion_name,
            "descendants_count": descendants_disposed
        }

    # TODO: Implement in Phase 5
    # - terminate_minion() - for forced termination
