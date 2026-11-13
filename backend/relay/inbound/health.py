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
from .public import effective_state, monitor_mode

ATTENTION = "attention"  # amber dot: the owner has to act
INFO = "info"  # plain tag: an owner-caused or self-healing state

WINDOW_SECONDS = 24 * 3600
SCAN_CACHE_SECONDS = 30.0
SCAN_MAX_ROWS = 5000
RECONNECTING_LONG_SECONDS = 120.0  # backoff / connecting for longer than this needs attention
EXPIRING_SOON_SECONDS = 24 * 3600
DIGEST_FAILURES_ATTENTION = 3  # consecutive failed runs of one scheduled monitor
DIGEST_RATE_LIMITED_ROWS = 2  # reschedule / give-up rows in the window
DIGEST_OVERDUE_SECONDS = 600  # an active scheduled monitor this far past its slot: the scheduler is stuck

# Audit events the health aggregate reads (an exact list: the scan skips every other row).
HEALTH_EVENTS = [
    "inbound_connection", "inbound_instruction", "inbound_housekeeping",
    "monitor_delivered", "monitor_delivery_failed", "monitor_flood", "monitor_backfill",
    "monitor_suspended", "monitor_paused", "monitor_digest",
]
# ``monitor_digest`` codes that mean Slack rate limited a scheduled slot
DIGEST_RATE_LIMIT_CODES = ("rate_limited_rescheduled", "rate_limited_gave_up")
# monitor_backfill ``reason_counts`` keys that mean a message was not delivered
BACKFILL_DROP_REASONS = frozenset({
    "non_member", "hydration_cap", "hydration_failed", "conversation_cap", "rate_limited",
})
BACKFILL_CAP_REASONS = ("hydration_cap", "conversation_cap")
# ``inbound_status`` detail codes of a ``disabled`` status that mean "monitors cannot work"
OFF_DETAILS = frozenset({"disabled_by_owner", "no_app_token", "no_user_token"})
# ... and the ones that stop SCHEDULED monitors too (they need only the user token and the owner switch)
SCHEDULED_OFF_DETAILS = frozenset({"disabled_by_owner", "no_user_token"})

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
    "digest_failures": ATTENTION,
    "digest_rate_limited_24h": ATTENTION,
    "digest_overdue": ATTENTION,
    "backfill_gap_24h": ATTENTION,
    "backfill_cap_24h": ATTENTION,
    "instruction_delivery_failed_24h": ATTENTION,
    "monitors_paused": INFO,
    "monitors_expiring": INFO,
    "digest_ran_late": INFO,
    "digest_deferred": INFO,
    **{code: sev for code, sev, _events in SUSPENSION_RULES.values()},
}


def connection_codes(
    status: str, detail: str | None, status_since: float | None, now: float, *,
    has_live_monitors: bool, has_scheduled_monitors: bool = False,
) -> list[str]:
    """Attention-level connection codes (shared by ``build_health`` and the per-session summary, so the
    Library tab and the session chip cannot disagree).

    Scheduled monitors (digest / unread) need only the user token and the owner's inbound switch, not
    the Socket Mode connection: when a relay (or session) has ONLY scheduled monitors the socket-level
    codes stay quiet, and the "inbound off" code applies to them only for the owner switch / a missing
    user token."""
    codes: list[str] = []
    socket_matters = has_live_monitors or not has_scheduled_monitors
    if socket_matters:
        if status == const.STATUS_AUTH_FAILED:
            codes.append("auth_failed")
        if status == const.STATUS_TRANSPORT_UNAVAILABLE:
            codes.append("transport_unavailable")
        if (
            status in (const.STATUS_BACKOFF, const.STATUS_CONNECTING) and status_since is not None
            and now - status_since > RECONNECTING_LONG_SECONDS
        ):
            codes.append("reconnecting_long")
    if status == const.STATUS_DISABLED and (
        (has_live_monitors and detail in OFF_DETAILS)
        or (has_scheduled_monitors and detail in SCHEDULED_OFF_DETAILS)
    ):
        codes.append("inbound_off_with_monitors")
    return codes


def _is_scheduled(monitor: dict[str, Any]) -> bool:
    return monitor_mode(monitor) != "live"


def monitor_is_failing_runs(monitor: dict[str, Any]) -> bool:
    """An ACTIVE scheduled monitor whose runs keep failing."""
    return (
        _is_scheduled(monitor) and effective_state(monitor) == "active"
        and int(monitor.get("run_failures") or 0) >= DIGEST_FAILURES_ATTENTION
    )


def monitor_is_overdue(monitor: dict[str, Any], now: float, *, eligible: bool) -> bool:
    """An ACTIVE scheduled monitor far past its slot while the relay can run digests."""
    next_run = monitor.get("next_run")
    return bool(
        eligible and _is_scheduled(monitor) and effective_state(monitor) == "active"
        and next_run is not None and now - float(next_run) > DIGEST_OVERDUE_SECONDS
    )


def monitor_is_deferred(monitor: dict[str, Any]) -> bool:
    return (
        _is_scheduled(monitor) and effective_state(monitor) == "active"
        and (monitor.get("last_run") or {}).get("outcome") == "deferred"
    )


def monitor_is_erroring(monitor: dict[str, Any]) -> bool:
    """An ACTIVE monitor whose deliveries keep failing (not yet suspended)."""
    return (
        effective_state(monitor) == "active" and int(monitor.get("failures") or 0) > 0
        and bool(monitor.get("last_error"))
    )


def monitor_needs_attention(monitor: dict[str, Any]) -> bool:
    """Suspended for an attention-level reason, or erroring. Paused and info-level suspensions are
    owner-caused / self-healing and stay quiet."""
    if effective_state(monitor) == "suspended":
        rule = SUSPENSION_RULES.get(monitor.get("suspended_reason") or "")
        return bool(rule and rule[1] == ATTENTION)
    return monitor_is_erroring(monitor)


def monitor_summary_counts(monitors: list[dict[str, Any]]) -> dict[str, int]:
    states = Counter(effective_state(m) for m in monitors)
    return {
        "active": states["active"], "paused": states["paused"], "suspended": states["suspended"],
        "scheduled": sum(1 for m in monitors if _is_scheduled(m)),
    }


def attention_for_session(
    monitors: list[dict[str, Any]], *, status: str, detail: str | None,
    status_since: float | None, now: float, digest_eligible: bool = True,
) -> bool:
    """The session chip's dot: only a session WITH monitors can be lit, by one of its own monitors or
    by an attention-level connection (same rules as the Inbound tab)."""
    if not monitors:
        return False
    if any(
        monitor_needs_attention(m) or monitor_is_failing_runs(m)
        or monitor_is_overdue(m, now, eligible=digest_eligible)
        for m in monitors
    ):
        return True
    running = [m for m in monitors if effective_state(m) in ("active", "paused")]
    return bool(connection_codes(
        status, detail, status_since, now,
        has_live_monitors=any(not _is_scheduled(m) for m in running),
        has_scheduled_monitors=any(_is_scheduled(m) for m in running),
    ))


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
    upcoming = [
        float(m["next_run"]) for m in monitors
        if _is_scheduled(m) and effective_state(m) == "active" and m.get("next_run") is not None
    ]
    reasons = Counter(
        m.get("suspended_reason") or "unknown" for m in monitors if effective_state(m) == "suspended"
    )
    return {
        "total": len(monitors), "active": states["active"], "paused": states["paused"],
        "suspended": states["suspended"], "by_reason": dict(reasons),
        "scheduled": sum(1 for m in monitors if _is_scheduled(m)),
        "next_run": min(upcoming) if upcoming else None,
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
        "digest_runs": sum(
            scan.rows_for("monitor_digest", outcome=o) for o in ("delivered", "empty", "rate_limited", "failed")
        ),
        "digest_delivered": scan.rows_for("monitor_digest", outcome="delivered"),
        "digest_empty": scan.rows_for("monitor_digest", outcome="empty"),
        "rate_limited_runs": scan.rows_for("monitor_digest", outcome="rate_limited"),
        "late_runs": scan.rows_for("monitor_digest", code="late"),
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
    digest_eligible: bool = False,
) -> dict[str, Any]:
    counts = _monitor_counts(monitors)
    window = _window(scan)
    attention: list[dict[str, Any]] = []
    since_iso = iso(status_since)

    # ---- connection
    running = [m for m in monitors if effective_state(m) in ("active", "paused")]
    live = sum(1 for m in running if not _is_scheduled(m))
    scheduled_running = len(running) - live
    for code in connection_codes(
        status, detail, status_since, now, has_live_monitors=bool(live),
        has_scheduled_monitors=bool(scheduled_running),
    ):
        if code == "inbound_off_with_monitors":
            off_count = live + (scheduled_running if detail in SCHEDULED_OFF_DETAILS else 0)
        else:
            off_count = 1
        attention.append(_item(code, since_iso, off_count, ["inbound_connection"]))

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
    erroring = [  # active monitors only: suspended / paused ones are already reported by their own tag
        m for m in monitors if monitor_is_erroring(m)
    ]
    if erroring:
        attention.append(_item(
            "monitor_error", scan.newest_for("monitor_delivery_failed"), len(erroring),
            ["monitor_delivery_failed"], sorted({m["session_id"] for m in erroring if m.get("session_id")}),
        ))

    # ---- scheduled (digest / unread) monitors
    failing = [m for m in monitors if monitor_is_failing_runs(m)]
    if failing:
        attention.append(_item(
            "digest_failures", scan.newest_for("monitor_digest", outcome="failed"), len(failing),
            ["monitor_digest"], sorted({m["session_id"] for m in failing if m.get("session_id")}),
        ))
    rate_limited = sum(scan.rows_for("monitor_digest", code=c) for c in DIGEST_RATE_LIMIT_CODES)
    if rate_limited >= DIGEST_RATE_LIMITED_ROWS:
        attention.append(_item(
            "digest_rate_limited_24h",
            max(filter(None, (scan.newest_for("monitor_digest", code=c) for c in DIGEST_RATE_LIMIT_CODES)),
                default=None),
            rate_limited, ["monitor_digest"],
        ))
    overdue = [m for m in monitors if monitor_is_overdue(m, now, eligible=digest_eligible)]
    if overdue:
        attention.append(_item(
            "digest_overdue", None, len(overdue), ["monitor_digest"],
            sorted({m["session_id"] for m in overdue if m.get("session_id")}),
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

    if window["late_runs"]:
        attention.append(_item(
            "digest_ran_late", scan.newest_for("monitor_digest", code="late"), window["late_runs"],
            ["monitor_digest"],
        ))
    deferred = [m for m in monitors if monitor_is_deferred(m)]
    if deferred:
        attention.append(_item(
            "digest_deferred", scan.newest_for("monitor_digest", outcome="deferred"), len(deferred),
            ["monitor_digest"], sorted({m["session_id"] for m in deferred if m.get("session_id")}),
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
    now: float, digest_eligible: bool = False,
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
        digest_eligible=digest_eligible,
    )
