"""
Core data models for Legion multi-agent system.

This module defines all the primary data structures for the Legion system including
legions, minions, hordes, channels, and communications.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any


# Enumerations

class MinionState(Enum):
    """State of a minion in its lifecycle."""
    CREATED = "created"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    PROCESSING = "processing"
    TERMINATED = "terminated"
    ERROR = "error"


class CommType(Enum):
    """Type of communication between minions/user."""
    TASK = "task"               # Assign work
    QUESTION = "question"       # Request info
    REPORT = "report"           # Provide findings
    GUIDE = "guide"             # Non-interrupting instruction
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

@dataclass
class LegionInfo:
    """
    Top-level multi-agent container.
    Extends Project concept with multi-agent capabilities.
    """
    legion_id: str              # UUID, same as project_id
    name: str                   # "SaaS Platform Overhaul"
    working_directory: str      # Absolute path

    # Multi-agent specific
    is_multi_agent: bool = True # Always True for legions
    horde_ids: List[str] = field(default_factory=list)        # All hordes in this legion
    channel_ids: List[str] = field(default_factory=list)      # All channels in this legion
    minion_ids: List[str] = field(default_factory=list)       # All minions (for quick lookup)

    # Configuration
    max_concurrent_minions: int = 20

    # State
    active_minion_count: int = 0

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Project compatibility
    session_ids: List[str] = field(default_factory=list)  # Maps to minion session_ids
    is_expanded: bool = True
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "legion_id": self.legion_id,
            "name": self.name,
            "working_directory": self.working_directory,
            "is_multi_agent": self.is_multi_agent,
            "horde_ids": self.horde_ids,
            "channel_ids": self.channel_ids,
            "minion_ids": self.minion_ids,
            "max_concurrent_minions": self.max_concurrent_minions,
            "active_minion_count": self.active_minion_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "session_ids": self.session_ids,
            "is_expanded": self.is_expanded,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LegionInfo':
        """Create from dictionary."""
        data = data.copy()
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


@dataclass
class MinionInfo:
    """
    Represents a single minion (SDK session instance).
    Extends SessionInfo concept with multi-agent capabilities.
    """
    minion_id: str              # UUID
    name: str                   # Unique within legion, e.g. "AuthExpert"
    role: str                   # Description: "Auth Service Expert"

    # Classification
    is_overseer: bool = False   # True if has spawned children
    overseer_level: int = 0     # 0=user-created, 1=child, 2=grandchild, etc.

    # Hierarchy
    parent_overseer_id: Optional[str] = None  # None if user-created
    child_minion_ids: List[str] = field(default_factory=list)
    horde_id: str = ""          # Which horde this minion belongs to
    legion_id: str = ""         # Parent legion

    # Communication
    channel_ids: List[str] = field(default_factory=list)

    # Session (Claude SDK)
    session_id: str = ""        # SDK session ID
    claude_code_session_id: Optional[str] = None  # For resumption
    state: MinionState = MinionState.CREATED
    is_processing: bool = False  # Currently processing user/minion input

    # Initialization
    initialization_context: str = ""  # System prompt for this minion
    permission_mode: str = "default"
    model: str = "claude-3-sonnet-20241022"
    tools: List[str] = field(default_factory=list)

    # Forking
    forked_from: Optional[str] = None  # If cloned from another minion
    fork_generation: int = 0    # 0=original, 1=first fork, etc.

    # Capabilities (for central registry discovery - MVP)
    capabilities: List[str] = field(default_factory=list)
    # Example: ["database_design", "postgres", "schema_migration"]

    expertise_scores: Dict[str, float] = field(default_factory=dict)
    # Format: {capability: score} where score is 0.0-1.0

    known_minions: Dict[str, List[str]] = field(default_factory=dict)
    # Format: {minion_id: [capability1, capability2, ...]}
    # Used for distributed gossip-based discovery (deferred to post-MVP)

    # Memory paths
    memory_directory: str = ""  # data/legions/{legion}/minions/{minion}

    # Quality tracking (for memory reinforcement)
    success_count: int = 0
    failure_count: int = 0
    correction_count: int = 0

    # Lifecycle
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    disposed_at: Optional[datetime] = None

    # Error state
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "minion_id": self.minion_id,
            "name": self.name,
            "role": self.role,
            "is_overseer": self.is_overseer,
            "overseer_level": self.overseer_level,
            "parent_overseer_id": self.parent_overseer_id,
            "child_minion_ids": self.child_minion_ids,
            "horde_id": self.horde_id,
            "legion_id": self.legion_id,
            "channel_ids": self.channel_ids,
            "session_id": self.session_id,
            "claude_code_session_id": self.claude_code_session_id,
            "state": self.state.value,
            "is_processing": self.is_processing,
            "initialization_context": self.initialization_context,
            "permission_mode": self.permission_mode,
            "model": self.model,
            "tools": self.tools,
            "forked_from": self.forked_from,
            "fork_generation": self.fork_generation,
            "capabilities": self.capabilities,
            "expertise_scores": self.expertise_scores,
            "known_minions": self.known_minions,
            "memory_directory": self.memory_directory,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "correction_count": self.correction_count,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "disposed_at": self.disposed_at.isoformat() if self.disposed_at else None,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MinionInfo':
        """Create from dictionary."""
        data = data.copy()
        data["state"] = MinionState(data["state"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["last_activity"] = datetime.fromisoformat(data["last_activity"])
        if data.get("disposed_at"):
            data["disposed_at"] = datetime.fromisoformat(data["disposed_at"])
        return cls(**data)


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

    # Communication log
    comm_log_path: str = ""     # Path to comms.jsonl

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

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
            "comm_log_path": self.comm_log_path,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Channel':
        """Create from dictionary."""
        data = data.copy()
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
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

    # Destination
    to_minion_id: Optional[str] = None    # Direct to minion
    to_channel_id: Optional[str] = None   # Broadcast to channel
    to_user: bool = False

    # Content
    content: str = ""
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
            "to_minion_id": self.to_minion_id,
            "to_channel_id": self.to_channel_id,
            "to_user": self.to_user,
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
