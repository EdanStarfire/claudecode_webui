"""
Data models for minion archival system.

This module defines dataclasses for archiving disposed minion session data
to support later analysis and debugging.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class DisposalMetadata:
    """
    Metadata captured when a minion is disposed.

    Stored as disposal_metadata.json in the archive directory.
    """
    disposed_at: float  # Unix timestamp
    reason: str  # Reason for disposal (e.g., "parent_initiated", "cascade_disposal")
    parent_overseer_id: str | None  # Parent who initiated disposal
    parent_overseer_name: str | None  # Captured parent name
    legion_id: str  # Project/legion this minion belonged to
    final_state: str  # Session state at disposal time (e.g., "active", "terminated")

    # Minion identity
    minion_id: str  # Session ID of disposed minion
    minion_name: str  # Captured name at disposal
    minion_role: str | None  # Role description

    # Hierarchy info
    overseer_level: int  # 0=user-created, 1=child, etc.
    child_minion_ids: list[str] = field(default_factory=list)  # Children at disposal time
    descendants_count: int = 0  # Number of descendants disposed in cascade

    # Additional context
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'DisposalMetadata':
        """Create from dictionary loaded from JSON."""
        return cls(**data)


@dataclass
class ArchiveResult:
    """
    Result of an archive operation.

    Returned by ArchiveManager.archive_minion().
    """
    success: bool
    archive_path: str | None  # Path to archive directory (None if failed)
    minion_id: str
    minion_name: str
    files_archived: list[str] = field(default_factory=list)  # List of archived file names
    error_message: str | None = None  # Error details if failed
    archived_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
