"""
Data models for Legion multi-agent system.

NOTE: LegionInfo and MinionInfo have been consolidated:
- Legions are now ProjectInfo with is_multi_agent=True (see src/project_manager.py)
- Minions are now SessionInfo with is_minion=True (see src/session_manager.py)
"""

from src.models.legion_models import (
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
    # Legion grouping models
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
