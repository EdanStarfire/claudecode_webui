"""
ArchiveManager - Archives minion session data before disposal.

Responsibilities:
- Copy session files (messages.jsonl, state.json) to archive directory
- Create disposal_metadata.json with context
- Support later analysis and debugging of disposed minions
"""

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
        descendants_count: int = 0
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
            metadata = DisposalMetadata(
                disposed_at=datetime.now(UTC).timestamp(),
                reason=reason,
                parent_overseer_id=parent_overseer_id,
                parent_overseer_name=parent_overseer_name,
                legion_id=session_info.project_id or "",
                final_state=session_info.state.value,
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
