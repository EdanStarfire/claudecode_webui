"""
Data models for Claude WebUI.

Includes:
- Message models for unified SDK and WebUI message handling
- Legion models for multi-agent communication
- Memory models for knowledge management
- Archive models for minion disposal archival

NOTE: LegionInfo and MinionInfo have been consolidated:
- Legions are ProjectInfo (all projects support minions - issue #313)
- All sessions are minions (is_minion field removed - issue #349)
"""

from backend.models.archive_models import (
    ArchiveResult,
    DisposalMetadata,
)
from backend.models.legion_models import (
    Comm,
    CommType,
    InterruptPriority,
)
from backend.models.memory_models import (
    MemoryEntry,
    MemoryType,
    MinionMemory,
    TaskMilestone,
)
from backend.models.messages import (
    DisplayMetadata,
    DisplayProjection,
    PermissionInfo,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PermissionSuggestion,
    StoredMessage,
    ToolCall,
    ToolDisplayInfo,
    ToolState,
)
from backend.models.permission_mode import PermissionMode
from backend.models.schedule_models import (
    Schedule,
    ScheduleExecution,
    ScheduleStatus,
)

__all__ = [
    # Permission mode enum (Issue #955)
    "PermissionMode",
    # Message models (Issue #310)
    "StoredMessage",
    "PermissionRequestMessage",
    "PermissionResponseMessage",
    "PermissionSuggestion",
    "DisplayMetadata",
    "DisplayProjection",
    "ToolDisplayInfo",
    "ToolState",
    # Unified ToolCall (Issue #324)
    "ToolCall",
    "PermissionInfo",
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
    # Schedule models (Issue #495)
    "Schedule",
    "ScheduleExecution",
    "ScheduleStatus",
]
