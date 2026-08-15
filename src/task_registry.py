"""Per-session registry of background-agent (SDK Task) lifecycle legs.

Tracks the four SDK Task lifecycle frame types — task_started, task_progress,
task_notification, task_updated — keyed by task_id, giving reload/reconnect a
task_id-first source of truth for "is this background agent actually done"
that is independent of the Agent tool call's own dispatch-ack status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Task statuses that mean a leg has finished. Mirrors claude_agent_sdk's own
# TERMINAL_TASK_STATUSES — task_notification never reports "killed" (it maps
# that to "stopped" itself), but task_updated can, so both are accepted here.
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped", "killed"})

# task_updated (and, in principle, any future terminal source) may report the
# raw SDK status "killed" for a TaskStop-terminated task. Normalized to
# "stopped" for display consistency with task_notification's own vocabulary —
# per the SDK's docs, consumers should treat the two the same way.
_STATUS_DISPLAY_MAP = {"killed": "stopped"}

TASK_LIFECYCLE_SUBTYPES = frozenset(
    {"task_started", "task_progress", "task_notification", "task_updated"}
)


def _normalize_status(status: str | None) -> str | None:
    if not status:
        return None
    return _STATUS_DISPLAY_MAP.get(status, status)


@dataclass
class TaskLeg:
    """A single launch-to-terminal-state run of a background agent task."""

    tool_use_id: str | None
    description: str | None
    started_at: float | None
    last_progress_at: float | None = None
    ended_at: float | None = None
    status: str = "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_use_id": self.tool_use_id,
            "description": self.description,
            "started_at": self.started_at,
            "last_progress_at": self.last_progress_at,
            "ended_at": self.ended_at,
            "status": self.status,
        }


@dataclass
class TaskLegEntry:
    """All known legs for a single task_id, in arrival order."""

    task_id: str
    legs: list[TaskLeg] = field(default_factory=list)

    @property
    def latest_leg(self) -> TaskLeg | None:
        return self.legs[-1] if self.legs else None

    @property
    def current_status(self) -> str | None:
        latest = self.latest_leg
        return latest.status if latest else None

    @property
    def description(self) -> str | None:
        latest = self.latest_leg
        return latest.description if latest else None

    def to_dict(self) -> dict[str, Any]:
        latest = self.latest_leg
        return {
            "task_id": self.task_id,
            "description": self.description,
            "legs": [leg.to_dict() for leg in self.legs],
            "latest_leg": latest.to_dict() if latest else None,
            "current_status": self.current_status,
        }


class TaskLegRegistry:
    """Per-session, in-memory index of background-agent (Task) lifecycle legs.

    Keyed by task_id — the one field the SDK guarantees stable across a
    resumed agent's legs (a stopped-and-resumed agent reuses task_id across
    two separate task_started frames). tool_use_id is per-leg: the tool call
    that triggered *that* leg's start, and must never be used as the
    cross-leg key.

    Fed live from the four SDK lifecycle frame types as they stream in, and
    rebuilt identically by replaying the same four frame types from stored
    messages on session reload — the two code paths must agree so a page
    refresh reconstructs the same state a live-streamed session would have
    reached.
    """

    def __init__(self) -> None:
        self._entries: dict[str, TaskLegEntry] = {}

    def apply_frame(
        self, subtype: str, metadata: dict[str, Any], timestamp: float | None = None
    ) -> None:
        """Apply one lifecycle frame's parsed metadata to the registry.

        `metadata` is the same shape message_parser's Task*Handler classes
        produce (and the reload path's _convert_stored_message_to_websocket
        reconstructs) — task_id, tool_use_id, description, status, patch.
        """
        task_id = metadata.get("task_id")
        if not task_id or subtype not in TASK_LIFECYCLE_SUBTYPES:
            return

        if subtype == "task_started":
            entry = self._entries.setdefault(task_id, TaskLegEntry(task_id=task_id))
            entry.legs.append(TaskLeg(
                tool_use_id=metadata.get("tool_use_id"),
                description=metadata.get("description"),
                started_at=timestamp,
                last_progress_at=timestamp,
            ))
            return

        # No task_started on record for this task_id — malformed/incomplete
        # frame sequence. Nothing to attach this frame to; do not create a
        # leg-less entry as a side effect of merely looking one up.
        entry = self._entries.get(task_id)
        if entry is None:
            return
        leg = entry.latest_leg
        if leg is None:
            return

        if subtype == "task_progress":
            # No-op once a leg has reached a terminal status: progress arriving
            # after termination must not resurrect it.
            if leg.status != "running":
                return
            leg.last_progress_at = timestamp
            if metadata.get("description") and not leg.description:
                leg.description = metadata["description"]
            return

        # task_notification / task_updated: terminal status carrier.
        # First-terminal-wins: a leg that's already terminal must not be
        # overwritten by a later/duplicate terminal frame (the SDK's own docs
        # note task_notification is only "sometimes" suppressed after a
        # task_updated has already closed a leg out, or vice versa).
        if leg.status != "running":
            return

        if subtype == "task_notification":
            raw_status = metadata.get("status")
        else:  # task_updated
            patch = metadata.get("patch") or {}
            raw_status = metadata.get("status") or patch.get("status")

        if raw_status not in TERMINAL_TASK_STATUSES:
            return

        leg.status = _normalize_status(raw_status)
        leg.ended_at = timestamp

    def snapshot(self) -> list[dict[str, Any]]:
        """Ordered snapshot of every known task_id's leg history."""
        return [entry.to_dict() for entry in self._entries.values()]

    def current_status(self, task_id: str) -> str | None:
        entry = self._entries.get(task_id)
        return entry.current_status if entry else None
