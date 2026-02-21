"""
Data models for cron-based scheduling system.

Defines Schedule, ScheduleStatus, and ScheduleExecution for
recurring task delivery to minion agents.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from croniter import croniter


class ScheduleStatus(Enum):
    """Status of a schedule."""
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass
class Schedule:
    """
    A recurring cron schedule that delivers a prompt to a minion.
    """
    schedule_id: str
    legion_id: str
    minion_id: str
    minion_name: str
    name: str
    cron_expression: str
    prompt: str
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    reset_session: bool = False
    max_retries: int = 3
    timeout_seconds: int = 3600
    created_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())
    updated_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())
    next_run: float | None = None
    last_run: float | None = None
    last_status: str | None = None
    execution_count: int = 0
    failure_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "schedule_id": self.schedule_id,
            "legion_id": self.legion_id,
            "minion_id": self.minion_id,
            "minion_name": self.minion_name,
            "name": self.name,
            "cron_expression": self.cron_expression,
            "prompt": self.prompt,
            "reset_session": self.reset_session,
            "status": self.status.value,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_run": self.next_run,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "execution_count": self.execution_count,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        """Create from dictionary."""
        data = data.copy()
        data["status"] = ScheduleStatus(data["status"])
        data.setdefault("reset_session", False)
        return cls(**data)


@dataclass
class ScheduleExecution:
    """
    Record of a single schedule execution attempt.
    Appended to schedule_history.jsonl.
    """
    execution_id: str
    schedule_id: str
    scheduled_time: float
    actual_time: float
    status: str  # "queued" | "failed" | "timeout" | "retry"
    minion_state: str
    error_message: str | None = None
    retry_number: int = 0
    queue_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "execution_id": self.execution_id,
            "schedule_id": self.schedule_id,
            "scheduled_time": self.scheduled_time,
            "actual_time": self.actual_time,
            "status": self.status,
            "minion_state": self.minion_state,
            "error_message": self.error_message,
            "retry_number": self.retry_number,
            "queue_id": self.queue_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleExecution":
        """Create from dictionary. Handles legacy comm_id field."""
        data = data.copy()
        # Migrate legacy comm_id to queue_id
        if "comm_id" in data and "queue_id" not in data:
            data["queue_id"] = data.pop("comm_id")
        elif "comm_id" in data:
            data.pop("comm_id")
        return cls(**data)


def validate_cron_expression(expression: str) -> bool:
    """Validate a cron expression (5-field: min hour dom mon dow)."""
    return croniter.is_valid(expression)


def get_next_run(cron_expression: str, base_time: float | None = None) -> float:
    """Calculate the next run time for a cron expression.

    Args:
        cron_expression: Standard 5-field cron expression.
        base_time: Unix timestamp to calculate from. Defaults to now.

    Returns:
        Unix timestamp of next execution.
    """
    if base_time is not None:
        base = datetime.fromtimestamp(base_time).astimezone()
    else:
        base = datetime.now().astimezone()
    cron = croniter(cron_expression, base)
    next_dt = cron.get_next(datetime)
    return next_dt.timestamp()
