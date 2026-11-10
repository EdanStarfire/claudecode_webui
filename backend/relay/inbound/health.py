"""Inbound health assembler (issue #2103 stage 3d-i).

Pure: ``build_health`` turns the live connection facts, the monitor records and an audit aggregate
(``AuditScan``) into the payload of ``GET /api/relays/{id}/inbound/health``. Counts, codes, ids and
times only: never message text, never a Slack call. The attention rules below are frozen constants
so the owner-facing noise level is tuned in one place (and unit-tested rule by rule).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from ..audit import AuditScan
from . import constants as const
from .public import effective_state

ATTENTION = "attention"  # amber dot: the owner has to act
INFO = "info"  # plain tag: an owner-caused or self-healing state

WINDOW_SECONDS = 24 * 3600
SCAN_CACHE_SECONDS = 30.0
SCAN_MAX_ROWS = 5000
RECONNECTING_LONG_SECONDS = 120.0  # backoff / connecting for longer than this needs attention
EXPIRING_SOON_SECONDS = 24 * 3600

# Audit events the health aggregate reads (an exact list: the scan skips every other row).
HEALTH_EVENTS = [
    "inbound_connection", "inbound_instruction", "inbound_housekeeping",
    "monitor_delivered", "monitor_delivery_failed", "monitor_flood", "monitor_backfill",
    "monitor_suspended", "monitor_paused",
]
# monitor_backfill ``reason_counts`` keys that mean a message was not delivered
BACKFILL_DROP_REASONS = frozenset({
    "non_member", "hydration_cap", "hydration_failed", "conversation_cap", "rate_limited",
})
BACKFILL_CAP_REASONS = ("hydration_cap", "conversation_cap")
# ``inbound_status`` detail codes of a ``disabled`` status that mean "monitors cannot work"
OFF_DETAILS = frozenset({"disabled_by_owner", "no_app_token", "no_user_token"})

# Suspension reason -> (attention code, severity, audit events the Audit link filters on)
SUSPENSION_RULES: dict[str, tuple[str, str, list[str]]] = {
    const.SUSPEND_FLOOD: ("monitors_flood", ATTENTION, ["monitor_flood", "monitor_suspended"]),
    const.SUSPEND_DELIVERY_FAILED: (
        "monitors_delivery_failed", ATTENTION, ["monitor_delivery_failed", "monitor_suspended"]),
    const.SUSPEND_POLICY_UNSET: ("monitors_policy_unset", ATTENTION, ["monitor_suspended"]),
    const.SUSPEND_CHANNEL_BLOCKED: ("monitors_channel_blocked", INFO, ["monitor_suspended"]),
    const.SUSPEND_DISPOSED: ("monitors_disposed", INFO, ["monitor_suspended"]),
    const.SUSPEND_SESSION_INACTIVE: ("monitors_session_inactive", INFO, ["monitor_suspended"]),
}

# Every code build_health can emit, with its severity (the frozen contract the tests pin).
ATTENTION_CODES: dict[str, str] = {
    "auth_failed": ATTENTION,
    "transport_unavailable": ATTENTION,
    "reconnecting_long": ATTENTION,
    "inbound_off_with_monitors": ATTENTION,
    "monitor_error": ATTENTION,
    "backfill_gap_24h": ATTENTION,
    "backfill_cap_24h": ATTENTION,
    "instruction_delivery_failed_24h": ATTENTION,
    "monitors_paused": INFO,
    "monitors_expiring": INFO,
    **{code: sev for code, sev, _events in SUSPENSION_RULES.values()},
}


def window_start_iso(now: float) -> str:
    return iso(now - WINDOW_SECONDS) or ""


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def parse_iso(ts: str | None) -> float | None:
    try:
        return datetime.fromisoformat(ts).timestamp() if ts else None
    except ValueError:
        return None


def _item(
    code: str, since: str | None, count: int, events: list[str],
    session_ids: list[str] | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {"events": events}
    item: dict[str, Any] = {
        "code": code, "severity": ATTENTION_CODES[code], "since": since, "count": count, "audit": audit,
    }
    if session_ids:
        item["session_ids"] = session_ids
        if len(session_ids) == 1:
            audit["session_id"] = session_ids[0]
    return item


def _monitor_counts(monitors: list[dict[str, Any]]) -> dict[str, Any]:
    states = Counter(effective_state(m) for m in monitors)
    reasons = Counter(
        m.get("suspended_reason") or "unknown" for m in monitors if effective_state(m) == "suspended"
    )
    return {
        "total": len(monitors), "active": states["active"], "paused": states["paused"],
        "suspended": states["suspended"], "by_reason": dict(reasons),
    }


def _window(scan: AuditScan) -> dict[str, Any]:
    backfill_reasons = scan.reasons.get("monitor_backfill", Counter())
    housekeeping_truncated = scan.rows_for("inbound_housekeeping", code="backfill_truncated")
    caps = (  # events (runs), not messages: rows that hit a cap
        sum(scan.reason_rows[("monitor_backfill", r)] for r in BACKFILL_CAP_REASONS)
        + scan.rows_for("monitor_backfill", code="backfill_truncated") + housekeeping_truncated
    )
    instructions = {
        name: scan.rows_for("inbound_instruction", outcome=name)
        for name in ("delivered", "refused", "late", "rate_limited", "delivery_failed")
    }
    return {
        "since": scan.since, "truncated": scan.truncated, "rows_read": scan.rows_read,
        "delivered_envelopes": scan.rows_for("monitor_delivered"),
        "delivered_messages": scan.sum_for("monitor_delivered"),
        "dropped_messages": sum(backfill_reasons[r] for r in BACKFILL_DROP_REASONS),
        "flood_events": scan.rows_for("monitor_flood"),
        "delivery_failed": scan.rows_for("monitor_delivery_failed") + instructions["delivery_failed"],
        "gap_events": scan.rows_for("monitor_backfill", code="gap_notice"),
        "cap_events": caps,
        "instructions": instructions,
        "connection_changes": scan.rows_for("inbound_connection"),
    }


def _last_backfill_audit(scan: AuditScan) -> dict[str, Any] | None:
    row = scan.latest.get("monitor_backfill")
    if row is None:
        return None
    return {"ts": row["ts"], "code": row["code"], "reason_counts": row["reason_counts"] or {}}


def build_health(
    *, relay_id: str, enabled: bool, status: str, detail: str | None,
    status_since: float | None, connected_at: float | None, inflight: int,
    monitors: list[dict[str, Any]], last_backfill: dict[str, Any] | None, scan: AuditScan, now: float,
) -> dict[str, Any]:
    counts = _monitor_counts(monitors)
    window = _window(scan)
    attention: list[dict[str, Any]] = []
    since_iso = iso(status_since)

    # ---- connection
    if status == const.STATUS_AUTH_FAILED:
        attention.append(_item("auth_failed", since_iso, 1, ["inbound_connection"]))
    if status == const.STATUS_TRANSPORT_UNAVAILABLE:
        attention.append(_item("transport_unavailable", since_iso, 1, ["inbound_connection"]))
    if (
        status in (const.STATUS_BACKOFF, const.STATUS_CONNECTING) and status_since is not None
        and now - status_since > RECONNECTING_LONG_SECONDS
    ):
        attention.append(_item("reconnecting_long", since_iso, 1, ["inbound_connection"]))
    live = counts["active"] + counts["paused"]
    if status == const.STATUS_DISABLED and detail in OFF_DETAILS and live:
        attention.append(_item("inbound_off_with_monitors", since_iso, live, ["inbound_connection"]))

    # ---- suspended monitors, by reason
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for m in monitors:
        if effective_state(m) == "suspended":
            by_reason.setdefault(m.get("suspended_reason") or "", []).append(m)
    for reason, (code, _severity, events) in SUSPENSION_RULES.items():
        rows = by_reason.get(reason)
        if rows:
            attention.append(_item(
                code, scan.newest_for("monitor_suspended", code=reason), len(rows), events,
                sorted({m["session_id"] for m in rows if m.get("session_id")}),
            ))

    # ---- a monitor whose deliveries keep failing (not yet suspended)
    erroring = [
        m for m in monitors
        if int(m.get("failures") or 0) > 0 and m.get("last_error")
        and m.get("suspended_reason") != const.SUSPEND_DELIVERY_FAILED
    ]
    if erroring:
        attention.append(_item(
            "monitor_error", scan.newest_for("monitor_delivery_failed"), len(erroring),
            ["monitor_delivery_failed"], sorted({m["session_id"] for m in erroring if m.get("session_id")}),
        ))

    # ---- 24 h audit facts
    if window["gap_events"]:
        attention.append(_item(
            "backfill_gap_24h", scan.newest_for("monitor_backfill", code="gap_notice"),
            window["gap_events"], ["monitor_backfill"]))
    if window["cap_events"]:
        attention.append(_item(
            "backfill_cap_24h", scan.newest_for("monitor_backfill"), window["cap_events"],
            ["monitor_backfill", "inbound_housekeeping"]))
    if window["instructions"]["delivery_failed"]:
        attention.append(_item(
            "instruction_delivery_failed_24h",
            scan.newest_for("inbound_instruction", outcome="delivery_failed"),
            window["instructions"]["delivery_failed"], ["inbound_instruction"]))

    # ---- info-level tags
    paused = [m for m in monitors if effective_state(m) == "paused"]
    if paused:
        attention.append(_item(
            "monitors_paused", scan.newest_for("monitor_paused"), len(paused), ["monitor_paused"],
            sorted({m["session_id"] for m in paused if m.get("session_id")}),
        ))
    expiring = [
        m for m in monitors
        if m.get("expires_at") is not None and 0 < float(m["expires_at"]) - now < EXPIRING_SOON_SECONDS
    ]
    if expiring:
        attention.append(_item(
            "monitors_expiring", None, len(expiring), ["monitor_created"],
            sorted({m["session_id"] for m in expiring if m.get("session_id")}),
        ))

    # attention-level first, then info; stable inside each group
    attention.sort(key=lambda a: a["severity"] != ATTENTION)
    lb = None
    if last_backfill is not None:
        lb = {
            "at": iso(last_backfill.get("at")), "gap": bool(last_backfill.get("gap")),
            "gap_from": iso(last_backfill.get("gap_from")),
            "truncated": bool(last_backfill.get("truncated")),
            "counts": dict(last_backfill.get("counts") or {}),
        }
    return {
        "relay_id": relay_id, "enabled": enabled, "status": status, "detail": detail,
        "status_since": since_iso, "connected_at": iso(connected_at), "inflight": inflight,
        "monitors": counts, "last_backfill": lb, "last_backfill_audit": _last_backfill_audit(scan),
        "window_24h": window, "attention": attention,
    }


async def collect_health(
    audit, relay_id: str, *, enabled: bool, status: dict[str, Any], live_since: float | None,
    started_at: float | None, monitors: list[dict[str, Any]], last_backfill: dict[str, Any] | None,
    now: float,
) -> dict[str, Any]:
    """Scan the (cached) 24 h audit aggregate and assemble the payload.

    ``status_since`` is the last status change seen in this process; right after a restart it is seeded
    from the newest ``inbound_connection`` audit row of the current status, else the service start."""
    scan = await audit.scan_since(
        relay_id, window_start_iso(now), events=HEALTH_EVENTS, max_rows=SCAN_MAX_ROWS,
        cache_seconds=SCAN_CACHE_SECONDS,
    )
    since = live_since
    if since is None:
        row = scan.latest.get("inbound_connection")
        seeded = parse_iso(row["ts"]) if row and row.get("inbound_status") == status["status"] else None
        since = seeded if seeded is not None else started_at
    return build_health(
        relay_id=relay_id, enabled=enabled, status=status["status"], detail=status.get("detail"),
        status_since=since, connected_at=status.get("connected_at"), inflight=status.get("inflight", 0),
        monitors=monitors, last_backfill=last_backfill, scan=scan, now=now,
    )
