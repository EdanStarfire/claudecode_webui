"""
Data models for Claude WebUI.

Includes:
- Message models for unified SDK and WebUI message handling
- Legion models for multi-agent communication
- Memory models for knowledge management
- Archive models for minion disposal archival

NOTE: LegionInfo and MinionInfo have been consolidated:
- Legions are now ProjectInfo with is_multi_agent=True (see src/project_manager.py)
- Minions are now SessionInfo with is_minion=True (see src/session_manager.py)
"""

from src.models.archive_models import (
    ArchiveResult,
    DisposalMetadata,
)
from src.models.legion_models import (
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
from src.models.messages import (
    DisplayMetadata,
    DisplayProjection,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PermissionSuggestion,
    StoredMessage,
    ToolDisplayInfo,
    ToolState,
)

__all__ = [
    # Message models (Issue #310)
    "StoredMessage",
    "PermissionRequestMessage",
    "PermissionResponseMessage",
    "PermissionSuggestion",
    "DisplayMetadata",
    "DisplayProjection",
    "ToolDisplayInfo",
    "ToolState",
    # Legion communication models
    "Comm",
    "CommType",
    "InterruptPriority",
    # Memory models
    "MemoryEntry",
    "MemoryType",
    "MinionMemory",
    "TaskMilestone",
    # Archive models (Issue #236)
    "ArchiveResult",
    "DisposalMetadata",
]
