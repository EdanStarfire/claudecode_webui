"""
OverseerController - Minion lifecycle management for Legion multi-agent system.

Responsibilities:
- Create minions (user-initiated and minion-spawned)
- Dispose minions (parent authority enforcement)
- Coordinate memory transfer on disposal
- Manage minion state transitions
"""

import asyncio
import uuid
from typing import TYPE_CHECKING

from src.models.permission_mode import PermissionMode
from src.session_config import SessionConfig
from src.slug_utils import slugify_name

if TYPE_CHECKING:
    from src.legion_system import LegionSystem


class OverseerController:
    """Manages minion lifecycle."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize OverseerController with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system
        self._reparent_lock = asyncio.Lock()

    async def create_minion_for_user(
        self,
        legion_id: str,
        name: str,
        config: SessionConfig,
        role: str = "",
        capabilities: list[str] | None = None,
    ) -> str:
        """
        Create a minion for the user (root overseer).

        Args:
            legion_id: ID of the legion (project) this minion belongs to
            name: Minion name (must be unique within legion)
            config: Bundled configuration (permission, tools, model, etc.)
            role: Optional role description (e.g., "Code Expert")
            capabilities: List of capability keywords for discovery

        Returns:
            str: The created minion's session_id

        Raises:
            ValueError: If name is not unique or project doesn't exist or is at capacity or invalid permission mode
        """
        # Validate project exists (all projects support minions - issue #313)
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if not project:
            raise ValueError(f"Project {legion_id} not found")

        # Validate permission_mode
        if config.permission_mode not in PermissionMode._value2member_map_:
            raise ValueError(f"Invalid permission_mode '{config.permission_mode}'. Must be one of: {', '.join(PermissionMode._value2member_map_)}")

        # Check minion limit (max 20 concurrent minions per legion)
        # Issue #349: All sessions are minions
        existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
        legion_minions = [s for s in existing_sessions if self._belongs_to_legion(s, legion_id)]

        default_max = self.system.default_max_minions
        if len(legion_minions) >= (project.max_concurrent_minions or default_max):
            raise ValueError(f"Legion {legion_id} has reached maximum concurrent minions ({project.max_concurrent_minions or default_max})")

        # Validate name uniqueness within legion (by slug - issue #546)
        new_slug = slugify_name(name)
        if any(m.slug == new_slug for m in legion_minions):
            raise ValueError(
                f"Minion name '{name}' (slug: '{new_slug}') conflicts with an existing minion in this legion. "
                f"Existing slugs: {[m.slug for m in legion_minions if m.slug]}"
            )

        # Generate session ID for the minion
        minion_id = str(uuid.uuid4())

        # Create session via SessionCoordinator
        await self.system.session_coordinator.create_session(
            session_id=minion_id,
            project_id=legion_id,
            config=config,
            name=name,
            role=role,
            capabilities=capabilities or [],
            can_spawn_minions=True,  # User-created minion can spawn by default
        )

        # Register capabilities in capability registry
        if capabilities:
            for capability in capabilities:
                try:
                    await self.system.legion_coordinator.register_capability(
                        minion_id=minion_id,
                        capability=capability,
                        expertise_score=None  # Uses minion's default score (0.5)
                    )
                except ValueError as e:
                    # Log error but don't fail minion creation if capability format is invalid
                    import logging
                    logging.warning(f"Failed to register capability '{capability}' for minion {minion_id}: {e}")

        return minion_id

    def _belongs_to_legion(self, session_info, legion_id: str) -> bool:
        """Check if a session belongs to a legion by checking project_id."""
        # Use project_id instead of working_directory since minions can have custom directories
        return session_info.project_id == legion_id

    async def spawn_minion(
        self,
        parent_overseer_id: str,
        name: str,
        role: str,
        config: SessionConfig,
        capabilities: list[str] | None = None,
    ) -> dict[str, any]:
        """
        Spawn a child minion autonomously by a parent overseer.

        This is called when a minion uses the spawn_minion MCP tool.

        Args:
            parent_overseer_id: Session ID of parent minion
            name: Unique name for child (provided by LLM)
            role: Role description for child
            config: Bundled configuration (permission, tools, model, etc.)
            capabilities: Capability keywords for discovery

        Returns:
            dict: {"minion_id": str}

        Raises:
            ValueError: If parent doesn't exist, name duplicate, or legion at capacity
            PermissionError: If parent is not a valid overseer
        """
        from src.logging_config import get_logger
        from src.models.legion_models import Comm, CommType, InterruptPriority

        coord_logger = get_logger('legion', category='OVERSEER')

        # 1. Get parent minion session
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        if not parent_session:
            raise ValueError(f"Parent overseer {parent_overseer_id} not found")

        # Issue #313: Allow any session with can_spawn_minions to spawn children
        # Check if parent can spawn minions
        if not parent_session.can_spawn_minions:
            raise PermissionError(f"Session {parent_overseer_id} cannot spawn minions (can_spawn_minions=False)")

        # 2. Get project (all projects support minions - issue #313)
        legion_id = parent_session.project_id
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if not project:
            raise ValueError(f"Project {legion_id} not found")

        # 3. Validate name uniqueness within legion (issue #349: all sessions are minions)
        existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
        legion_minions = [s for s in existing_sessions if self._belongs_to_legion(s, legion_id)]

        new_slug = slugify_name(name)
        if any(m.slug == new_slug for m in legion_minions):
            raise ValueError(
                f"Minion name '{name}' (slug: '{new_slug}') conflicts with an existing minion. "
                f"Choose a different name. Existing slugs: {[m.slug for m in legion_minions if m.slug]}"
            )

        # 4. Check minion limit
        default_max = self.system.default_max_minions
        if len(legion_minions) >= (project.max_concurrent_minions or default_max):
            raise ValueError(f"Legion at maximum capacity ({project.max_concurrent_minions or default_max} minions). Cannot spawn more.")

        # 5. Generate child minion ID
        child_minion_id = str(uuid.uuid4())

        # 6. Calculate overseer level (parent + 1)
        overseer_level = (parent_session.overseer_level or 0) + 1

        # 7. Create child session via SessionCoordinator
        await self.system.session_coordinator.create_session(
            session_id=child_minion_id,
            project_id=legion_id,
            config=config,
            name=name,
            role=role,
            capabilities=capabilities or [],
            parent_overseer_id=parent_overseer_id,
            overseer_level=overseer_level,
            can_spawn_minions=True,  # Child can spawn by default
        )

        # 8. Update parent: mark as overseer, add child to child_minion_ids
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

        # 9. Register capabilities in central registry
        if capabilities:
            for capability in capabilities:
                try:
                    await self.system.legion_coordinator.register_capability(
                        minion_id=child_minion_id,
                        capability=capability,
                        expertise_score=None  # Uses minion's default score (0.5)
                    )
                except ValueError as e:
                    # Log error but don't fail spawn if capability format is invalid
                    coord_logger.warning(f"Failed to register capability '{capability}' for spawned minion {child_minion_id}: {e}")

        # 10. Send SPAWN notification to user
        spawn_comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=parent_overseer_id,
            from_minion_name=parent_session.name,
            to_user=True,
            summary=f"Spawned {name}",
            content=f"**{parent_session.name}** spawned minion **{name}** ({role})",
            comm_type=CommType.SPAWN,
            interrupt_priority=InterruptPriority.NONE,
            visible_to_user=True
        )
        await self.system.comm_router.route_comm(spawn_comm)

        # 12. Register WebSocket message callback for child session
        # This ensures messages from the spawned minion broadcast to WebSocket clients
        if self.system.message_callback_registrar:
            self.system.message_callback_registrar(child_minion_id)
            coord_logger.info(f"Registered WebSocket message callback for spawned minion {child_minion_id} ({name})")
        else:
            coord_logger.warning(f"No message callback registrar available for minion {child_minion_id} - WebSocket streaming will not work!")

        # 13. Start child session with permission callback
        # Create permission callback using factory (same pattern as user-created minions)
        permission_callback = None
        if self.system.permission_callback_factory:
            permission_callback = self.system.permission_callback_factory(child_minion_id)
            coord_logger.info(f"Created permission callback for spawned minion {child_minion_id} ({name})")
        else:
            coord_logger.warning(f"No permission callback factory available for minion {child_minion_id} - permissions will not work!")

        await self.system.session_coordinator.start_session(
            child_minion_id,
            permission_callback=permission_callback
        )

        # 14. Broadcast project update to UI poll queue (new session added)
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if project:
            self.system.broadcast_ui_event({
                "type": "project_updated",
                "data": {"project": project.to_dict()}
            })
            coord_logger.debug(f"Appended project_updated for legion {legion_id} after minion spawn")

        coord_logger.info(f"Minion {name} spawned by {parent_session.name} (parent={parent_overseer_id}, child={child_minion_id})")

        return {
            "minion_id": child_minion_id
        }

    async def dispose_minion(
        self,
        parent_overseer_id: str,
        child_minion_name: str,
        delete_after_archive: bool = False
    ) -> dict[str, any]:
        """
        Dispose of a child minion (terminate with cleanup).

        Args:
            parent_overseer_id: Session ID of parent overseer
            child_minion_name: Name of child to dispose (NOT session_id)
            delete_after_archive: If True, delete the session after archiving.
                If False (default), terminate and archive but keep session and
                relationships intact (minion can be restarted and re-disposed).

        Returns:
            dict: {"success": bool, "disposed_minion_id": str, "descendants_count": int,
                   "deleted": bool}

        Raises:
            ValueError: If child not found
            PermissionError: If caller is not parent of child
        """
        from src.logging_config import get_logger
        from src.models.legion_models import Comm, CommType, InterruptPriority

        coord_logger = get_logger('legion', category='OVERSEER')

        # 1. Get parent session
        parent_session = await self.system.session_coordinator.session_manager.get_session_info(parent_overseer_id)
        if not parent_session:
            raise ValueError(f"Parent overseer {parent_overseer_id} not found")

        # 2. Find child by slug from parent's children (issue #546)
        child_session = None
        child_minion_id = None
        target_slug = slugify_name(child_minion_name)

        for child_id in (parent_session.child_minion_ids or []):
            session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
            if session and session.slug == target_slug:
                child_session = session
                child_minion_id = child_id
                break

        if not child_session:
            # Get slugs of actual children for helpful error message
            child_slugs = []
            for cid in (parent_session.child_minion_ids or []):
                s = await self.system.session_coordinator.session_manager.get_session_info(cid)
                if s:
                    child_slugs.append(s.slug or s.name)

            raise ValueError(
                f"No child minion with slug '{target_slug}' found. "
                f"You can only dispose minions you spawned. "
                f"Your children: {child_slugs if child_slugs else 'none'}"
            )

        # 3. Recursively dispose descendants first (depth-first disposal)
        # Propagate delete_after_archive to ensure consistent behavior for entire subtree
        descendants_disposed = 0
        if child_session.child_minion_ids:
            for grandchild_id in list(child_session.child_minion_ids):
                grandchild = await self.system.session_coordinator.session_manager.get_session_info(grandchild_id)
                if grandchild:
                    result = await self.dispose_minion(
                        child_minion_id, grandchild.name, delete_after_archive=delete_after_archive
                    )
                    descendants_disposed += result["descendants_count"] + 1

        # 4. Memory distillation (stub for now - Phase 7)
        # await self.system.memory_manager.distill_completion(child_minion_id)

        # 5. Knowledge transfer to parent (stub for now - Phase 7)
        # await self.system.memory_manager.transfer_knowledge(child_minion_id, parent_overseer_id)

        # 5b. Delete schedules for disposed minion (Issue #495)
        try:
            deleted = await self.system.scheduler_service.delete_schedules_for_minion(child_minion_id)
            if deleted:
                coord_logger.info(f"Deleted {deleted} schedules for disposed minion {child_minion_id}")
        except Exception as e:
            coord_logger.warning(f"Failed to delete schedules for minion {child_minion_id}: {e}")

        # 6. Terminate SDK session
        await self.system.session_coordinator.terminate_session(child_minion_id)

        # 7. Conditional cleanup based on delete_after_archive
        # Note: Archival is handled by delete_session for hard deletes (Issue #236)
        # For soft dispose, session files persist so no archive is needed
        deleted = False
        if delete_after_archive:
            # Full cleanup: remove relationships and delete session
            # 7a. Update parent: remove child from child_minion_ids
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

            # 7b. Deregister from capability registry
            for _capability, minion_ids in self.system.legion_coordinator.capability_registry.items():
                if child_minion_id in minion_ids:
                    minion_ids.remove(child_minion_id)

            # 7c. Delete the session completely (delete_session handles archival)
            try:
                await self.system.session_coordinator.delete_session(
                    child_minion_id, archive_reason="parent_initiated"
                )
                deleted = True
                coord_logger.info(f"Deleted minion session {child_minion_name} ({child_minion_id})")
            except Exception as e:
                coord_logger.warning(f"Failed to delete minion session {child_minion_id}: {e}")
        else:
            # Soft dispose: keep relationships intact, minion can be restarted.
            # Only deregister from capability registry (capabilities are session-specific).
            for _capability, minion_ids in self.system.legion_coordinator.capability_registry.items():
                if child_minion_id in minion_ids:
                    minion_ids.remove(child_minion_id)

        # 8. Send DISPOSE notification to user
        action_word = "deleted" if deleted else "disposed"
        dispose_comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=parent_overseer_id,
            from_minion_name=parent_session.name,
            to_user=True,
            summary=f"{'Deleted' if deleted else 'Disposed'} {child_minion_name}",
            content=f"**{parent_session.name}** {action_word} minion **{child_minion_name}**" + (
                f" (and {descendants_disposed} descendants)" if descendants_disposed > 0 else ""
            ),
            comm_type=CommType.DISPOSE,
            interrupt_priority=InterruptPriority.NONE,
            visible_to_user=True
        )
        await self.system.comm_router.route_comm(dispose_comm)

        # 9. Broadcast project update to UI poll queue (session state changed or removed)
        legion_id = parent_session.project_id
        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if project:
            self.system.broadcast_ui_event({
                "type": "project_updated",
                "data": {"project": project.to_dict()}
            })

        coord_logger.info(
            f"Minion {child_minion_name} {action_word} by {parent_session.name} "
            f"(disposed_id={child_minion_id}, descendants={descendants_disposed}, deleted={deleted})"
        )

        return {
            "success": True,
            "disposed_minion_id": child_minion_id,
            "disposed_minion_name": child_minion_name,
            "descendants_count": descendants_disposed,
            "deleted": deleted
        }

    async def _get_descendant_ids(self, session_id: str) -> set[str]:
        """Return the set of all descendant session IDs (BFS, excludes session_id itself)."""
        result: set[str] = set()
        session = await self.system.session_coordinator.session_manager.get_session_info(session_id)
        if not session or not session.child_minion_ids:
            return result
        queue = list(session.child_minion_ids)
        visited: set[str] = set()
        while queue:
            child_id = queue.pop()
            if child_id in visited:
                continue
            visited.add(child_id)
            result.add(child_id)
            child_session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
            if child_session and child_session.child_minion_ids:
                queue.extend(child_session.child_minion_ids)
        return result

    async def _recompute_levels(self, session_id: str, level: int) -> None:
        """Set overseer_level for session_id and all descendants (DFS)."""
        await self.system.session_coordinator.session_manager.update_session(
            session_id, overseer_level=level
        )
        session = await self.system.session_coordinator.session_manager.get_session_info(session_id)
        if session and session.child_minion_ids:
            for child_id in session.child_minion_ids:
                await self._recompute_levels(child_id, level + 1)

    async def reparent_minion(
        self,
        subject_id: str,
        new_parent_id: str | None,
        caller_id: str | None = None,
    ) -> dict:
        """
        Move a minion to a new parent within the same legion.

        Args:
            subject_id: Session ID of the minion to move
            new_parent_id: Session ID of the new parent, or None to promote to root
            caller_id: If set (MCP-initiated), full authority checks are applied;
                       None means user-initiated (skips MCP authority check)

        Returns:
            dict with success, subject_id, old_parent_id, new_parent_id, descendants_moved

        Raises:
            ValueError: On self-reparent, cross-legion, cycle, or MCP authority violation
        """
        from src.logging_config import get_logger
        from src.models.legion_models import Comm, CommType, InterruptPriority

        coord_logger = get_logger('legion', category='OVERSEER')

        if subject_id == new_parent_id:
            raise ValueError("Cannot reparent a minion to itself")

        subject = await self.system.session_coordinator.session_manager.get_session_info(subject_id)
        if not subject:
            raise ValueError(f"Subject minion {subject_id} not found")

        legion_id = subject.project_id
        old_parent_id = subject.parent_overseer_id

        if new_parent_id == old_parent_id:
            return {
                "success": True,
                "subject_id": subject_id,
                "old_parent_id": old_parent_id,
                "new_parent_id": new_parent_id,
                "descendants_moved": 0,
            }

        if new_parent_id is not None:
            new_parent = await self.system.session_coordinator.session_manager.get_session_info(new_parent_id)
            if not new_parent:
                raise ValueError(f"New parent minion {new_parent_id} not found")
            if new_parent.project_id != legion_id:
                raise ValueError("Cannot reparent to a minion in a different legion")

        subject_descendant_ids = await self._get_descendant_ids(subject_id)
        if new_parent_id is not None and new_parent_id in subject_descendant_ids:
            raise ValueError(
                "Cannot reparent: the new parent is a descendant of the subject (would create a cycle)"
            )

        if caller_id is not None:
            caller = await self.system.session_coordinator.session_manager.get_session_info(caller_id)
            if not caller:
                raise ValueError(f"Caller {caller_id} not found")

            caller_descendant_ids = await self._get_descendant_ids(caller_id)

            # Subject must be in caller's descendant closure
            if subject_id not in caller_descendant_ids:
                raise ValueError(
                    f"Authority violation: you can only reparent your own descendants. "
                    f"'{subject.name}' is not in your subtree."
                )

            # MCP cannot promote to root (user-only operation)
            if new_parent_id is None:
                raise ValueError(
                    "Only a user can promote a minion to root level. "
                    "Specify a valid minion name as new_parent_name."
                )

            # New parent must be caller itself or one of caller's descendants
            if new_parent_id != caller_id and new_parent_id not in caller_descendant_ids:
                np = await self.system.session_coordinator.session_manager.get_session_info(new_parent_id)
                np_name = np.name if np else new_parent_id
                raise ValueError(
                    f"Authority violation: the new parent must be yourself or one of your descendants. "
                    f"'{np_name}' is not in your subtree."
                )

        descendants_moved = len(subject_descendant_ids)

        # Serialize mutations: concurrent reparents on overlapping subtrees would corrupt
        # child_minion_ids lists. Reads (cycle check, authority check) are done pre-lock
        # so the lock window stays as narrow as possible.
        async with self._reparent_lock:
            if old_parent_id:
                old_parent = await self.system.session_coordinator.session_manager.get_session_info(old_parent_id)
                if old_parent:
                    old_parent_children = list(old_parent.child_minion_ids or [])
                    if subject_id in old_parent_children:
                        old_parent_children.remove(subject_id)
                        await self.system.session_coordinator.session_manager.update_session(
                            old_parent_id, child_minion_ids=old_parent_children
                        )
                    if not old_parent_children:
                        await self.system.session_coordinator.session_manager.update_session(
                            old_parent_id, is_overseer=False
                        )

            if new_parent_id:
                fresh_new_parent = await self.system.session_coordinator.session_manager.get_session_info(new_parent_id)
                if fresh_new_parent:
                    new_parent_children = list(fresh_new_parent.child_minion_ids or [])
                    if subject_id not in new_parent_children:
                        new_parent_children.append(subject_id)
                        await self.system.session_coordinator.session_manager.update_session(
                            new_parent_id, child_minion_ids=new_parent_children
                        )
                    if not fresh_new_parent.is_overseer:
                        await self.system.session_coordinator.session_manager.update_session(
                            new_parent_id, is_overseer=True
                        )

            await self.system.session_coordinator.session_manager.update_session(
                subject_id, parent_overseer_id=new_parent_id
            )

            if new_parent_id:
                np_session = await self.system.session_coordinator.session_manager.get_session_info(new_parent_id)
                new_level = (np_session.overseer_level if np_session else 0) + 1
            else:
                new_level = 0
            await self._recompute_levels(subject_id, new_level)

        # Post-lock: reads only — safe outside the critical section.
        caller_name = "user"
        from_minion_id = None
        from_user = True
        if caller_id is not None:
            caller_session = await self.system.session_coordinator.session_manager.get_session_info(caller_id)
            if caller_session:
                caller_name = caller_session.name
            from_minion_id = caller_id
            from_user = False

        old_parent_name = "root"
        if old_parent_id:
            op_session = await self.system.session_coordinator.session_manager.get_session_info(old_parent_id)
            if op_session:
                old_parent_name = op_session.name

        new_parent_label = "root"
        if new_parent_id:
            np_session = await self.system.session_coordinator.session_manager.get_session_info(new_parent_id)
            if np_session:
                new_parent_label = np_session.name

        extra = ""
        if descendants_moved:
            extra += f"\n\n{descendants_moved} descendant(s) moved with the subject."
        if getattr(subject, 'is_processing', False):
            extra += "\n\nNote: subject was actively processing at time of reparent."

        reparent_comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=from_user,
            from_minion_id=from_minion_id,
            from_minion_name=caller_name if from_minion_id else None,
            to_user=True,
            summary=f"Reparented {subject.name} → {new_parent_label}",
            content=(
                f"**{caller_name}** moved **{subject.name}** "
                f"from **{old_parent_name}** to **{new_parent_label}**{extra}"
            ),
            comm_type=CommType.REPARENT,
            interrupt_priority=InterruptPriority.NONE,
            visible_to_user=True,
        )
        await self.system.comm_router.route_comm(reparent_comm)

        project = await self.system.session_coordinator.project_manager.get_project(legion_id)
        if project:
            self.system.broadcast_ui_event({
                "type": "project_updated",
                "data": {"project": project.to_dict()}
            })

        coord_logger.info(
            f"Reparented {subject.name} ({subject_id}) from {old_parent_id} to {new_parent_id} "
            f"(caller={caller_name}, descendants_moved={descendants_moved})"
        )

        return {
            "success": True,
            "subject_id": subject_id,
            "old_parent_id": old_parent_id,
            "new_parent_id": new_parent_id,
            "descendants_moved": descendants_moved,
        }

    # TODO: Implement in Phase 5
    # - terminate_minion() - for forced termination
