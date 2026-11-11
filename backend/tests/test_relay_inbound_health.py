"""Inbound health assembler, attention rules, service wiring and routes (issue #2103 3d-i)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.relay.audit import AuditScan, RelayAuditLog
from backend.relay.inbound.health import ATTENTION, ATTENTION_CODES, INFO, build_health
from backend.relay.inbound.manager import InboundManager
from backend.routers import relay as relay_router
from backend.tests.relay_inbound_support import RELAY_ID, make_inbound, started
from backend.tests.relay_monitor_support import monitor

NOW = 1_800_000_000.0


def iso(offset: float) -> str:
    return datetime.fromtimestamp(NOW + offset, UTC).isoformat()


def mon(mid="m1", *, status="active", paused=False, reason=None, session="s1", failures=0,
        last_error=None, expires_at=None, kind="monitor_channel"):
    return {
        "id": mid, "kind": kind, "session_id": session, "status": status, "paused": paused,
        "suspended_reason": reason, "failures": failures, "last_error": last_error,
        "expires_at": expires_at,
    }


def scan(rows=(), *, truncated=False) -> AuditScan:
    s = AuditScan(since=iso(-86400), truncated=truncated)
    for r in rows:
        s.rows_read += 1
        s.add({"ts": iso(-60), **r})
    return s


def build(*, status="connected", detail=None, since=-1000.0, monitors=(), audit=None, last=None):
    return build_health(
        relay_id="r1", enabled=True, status=status, detail=detail, status_since=NOW + since,
        connected_at=None, inflight=0, monitors=list(monitors), last_backfill=last,
        scan=audit or scan(), now=NOW,
    )


def codes(payload, severity=None):
    return [a["code"] for a in payload["attention"] if severity in (None, a["severity"])]


# ------------------------------------------------------------------ attention rules

def test_healthy_relay_has_no_attention():
    p = build(monitors=[mon()])
    assert p["attention"] == [] and p["monitors"]["active"] == 1 and p["status"] == "connected"


def test_every_rule_code_is_frozen_with_its_severity():
    assert {c for c, s in ATTENTION_CODES.items() if s == ATTENTION} == {
        "auth_failed", "transport_unavailable", "reconnecting_long", "inbound_off_with_monitors",
        "monitors_flood", "monitors_delivery_failed", "monitors_policy_unset", "monitor_error",
        "backfill_gap_24h", "backfill_cap_24h", "instruction_delivery_failed_24h",
    }
    assert {c for c, s in ATTENTION_CODES.items() if s == INFO} == {
        "monitors_channel_blocked", "monitors_disposed", "monitors_session_inactive",
        "monitors_paused", "monitors_expiring",
    }


def test_auth_failed_and_transport_unavailable():
    p = build(status="auth_failed", detail="invalid_auth")
    assert codes(p) == ["auth_failed"] and p["attention"][0]["since"] == iso(-1000)
    assert p["attention"][0]["audit"] == {"events": ["inbound_connection"]}
    assert codes(build(status="transport_unavailable")) == ["transport_unavailable"]


@pytest.mark.parametrize("status", ["backoff", "connecting"])
def test_reconnecting_long_only_after_two_minutes(status):
    assert codes(build(status=status, since=-119)) == []
    assert codes(build(status=status, since=-121)) == ["reconnecting_long"]
    assert codes(build(status="connected", since=-9999)) == []


@pytest.mark.parametrize("detail", ["disabled_by_owner", "no_app_token", "no_user_token"])
def test_inbound_off_with_monitors(detail):
    live = [mon("a"), mon("b", paused=True), mon("c", status="suspended", reason="flood")]
    p = build(status="disabled", detail=detail, monitors=live)
    item = next(a for a in p["attention"] if a["code"] == "inbound_off_with_monitors")
    assert item["count"] == 2 and item["severity"] == ATTENTION
    assert "inbound_off_with_monitors" not in codes(build(status="disabled", detail=detail))
    assert "inbound_off_with_monitors" not in codes(build(status="disabled", detail="no_owner", monitors=live))


@pytest.mark.parametrize(("reason", "code", "severity"), [
    ("flood", "monitors_flood", ATTENTION),
    ("delivery_failed", "monitors_delivery_failed", ATTENTION),
    ("policy_unset", "monitors_policy_unset", ATTENTION),
    ("channel_blocked", "monitors_channel_blocked", INFO),
    ("disposed", "monitors_disposed", INFO),
    ("session_inactive", "monitors_session_inactive", INFO),
])
def test_suspension_reasons(reason, code, severity):
    audit = scan([{"event": "monitor_suspended", "code": reason}])
    p = build(monitors=[mon("a", status="suspended", reason=reason, session="s1"),
                        mon("b", status="suspended", reason=reason, session="s2")], audit=audit)
    item = next(a for a in p["attention"] if a["code"] == code)
    assert item["severity"] == severity and item["count"] == 2
    assert item["session_ids"] == ["s1", "s2"] and "session_id" not in item["audit"]
    assert item["since"] == iso(-60)
    assert p["monitors"]["by_reason"] == {reason: 2}
    one = build(monitors=[mon("a", status="suspended", reason=reason, session="s1")])
    assert next(a for a in one["attention"] if a["code"] == code)["audit"]["session_id"] == "s1"


def test_amber_only_for_attention_level_and_sorted_first():
    p = build(monitors=[
        mon("a", status="suspended", reason="disposed"), mon("b", status="suspended", reason="flood"),
        mon("c", paused=True),
    ])
    assert codes(p) == ["monitors_flood", "monitors_disposed", "monitors_paused"]
    assert codes(p, ATTENTION) == ["monitors_flood"]
    owner_only = build(monitors=[mon("a", status="suspended", reason="channel_blocked"), mon("b", paused=True)])
    assert codes(owner_only, ATTENTION) == []


def test_monitor_error_rule():
    assert "monitor_error" not in codes(build(monitors=[mon(failures=2)]))  # no last_error
    assert "monitor_error" not in codes(build(monitors=[mon(last_error="x")]))  # no failures
    p = build(monitors=[mon(failures=2, last_error="channel_not_found")])
    item = next(a for a in p["attention"] if a["code"] == "monitor_error")
    assert item["count"] == 1 and item["audit"]["events"] == ["monitor_delivery_failed"]
    dup = build(monitors=[mon(status="suspended", reason="delivery_failed", failures=3, last_error="x")])
    assert "monitor_error" not in codes(dup) and "monitors_delivery_failed" in codes(dup)
    for other in (mon(status="suspended", reason="flood", failures=2, last_error="x"),
                  mon(paused=True, failures=2, last_error="x")):
        assert "monitor_error" not in codes(build(monitors=[other]))


def test_paused_wins_over_suspended_in_counts():
    p = build(monitors=[mon(paused=True, status="suspended", reason="flood")])
    assert p["monitors"] == {"total": 1, "active": 0, "paused": 1, "suspended": 0, "by_reason": {}}
    assert codes(p) == ["monitors_paused"]


def test_expiring_rule():
    soon, later, gone = NOW + 3600, NOW + 2 * 86400, NOW - 5
    p = build(monitors=[mon("a", expires_at=soon), mon("b", expires_at=later), mon("c", expires_at=gone)])
    item = next(a for a in p["attention"] if a["code"] == "monitors_expiring")
    assert item["count"] == 1 and item["severity"] == INFO


def test_gap_cap_and_instruction_failure_rules():
    audit = scan([
        {"event": "monitor_backfill", "outcome": "truncated", "code": "gap_notice",
         "reason_counts": {"non_member": 2, "hydration_cap": 1}},
        {"event": "monitor_backfill", "outcome": "truncated", "code": "backfill_truncated"},
        {"event": "inbound_housekeeping", "outcome": "truncated", "code": "backfill_truncated"},
        {"event": "inbound_instruction", "outcome": "delivery_failed", "code": "delivery_failed"},
    ])
    p = build(audit=audit)
    assert codes(p) == ["backfill_gap_24h", "backfill_cap_24h", "instruction_delivery_failed_24h"]
    by = {a["code"]: a for a in p["attention"]}
    assert by["backfill_gap_24h"]["count"] == 1 and by["backfill_cap_24h"]["count"] == 3
    assert by["instruction_delivery_failed_24h"]["count"] == 1
    assert by["backfill_gap_24h"]["since"] == iso(-60)
    assert all(a["severity"] == ATTENTION for a in p["attention"])


# ------------------------------------------------------------------ payload facts

def test_window_counters_from_audit_rows():
    audit = scan([
        {"event": "monitor_delivered", "outcome": "ok", "count": 5},
        {"event": "monitor_delivered", "outcome": "ok", "count": 7},
        {"event": "monitor_flood", "outcome": "dropped"},
        {"event": "monitor_delivery_failed", "outcome": "error"},
        {"event": "monitor_backfill", "outcome": "truncated", "code": "gap_notice",
         "reason_counts": {"non_member": 2, "hydration_failed": 1, "truncated": 4, "conversation_gone": 9}},
        {"event": "inbound_instruction", "outcome": "delivered"},
        {"event": "inbound_instruction", "outcome": "delivered"},
        {"event": "inbound_instruction", "outcome": "refused", "code": "unknown_handle"},
        {"event": "inbound_instruction", "outcome": "late"},
        {"event": "inbound_instruction", "outcome": "rate_limited"},
        {"event": "inbound_instruction", "outcome": "delivery_failed"},
        {"event": "inbound_connection", "inbound_status": "connected"},
        {"event": "inbound_connection", "inbound_status": "backoff"},
    ], truncated=True)
    w = build(audit=audit)["window_24h"]
    assert w["delivered_envelopes"] == 2 and w["delivered_messages"] == 12
    assert w["dropped_messages"] == 3 and w["flood_events"] == 1 and w["gap_events"] == 1
    assert w["delivery_failed"] == 2 and w["connection_changes"] == 2 and w["truncated"] is True
    assert w["instructions"] == {
        "delivered": 2, "refused": 1, "late": 1, "rate_limited": 1, "delivery_failed": 1}


def test_last_backfill_live_and_audit():
    last = {"at": NOW - 30, "gap": True, "gap_from": NOW - 90, "truncated": False, "counts": {"non_member": 1}}
    audit = scan([{"event": "monitor_backfill", "outcome": "truncated", "code": "gap_notice",
                   "reason_counts": {"non_member": 1}}])
    p = build(last=last, audit=audit)
    assert p["last_backfill"] == {
        "at": iso(-30), "gap": True, "gap_from": iso(-90), "truncated": False, "counts": {"non_member": 1}}
    assert p["last_backfill_audit"] == {"ts": iso(-60), "code": "gap_notice", "reason_counts": {"non_member": 1}}
    quiet = build()
    assert quiet["last_backfill"] is None and quiet["last_backfill_audit"] is None


def test_payload_carries_no_message_text():
    audit = scan([{"event": "inbound_instruction", "outcome": "delivered",
                   "text_preview": "SECRET TEXT", "text_sha256": "abc"}])
    blob = json.dumps(build(audit=audit, monitors=[mon()]))
    assert "SECRET TEXT" not in blob and "text_preview" not in blob and "abc" not in blob


# ------------------------------------------------------------------ service wiring

async def test_service_health_status_since_and_last_backfill(tmp_path):
    env = make_inbound(tmp_path)
    await started(env)
    await env.service.backfill_monitors()
    payload = await env.service.health()
    assert payload["status"] == "connected" and payload["enabled"] is True
    assert payload["status_since"] == datetime.fromtimestamp(env.service.status_changed_at, UTC).isoformat()
    assert payload["last_backfill"]["gap"] is False and payload["last_backfill"]["at"] is not None
    assert payload["attention"] == []
    await env.service.stop()


async def test_status_since_seeded_from_audit_after_restart(tmp_path):
    env = make_inbound(tmp_path)
    await env.audit.record(
        RELAY_ID, ts=datetime.fromtimestamp(env.clock.now() - 500, UTC).isoformat(),
        event="inbound_connection", inbound_status="stopped")
    await env.audit.record(
        RELAY_ID, ts=datetime.fromtimestamp(env.clock.now() - 300, UTC).isoformat(),
        event="inbound_connection", inbound_status="stopped")
    env.service.status = "stopped"
    payload = await env.service.health()
    assert payload["status_since"] == datetime.fromtimestamp(env.clock.now() - 300, UTC).isoformat()
    # no matching audit row: the service start time
    env.service.status = "backoff"
    env.audit._scan_cache.clear()
    payload = await env.service.health()
    assert payload["status_since"] == datetime.fromtimestamp(env.service.started_at, UTC).isoformat()


async def test_monitor_flood_and_counts_through_service(tmp_path):
    env = make_inbound(tmp_path)
    await started(env)
    rec = await monitor(env)
    env.service.store.touch_monitor(rec["id"], failures=2, last_error="boom")
    payload = await env.service.health()
    assert payload["monitors"]["active"] == 1
    assert "monitor_error" in codes(payload)
    await env.service.stop()


async def test_manager_health_without_service(tmp_path):
    audit = RelayAuditLog(tmp_path)
    relays = {"r1": SimpleNamespace(inbound_enabled=False)}
    root = tmp_path / "relays"
    (root / "r1").mkdir(parents=True)
    (root / "r1" / "subscriptions.json").write_text(json.dumps({"version": 1, "subscriptions": [
        {"id": "m1", "kind": "monitor_dms", "session_id": "s1", "status": "active", "paused": False},
    ]}))
    mgr = InboundManager(
        relay_manager=SimpleNamespace(relays=relays), relay_service=None, session_manager=None,
        audit=audit, relays_root=root)
    payload = await mgr.health_for("r1")
    assert payload["status"] == "disabled" and payload["enabled"] is False
    assert payload["monitors"]["total"] == 1
    assert "inbound_off_with_monitors" in codes(payload)


# ------------------------------------------------------------------ routes

@pytest.fixture
def client(tmp_path):
    audit = RelayAuditLog(tmp_path)

    async def get_relay(rid):
        return object() if rid == "r1" else None

    async def health_for(rid):
        return {"relay_id": rid, "attention": []}

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay), relay_audit=audit,
        relay_inbound=SimpleNamespace(health_for=health_for),
    ))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    return TestClient(app), audit


def test_health_route(client):
    c, _ = client
    assert c.get("/api/relays/r1/inbound/health").json() == {"relay_id": "r1", "attention": []}
    assert c.get("/api/relays/nope/inbound/health").status_code == 404


async def test_audit_route_filters(client, tmp_path):
    c, audit = client
    now = datetime.now(UTC)
    await audit.record("r1", ts=(now - timedelta(hours=30)).isoformat(), event="monitor_flood", outcome="dropped")
    await audit.record("r1", ts=(now - timedelta(hours=1)).isoformat(), event="monitor_delivered", outcome="ok")
    await audit.record("r1", ts=now.isoformat(), event="inbound_instruction", outcome="refused")

    def get(**params):
        return [r["event"] for r in c.get("/api/relays/r1/audit", params=params).json()["records"]]

    assert get(events="monitor_*") == ["monitor_delivered", "monitor_flood"]
    assert get(events="monitor_flood,inbound_instruction") == ["inbound_instruction", "monitor_flood"]
    assert get(outcome="refused") == ["inbound_instruction"]
    day = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert get(since=day) == ["inbound_instruction", "monitor_delivered"]
    assert c.get("/api/relays/r1/audit", params={"events": "Bad Name"}).status_code == 400
    assert c.get("/api/relays/r1/audit", params={"since": "yesterday"}).status_code == 400
    assert c.get("/api/relays/r1/audit", params={"events": ",".join(["a"] * 21)}).status_code == 400


async def test_reconnecting_spell_is_not_reset_by_each_attempt(tmp_path):
    env = make_inbound(tmp_path)
    svc = env.service
    svc._set_status("connecting", None, audit=False)
    t0 = svc.status_changed_at
    env.clock.advance(60)
    svc._set_status("backoff", "network_error", audit=False)
    env.clock.advance(60)
    svc._set_status("connecting", None, audit=False)
    env.clock.advance(30)
    svc._set_status("backoff", "network_error", audit=False)
    assert svc.status_changed_at == t0  # one continuous spell, so reconnecting_long can fire
    svc._set_status("connected", None, audit=False)
    assert svc.status_changed_at == env.clock.now()


async def test_health_without_service_never_moves_a_corrupt_store_aside(tmp_path):
    root = tmp_path / "relays"
    (root / "r1").mkdir(parents=True)
    (root / "r1" / "subscriptions.json").write_text("{not json")
    mgr = InboundManager(
        relay_manager=SimpleNamespace(relays={"r1": SimpleNamespace(inbound_enabled=True)}),
        relay_service=None, session_manager=None, audit=RelayAuditLog(tmp_path), relays_root=root)
    payload = await mgr.health_for("r1")
    assert payload["monitors"]["total"] == 0
    assert (root / "r1" / "subscriptions.json").read_text() == "{not json"
    assert [p.name for p in (root / "r1").iterdir()] == ["subscriptions.json"]
