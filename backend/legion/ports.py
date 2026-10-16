"""
Legion narrow-interface Protocols (issue #2189, coordinator-split PR 9).

Structural (duck-typed) views of the subset of SessionCoordinator and its
chained-through managers that Legion components actually use. SessionCoordinator
already satisfies these Protocols today, with zero behavior change — no
adapter/wrapper object is needed.

Single unified LegionCoordinatorPort, not a query/lifecycle split: real Legion
call chains interleave reads and writes in the same function (e.g.
spawn_minion: get_session_info -> create_session -> update_session), so a
two-port split would mean nearly every file needs both ports anyway.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from backend.credential_vault import CredentialVault
from backend.profile_manager import ProfileManager
from backend.project_manager import ProjectInfo
from backend.queue_manager import QueueItem
from backend.session_config import SessionConfig
from backend.session_manager import SessionInfo
from backend.template_manager import TemplateManager


class SessionManagerPort(Protocol):
    """Narrow view of SessionManager used by Legion components."""

    sessions_dir: Path
    data_dir: Path

    async def get_session_info(self, session_id: str) -> SessionInfo | None: ...
    async def list_sessions(self) -> list[SessionInfo]: ...
    async def get_session_directory(self, session_id: str) -> Path | None: ...
    async def update_session(
        self,
        session_id: str,
        template_manager: TemplateManager | None = None,
        **kwargs,
    ) -> bool: ...


class ProjectManagerPort(Protocol):
    """Narrow view of ProjectManager used by Legion components."""

    async def get_project(self, project_id: str) -> ProjectInfo | None: ...
    async def list_projects(self) -> list[ProjectInfo]: ...
    async def record_fleet_stopped(
        self,
        project_id: str,
        session_ids: list[str],
        processing_session_ids: list[str] | None = None,
    ) -> ProjectInfo | None: ...
    async def clear_fleet_stopped(
        self, project_id: str, session_ids: list[str]
    ) -> ProjectInfo | None: ...
    async def get_fleet_snapshot(self, project_id: str) -> dict[str, list[str]] | None: ...


class QueueManagerPort(Protocol):
    """Narrow view of QueueManager used by Legion components."""

    def get_queue(self, session_id: str) -> list[QueueItem]: ...


class LegionCoordinatorPort(Protocol):
    """Narrow view of SessionCoordinator used by Legion components.

    Covers data_dir, template_manager, profile_manager, credential_vault, the 3
    sub-manager ports, and the async verbs Legion calls directly on the
    coordinator.
    """

    data_dir: Path
    template_manager: TemplateManager
    profile_manager: ProfileManager
    credential_vault: CredentialVault
    session_manager: SessionManagerPort
    project_manager: ProjectManagerPort
    queue_manager: QueueManagerPort

    async def enqueue_message(
        self,
        session_id: str,
        content: str,
        reset_session: bool | None = None,
        metadata: dict | None = None,
        inject_timestamp: bool = True,
    ) -> dict: ...

    async def start_session(
        self,
        session_id: str,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        _lifecycle_action: str = "start",
    ) -> bool: ...

    async def terminate_session(self, session_id: str) -> bool: ...

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        config: SessionConfig,
        name: str | None = None,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        role: str | None = None,
        capabilities: list[str] = None,
        parent_overseer_id: str | None = None,
        overseer_level: int = 0,
        can_spawn_minions: bool = True,
    ) -> str: ...

    async def delete_session(self, session_id: str, archive_reason: str = "user_deleted") -> dict: ...

    async def create_ephemeral_session(
        self,
        session_config: dict,
        schedule_name: str,
        project_id: str,
        permission_callback=None,
    ) -> str: ...

    async def send_message(
        self,
        session_id: str,
        message: str,
        metadata: dict | None = None,
        inject_timestamp: bool = True,
    ) -> bool: ...

    async def interrupt_session(self, session_id: str) -> bool: ...

    async def wait_for_session_ready(self, session_id: str, timeout: float = 60.0) -> bool: ...

    async def get_all_descendants(self, session_id: str) -> list[dict]: ...

    async def archive_and_clear_session(self, session_id: str) -> bool: ...

    async def resolve_orphaned_ephemeral_session(self, session_id: str) -> None: ...

    async def restart_session(
        self, session_id: str, permission_callback: Callable | None = None
    ) -> bool: ...

    async def register_uploaded_file(self, session_id: str, file_path: str) -> None: ...

    async def register_uploaded_resource(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None,
    ) -> dict | None: ...

    async def pause_queue(self, session_id: str, paused: bool) -> bool: ...
