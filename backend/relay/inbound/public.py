"""Public (owner-facing) shape of a monitor record for the REST surface (issue #2103 3b-ii; the
monitors panel is 3d). Labels are plain text resolved through ``NameResolver`` for the OWNER; never
message content, never the persisted delivered-key list. ``last_seen_ts`` is where the monitor has
caught up to (a Slack ts); ``failures`` / ``flood_windows`` are plain counters."""

from __future__ import annotations

from typing import Any

from . import constants as const
from .schedule import describe


def effective_state(record: dict[str, Any]) -> str:
    if record.get("paused"):
        return "paused"
    if record.get("status") != "active":
        return "suspended"
    return "active"


def monitor_mode(record: dict[str, Any]) -> str:
    """``live`` | ``digest`` | ``unread`` (an unread monitor is stored as a digest of kind monitor_unread)."""
    if record.get("kind") == const.KIND_MONITOR_UNREAD:
        return "unread"
    return "digest" if record.get("mode") == const.MODE_DIGEST else "live"


_LAST_RUN_KEYS = ("at", "outcome", "messages", "omitted", "gap", "late", "trigger", "code", "retry", "retry_at")


def _schedule_fields(record: dict[str, Any], channel_labels: dict[str, str | None]) -> dict[str, Any]:
    mode = monitor_mode(record)
    schedule = record.get("schedule") if mode != "live" else None
    last_run = record.get("last_run")
    options = record.get("options") if mode == "unread" else None
    unread = None
    if options is not None:
        channels = list(options.get("channels") or [])
        exclude = list(options.get("exclude") or [])
        unread = {
            "dms": bool(options.get("dms", True)), "channels": channels, "exclude": exclude,
            "mentions_only": bool(options.get("mentions_only")), "repeat": bool(options.get("repeat")),
            "labels": {c: channel_labels.get(c) or c for c in channels + exclude},
        }
    return {
        "mode": mode,
        "schedule": dict(schedule) if schedule else None,
        "schedule_text": describe(schedule) if schedule else None,
        "tz": schedule.get("tz") if schedule and schedule.get("type") == "daily" else None,
        "tz_source": schedule.get("tz_source") if schedule and schedule.get("type") == "daily" else None,
        "next_run": record.get("next_run") if mode != "live" else None,
        "last_run_at": record.get("last_run_at") if mode != "live" else None,
        "last_run": {k: last_run[k] for k in _LAST_RUN_KEYS if k in last_run} if last_run else None,
        "last_run_trigger": record.get("last_run_trigger") if mode != "live" else None,
        "run_failures": int(record.get("run_failures") or 0) if mode != "live" else 0,
        "unread": unread,
        "unread_text": unread_text(unread) if unread else None,
        "notified_count": len(record.get("notified") or {}) if mode == "unread" else 0,
    }


def unread_text(unread: dict[str, Any]) -> str:
    """The unread options in words (labels are plain text; IDs when unresolved)."""
    labels = unread.get("labels") or {}

    parts = []
    if unread.get("dms"):
        n = len(unread.get("exclude") or [])
        parts.append("all direct and group messages" + (f" except {n}" if n else ""))
    if unread.get("channels"):
        parts.append("channels " + " ".join(labels.get(c) or c for c in unread["channels"]))
    text = ", ".join(parts) or "nothing selected"
    if unread.get("mentions_only"):
        text += ", only messages that mention you"
    if not unread.get("repeat"):
        text += ", only newly unread"
    return text


def public_monitor(
    record: dict[str, Any], *, session_name: str | None, handle: str | None,
    channel_label: str | None, user_labels: dict[str, str | None],
    channel_labels: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    filters = record.get("filters") or {}
    users = list(filters.get("users") or [])
    return {
        "id": record["id"], "kind": record["kind"], "session_id": record.get("session_id"),
        "session_name": session_name, "handle": handle, "created_by": record.get("created_by"),
        "created_at": record.get("created_at"), "expires_at": record.get("expires_at"),
        "status": record.get("status"), "suspended_reason": record.get("suspended_reason"),
        "paused": bool(record.get("paused")), "effective_state": effective_state(record),
        "channel": record.get("channel"), "channel_label": channel_label,
        "thread_ts": record.get("thread_ts"),
        "filters": {
            "keywords": list(filters.get("keywords") or []), "users": users,
            "user_labels": {u: user_labels.get(u) for u in users},
        },
        "consent": record.get("consent"), "last_delivered_at": record.get("last_delivered_at"),
        "last_error": record.get("last_error"), "counters": dict(record.get("counters") or {}),
        "last_seen_ts": record.get("last_seen_ts"), "last_activity_at": record.get("last_activity_at"),
        "failures": int(record.get("failures") or 0), "flood_windows": int(record.get("flood_windows") or 0),
        **_schedule_fields(record, channel_labels or {}),
    }
