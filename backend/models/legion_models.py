"""
Core data models for Legion multi-agent system.

This module defines data structures for Legion communications.

NOTE: Legion and Minion data models have been consolidated:
- Legions are ProjectInfo (all projects support minions - issue #313)
- All sessions are minions (is_minion field removed - issue #349)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from backend.timestamp_utils import normalize_timestamp
from shared.event_envelope import DEFAULT_BACKEND_ID

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
    REPARENT = "reparent"       # Minion moved to new parent
    SYSTEM = "system"           # System notification


class DeliveryPolicy(Enum):
    """Sender-chosen delivery policy for a Comm (issue #2193)."""
    IMMEDIATE = "immediate"
    WHEN_IDLE = "when_idle"
    PIVOT = "pivot"
    HALT = "halt"


class SenderType(Enum):
    """Discriminates which kind of producer originated a Comm (issue #2193, AC1)."""
    USER = "user"
    MINION = "minion"
    SCHEDULE = "schedule"
    EXTERNAL = "external"
    SYSTEM = "system"


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
    to_minion_name: str | None = None  # Captured name (for historical display)

    # Content
    summary: str = ""  # Brief one-line description (~50 chars, shown collapsed)
    content: str = ""  # Full detailed message (shown expanded, supports markdown)
    comm_type: CommType = CommType.SYSTEM
    delivery: DeliveryPolicy = DeliveryPolicy.IMMEDIATE

    # Attachments (file passing between minions)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # Each dict: {"name": str, "size": int, "mime_type": str,
    #             "resource_id": str|None, "session_id": str|None}

    # Context
    in_reply_to: str | None = None
    related_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Timestamp (Unix timestamp in seconds)
    timestamp: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    # Delivery control (issue #2193, AC1) — inert until PR3/PR2/PR6 wire real gate/queue logic
    auto_start: bool = False
    reset_first: bool = False
    fire_at: float | None = None

    # Sender identity (issue #2193, AC1)
    sender_type: SenderType | None = None
    sender_id: str | None = None

    # Issue #2268 AC7: unused by any routing logic until #1819 — a seam so that
    # stage doesn't need a schema migration. Self-contained default (mirrors
    # TenantContext.backend_id's own default), not threaded in from every
    # construction call site, same as sender_type's own inference below.
    origin_backend_id: str | None = None

    def __post_init__(self) -> None:
        """Infer sender_type from existing source fields when not explicitly provided."""
        if self.origin_backend_id is None:
            self.origin_backend_id = DEFAULT_BACKEND_ID
        if self.sender_type is not None:
            return
        if self.from_user:
            self.sender_type = SenderType.USER
        elif self.from_minion_id == SYSTEM_MINION_ID:
            self.sender_type = SenderType.SYSTEM
        elif self.from_minion_id:
            self.sender_type = SenderType.MINION

    def validate(self) -> bool:
        """Ensure Comm has valid routing."""
        if self.to_minion_id is None:
            raise ValueError("Comm must have a destination minion")

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
            "to_minion_name": self.to_minion_name,
            "summary": self.summary,
            "content": self.content,
            "comm_type": self.comm_type.value,
            "delivery": self.delivery.value,
            "attachments": self.attachments,
            "in_reply_to": self.in_reply_to,
            "related_task_id": self.related_task_id,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "auto_start": self.auto_start,
            "reset_first": self.reset_first,
            "fire_at": self.fire_at,
            "sender_type": self.sender_type.value if self.sender_type else None,
            "sender_id": self.sender_id,
            "origin_backend_id": self.origin_backend_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Comm':
        """Create from dictionary."""
        data = data.copy()
        data["comm_type"] = CommType(data["comm_type"])

        # Backward compat: every Comm persisted before issue #2259 serialized
        # visible_to_user (default True on ALL comms, not just to_user ones),
        # and to_user comms also serialized to_user — both fields are gone
        # from the dataclass now, so drop them before construction or every
        # historical PendingItem replay raises TypeError.
        data.pop("to_user", None)
        data.pop("visible_to_user", None)

        # Backward compat: old comms persisted under the removed interrupt_priority field
        if "delivery" not in data and "interrupt_priority" in data:
            data["delivery"] = {
                "none": "immediate", "halt": "halt", "pivot": "pivot",
            }.get(data.pop("interrupt_priority"), "immediate")
        data["delivery"] = DeliveryPolicy(data["delivery"])

        # Backward compat: old comms may not have attachments
        data.setdefault("attachments", [])
        data.setdefault("sender_type", None)
        data.setdefault("sender_id", None)
        data.setdefault("auto_start", False)
        data.setdefault("reset_first", False)
        data.setdefault("fire_at", None)
        data.setdefault("origin_backend_id", None)
        data["sender_type"] = SenderType(data["sender_type"]) if data["sender_type"] else None

        # Normalize timestamp to handle mixed string/float formats (backwards compatibility)
        if "timestamp" in data:
            try:
                data["timestamp"] = normalize_timestamp(data["timestamp"])
            except (ValueError, TypeError):
                # Fallback to current time if timestamp is invalid
                data["timestamp"] = datetime.now(UTC).timestamp()

        return cls(**data)


_TYPE_DELIVERY_DEFAULTS: dict[CommType, DeliveryPolicy] = {
    CommType.TASK: DeliveryPolicy.IMMEDIATE,
    CommType.QUESTION: DeliveryPolicy.IMMEDIATE,
    CommType.REPORT: DeliveryPolicy.WHEN_IDLE,
    CommType.INFO: DeliveryPolicy.WHEN_IDLE,
    CommType.HALT: DeliveryPolicy.HALT,
    CommType.PIVOT: DeliveryPolicy.PIVOT,
}


def default_delivery_for_type(comm_type: CommType) -> DeliveryPolicy:
    """Per-type delivery default (issue #2193 AC2). Every other CommType
    (THOUGHT/SPAWN/DISPOSE/REPARENT/SYSTEM) defaults to IMMEDIATE, matching
    their current always-immediate, to_user, non-gated behavior."""
    return _TYPE_DELIVERY_DEFAULTS.get(comm_type, DeliveryPolicy.IMMEDIATE)
