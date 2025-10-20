"""
Memory data models for Legion multi-agent system.

This module defines data structures for minion memory management including
memory entries, distillation, and reinforcement learning.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any


class MemoryType(Enum):
    """Type of memory entry."""
    FACT = "fact"               # Discrete piece of knowledge
    PATTERN = "pattern"         # Recognized pattern
    RULE = "rule"               # Procedural knowledge
    RELATIONSHIP = "relationship"  # Connection between entities
    EVENT = "event"             # Significant occurrence


@dataclass
class MemoryEntry:
    """
    Single unit of minion memory.
    """
    entry_id: str               # UUID
    content: str                # The actual knowledge
    entry_type: MemoryType

    # Source
    source_task_id: Optional[str] = None
    source_comm_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    # Reinforcement learning
    quality_score: float = 0.5  # 0.0 to 1.0
    times_used_successfully: int = 0
    times_used_unsuccessfully: int = 0
    last_reinforcement: Optional[datetime] = None

    # Context
    related_capabilities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "entry_id": self.entry_id,
            "content": self.content,
            "entry_type": self.entry_type.value,
            "source_task_id": self.source_task_id,
            "source_comm_id": self.source_comm_id,
            "created_at": self.created_at.isoformat(),
            "quality_score": self.quality_score,
            "times_used_successfully": self.times_used_successfully,
            "times_used_unsuccessfully": self.times_used_unsuccessfully,
            "last_reinforcement": self.last_reinforcement.isoformat() if self.last_reinforcement else None,
            "related_capabilities": self.related_capabilities,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryEntry':
        """Create from dictionary."""
        data = data.copy()
        data["entry_type"] = MemoryType(data["entry_type"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("last_reinforcement"):
            data["last_reinforcement"] = datetime.fromisoformat(data["last_reinforcement"])
        return cls(**data)


@dataclass
class MinionMemory:
    """
    Complete memory structure for a minion.
    """
    minion_id: str

    # Session memory (full conversation history)
    # Stored in: session_messages.jsonl (existing format)

    # Short-term memory (recent distillations)
    short_term: List[MemoryEntry] = field(default_factory=list)
    # Stored in: short_term_memory.json

    # Long-term memory (promoted patterns/rules)
    long_term: List[MemoryEntry] = field(default_factory=list)
    # Stored in: long_term_memory.json

    # Capability evidence (tasks demonstrating capabilities)
    capability_evidence: Dict[str, List[str]] = field(default_factory=dict)
    # Format: {capability: [task_id1, task_id2, ...]}
    # Stored in: capability_evidence.json

    # Last distillation timestamp
    last_distilled_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "minion_id": self.minion_id,
            "short_term": [entry.to_dict() for entry in self.short_term],
            "long_term": [entry.to_dict() for entry in self.long_term],
            "capability_evidence": self.capability_evidence,
            "last_distilled_at": self.last_distilled_at.isoformat() if self.last_distilled_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MinionMemory':
        """Create from dictionary."""
        data = data.copy()
        data["short_term"] = [MemoryEntry.from_dict(entry) for entry in data.get("short_term", [])]
        data["long_term"] = [MemoryEntry.from_dict(entry) for entry in data.get("long_term", [])]
        if data.get("last_distilled_at"):
            data["last_distilled_at"] = datetime.fromisoformat(data["last_distilled_at"])
        return cls(**data)


@dataclass
class TaskMilestone:
    """
    Represents completion of a task or sub-task.
    Triggers memory distillation.
    """
    milestone_id: str           # UUID
    task_id: str                # Task identifier
    minion_id: str              # Which minion completed this
    description: str            # What was accomplished

    # Completion
    completed_at: datetime = field(default_factory=datetime.now)
    success: bool = True        # True if successful, False if failed

    # Distillation
    messages_to_distill: List[str] = field(default_factory=list)  # Message IDs from this chunk
    distilled: bool = False
    distilled_memory_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "milestone_id": self.milestone_id,
            "task_id": self.task_id,
            "minion_id": self.minion_id,
            "description": self.description,
            "completed_at": self.completed_at.isoformat(),
            "success": self.success,
            "messages_to_distill": self.messages_to_distill,
            "distilled": self.distilled,
            "distilled_memory_ids": self.distilled_memory_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskMilestone':
        """Create from dictionary."""
        data = data.copy()
        data["completed_at"] = datetime.fromisoformat(data["completed_at"])
        return cls(**data)
