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
        session = await self.session_manager.get_session(minion_id)
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

    # TODO: Implement additional legion management methods in later phases
    # - delete_legion()
    # - emergency_halt_all()
    # - resume_all()
    # - get_fleet_status()
    # - register_capability()
    # - search_capability_registry()
