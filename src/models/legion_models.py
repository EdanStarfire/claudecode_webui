"""
Core data models for Legion multi-agent system.

This module defines data structures for Legion grouping mechanisms (hordes, channels, communications).

NOTE: Legion and Minion data models have been consolidated:
- Legions are now ProjectInfo with is_multi_agent=True (see src/project_manager.py)
- Minions are now SessionInfo with is_minion=True (see src/session_manager.py)

This consolidation reduces duplication and leverages existing infrastructure.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any


# Constants

# Reserved minion ID for the user (all zeros UUID)
USER_MINION_ID = "00000000-0000-0000-0000-000000000000"


# Enumerations


class CommType(Enum):
    """Type of communication between minions/user."""
    TASK = "task"               # Assign work
    QUESTION = "question"       # Request info
    REPORT = "report"           # Provide findings
    INFO = "info"               # Non-interrupting information/instruction
    HALT = "halt"               # Stop and wait
    PIVOT = "pivot"             # Stop, clear, redirect
    THOUGHT = "thought"         # Minion self-talk
    SPAWN = "spawn"             # Minion created
    DISPOSE = "dispose"         # Minion terminated
    SYSTEM = "system"           # System notification


class InterruptPriority(Enum):
    """Priority level for interrupt handling."""
    ROUTINE = "routine"         # Normal queue
    IMPORTANT = "important"     # High priority (unused in MVP)
    PIVOT = "pivot"             # Clear queue, redirect
    CRITICAL = "critical"       # Emergency (unused in MVP)


# Core Data Models
# NOTE: LegionInfo and MinionInfo have been removed - use ProjectInfo and SessionInfo instead

@dataclass
class Horde:
    """
    Hierarchical group: overseer + all descendant minions.
    """
    horde_id: str               # UUID
    legion_id: str              # Parent legion
    name: str                   # "Architecture Planning Team"

    # Hierarchy
    root_overseer_id: str       # Top-level overseer (user-created minion)
    all_minion_ids: List[str] = field(default_factory=list)   # Flattened list of all minions in tree

    # Metadata
    created_by: str = "user"    # "user" or minion_id
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "horde_id": self.horde_id,
            "legion_id": self.legion_id,
            "name": self.name,
            "root_overseer_id": self.root_overseer_id,
            "all_minion_ids": self.all_minion_ids,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Horde':
        """Create from dictionary."""
        data = data.copy()
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


@dataclass
class Channel:
    """
    Purpose-driven communication group for cross-horde collaboration.
    """
    channel_id: str             # UUID
    legion_id: str              # Parent legion
    name: str                   # "Implementation Planning"
    description: str            # Purpose description
    purpose: str                # "coordination" | "planning" | "scene" | "research"

    # Membership
    member_minion_ids: List[str] = field(default_factory=list)
    created_by_minion_id: Optional[str] = None  # None if user-created

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def get_comm_log_path(self, data_dir: 'Path') -> 'Path':
        """
        Get the path to the channel's comm log file.

        Args:
            data_dir: Base data directory path

        Returns:
            Absolute path to comms.jsonl for this channel
        """
        from pathlib import Path
        return data_dir / "legions" / self.legion_id / "channels" / self.channel_id / "comms.jsonl"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "channel_id": self.channel_id,
            "legion_id": self.legion_id,
            "name": self.name,
            "description": self.description,
            "purpose": self.purpose,
            "member_minion_ids": self.member_minion_ids,
            "created_by_minion_id": self.created_by_minion_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Channel':
        """Create from dictionary."""
        data = data.copy()
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        # Remove deprecated comm_log_path if present (backward compatibility)
        data.pop("comm_log_path", None)
        return cls(**data)


@dataclass
class Comm:
    """
    High-level message in multi-agent system.
    """
    comm_id: str                # UUID

    # Source
    from_minion_id: Optional[str] = None  # None if from user
    from_user: bool = False
    from_minion_name: Optional[str] = None  # Captured name (for historical display)

    # Destination
    to_minion_id: Optional[str] = None    # Direct to minion
    to_channel_id: Optional[str] = None   # Broadcast to channel
    to_user: bool = False
    to_minion_name: Optional[str] = None  # Captured name (for historical display)
    to_channel_name: Optional[str] = None  # Captured name (for historical display)

    # Content
    summary: str = ""  # Brief one-line description (~50 chars, shown collapsed)
    content: str = ""  # Full detailed message (shown expanded, supports markdown)
    comm_type: CommType = CommType.SYSTEM
    interrupt_priority: InterruptPriority = InterruptPriority.ROUTINE

    # Context
    in_reply_to: Optional[str] = None
    related_task_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Visibility
    visible_to_user: bool = True  # Should this show in UI?

    # Timestamp
    timestamp: datetime = field(default_factory=datetime.now)

    def validate(self) -> bool:
        """Ensure Comm has valid routing."""
        destinations = sum([
            self.to_minion_id is not None,
            self.to_channel_id is not None,
            self.to_user
        ])
        if destinations != 1:
            raise ValueError("Comm must have exactly one destination")

        sources = sum([
            self.from_minion_id is not None,
            self.from_user
        ])
        if sources != 1:
            raise ValueError("Comm must have exactly one source")

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "comm_id": self.comm_id,
            "from_minion_id": self.from_minion_id,
            "from_user": self.from_user,
            "from_minion_name": self.from_minion_name,
            "to_minion_id": self.to_minion_id,
            "to_channel_id": self.to_channel_id,
            "to_user": self.to_user,
            "to_minion_name": self.to_minion_name,
            "to_channel_name": self.to_channel_name,
            "summary": self.summary,
            "content": self.content,
            "comm_type": self.comm_type.value,
            "interrupt_priority": self.interrupt_priority.value,
            "in_reply_to": self.in_reply_to,
            "related_task_id": self.related_task_id,
            "metadata": self.metadata,
            "visible_to_user": self.visible_to_user,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Comm':
        """Create from dictionary."""
        data = data.copy()
        data["comm_type"] = CommType(data["comm_type"])
        data["interrupt_priority"] = InterruptPriority(data["interrupt_priority"])
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)
