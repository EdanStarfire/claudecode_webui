"""
Core data models for Legion multi-agent system.

This module defines data structures for Legion communications.

NOTE: Legion and Minion data models have been consolidated:
- Legions are ProjectInfo (all projects support minions - issue #313)
- Minions are SessionInfo with is_minion=True (see src/session_manager.py)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.timestamp_utils import normalize_timestamp

# Constants

# Reserved minion ID for the user (all zeros UUID)
USER_MINION_ID = "00000000-0000-0000-0000-000000000000"

# Reserved minion ID for system-generated messages (all F's UUID)
SYSTEM_MINION_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"
SYSTEM_MINION_NAME = "system"

# Set of reserved minion IDs that cannot be used for real minions
RESERVED_MINION_IDS = {USER_MINION_ID, SYSTEM_MINION_ID}


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
    """Priority level for message delivery and interrupt handling."""
    NONE = "none"               # Normal queue (default)
    HALT = "halt"               # Interrupt target, deliver immediately
    PIVOT = "pivot"             # Interrupt target with redirect message
    CRITICAL = "critical"       # Emergency (unused in MVP)


# Core Data Models
# NOTE: LegionInfo and MinionInfo have been removed - use ProjectInfo and SessionInfo instead


@dataclass
class Comm:
    """
    High-level message in multi-agent system.
    """
    comm_id: str                # UUID

    # Source
    from_minion_id: str | None = None  # None if from user
    from_user: bool = False
    from_minion_name: str | None = None  # Captured name (for historical display)

    # Destination
    to_minion_id: str | None = None    # Direct to minion
    to_user: bool = False
    to_minion_name: str | None = None  # Captured name (for historical display)

    # Content
    summary: str = ""  # Brief one-line description (~50 chars, shown collapsed)
    content: str = ""  # Full detailed message (shown expanded, supports markdown)
    comm_type: CommType = CommType.SYSTEM
    interrupt_priority: InterruptPriority = InterruptPriority.NONE

    # Context
    in_reply_to: str | None = None
    related_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Visibility
    visible_to_user: bool = True  # Should this show in UI?

    # Timestamp (Unix timestamp in seconds)
    timestamp: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    def validate(self) -> bool:
        """Ensure Comm has valid routing."""
        destinations = sum([
            self.to_minion_id is not None,
            self.to_user
        ])
        if destinations != 1:
            raise ValueError("Comm must have exactly one destination (minion or user)")

        sources = sum([
            self.from_minion_id is not None,
            self.from_user
        ])
        if sources != 1:
            raise ValueError("Comm must have exactly one source")

        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "comm_id": self.comm_id,
            "from_minion_id": self.from_minion_id,
            "from_user": self.from_user,
            "from_minion_name": self.from_minion_name,
            "to_minion_id": self.to_minion_id,
            "to_user": self.to_user,
            "to_minion_name": self.to_minion_name,
            "summary": self.summary,
            "content": self.content,
            "comm_type": self.comm_type.value,
            "interrupt_priority": self.interrupt_priority.value,
            "in_reply_to": self.in_reply_to,
            "related_task_id": self.related_task_id,
            "metadata": self.metadata,
            "visible_to_user": self.visible_to_user,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Comm':
        """Create from dictionary."""
        data = data.copy()
        data["comm_type"] = CommType(data["comm_type"])
        data["interrupt_priority"] = InterruptPriority(data["interrupt_priority"])

        # Normalize timestamp to handle mixed string/float formats (backwards compatibility)
        if "timestamp" in data:
            try:
                data["timestamp"] = normalize_timestamp(data["timestamp"])
            except (ValueError, TypeError):
                # Fallback to current time if timestamp is invalid
                data["timestamp"] = datetime.now(UTC).timestamp()

        return cls(**data)
