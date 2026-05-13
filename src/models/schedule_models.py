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

# Maximum bytes of stdout/stderr persisted per run. Larger streams are truncated
# with a "[truncated: N bytes]" marker. Bounds schedules.json + schedule_history.jsonl.
MAX_STREAM_BYTES = 64 * 1024


def cap_stream(s: str) -> str:
    """Cap a stdout/stderr string to MAX_STREAM_BYTES, appending a truncation marker."""
    if not s:
        return s
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_STREAM_BYTES:
        return s
    truncated = encoded[:MAX_STREAM_BYTES].decode("utf-8", errors="replace")
    return truncated + f"\n[truncated: {len(encoded)} bytes]"


class ScheduleStatus(Enum):
    """Status of a schedule."""
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass
class Schedule:
    """
    A recurring cron schedule that delivers a prompt to a minion.
    """
    schedule_id: str
    legion_id: str
    name: str
    cron_expression: str
    prompt: str
    minion_id: str | None = None  # None for ephemeral schedules (issue #578)
    minion_name: str | None = None  # None for ephemeral schedules (issue #578)
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
    # Ephemeral session support (issue #578)
    session_config: dict | None = None  # Stored session configuration for ephemeral schedules
    ephemeral_agent_id: str | None = None  # Fixed agent session ID for ephemeral schedules
    # Script schedule support (issue #1356)
    schedule_type: str = "prompt"            # "prompt" | "script"
    script_command: str | None = None        # required when schedule_type == "script"
    script_timeout_seconds: int = 60         # wall-clock kill timer for script runs
    last_stdout: str | None = None           # most recent run's stdout (capped)
    last_stderr: str | None = None           # most recent run's stderr (capped)
    last_exit_code: int | None = None        # null when no run yet, -1 on timeout

    def to_dict(self, metrics: "ScheduleMetrics | None" = None) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Pass metrics to include the aggregate metrics sub-object in API responses.
        When called without metrics (e.g. from _persist_schedules), the key is omitted
        so schedules.json stays lean.
        """
        data = {
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
            "schedule_type": self.schedule_type,
            "script_command": self.script_command,
            "script_timeout_seconds": self.script_timeout_seconds,
            "last_stdout": self.last_stdout,
            "last_stderr": self.last_stderr,
            "last_exit_code": self.last_exit_code,
        }
        # Ephemeral fields (issue #578) - only include when set
        if self.session_config is not None:
            data["session_config"] = self.session_config
        if self.ephemeral_agent_id is not None:
            data["ephemeral_agent_id"] = self.ephemeral_agent_id
        if metrics is not None:
            data["metrics"] = {
                "total_runs": metrics.total_runs,
                "total_errors": metrics.total_errors,
                "consecutive_errors": metrics.consecutive_errors,
                "last_success_time": metrics.last_success_time,
                "last_error_time": metrics.last_error_time,
                "last_error_message": metrics.last_error_message,
                "last_status": metrics.last_status,
                "last_run": metrics.last_run,
            }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Schedule":
        """Create from dictionary."""
        data = data.copy()
        # Migrate legacy "cancelled" status — enum value removed in #1416
        if data.get("status") == "cancelled":
            data["status"] = "paused"
        data["status"] = ScheduleStatus(data["status"])
        data.setdefault("reset_session", False)
        # Ephemeral fields (issue #578) - default to None if missing
        data.setdefault("session_config", None)
        data.setdefault("ephemeral_agent_id", None)
        # Script schedule fields (issue #1356) - backwards-compatible defaults
        data.setdefault("schedule_type", "prompt")
        data.setdefault("script_command", None)
        data.setdefault("script_timeout_seconds", 60)
        data.setdefault("last_stdout", None)
        data.setdefault("last_stderr", None)
        data.setdefault("last_exit_code", None)
        # Discard legacy field from old data
        data.pop("current_ephemeral_session_id", None)
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
    status: str  # "queued" | "failed" | "timeout" | "retry" | "delivered" | "discarded" | "error"
    minion_state: str
    error_message: str | None = None
    retry_number: int = 0
    queue_id: str | None = None
    trigger: str = "cron"  # "cron" or "manual"
    # Script schedule fields (issue #1356)
    schedule_type: str = "prompt"
    exit_code: int | None = None
    stdout: str | None = None     # capped
    stderr: str | None = None     # capped
    duration_ms: int | None = None

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
            "trigger": self.trigger,
            "schedule_type": self.schedule_type,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
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
        # Backward compat: default trigger to "cron" for old records
        data.setdefault("trigger", "cron")
        # Script schedule fields (issue #1356)
        data.setdefault("schedule_type", "prompt")
        data.setdefault("exit_code", None)
        data.setdefault("stdout", None)
        data.setdefault("stderr", None)
        data.setdefault("duration_ms", None)
        return cls(**data)


def is_error_status(status: str) -> bool:
    """Return True if status counts as an error for metric purposes.

    Error: failed, timeout, error
    Success: queued, delivered, discarded
    Ignored: retry (intermediate — the retried fire generates its own record)
    """
    return status in {"failed", "timeout", "error"}


@dataclass
class ScheduleMetrics:
    """Aggregate lifetime metrics for one schedule, stored in schedule_metrics.json.

    Retained indefinitely; never pruned with schedule_history.jsonl.
    """

    schedule_id: str
    total_runs: int = 0
    total_errors: int = 0
    consecutive_errors: int = 0
    last_success_time: float | None = None
    last_error_time: float | None = None
    last_error_message: str | None = None
    last_status: str | None = None
    last_run: float | None = None
    updated_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "total_runs": self.total_runs,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
            "last_success_time": self.last_success_time,
            "last_error_time": self.last_error_time,
            "last_error_message": self.last_error_message,
            "last_status": self.last_status,
            "last_run": self.last_run,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleMetrics":
        return cls(
            schedule_id=data["schedule_id"],
            total_runs=data.get("total_runs", 0),
            total_errors=data.get("total_errors", 0),
            consecutive_errors=data.get("consecutive_errors", 0),
            last_success_time=data.get("last_success_time"),
            last_error_time=data.get("last_error_time"),
            last_error_message=data.get("last_error_message"),
            last_status=data.get("last_status"),
            last_run=data.get("last_run"),
            updated_at=data.get("updated_at", datetime.now(UTC).timestamp()),
        )


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
