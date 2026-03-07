"""
ArchiveManager - Archives minion session data before disposal.

Responsibilities:
- Copy session files (messages.jsonl, state.json) to archive directory
- Create disposal_metadata.json with context
- Support later analysis and debugging of disposed minions
"""

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.history_distiller import distill_session_history
from src.logging_config import get_logger
from src.models.archive_models import ArchiveResult, DisposalMetadata

if TYPE_CHECKING:
    from src.legion_system import LegionSystem

# Get specialized logger for archive operations
archive_logger = get_logger('archive', category='ARCHIVE')
logger = logging.getLogger(__name__)


class ArchiveManager:
    """Manages archiving of disposed minion session data."""

    def __init__(self, system: 'LegionSystem'):
        """
        Initialize ArchiveManager with LegionSystem.

        Args:
            system: LegionSystem instance for accessing session coordinator
        """
        self.system = system
        self._archives_dir: Path | None = None

    @property
    def archives_dir(self) -> Path:
        """Get the archives directory, creating if needed."""
        if self._archives_dir is None:
            data_dir = self.system.session_coordinator.session_manager.data_dir
            self._archives_dir = data_dir / "archives" / "minions"
            self._archives_dir.mkdir(parents=True, exist_ok=True)
        return self._archives_dir

    async def archive_minion(
        self,
        minion_id: str,
        reason: str,
        parent_overseer_id: str | None,
        parent_overseer_name: str | None,
        descendants_count: int = 0,
        will_be_deleted: bool = False
    ) -> ArchiveResult:
        """
        Archive a minion's session data before disposal.

        Creates an archive at:
        data/archives/minions/{session_id}/{timestamp}/
            - messages.jsonl (copy of session messages)
            - state.json (copy of session state)
            - disposal_metadata.json (disposal context)

        Args:
            minion_id: Session ID of the minion being disposed
            reason: Reason for disposal (e.g., "parent_initiated")
            parent_overseer_id: Parent who initiated disposal
            parent_overseer_name: Captured parent name
            descendants_count: Number of descendants disposed in cascade
            will_be_deleted: If True, session will be deleted after archive (affects final_state)

        Returns:
            ArchiveResult with success status and archive path
        """
        try:
            # Get minion session info
            session_info = await self.system.session_coordinator.session_manager.get_session_info(
                minion_id
            )
            if not session_info:
                return ArchiveResult(
                    success=False,
                    archive_path=None,
                    minion_id=minion_id,
                    minion_name="unknown",
                    error_message=f"Session {minion_id} not found"
                )

            # Create archive directory with timestamp
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            archive_dir = self.archives_dir / minion_id / timestamp
            archive_dir.mkdir(parents=True, exist_ok=True)

            files_archived = []

            # Get source session directory
            session_dir = self.system.session_coordinator.session_manager.sessions_dir / minion_id

            # Copy messages.jsonl if exists
            messages_file = session_dir / "messages.jsonl"
            if messages_file.exists():
                shutil.copy2(messages_file, archive_dir / "messages.jsonl")
                files_archived.append("messages.jsonl")
                archive_logger.debug(f"Archived messages.jsonl for minion {minion_id}")

            # Copy state.json if exists
            state_file = session_dir / "state.json"
            if state_file.exists():
                shutil.copy2(state_file, archive_dir / "state.json")
                files_archived.append("state.json")
                archive_logger.debug(f"Archived state.json for minion {minion_id}")

            # Create disposal metadata
            # Use "deleted" as final_state if session will be deleted after archive
            final_state = "deleted" if will_be_deleted else session_info.state.value
            metadata = DisposalMetadata(
                disposed_at=datetime.now(UTC).timestamp(),
                reason=reason,
                parent_overseer_id=parent_overseer_id,
                parent_overseer_name=parent_overseer_name,
                legion_id=session_info.project_id or "",
                final_state=final_state,
                minion_id=minion_id,
                minion_name=session_info.name or "unknown",
                minion_role=session_info.role,
                overseer_level=session_info.overseer_level or 0,
                child_minion_ids=list(session_info.child_minion_ids or []),
                descendants_count=descendants_count
            )

            # Write disposal_metadata.json
            metadata_file = archive_dir / "disposal_metadata.json"
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)
            files_archived.append("disposal_metadata.json")

            archive_logger.info(
                f"Archived minion {session_info.name} ({minion_id}) to {archive_dir}"
            )

            # Fire-and-forget distillation of session history into markdown
            if messages_file.exists():
                sessions_dir = self.system.session_coordinator.session_manager.sessions_dir
                history_output = sessions_dir / minion_id / "history" / f"{timestamp}.md"
                archive_ts = datetime.now(UTC).isoformat()
                asyncio.create_task(
                    distill_session_history(messages_file, history_output, minion_id, archive_ts)
                )
                archive_logger.debug(f"Launched history distillation for minion {minion_id}")

            return ArchiveResult(
                success=True,
                archive_path=str(archive_dir),
                minion_id=minion_id,
                minion_name=session_info.name or "unknown",
                files_archived=files_archived
            )

        except Exception as e:
            error_msg = f"Failed to archive minion {minion_id}: {e}"
            logger.error(error_msg)
            archive_logger.error(error_msg)
            return ArchiveResult(
                success=False,
                archive_path=None,
                minion_id=minion_id,
                minion_name="unknown",
                error_message=str(e)
            )

    async def get_archive_info(self, minion_id: str) -> list[dict]:
        """
        Get list of archives for a minion.

        Args:
            minion_id: Session ID of the minion

        Returns:
            List of archive info dicts with timestamp and path
        """
        archives = []
        minion_archive_dir = self.archives_dir / minion_id

        if not minion_archive_dir.exists():
            return archives

        for archive_dir in sorted(minion_archive_dir.iterdir()):
            if archive_dir.is_dir():
                metadata_file = archive_dir / "disposal_metadata.json"
                metadata = None
                if metadata_file.exists():
                    with open(metadata_file, encoding='utf-8') as f:
                        metadata = json.load(f)

                archives.append({
                    "timestamp": archive_dir.name,
                    "path": str(archive_dir),
                    "metadata": metadata
                })

        return archives

    async def get_archives(self, session_id: str) -> list[dict]:
        """
        List all archives for a session with summary info.

        Args:
            session_id: Session ID to list archives for

        Returns:
            List of archive summaries with archive_id, timestamp, reason, messages_count
        """
        archives = []
        session_archive_dir = self.archives_dir / session_id

        if not session_archive_dir.exists():
            return archives

        for archive_dir in sorted(session_archive_dir.iterdir()):
            if not archive_dir.is_dir():
                continue

            archive_id = archive_dir.name
            reason = None
            disposed_at = None

            # Read disposal metadata
            metadata_file = archive_dir / "disposal_metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, encoding='utf-8') as f:
                        metadata = json.load(f)
                    reason = metadata.get("reason")
                    disposed_at = metadata.get("disposed_at")
                except (json.JSONDecodeError, OSError):
                    pass

            # Count messages
            messages_count = 0
            messages_file = archive_dir / "messages.jsonl"
            if messages_file.exists():
                try:
                    with open(messages_file, encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                messages_count += 1
                except OSError:
                    pass

            archives.append({
                "archive_id": archive_id,
                "timestamp": archive_id,
                "reason": reason,
                "disposed_at": disposed_at,
                "messages_count": messages_count,
            })

        return archives

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50
    ) -> dict:
        """
        Read paginated messages from an archive's messages.jsonl.

        Args:
            session_id: Session ID
            archive_id: Archive timestamp directory name
            offset: Start index
            limit: Max messages to return (None for all)

        Returns:
            Dict with messages list, total count, offset, has_more
        """
        archive_dir = self.archives_dir / session_id / archive_id
        messages_file = archive_dir / "messages.jsonl"

        if not messages_file.exists():
            return {"messages": [], "total_count": 0, "offset": offset, "has_more": False}

        messages = []
        total_count = 0

        try:
            with open(messages_file, encoding='utf-8') as f:
                lines = f.readlines()

            total_count = sum(1 for line in lines if line.strip())
            start_idx = offset
            end_idx = start_idx + limit if limit else None
            selected_lines = lines[start_idx:end_idx]

            for line in selected_lines:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError as e:
            archive_logger.error(f"Failed to read archive messages: {e}")

        return {
            "messages": messages,
            "total_count": total_count,
            "offset": offset,
            "has_more": (offset + len(messages)) < total_count,
        }

    async def get_archive_state(self, session_id: str, archive_id: str) -> dict | None:
        """
        Read state.json and disposal_metadata.json from an archive.

        Args:
            session_id: Session ID
            archive_id: Archive timestamp directory name

        Returns:
            Dict with state and metadata, or None if archive not found
        """
        archive_dir = self.archives_dir / session_id / archive_id
        if not archive_dir.exists():
            return None

        result = {"state": None, "metadata": None}

        state_file = archive_dir / "state.json"
        if state_file.exists():
            try:
                with open(state_file, encoding='utf-8') as f:
                    result["state"] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        metadata_file = archive_dir / "disposal_metadata.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, encoding='utf-8') as f:
                    result["metadata"] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return result

    async def get_archive_resources(
        self, session_id: str, archive_id: str
    ) -> list[dict]:
        """
        Read resource metadata from an archive's resources/resources.jsonl.

        Args:
            session_id: Session ID
            archive_id: Archive timestamp directory name

        Returns:
            List of resource metadata dicts (filtered, sorted by timestamp)
        """
        archive_dir = self.archives_dir / session_id / archive_id
        resources_file = archive_dir / "resources" / "resources.jsonl"

        if not resources_file.exists():
            return []

        resources = []
        removed_ids: set[str] = set()

        try:
            with open(resources_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "remove":
                                removed_ids.add(entry.get("resource_id", ""))
                            else:
                                resources.append(entry)
                        except json.JSONDecodeError:
                            pass
        except OSError as e:
            archive_logger.error(f"Failed to read archive resources: {e}")

        if removed_ids:
            resources = [r for r in resources if r.get("resource_id") not in removed_ids]

        resources.sort(key=lambda x: x.get("timestamp", 0))
        return resources

    async def get_archive_resource_file(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> bytes | None:
        """
        Read a resource binary file from an archive.

        Args:
            session_id: Session ID
            archive_id: Archive timestamp directory name
            resource_id: Resource identifier

        Returns:
            Raw file bytes or None if not found
        """
        archive_dir = self.archives_dir / session_id / archive_id
        resource_path = archive_dir / "resources" / f"{resource_id}.bin"

        if not resource_path.exists():
            return None

        try:
            with open(resource_path, "rb") as f:
                return f.read()
        except OSError as e:
            archive_logger.error(f"Failed to read archive resource file: {e}")
            return None

    async def list_project_deleted_agents(self, project_id: str) -> list[dict]:
        """
        List deleted agents for a project by scanning archives.

        Filters for agents whose session no longer exists in active sessions
        (excluding reset snapshots whose sessions still exist).

        Args:
            project_id: Project/legion ID

        Returns:
            List of deleted agent summaries with agent_id, name, role,
            archive_count, last_archived_at
        """
        if not self.archives_dir.exists():
            return []

        # Get active session IDs to exclude reset snapshots
        # But include ephemeral sessions (they persist but should appear here)
        active_session_ids = set()
        ephemeral_session_ids = set()
        session_manager = self.system.session_coordinator.session_manager
        sessions_dir = session_manager.sessions_dir
        if sessions_dir.exists():
            for session_dir in sessions_dir.iterdir():
                if session_dir.is_dir():
                    active_session_ids.add(session_dir.name)
                    # Check if this session is ephemeral
                    session_info = session_manager._active_sessions.get(session_dir.name)
                    if session_info and session_info.is_ephemeral:
                        ephemeral_session_ids.add(session_dir.name)

        agents: dict[str, dict] = {}

        for minion_dir in self.archives_dir.iterdir():
            if not minion_dir.is_dir():
                continue

            session_id = minion_dir.name

            # Skip sessions that still exist (reset snapshots) — but include ephemeral
            if session_id in active_session_ids and session_id not in ephemeral_session_ids:
                continue

            # Scan archive timestamps for this minion
            for archive_dir in sorted(minion_dir.iterdir()):
                if not archive_dir.is_dir():
                    continue

                metadata_file = archive_dir / "disposal_metadata.json"
                if not metadata_file.exists():
                    continue

                try:
                    with open(metadata_file, encoding='utf-8') as f:
                        metadata = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                # Filter by project/legion ID
                if metadata.get("legion_id") != project_id:
                    continue

                if session_id not in agents:
                    agents[session_id] = {
                        "agent_id": session_id,
                        "name": metadata.get("minion_name", "unknown"),
                        "role": metadata.get("minion_role"),
                        "archive_count": 0,
                        "last_archived_at": None,
                    }

                agents[session_id]["archive_count"] += 1
                # Update role if not yet set (e.g. first archive was a reset with None role)
                if not agents[session_id]["role"] and metadata.get("minion_role"):
                    agents[session_id]["role"] = metadata["minion_role"]
                # Fallback: read role from state.json if still missing
                if not agents[session_id]["role"]:
                    state_file = archive_dir / "state.json"
                    if state_file.exists():
                        try:
                            with open(state_file, encoding='utf-8') as sf:
                                state = json.load(sf)
                            if state.get("role"):
                                agents[session_id]["role"] = state["role"]
                        except (json.JSONDecodeError, OSError):
                            pass
                disposed_at = metadata.get("disposed_at")
                current_last = agents[session_id]["last_archived_at"]
                if disposed_at and (current_last is None or disposed_at > current_last):
                    agents[session_id]["last_archived_at"] = disposed_at

        return list(agents.values())

    async def erase_history(self, session_id: str) -> bool:
        """Delete all distilled history .md files for a session."""
        sessions_dir = self.system.session_coordinator.session_manager.sessions_dir
        history_dir = sessions_dir / session_id / "history"
        if not history_dir.exists():
            return False
        try:
            shutil.rmtree(history_dir)
            archive_logger.info(f"Erased history for session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to erase history for {session_id}: {e}")
            return False

    async def erase_archives(self, session_id: str) -> bool:
        """Delete all archives for a session."""
        session_archive_dir = self.archives_dir / session_id
        if not session_archive_dir.exists():
            return False
        try:
            shutil.rmtree(session_archive_dir)
            archive_logger.info(f"Erased archives for session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to erase archives for {session_id}: {e}")
            return False

    async def check_history_archives_exist(self, session_id: str) -> dict:
        """Check if history and/or archives exist for a session."""
        sessions_dir = self.system.session_coordinator.session_manager.sessions_dir
        history_dir = sessions_dir / session_id / "history"
        has_history = history_dir.exists() and any(history_dir.glob("*.md"))

        session_archive_dir = self.archives_dir / session_id
        has_archives = session_archive_dir.exists() and any(session_archive_dir.iterdir())

        return {"has_history": has_history, "has_archives": has_archives}
