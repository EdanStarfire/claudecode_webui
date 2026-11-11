"""Public (owner-facing) shape of a monitor record for the REST surface (issue #2103 3b-ii; the
monitors panel is 3d). Labels are plain text resolved through ``NameResolver`` for the OWNER; never
message content, never the persisted delivered-key list. ``last_seen_ts`` is where the monitor has
caught up to (a Slack ts); ``failures`` / ``flood_windows`` are plain counters."""

from __future__ import annotations

from typing import Any


def effective_state(record: dict[str, Any]) -> str:
    if record.get("paused"):
        return "paused"
    if record.get("status") != "active":
        return "suspended"
    return "active"


def public_monitor(
    record: dict[str, Any], *, session_name: str | None, handle: str | None,
    channel_label: str | None, user_labels: dict[str, str | None],
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
    }
