"""
LegionCoordinator - Top-level orchestrator for Legion multi-agent system.

Responsibilities:
- Delegate legion/minion CRUD to ProjectManager/SessionManager
- Track hordes and channels (grouping mechanisms)
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
        self.hordes: Dict = {}   # Dict[str, Horde]
        self.channels: Dict = {} # Dict[str, Channel]

        # Central capability registry (MVP approach)
        # Format: {session_id: [capability1, capability2, ...]}
        self.capability_registry: Dict[str, List[str]] = {}

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
        Get minion by name (case-sensitive).

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

    async def list_legions(self) -> List['ProjectInfo']:
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

    async def assemble_horde_hierarchy(self, legion_id: str) -> dict:
        """
        Assemble complete horde hierarchy with user at root.

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
        from_minion_id: Optional[str] = None
    ) -> Optional[dict]:
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
            with open(timeline_path, 'r', encoding='utf-8') as f:
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
        Format comm data for preview display.

        Args:
            comm: Full comm dict from timeline

        Returns:
            Formatted dict for frontend display
        """
        # Determine recipient
        to_user = comm.get('to_user', False)
        to_minion_name = comm.get('to_minion_name')
        to_channel_name = comm.get('to_channel_name')

        # Truncate content to 150 chars for preview
        content = comm.get('content', comm.get('summary', ''))
        if len(content) > 150:
            content = content[:150]

        return {
            "to_user": to_user,
            "to_minion_name": to_minion_name,
            "to_channel_name": to_channel_name,
            "content": content,
            "comm_type": comm.get('comm_type', 'info'),
            "timestamp": comm.get('timestamp')
        }

    # TODO: Implement additional legion management methods in later phases
    # - delete_legion()
    # - emergency_halt_all()
    # - resume_all()
    # - get_fleet_status()
    # - register_capability()
    # - search_capability_registry()
