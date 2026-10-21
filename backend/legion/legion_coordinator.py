"""
LegionCoordinator - Top-level orchestrator for Legion multi-agent system.

Responsibilities:
- Delegate legion/minion CRUD to ProjectManager/SessionManager
- Coordinate emergency halt/resume
- Provide fleet status
- Maintain central capability registry (MVP approach)
"""

import asyncio
from typing import TYPE_CHECKING, Optional

from backend.models.legion_models import SenderType
from backend.session_lifecycle import is_live, is_not_terminated
from shared.logging_config import get_logger

legion_logger = get_logger('legion', 'LEGION_COORDINATOR')

# Issue #2159 (stage 6c): mirrors frontend/src/components/project/ProjectOverview.vue's
# RESUME_MESSAGE constant — keep both in sync if this wording ever changes.
FLEET_RESUME_MESSAGE = (
    "Your session was stopped using an emergency stop feature, and is now being "
    "allowed to resume. If you had anything you were mid-progress on, continue it "
    "now. Otherwise, you are back online and ready to help."
)

if TYPE_CHECKING:
    from backend.legion_system import LegionSystem
    from backend.project_manager import ProjectInfo
    from backend.session_manager import SessionInfo


class LegionCoordinator:
    """Top-level orchestrator for legion lifecycle and fleet management."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionCoordinator with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Central capability registry (MVP approach)
        # Format: {capability_keyword: [(minion_id, expertise_score), ...]}
        # Example: {"python": [("minion-123", 0.9), ("minion-456", 0.6)]}
        self.capability_registry: dict[str, list[tuple]] = {}

    @property
    def project_manager(self):
        """Access ProjectManager through SessionCoordinator."""
        return self.system.session_port.project_manager

    @property
    def session_manager(self):
        """Access SessionManager through SessionCoordinator."""
        return self.system.session_port.session_manager

    async def get_minion_info(self, minion_id: str) -> Optional['SessionInfo']:
        """
        Get minion (session) by ID.

        Issue #349: All sessions are minions, so this just returns the session.

        Args:
            minion_id: Session UUID

        Returns:
            SessionInfo if found, None otherwise
        """
        return await self.session_manager.get_session_info(minion_id)

    async def get_minion_by_name(self, name: str) -> Optional['SessionInfo']:
        """
        Get minion by name or slug - GLOBAL search across all legions.

        Matches slug first, then falls back to display name for backward compat.

        WARNING: This method searches globally and may return minions from other legions.
        For legion-scoped lookups, use get_minion_by_name_in_legion() instead.

        Issue #349: All sessions are minions.
        Issue #546: Slug-first matching.

        Args:
            name: Minion slug or display name

        Returns:
            SessionInfo if found, None otherwise
        """
        sessions = await self.session_manager.list_sessions()
        # Slug-first matching
        for session in sessions:
            if session.slug and session.slug == name:
                return session
        # Fallback to display name for backward compat
        for session in sessions:
            if session.name == name:
                return session
        return None

    async def get_minion_by_name_in_legion(self, legion_id: str, name: str) -> Optional['SessionInfo']:
        """
        Get minion by name within a specific legion (case-sensitive).

        This method enforces legion boundaries by only searching sessions
        that belong to the specified legion (where project_id == legion_id).

        Issue #349: All sessions are minions.

        Args:
            legion_id: Legion UUID (project_id)
            name: Minion name

        Returns:
            SessionInfo if found within legion, None otherwise
        """
        # Get all sessions in this legion (project_id == legion_id)
        legion = await self.get_legion(legion_id)
        if not legion:
            return None

        # Issue #546: Slug-first matching, then fallback to display name
        for session_id in legion.session_ids:
            session = await self.session_manager.get_session_info(session_id)
            if session and session.slug and session.slug == name:
                return session

        # Fallback to display name for backward compat
        for session_id in legion.session_ids:
            session = await self.session_manager.get_session_info(session_id)
            if session and session.name == name:
                return session

        return None

    async def get_legion(self, legion_id: str) -> Optional['ProjectInfo']:
        """
        Get project by ID (all projects support Legion capabilities).

        Args:
            legion_id: Project UUID

        Returns:
            ProjectInfo if found, None otherwise
        """
        return await self.project_manager.get_project(legion_id)

    async def list_legions(self) -> list['ProjectInfo']:
        """
        List all projects (all projects support Legion capabilities).

        Note: For backward compatibility, this method still exists but returns
        all projects. Use project_manager.list_projects() directly for clarity.

        Returns:
            List of all ProjectInfo objects
        """
        return await self.project_manager.list_projects()

    async def _create_legion_directories(self, legion_id: str) -> None:
        """
        Create directory structure for legion-specific data.

        Creates:
        - data/legions/{legion_id}/

        Note: Minion data is stored in data/sessions/{session_id}/ via SessionManager

        Args:
            legion_id: Legion UUID (same as project_id)
        """
        base_path = self.system.session_port.data_dir / "legions" / legion_id
        base_path.mkdir(parents=True, exist_ok=True)

    async def assemble_minion_hierarchy(self, legion_id: str) -> dict:
        """
        Assemble complete minion hierarchy with user at root.

        Returns hierarchy structure with:
        - User entry at root
        - All minions in tree structure (parent-child relationships)
        - Current state for each minion

        Args:
            legion_id: Legion UUID (project_id)

        Returns:
            Dict with hierarchy structure:
            {
                "id": "user",
                "type": "user",
                "name": "User (you)",
                "children": [<minion nodes>]
            }
        """
        # Get all sessions for this legion/project
        all_sessions = await self.session_manager.list_sessions()
        project_sessions = [s for s in all_sessions if s.project_id == legion_id]

        # Issue #349: All sessions are minions - build full hierarchy
        session_map = {}
        for session in project_sessions:
            # Issue #349: All sessions are minions
            session_type = "minion"

            session_data = {
                "id": session.session_id,
                "type": session_type,
                "name": session.name,
                "state": session.state.value if hasattr(session.state, 'value') else str(session.state),
                "is_overseer": session.is_overseer or False,
                "is_processing": session.is_processing or False,
                # Latest message tracking (issue #291)
                "latest_message": session.latest_message,
                "latest_message_type": session.latest_message_type,
                "latest_message_time": session.latest_message_time.isoformat() if session.latest_message_time else None,
                # Spawn/dispose activity fallback (issue #2259)
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "disposed_at": session.disposed_at.isoformat() if session.disposed_at else None,
                "children": []
            }
            session_map[session.session_id] = session_data

        # Build tree by linking children to parents
        root_sessions = []
        for session in project_sessions:
            session_data = session_map[session.session_id]

            if session.parent_overseer_id is None:
                # User-created session (root level) - always include (all sessions are minions)
                root_sessions.append(session_data)
            else:
                # Child session - add to parent's children
                parent = session_map.get(session.parent_overseer_id)
                if parent:
                    parent["children"].append(session_data)
                else:
                    # Parent not in project (shouldn't happen) - add as root
                    root_sessions.append(session_data)

        # Build user entry with children
        user_entry = {
            "id": "user",
            "type": "user",
            "name": "User (you)",
            "children": root_sessions
        }

        return user_entry

    async def register_capability(
        self,
        minion_id: str,
        capability: str,
        expertise_score: float | None = None
    ) -> None:
        """
        Register a capability for a minion in the central capability registry.

        Capabilities must be lowercase with underscores between words (e.g., "rest_api", "postgresql").
        Varied case input is automatically lowercased. Improperly formatted capabilities
        (special characters other than underscore) are rejected.

        If the same minion_id + capability combination already exists, updates the expertise score.

        Args:
            minion_id: Minion session ID
            capability: Capability keyword (will be normalized to lowercase)
            expertise_score: Expertise level (0.0-1.0). If None, uses minion's default score.

        Raises:
            ValueError: If capability contains invalid characters or minion doesn't exist
        """
        # Validate minion exists
        minion = await self.get_minion_info(minion_id)
        if not minion:
            raise ValueError(f"Minion {minion_id} does not exist")

        # Normalize capability to lowercase
        normalized_capability = capability.lower()

        # Validate capability format (only lowercase letters, numbers, underscores)
        import re
        if not re.match(r'^[a-z0-9_]+$', normalized_capability):
            raise ValueError(
                f"Invalid capability format: '{capability}'. "
                "Capabilities must contain only lowercase letters, numbers, and underscores (e.g., 'rest_api', 'postgresql')"
            )

        # Use minion's expertise_score if not provided
        if expertise_score is None:
            expertise_score = minion.expertise_score

        # Validate expertise_score range
        if not (0.0 <= expertise_score <= 1.0):
            raise ValueError(f"Expertise score must be between 0.0 and 1.0, got {expertise_score}")

        # Initialize list for this capability if it doesn't exist
        if normalized_capability not in self.capability_registry:
            self.capability_registry[normalized_capability] = []

        # Check if minion already has this capability registered
        existing_entries = self.capability_registry[normalized_capability]
        is_update = False
        for i, (existing_minion_id, _) in enumerate(existing_entries):
            if existing_minion_id == minion_id:
                # Update existing entry
                existing_entries[i] = (minion_id, expertise_score)
                is_update = True
                break

        if not is_update:
            # Add new entry to registry
            self.capability_registry[normalized_capability].append((minion_id, expertise_score))

            # Add to minion's capabilities list if not already present
            if normalized_capability not in minion.capabilities:
                minion.capabilities.append(normalized_capability)
                # Persist to state.json
                await self.session_manager.update_session(minion_id)

    def unregister_minion_capabilities(self, minion_id: str) -> None:
        """
        Remove all capabilities for a minion from the registry.
        Called when a minion/session is deleted to clean up stale references.

        Args:
            minion_id: ID of the minion being deleted
        """
        # Iterate through all capabilities and remove entries for this minion
        for capability, entries in list(self.capability_registry.items()):
            # Filter out entries for this minion
            filtered_entries = [(mid, score) for mid, score in entries if mid != minion_id]

            if filtered_entries:
                # Update the registry with filtered list
                self.capability_registry[capability] = filtered_entries
            else:
                # If no entries remain, remove the capability key entirely
                del self.capability_registry[capability]

    async def rebuild_capability_registry(self) -> None:
        """
        Rebuild capability registry from persisted SessionInfo data.
        Called on startup to restore in-memory registry from disk.
        """
        # Clear existing registry
        self.capability_registry.clear()

        # Get all sessions
        all_sessions = await self.session_manager.list_sessions()

        # Filter to minions with capabilities
        # Issue #349: All sessions are minions - just check for capabilities
        minions_with_capabilities = [
            session for session in all_sessions
            if session.capabilities
        ]

        # Rebuild registry from persisted capabilities
        for minion in minions_with_capabilities:
            for capability in minion.capabilities:
                # Use minion's default expertise_score (0.5) for capabilities without explicit scores
                # In the future, we could store per-capability scores in SessionInfo
                expertise_score = minion.expertise_score if minion.expertise_score is not None else 0.5

                # Register capability
                if capability not in self.capability_registry:
                    self.capability_registry[capability] = []

                # Add entry if not already present
                if not any(mid == minion.session_id for mid, _ in self.capability_registry[capability]):
                    self.capability_registry[capability].append((minion.session_id, expertise_score))

        # Log rebuild summary
        total_capabilities = len(self.capability_registry)
        total_entries = sum(len(entries) for entries in self.capability_registry.values())
        if total_capabilities > 0:
            legion_logger.info(
                f"Rebuilt capability registry: {total_capabilities} capabilities, "
                f"{total_entries} total entries from {len(minions_with_capabilities)} minions"
            )

    async def search_capability_registry(
        self,
        keyword: str,
        legion_id: str | None = None
    ) -> list[tuple]:
        """
        Search capability registry for minions with matching capabilities.

        Performs case-insensitive substring matching. Returns ranked results by
        expertise score (highest first). Minions with 0.0 or None expertise scores
        are excluded from results.

        Args:
            keyword: Search keyword (case-insensitive substring match)
            legion_id: Optional legion filter (only return minions from this legion)

        Returns:
            List of tuples: [(minion_id, expertise_score, capability_matched), ...]
            Sorted by expertise_score descending

        Raises:
            ValueError: If keyword is empty or only whitespace
        """
        # Validate keyword is not empty
        keyword_trimmed = keyword.strip()
        if not keyword_trimmed:
            raise ValueError("Search keyword cannot be empty or only whitespace")

        # Normalize keyword to lowercase for case-insensitive search
        search_term = keyword_trimmed.lower()

        # Find all capabilities that contain the search term (substring match)
        matching_results = []
        for capability, entries in self.capability_registry.items():
            if search_term in capability:
                # This capability matches - add all minions with non-zero scores
                for minion_id, expertise_score in entries:
                    # Skip minions with zero or None expertise
                    if expertise_score is None or expertise_score == 0.0:
                        continue

                    # If legion filter is specified, only include minions from that legion
                    if legion_id:
                        minion = await self.get_minion_info(minion_id)
                        if not minion or minion.project_id != legion_id:
                            continue

                    matching_results.append((minion_id, expertise_score, capability))

        # Sort by expertise_score descending (highest first)
        matching_results.sort(key=lambda x: x[1], reverse=True)

        return matching_results

    async def emergency_halt_all(self, legion_id: str) -> dict:
        """
        Emergency halt all non-terminated sessions in a project by terminating them.

        Sessions already in TERMINATED state are silently skipped — they are not
        counted in total_sessions, not added to stopped_session_ids, and not
        reported as failures. All other states (CREATED, ACTIVE, PAUSED, etc.)
        are terminated in parallel.

        Args:
            legion_id: Legion UUID (project_id)

        Returns:
            Dict with operation results:
            {
                "stopped_session_ids": [str, ...],
                "failed_sessions": [(session_id, error_msg), ...],
                "total_sessions": int  # excludes pre-TERMINATED sessions
            }
        """
        # Get all sessions in this project (issue #349: all sessions are minions)
        all_sessions = await self.session_manager.list_sessions()
        sessions = [s for s in all_sessions if s.project_id == legion_id]
        # Skip sessions already in TERMINATED state — they need no action
        sessions = [s for s in sessions if is_not_terminated(s.state)]

        # Issue #2159 (stage 6c): snapshot is_processing BEFORE terminating — a
        # session mid-task when halted needs a catch-up message on resume, not a
        # bare cold start. terminate_session() resets is_processing itself, so this
        # has to be read first.
        was_processing = {s.session_id: bool(s.is_processing) for s in sessions}

        # Terminate all sessions in parallel
        terminate_tasks = [
            self.system.session_port.terminate_session(s.session_id)
            for s in sessions
        ]
        results = await asyncio.gather(*terminate_tasks, return_exceptions=True)

        stopped_session_ids = []
        failed_sessions = []
        for i, result in enumerate(results):
            session_id = sessions[i].session_id
            if isinstance(result, Exception):
                failed_sessions.append((session_id, str(result)))
            elif result is True:
                stopped_session_ids.append(session_id)
            else:
                # terminate_session() returned False: session not found or already cleaned up
                failed_sessions.append((session_id, "terminate_session returned False"))

        if stopped_session_ids:
            processing_ids = [sid for sid in stopped_session_ids if was_processing.get(sid)]
            await self.project_manager.record_fleet_stopped(legion_id, stopped_session_ids, processing_ids)

        return {
            "stopped_session_ids": stopped_session_ids,
            "failed_sessions": failed_sessions,
            "total_sessions": len(sessions),
        }

    async def resume_all(self, legion_id: str) -> dict:
        """
        Resume exactly the sessions this project's "Stop All"/"Stop Selected"
        halted (issue #2159 stage 6c's persisted fleet snapshot — see
        ProjectManager.get_fleet_snapshot()), not "every deliverable minion in the
        project": a plain state scan has no relationship to what Stop All actually
        stopped, would resume sessions nothing ever halted, and (pre-6c) only lived
        in frontend sessionStorage, so this never survived a browser reload or a
        Backend restart — the bug this stage fixes (T3).

        A session that was mid-task when halted (the processing subset of the
        snapshot) gets a catch-up message instead of a bare cold start, since it
        has no other way to learn what happened — same distinction the issue #1613
        frontend orchestration this backs now makes on the backend side too.

        Args:
            legion_id: Legion UUID (project_id)

        Returns:
            Dict with operation results:
            {
                "resumed_count": int,
                "failed_minions": [(minion_id, error_msg), ...],
                "total_minions": int
            }
        """
        snapshot = await self.project_manager.get_fleet_snapshot(legion_id)
        if not snapshot:
            return {"resumed_count": 0, "failed_minions": [], "total_minions": 0}

        stopped_ids = snapshot["stopped_session_ids"]
        processing_ids = set(snapshot["processing_session_ids"])

        resolved_ids = []
        failed_minions = []

        for session_id in stopped_ids:
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info is None:
                # Gone entirely — nothing to resume, just drop the stale entry below.
                resolved_ids.append(session_id)
                continue
            if is_live(session_info.state):
                # Already brought back some other way (e.g. started individually) —
                # just clear the now-stale snapshot entry.
                resolved_ids.append(session_id)
                continue
            try:
                if session_id in processing_ids:
                    # reset_session=False: resuming must not clear conversation
                    # history. inject_timestamp=False: this catch-up message is
                    # system-generated, not a user message (issue #1779) — same
                    # opt-out as comm_router.py's comms.
                    await self.system.session_port.enqueue_message(
                        session_id,
                        FLEET_RESUME_MESSAGE,
                        reset_session=False,
                        inject_timestamp=False,
                        sender_type=SenderType.SYSTEM,
                    )
                else:
                    # Mirrors queue_processor.py's own auto-start call: a
                    # permission_callback is required for the resumed session to
                    # actually be able to request/respond to tool permissions.
                    perm_callback = None
                    factory = self.system.permission_callback_factory
                    if factory:
                        perm_callback = factory(session_id)
                    await self.system.session_port.start_session(session_id, perm_callback)
                resolved_ids.append(session_id)
            except Exception as e:
                failed_minions.append((session_id, str(e)))

        if resolved_ids:
            await self.project_manager.clear_fleet_stopped(legion_id, resolved_ids)

        return {
            "resumed_count": len(resolved_ids),
            "failed_minions": failed_minions,
            "total_minions": len(stopped_ids),
        }

    # TODO: Implement additional legion management methods in later phases
    # - delete_legion()
    # - get_fleet_status()
