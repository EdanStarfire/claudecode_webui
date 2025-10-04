"""
Project Management for Claude Code WebUI

Handles project lifecycle, session association, and hierarchical organization
of sessions within working directory-based projects.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProjectInfo:
    """Project metadata and state information"""
    project_id: str
    name: str
    working_directory: str  # Absolute path (IMMUTABLE)
    session_ids: List[str]  # Ordered list of child session IDs
    is_expanded: bool = True  # Expansion state (persisted)
    created_at: datetime = None
    updated_at: datetime = None
    order: int = 0  # Display order among projects

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = datetime.now(timezone.utc)
        if self.session_ids is None:
            self.session_ids = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectInfo':
        """Create from dictionary loaded from JSON"""
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        if 'session_ids' not in data:
            data['session_ids'] = []
        return cls(**data)


class ProjectManager:
    """Manages project lifecycle and session associations"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or Path("data")
        self.projects_dir = self.data_dir / "projects"
        self._active_projects: Dict[str, ProjectInfo] = {}
        self._project_locks: Dict[str, asyncio.Lock] = {}

    async def initialize(self):
        """Initialize project manager and load existing projects"""
        try:
            self.data_dir.mkdir(exist_ok=True)
            self.projects_dir.mkdir(exist_ok=True)
            await self._load_existing_projects()
            logger.info(f"ProjectManager initialized with {len(self._active_projects)} existing projects")
        except Exception as e:
            logger.error(f"Failed to initialize ProjectManager: {e}")
            raise

    async def _load_existing_projects(self):
        """Load existing project state from filesystem"""
        try:
            for project_dir in self.projects_dir.iterdir():
                if project_dir.is_dir():
                    state_file = project_dir / "state.json"
                    if state_file.exists():
                        try:
                            with open(state_file, 'r') as f:
                                data = json.load(f)
                            project_info = ProjectInfo.from_dict(data)
                            self._active_projects[project_info.project_id] = project_info
                            self._project_locks[project_info.project_id] = asyncio.Lock()
                            logger.debug(f"Loaded project {project_info.project_id} ({project_info.name})")
                        except Exception as e:
                            logger.error(f"Failed to load project from {project_dir}: {e}")
        except Exception as e:
            logger.error(f"Error loading existing projects: {e}")

    async def create_project(
        self,
        name: str,
        working_directory: str,
        order: Optional[int] = None
    ) -> ProjectInfo:
        """Create a new project with unique ID"""
        project_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Convert to absolute path
        working_directory = str(Path(working_directory).resolve())

        # Set new project order to 0 (top of list) and increment existing projects
        if order is None:
            await self._shift_existing_projects_down()
            order = 0

        project_info = ProjectInfo(
            project_id=project_id,
            name=name,
            working_directory=working_directory,
            session_ids=[],
            is_expanded=True,
            created_at=now,
            updated_at=now,
            order=order
        )

        try:
            # Create project directory structure
            project_dir = self.projects_dir / project_id
            project_dir.mkdir(exist_ok=True)

            # Store project info
            self._active_projects[project_id] = project_info
            self._project_locks[project_id] = asyncio.Lock()

            await self._persist_project_state(project_id)

            logger.info(f"Created project {project_id} ({name}) at {working_directory}")
            return project_info

        except Exception as e:
            logger.error(f"Failed to create project {project_id}: {e}")
            # Cleanup on failure
            if project_id in self._active_projects:
                del self._active_projects[project_id]
            if project_id in self._project_locks:
                del self._project_locks[project_id]
            raise

    async def get_project(self, project_id: str) -> Optional[ProjectInfo]:
        """Get project information"""
        return self._active_projects.get(project_id)

    async def list_projects(self) -> List[ProjectInfo]:
        """List all projects sorted by order"""
        projects = list(self._active_projects.values())

        # Sort by order (None/missing order gets high value), then by created_at as fallback
        def sort_key(project: ProjectInfo):
            order = project.order if project.order is not None else 999999
            return (order, project.created_at)

        return sorted(projects, key=sort_key)

    async def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        is_expanded: Optional[bool] = None,
        order: Optional[int] = None
    ) -> bool:
        """Update project metadata (name, expansion state, order only - path is immutable)"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                # Update mutable fields only
                if name is not None:
                    project.name = name
                if is_expanded is not None:
                    project.is_expanded = is_expanded
                if order is not None:
                    project.order = order

                project.updated_at = datetime.now(timezone.utc)
                await self._persist_project_state(project_id)
                logger.info(f"Updated project {project_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to update project {project_id}: {e}")
                return False

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and remove from registry"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                # Delete project directory and all contents
                import shutil
                import os
                project_dir = self.projects_dir / project_id
                if project_dir.exists():
                    try:
                        shutil.rmtree(project_dir)
                        logger.info(f"Deleted project directory: {project_dir}")
                    except Exception as e:
                        logger.warning(f"Standard deletion failed for {project_dir}: {e}")

                        # Try Windows-specific fallback deletion
                        if os.name == 'nt':  # Windows
                            try:
                                logger.info(f"Attempting Windows-specific deletion for {project_dir}")
                                import subprocess
                                import time
                                import gc
                                gc.collect()
                                time.sleep(0.5)

                                result = subprocess.run(
                                    ['rmdir', '/s', '/q', str(project_dir)],
                                    shell=True,
                                    capture_output=True,
                                    text=True
                                )

                                if result.returncode == 0:
                                    logger.info(f"Successfully deleted directory using Windows rmdir: {project_dir}")
                                else:
                                    logger.error(f"Windows rmdir failed: {result.stderr}")
                                    return False

                            except Exception as fallback_e:
                                logger.error(f"Windows fallback deletion also failed for {project_dir}: {fallback_e}")
                                return False
                        else:
                            logger.error(f"Failed to delete project directory {project_dir}: {e}")
                            return False

                # Only remove from memory after successful file deletion
                del self._active_projects[project_id]

                # Remove project lock
                if project_id in self._project_locks:
                    del self._project_locks[project_id]

                logger.info(f"Successfully deleted project {project_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to delete project {project_id}: {e}")
                return False

    async def add_session_to_project(self, project_id: str, session_id: str) -> bool:
        """Add a session to project's session list"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                if session_id not in project.session_ids:
                    project.session_ids.append(session_id)
                    project.updated_at = datetime.now(timezone.utc)
                    await self._persist_project_state(project_id)
                    logger.info(f"Added session {session_id} to project {project_id}")
                    return True
                else:
                    logger.warning(f"Session {session_id} already in project {project_id}")
                    return True

            except Exception as e:
                logger.error(f"Failed to add session to project: {e}")
                return False

    async def remove_session_from_project(self, project_id: str, session_id: str) -> bool:
        """Remove a session from project's session list"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                if session_id in project.session_ids:
                    project.session_ids.remove(session_id)
                    project.updated_at = datetime.now(timezone.utc)
                    await self._persist_project_state(project_id)
                    logger.info(f"Removed session {session_id} from project {project_id}")
                    return True
                else:
                    logger.warning(f"Session {session_id} not found in project {project_id}")
                    return True

            except Exception as e:
                logger.error(f"Failed to remove session from project: {e}")
                return False

    async def reorder_project_sessions(self, project_id: str, session_ids: List[str]) -> bool:
        """Reorder sessions within a project"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                # Validate that all session_ids belong to this project
                current_session_set = set(project.session_ids)
                new_session_set = set(session_ids)

                if current_session_set != new_session_set:
                    logger.error(f"Session IDs mismatch for project {project_id}")
                    return False

                # Update session order
                project.session_ids = session_ids
                project.updated_at = datetime.now(timezone.utc)
                await self._persist_project_state(project_id)
                logger.info(f"Reordered sessions in project {project_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to reorder sessions in project: {e}")
                return False

    async def reorder_projects(self, project_ids: List[str]) -> bool:
        """Reorder projects by assigning sequential order values"""
        try:
            # Validate that all project_ids exist
            valid_project_ids = []
            for project_id in project_ids:
                if project_id in self._active_projects:
                    valid_project_ids.append(project_id)
                else:
                    logger.warning(f"Project {project_id} not found during reorder")

            if not valid_project_ids:
                logger.error("No valid projects found for reordering")
                return False

            # Update order values sequentially
            tasks = []
            for i, project_id in enumerate(valid_project_ids):
                tasks.append(self._update_project_order(project_id, i))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check if all updates succeeded
            success_count = sum(1 for result in results if result is True)
            if success_count == len(valid_project_ids):
                logger.info(f"Successfully reordered {success_count} projects")
                return True
            else:
                logger.error(f"Reorder partially failed: {success_count}/{len(valid_project_ids)} projects updated")
                return False

        except Exception as e:
            logger.error(f"Failed to reorder projects: {e}")
            return False

    async def toggle_expansion(self, project_id: str) -> bool:
        """Toggle project expansion state"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                project.is_expanded = not project.is_expanded
                project.updated_at = datetime.now(timezone.utc)
                await self._persist_project_state(project_id)
                logger.info(f"Toggled expansion for project {project_id} to {project.is_expanded}")
                return True

            except Exception as e:
                logger.error(f"Failed to toggle expansion for project {project_id}: {e}")
                return False

    async def _update_project_order(self, project_id: str, order: int) -> bool:
        """Update project order"""
        async with self._get_project_lock(project_id):
            try:
                project = self._active_projects.get(project_id)
                if not project:
                    logger.error(f"Project {project_id} not found")
                    return False

                project.order = order
                project.updated_at = datetime.now(timezone.utc)
                await self._persist_project_state(project_id)
                return True

            except Exception as e:
                logger.error(f"Failed to update project {project_id} order: {e}")
                return False

    async def _shift_existing_projects_down(self):
        """Increment order of all existing projects by 1 to make room for new project at top"""
        try:
            existing_projects = list(self._active_projects.values())

            tasks = []
            for project in existing_projects:
                current_order = project.order if project.order is not None else 999999
                new_order = current_order + 1
                tasks.append(self._update_project_order(project.project_id, new_order))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for result in results if result is True)
                logger.info(f"Shifted {success_count}/{len(tasks)} existing projects down for new project")

        except Exception as e:
            logger.error(f"Failed to shift existing projects down: {e}")

    def _get_project_lock(self, project_id: str) -> asyncio.Lock:
        """Get or create lock for project"""
        if project_id not in self._project_locks:
            self._project_locks[project_id] = asyncio.Lock()
        return self._project_locks[project_id]

    async def _persist_project_state(self, project_id: str):
        """Persist project state to filesystem"""
        try:
            project = self._active_projects[project_id]
            project_dir = self.projects_dir / project_id
            project_dir.mkdir(exist_ok=True)

            state_file = project_dir / "state.json"
            with open(state_file, 'w') as f:
                json.dump(project.to_dict(), f, indent=2)

        except Exception as e:
            logger.error(f"Failed to persist project state for {project_id}: {e}")
            raise
