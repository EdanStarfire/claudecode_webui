"""
LegionCoordinator - Top-level orchestrator for Legion multi-agent system.

Responsibilities:
- Delegate legion/minion CRUD to ProjectManager/SessionManager
- Track hordes and channels (grouping mechanisms)
- Coordinate emergency halt/resume
- Provide fleet status
- Maintain central capability registry (MVP approach)
"""

import asyncio
import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.legion_system import LegionSystem
    from src.project_manager import ProjectInfo
    from src.session_manager import SessionInfo


class LegionCoordinator:
    """Top-level orchestrator for legion lifecycle and fleet management."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize LegionCoordinator with LegionSystem.

        Args:
            system: LegionSystem instance for accessing other components
        """
        self.system = system

        # Multi-agent grouping state (hordes and channels are not duplicated elsewhere)
        self.hordes: dict = {}   # Dict[str, Horde]
        self.channels: dict = {} # Dict[str, Channel]

        # Central capability registry (MVP approach)
        # Format: {capability_keyword: [(minion_id, expertise_score), ...]}
        # Example: {"python": [("minion-123", 0.9), ("minion-456", 0.6)]}
        self.capability_registry: dict[str, list[tuple]] = {}

        # Emergency halt state
        self.emergency_halt_active: dict[str, bool] = {}  # {legion_id: bool}

        # Queued comms during halt, organized by recipient minion
        # {legion_id: {minion_id: [Comm, Comm, ...], ...}}
        self.halted_comm_queues: dict[str, dict[str, list]] = {}

        # Queue size limit per legion
        self.MAX_QUEUED_COMMS = 10000

    @property
    def project_manager(self):
        """Access ProjectManager through SessionCoordinator."""
        return self.system.session_coordinator.project_manager

    @property
    def session_manager(self):
        """Access SessionManager through SessionCoordinator."""
        return self.system.session_coordinator.session_manager

    async def get_minion_info(self, minion_id: str) -> Optional['SessionInfo']:
        """
        Get minion (session with is_minion=True) by ID.

        Args:
            minion_id: Session UUID

        Returns:
            SessionInfo if found and is_minion=True, None otherwise
        """
        session = await self.session_manager.get_session_info(minion_id)
        if session and session.is_minion:
            return session
        return None

    async def get_minion_by_name(self, name: str) -> Optional['SessionInfo']:
        """
        Get minion by name (case-sensitive) - GLOBAL search across all legions.

        WARNING: This method searches globally and may return minions from other legions.
        For legion-scoped lookups, use get_minion_by_name_in_legion() instead.

        Args:
            name: Minion name

        Returns:
            SessionInfo if found and is_minion=True, None otherwise
        """
        sessions = await self.session_manager.list_sessions()
        for session in sessions:
            if session.is_minion and session.name == name:
                return session
        return None

    async def get_minion_by_name_in_legion(self, legion_id: str, name: str) -> Optional['SessionInfo']:
        """
        Get minion by name within a specific legion (case-sensitive).

        This method enforces legion boundaries by only searching minions
        that belong to the specified legion (where project_id == legion_id).

        Args:
            legion_id: Legion UUID (project_id)
            name: Minion name

        Returns:
            SessionInfo if found within legion and is_minion=True, None otherwise
        """
        # Get all sessions in this legion (project_id == legion_id)
        legion = await self.get_legion(legion_id)
        if not legion:
            return None

        # Search through legion's sessions for matching minion name
        for session_id in legion.session_ids:
            session = await self.session_manager.get_session_info(session_id)
            if session and session.is_minion and session.name == name:
                return session

        return None

    async def get_channel_by_name(self, legion_id: str, channel_name: str) -> Optional['Channel']:
        """
        Get channel by name within a specific legion (case-sensitive).

        Args:
            legion_id: Legion UUID
            channel_name: Channel name

        Returns:
            Channel if found, None otherwise
        """
        channels = await self.system.channel_manager.list_channels(legion_id)
        for channel in channels:
            if channel.name == channel_name:
                return channel
        return None

    async def get_legion(self, legion_id: str) -> Optional['ProjectInfo']:
        """
        Get legion (project with is_multi_agent=True) by ID.

        Args:
            legion_id: Project UUID

        Returns:
            ProjectInfo if found and is_multi_agent=True, None otherwise
        """
        project = await self.project_manager.get_project(legion_id)
        if project and project.is_multi_agent:
            return project
        return None

    async def list_legions(self) -> list['ProjectInfo']:
        """
        List all legions (projects with is_multi_agent=True).

        Returns:
            List of all ProjectInfo objects where is_multi_agent=True
        """
        all_projects = await self.project_manager.list_projects()
        return [p for p in all_projects if p.is_multi_agent]

    async def _create_legion_directories(self, legion_id: str) -> None:
        """
        Create directory structure for legion-specific data (hordes/channels).

        Creates:
        - data/legions/{legion_id}/
        - data/legions/{legion_id}/channels/
        - data/legions/{legion_id}/hordes/

        Note: Minion data is stored in data/sessions/{session_id}/ via SessionManager

        Args:
            legion_id: Legion UUID (same as project_id)
        """
        base_path = self.system.session_coordinator.data_dir / "legions" / legion_id

        # Create subdirectories for legion-specific grouping data
        (base_path / "channels").mkdir(parents=True, exist_ok=True)
        (base_path / "hordes").mkdir(parents=True, exist_ok=True)

    async def assemble_minion_hierarchy(self, legion_id: str) -> dict:
        """
        Assemble complete minion hierarchy with user at root.

        Returns hierarchy structure with:
        - User entry at root with last outgoing comm
        - All minions in tree structure (parent-child relationships)
        - Last outgoing comm for each minion
        - Current state for each minion

        Args:
            legion_id: Legion UUID (project_id)

        Returns:
            Dict with hierarchy structure:
            {
                "id": "user",
                "type": "user",
                "name": "User (you)",
                "last_comm": {...} or None,
                "children": [<minion nodes>]
            }
        """
        # Get all minions for this legion
        all_sessions = await self.session_manager.list_sessions()
        minions = [s for s in all_sessions if s.is_minion and s.project_id == legion_id]

        # Get user's last outgoing comm
        user_last_comm = await self._get_last_outgoing_comm(legion_id, from_user=True)

        # Build minion tree structure
        minion_map = {}
        for minion in minions:
            minion_data = {
                "id": minion.session_id,
                "type": "minion",
                "name": minion.name,
                "state": minion.state.value if hasattr(minion.state, 'value') else str(minion.state),
                "is_overseer": minion.is_overseer or False,
                "is_processing": minion.is_processing or False,
                "last_comm": await self._get_last_outgoing_comm(legion_id, from_minion_id=minion.session_id),
                "children": []
            }
            minion_map[minion.session_id] = minion_data

        # Build tree by linking children to parents
        root_minions = []
        for minion in minions:
            minion_data = minion_map[minion.session_id]

            if minion.parent_overseer_id is None:
                # User-spawned minion (root level)
                root_minions.append(minion_data)
            else:
                # Child minion - add to parent's children
                parent = minion_map.get(minion.parent_overseer_id)
                if parent:
                    parent["children"].append(minion_data)

        # Build user entry with children
        user_entry = {
            "id": "user",
            "type": "user",
            "name": "User (you)",
            "last_comm": user_last_comm,
            "children": root_minions
        }

        return user_entry

    async def _get_last_outgoing_comm(
        self,
        legion_id: str,
        from_user: bool = False,
        from_minion_id: str | None = None
    ) -> dict | None:
        """
        Get last outgoing comm from user or specific minion.

        Reads timeline.jsonl in reverse to find most recent comm where
        from_user matches or from_minion_id matches.

        Args:
            legion_id: Legion UUID
            from_user: If True, find comm from user
            from_minion_id: If provided, find comm from this minion

        Returns:
            Dict with comm data or None if no comms found:
            {
                "to_user": bool,
                "to_minion_name": str or None,
                "to_channel_name": str or None,
                "content": str (truncated to 150 chars),
                "comm_type": str,
                "timestamp": str (ISO format)
            }
        """
        # Path to timeline file
        timeline_path = self.system.session_coordinator.data_dir / "legions" / legion_id / "timeline.jsonl"

        if not timeline_path.exists():
            return None

        # Read timeline in reverse to find most recent match
        try:
            with open(timeline_path, encoding='utf-8') as f:
                lines = f.readlines()

            # Process lines in reverse (most recent first)
            for line in reversed(lines):
                if not line.strip():
                    continue

                try:
                    comm = json.loads(line)

                    # Check if this comm matches our criteria
                    if from_user and comm.get('from_user'):
                        # Found user's outgoing comm
                        return self._format_comm_preview(comm)
                    elif from_minion_id and comm.get('from_minion_id') == from_minion_id:
                        # Found minion's outgoing comm
                        return self._format_comm_preview(comm)

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            # Log error but don't fail - just return None
            print(f"Error reading timeline for last comm: {e}")

        return None

    def _format_comm_preview(self, comm: dict) -> dict:
        """
        Return the full comm object for frontend display.

        Args:
            comm: Full comm dict from timeline

        Returns:
            Full comm dict (frontend handles display logic)
        """
        # Return the full comm object - let frontend decide what to display
        return comm

    async def _load_all_channels(self) -> None:
        """
        Load all channels for all legions from disk into memory.

        Called during system initialization to restore channel state.
        """
        # Get all legions
        legions = await self.list_legions()

        # Load channels for each legion
        for legion in legions:
            await self.system.channel_manager._load_channels_for_legion(legion.project_id)

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
                await self.system.session_coordinator.session_manager._persist_session_state(minion_id)

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
        minions_with_capabilities = [
            session for session in all_sessions
            if session.is_minion and session.capabilities
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
            import logging
            logging.info(
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
        Emergency halt all minions in a legion with comm queuing.

        Steps:
        1. Set emergency_halt_active flag (atomic - blocks all comm routing)
        2. Initialize empty comm queue for this legion
        3. Halt all minions in parallel (minimize race window)
        4. Return results

        Calls SessionCoordinator.interrupt_session() for each ACTIVE minion.
        Does NOT change session state - minions stay ACTIVE.
        Equivalent to user clicking stop button for each minion.

        While halt is active, all comms are queued and delivered on resume.

        Args:
            legion_id: Legion UUID (project_id)

        Returns:
            Dict with operation results:
            {
                "halted_count": int,
                "failed_minions": [(minion_id, error_msg), ...],
                "total_minions": int
            }
        """
        # Step 1: Atomic flag - blocks comm routing IMMEDIATELY
        self.emergency_halt_active[legion_id] = True
        self.halted_comm_queues[legion_id] = {}

        try:
            # Step 2: Get all minions
            all_sessions = await self.session_manager.list_sessions()
            minions = [s for s in all_sessions if s.is_minion and s.project_id == legion_id]

            # Step 3: Parallel halt (minimize window)
            active_minions = [m for m in minions if m.state.value == "active"]
            halt_tasks = [
                self.system.session_coordinator.interrupt_session(m.session_id)
                for m in active_minions
            ]

            results = await asyncio.gather(*halt_tasks, return_exceptions=True)

            # Count successes/failures
            halted_count = sum(1 for r in results if r is True)
            failed_minions = [
                (active_minions[i].session_id, str(results[i]))
                for i, r in enumerate(results)
                if r is not True
            ]

            return {
                "halted_count": halted_count,
                "failed_minions": failed_minions,
                "total_minions": len(minions)
            }
        except Exception:
            # On error, clear halt state to avoid deadlock
            self.emergency_halt_active.pop(legion_id, None)
            self.halted_comm_queues.pop(legion_id, None)
            raise

    async def resume_all(self, legion_id: str) -> dict:
        """
        Resume all minions in a legion and deliver queued comms.

        Steps:
        1. Clear emergency_halt_active flag (re-enable comm routing)
        2. Send "continue" to each minion
        3. Immediately flush queued comms to that minion (FIFO order)
        4. Clear queue

        Calls SessionCoordinator.send_message() with "continue" for each minion.
        Does NOT use comm protocol - sends message directly to minion session.

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
        # Step 1: Clear halt flag FIRST - allow normal comm routing
        self.emergency_halt_active.pop(legion_id, None)

        # Get queued comms (if any)
        queued_comms = self.halted_comm_queues.pop(legion_id, {})

        # Get all minions
        all_sessions = await self.session_manager.list_sessions()
        minions = [s for s in all_sessions if s.is_minion and s.project_id == legion_id]

        resumed_count = 0
        failed_minions = []

        for minion in minions:
            # Send "continue" to all ACTIVE minions
            if minion.state.value == "active":
                try:
                    # Step 2: Send "continue"
                    success = await self.system.session_coordinator.send_message(
                        minion.session_id,
                        "continue"
                    )

                    if success:
                        resumed_count += 1

                        # Step 3: Flush this minion's queued comms immediately
                        minion_queue = queued_comms.get(minion.session_id, [])
                        if minion_queue:
                            await self._flush_queued_comms_for_minion(
                                minion.session_id,
                                minion_queue
                            )
                    else:
                        failed_minions.append((minion.session_id, "Send message returned False"))
                except Exception as e:
                    failed_minions.append((minion.session_id, str(e)))

        return {
            "resumed_count": resumed_count,
            "failed_minions": failed_minions,
            "total_minions": len(minions)
        }

    async def _flush_queued_comms_for_minion(
        self,
        minion_id: str,
        queued_comms: list
    ) -> None:
        """
        Deliver queued comms to a specific minion in FIFO order.

        Args:
            minion_id: Target minion session ID
            queued_comms: List of Comm objects in chronological order
        """
        for comm in queued_comms:
            try:
                # Route through normal comm system
                await self.system.comm_router.route_comm(comm)
            except Exception as e:
                # Log error but continue flushing
                print(f"Failed to flush queued comm {comm.comm_id} to {minion_id}: {e}")

    # TODO: Implement additional legion management methods in later phases
    # - delete_legion()
    # - get_fleet_status()
