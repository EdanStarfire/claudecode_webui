"""
OverseerController - Minion lifecycle management for Legion multi-agent system.

Responsibilities:
- Create minions (user-initiated and minion-spawned)
- Dispose minions (parent authority enforcement)
- Coordinate memory transfer on disposal
- Manage minion state transitions
"""

import uuid
from typing import TYPE_CHECKING

from src.session_manager import slugify_name

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

    async def create_minion_for_user(
        self,
        legion_id: str,
        name: str,
        role: str = "",
        system_prompt: str = "",
        override_system_prompt: bool = False,
        capabilities: list[str] | None = None,
        permission_mode: str = "default",
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        working_directory: str | None = None,
        model: str | None = None,
        sandbox_enabled: bool = False,
        sandbox_config: dict | None = None,
        setting_sources: list[str] | None = None,
        cli_path: str | None = None,
        # Docker session isolation (issue #496)
        docker_enabled: bool = False,
        docker_image: str | None = None,
        docker_extra_mounts: list[str] | None = None,
        # Thinking and effort configuration (issue #540)
        thinking_mode: str | None = None,
        thinking_budget_tokens: int | None = None,
        effort: str | None = None,
    ) -> str:
        """
        Create a minion for the user (root overseer).

        Args:
            legion_id: ID of the legion (project) this minion belongs to
            name: Minion name (must be unique within legion)
            role: Optional role description (e.g., "Code Expert")
            system_prompt: Instructions/context for the minion (appended to Claude Code preset unless override is True)
            override_system_prompt: If True, use only custom prompt (no Claude Code preset or legion guide)
            capabilities: List of capability keywords for discovery
            permission_mode: Permission mode (default, acceptEdits, plan, bypassPermissions)
            allowed_tools: List of pre-authorized tools (e.g., ["edit", "read", "bash"])
            working_directory: Optional custom working directory (defaults to project directory)
            model: Model selection (sonnet, opus, haiku, opusplan)
            sandbox_enabled: Enable OS-level sandboxing (issue #319)
            sandbox_config: Optional sandbox configuration settings (issue #458)
            setting_sources: Which settings files to load (issue #36)

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
        valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
        if permission_mode not in valid_modes:
            raise ValueError(f"Invalid permission_mode '{permission_mode}'. Must be one of: {', '.join(valid_modes)}")

        # Check minion limit (max 20 concurrent minions per legion)
        # Issue #349: All sessions are minions
        existing_sessions = await self.system.session_coordinator.session_manager.list_sessions()
        legion_minions = [s for s in existing_sessions if self._belongs_to_legion(s, legion_id)]

        if len(legion_minions) >= (project.max_concurrent_minions or 20):
            raise ValueError(f"Legion {legion_id} has reached maximum concurrent minions ({project.max_concurrent_minions or 20})")

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
        # Convert None to empty list for allowed_tools (safe default: no pre-authorized tools)
        await self.system.session_coordinator.create_session(
            session_id=minion_id,
            project_id=legion_id,
            name=name,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools if allowed_tools is not None else [],
            disallowed_tools=disallowed_tools if disallowed_tools is not None else [],
            system_prompt=system_prompt,
            override_system_prompt=override_system_prompt,
            working_directory=working_directory,
            model=model,  # Model selection (sonnet, opus, haiku, opusplan)
            # Multi-agent fields (issue #313, #349)
            role=role,
            capabilities=capabilities or [],
            can_spawn_minions=True,  # User-created minion can spawn by default
            # Sandbox mode (issue #319)
            sandbox_enabled=sandbox_enabled,
            sandbox_config=sandbox_config,
            # Settings sources (issue #36)
            setting_sources=setting_sources,
            # CLI path override (issue #489)
            cli_path=cli_path,
            # Docker session isolation (issue #496)
            docker_enabled=docker_enabled,
            docker_image=docker_image,
            docker_extra_mounts=docker_extra_mounts,
            # Thinking and effort configuration (issue #540)
            thinking_mode=thinking_mode,
            thinking_budget_tokens=thinking_budget_tokens,
            effort=effort,
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
        system_prompt: str,
        capabilities: list[str] | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        working_directory: str | None = None,
        sandbox_enabled: bool = False,
        model: str | None = None,
        override_system_prompt: bool = False,
        cli_path: str | None = None,
        # Docker session isolation (issue #496)
        docker_enabled: bool = False,
        docker_image: str | None = None,
        docker_extra_mounts: list[str] | None = None,
    ) -> dict[str, any]:
        """
        Spawn a child minion autonomously by a parent overseer.

        This is called when a minion uses the spawn_minion MCP tool.

        Args:
            parent_overseer_id: Session ID of parent minion
            name: Unique name for child (provided by LLM)
            role: Role description for child
            system_prompt: System prompt/instructions for child (appended to Claude Code preset)
            capabilities: Capability keywords for discovery
            permission_mode: Permission mode (from template or safe default)
            allowed_tools: List of allowed tools (from template or safe default)
            working_directory: Optional custom working directory (defaults to parent's directory)
            sandbox_enabled: Enable OS-level sandboxing (issue #319)

        Returns:
            dict: {
                "minion_id": str  # Child minion's session_id
            }

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
        if len(legion_minions) >= (project.max_concurrent_minions or 20):
            raise ValueError(f"Legion at maximum capacity ({project.max_concurrent_minions or 20} minions). Cannot spawn more.")

        # 5. Generate child minion ID
        child_minion_id = str(uuid.uuid4())

        # 6. Calculate overseer level (parent + 1)
        overseer_level = (parent_session.overseer_level or 0) + 1

        # 7. Create child session via SessionCoordinator
        # Use provided permission_mode/allowed_tools (from template or safe defaults)
        await self.system.session_coordinator.create_session(
            session_id=child_minion_id,
            project_id=legion_id,
            name=name,
            permission_mode=permission_mode or "default",
            allowed_tools=allowed_tools,  # Now uses consistent parameter name
            disallowed_tools=disallowed_tools,
            system_prompt=system_prompt,
            override_system_prompt=override_system_prompt,
            model=model,
            working_directory=working_directory,  # Custom working directory for git worktrees/multi-repo
            # Multi-agent fields (issue #313, #349)
            role=role,
            capabilities=capabilities or [],
            parent_overseer_id=parent_overseer_id,
            overseer_level=overseer_level,
            can_spawn_minions=True,  # Child can spawn by default
            # Sandbox mode (issue #319)
            sandbox_enabled=sandbox_enabled,
            # CLI path override (issue #489)
            cli_path=cli_path,
            # Docker session isolation (issue #496)
            docker_enabled=docker_enabled,
            docker_image=docker_image,
            docker_extra_mounts=docker_extra_mounts,
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

        # 14. Broadcast project update to UI WebSocket (new session added)
        if self.system.ui_websocket_manager:
            project = await self.system.session_coordinator.project_manager.get_project(legion_id)
            if project:
                await self.system.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })
                coord_logger.debug(f"Broadcasted project_updated for legion {legion_id} after minion spawn")

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

        for child_id in (parent_session.child_minion_ids or []):
            session = await self.system.session_coordinator.session_manager.get_session_info(child_id)
            if session and session.slug == child_minion_name:
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
                f"No child minion with slug '{child_minion_name}' found. "
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

        # 5b. Cancel active schedules for disposed minion (Issue #495)
        try:
            cancelled = await self.system.scheduler_service.cancel_schedules_for_minion(child_minion_id)
            if cancelled:
                coord_logger.info(f"Cancelled {cancelled} schedules for disposed minion {child_minion_id}")
        except Exception as e:
            coord_logger.warning(f"Failed to cancel schedules for minion {child_minion_id}: {e}")

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
            # Soft dispose: keep relationships intact, minion can be restarted
            # Only deregister from capability registry (capabilities are session-specific)
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

        # 9. Broadcast project update to UI WebSocket (session state changed or removed)
        legion_id = parent_session.project_id
        if self.system.ui_websocket_manager:
            project = await self.system.session_coordinator.project_manager.get_project(legion_id)
            if project:
                await self.system.ui_websocket_manager.broadcast_to_all({
                    "type": "project_updated",
                    "data": {"project": project.to_dict()}
                })
                coord_logger.debug(f"Broadcasted project_updated for legion {legion_id} after minion disposal")

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

    # TODO: Implement in Phase 5
    # - terminate_minion() - for forced termination
