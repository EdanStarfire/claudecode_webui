"""
Data models for Legion multi-agent system.
"""

from src.models.legion_models import (
    LegionInfo,
    MinionInfo,
    MinionState,
    Horde,
    Channel,
    Comm,
    CommType,
    InterruptPriority,
)

from src.models.memory_models import (
    MemoryEntry,
    MemoryType,
    MinionMemory,
    TaskMilestone,
)

__all__ = [
    # Legion models
    "LegionInfo",
    "MinionInfo",
    "MinionState",
    "Horde",
    "Channel",
    "Comm",
    "CommType",
    "InterruptPriority",
    # Memory models
    "MemoryEntry",
    "MemoryType",
    "MinionMemory",
    "TaskMilestone",
]
