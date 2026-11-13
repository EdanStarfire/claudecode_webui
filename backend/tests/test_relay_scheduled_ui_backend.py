"""Scheduled monitors on the owner surface (issue #2103 stage 3c-iii): public row fields, owner run-now
REST, health / attention rules and the session summary. Real store + service for rows and routes; pure
``health`` functions for the rules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.relay.audit import AuditScan
from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.relay.inbound.health import (
    ATTENTION,
    DIGEST_OVERDUE_SECONDS,
    INFO,
    attention_for_session,
    build_health,
    connection_codes,
    monitor_summary_counts,
)
from backend.relay.inbound.public import monitor_mode, public_monitor, unread_text
from backend.relay.service import RelayNotAttachedError, SessionNotFoundError
from backend.relay_manager import RelayNotFoundError
from backend.routers import relay as relay_router
from backend.tests.relay_inbound_support import (
    BASE_TIME,
    OWNER,
    SESSION,
    audit_events,
    make_inbound,
)
from backend.tests.relay_monitor_support import CHANNEL, started

NOW = 1_800_000_000.0
CH, UNREAD, MENT = const.KIND_MONITOR_CHANNEL, const.KIND_MONITOR_UNREAD, const.KIND_MONITOR_MENTIONS


def iso(offset: float) -> str:
    return datetime.fromtimestamp(NOW + offset, UTC).isoformat()


def smon(mid="m1", *, kind=CH, status="active", paused=False, reason=None, session="s1", run_failures=0,
         next_run=NOW + 3600, last_run=None, scheduled=True, failures=0, last_error=None):
    rec = {
        "id": mid, "kind": kind, "session_id": session, "status": status, "paused": paused,
        "suspended_reason": reason, "failures": failures, "last_error": last_error, "expires_at": None,
    }
    if scheduled:
        rec.update(mode="digest", schedule={"type": "interval", "minutes": 120, "anchor": 0.0},
                   next_run=next_run, run_failures=run_failures, last_run=last_run)
    return rec


def scan(rows=()) -> AuditScan:
    s = AuditScan(since=iso(-86400))
    for r in rows:
        s.rows_read += 1
        s.add({"ts": iso(-60), **r})
    return s


def build(*, status="connected", detail=None, since=-1000.0, monitors=(), audit=None, eligible=True):
    return build_health(
        relay_id="r1", enabled=True, status=status, detail=detail, status_since=NOW + since,
        connected_at=None, inflight=0, monitors=list(monitors), last_backfill=None,
        scan=audit or scan(), now=NOW, digest_eligible=eligible,
    )


def codes(payload, severity=None):
    return [a["code"] for a in payload["attention"] if severity in (None, a["severity"])]


# ------------------------------------------------------------------ public row

class TestPublicRow:
    def row(self, rec, **kw):
        return public_monitor(rec, session_name="S", handle="h", channel_label="#c", user_labels={}, **kw)

    def test_live_row_has_null_schedule_fields(self):
        r = self.row(smon(scheduled=False))
        assert r["mode"] == "live" and r["schedule"] is None and r["schedule_text"] is None
        assert r["next_run"] is None and r["last_run"] is None and r["unread"] is None
        assert r["run_failures"] == 0 and r["notified_count"] == 0

    def test_daily_digest_words_tz_sources(self):
        for source, expect in (("slack", "your Slack timezone"), ("explicit", "Europe/Paris"),
                               ("utc_fallback", "Slack did not report")):
            rec = smon()
            rec["schedule"] = {"type": "daily", "times": ["09:00", "17:00"], "days": [0, 1, 2, 3, 4],
                               "tz": "Europe/Paris" if source != "utc_fallback" else "UTC", "tz_source": source}
            r = self.row(rec)
            assert r["mode"] == "digest" and r["tz_source"] == source and expect in r["schedule_text"]
            assert "09:00 and 17:00" in r["schedule_text"] and "Mon–Fri" in r["schedule_text"]

    def test_interval_digest_and_last_run_variants(self):
        lr = {"at": 5.0, "outcome": "rate_limited", "messages": 0, "omitted": 0, "gap": False, "late": False,
              "trigger": "schedule", "code": "rate_limited_rescheduled", "retry": 1, "retry_at": 305.0,
              "secret": "dropped"}
        r = self.row(smon(last_run=lr, run_failures=2))
        assert r["schedule_text"] == "every 2 hours" and r["tz"] is None and r["run_failures"] == 2
        assert r["last_run"]["retry_at"] == 305.0 and "secret" not in r["last_run"]
        assert r["next_run"] == NOW + 3600

    def test_unread_row_options_and_labels(self):
        rec = smon(kind=UNREAD)
        rec["options"] = {"dms": True, "channels": ["C0AAA0001"], "exclude": ["D0X", "D0Y"],
                          "mentions_only": True, "repeat": False}
        rec["notified"] = {"D1": "1.0"}
        r = self.row(rec, channel_labels={"C0AAA0001": "#alpha"})
        assert r["mode"] == "unread" and r["notified_count"] == 1
        assert r["unread"]["labels"]["C0AAA0001"] == "#alpha"
        assert r["unread_text"] == (
            "all direct and group messages except 2, channels #alpha, only messages that mention you, "
            "only newly unread")

    def test_unread_text_id_fallback_and_repeat(self):
        text = unread_text({"dms": False, "channels": ["C0ZZZ0001"], "exclude": [], "labels": {},
                            "mentions_only": False, "repeat": True})
        assert text == "channels C0ZZZ0001"

    def test_no_message_text_keys(self):
        r = self.row(smon())
        assert not any("text" == k or k.endswith("_content") for k in r)

    def test_monitor_mode(self):
        assert monitor_mode(smon(scheduled=False)) == "live"
        assert monitor_mode(smon()) == "digest"
        assert monitor_mode(smon(kind=UNREAD)) == "unread"


# ------------------------------------------------------------------ health rules

class TestHealthRules:
    def test_digest_failures_boundary(self):
        assert "digest_failures" not in codes(build(monitors=[smon(run_failures=2)]))
        p = build(monitors=[smon(run_failures=3)])
        item = next(a for a in p["attention"] if a["code"] == "digest_failures")
        assert item["severity"] == ATTENTION and item["session_ids"] == ["s1"]
        assert item["audit"] == {"events": ["monitor_digest"], "session_id": "s1"}

    def test_digest_failures_ignores_live_paused_suspended(self):
        for m in (smon(scheduled=False), smon(paused=True, run_failures=5),
                  smon(status="suspended", reason="channel_blocked", run_failures=5)):
            assert "digest_failures" not in codes(build(monitors=[m]))

    def test_rate_limited_needs_two_rows(self):
        one = scan([{"event": "monitor_digest", "outcome": "rate_limited", "code": "rate_limited_rescheduled"}])
        assert "digest_rate_limited_24h" not in codes(build(audit=one))
        two = scan([
            {"event": "monitor_digest", "outcome": "rate_limited", "code": "rate_limited_rescheduled"},
            {"event": "monitor_digest", "outcome": "rate_limited", "code": "rate_limited_gave_up"},
        ])
        p = build(audit=two)
        item = next(a for a in p["attention"] if a["code"] == "digest_rate_limited_24h")
        assert item["count"] == 2 and item["severity"] == ATTENTION
        # a run-now rate limit (code rate_limited) is not a scheduled-slot reschedule
        manual = scan([{"event": "monitor_digest", "outcome": "rate_limited", "code": "rate_limited"}] * 3)
        assert "digest_rate_limited_24h" not in codes(build(audit=manual))

    def test_overdue_boundary_and_eligibility(self):
        late = NOW - DIGEST_OVERDUE_SECONDS - 1
        assert "digest_overdue" in codes(build(monitors=[smon(next_run=late)]))
        assert "digest_overdue" not in codes(build(monitors=[smon(next_run=NOW - DIGEST_OVERDUE_SECONDS)]))
        assert "digest_overdue" not in codes(build(monitors=[smon(next_run=late)], eligible=False))
        assert "digest_overdue" not in codes(build(monitors=[smon(next_run=late, paused=True)]))

    def test_ran_late_and_deferred_are_info(self):
        late = scan([{"event": "monitor_digest", "outcome": "delivered", "code": "late"}])
        p = build(audit=late, monitors=[smon(last_run={"outcome": "deferred"})])
        assert codes(p, INFO) == ["digest_ran_late", "digest_deferred"] and codes(p, ATTENTION) == []

    def test_window_counters_and_monitor_facts(self):
        rows = scan([
            {"event": "monitor_digest", "outcome": "delivered"},
            {"event": "monitor_digest", "outcome": "delivered", "code": "late"},
            {"event": "monitor_digest", "outcome": "empty"},
            {"event": "monitor_digest", "outcome": "rate_limited", "code": "rate_limited"},
            {"event": "monitor_digest", "outcome": "accepted", "code": "run_now"},
            {"event": "monitor_digest", "outcome": "refused", "code": "monitor_paused"},
        ])
        mons = [smon("a", next_run=NOW + 50), smon("b", next_run=NOW + 10), smon("c", paused=True, next_run=NOW + 1),
                smon("d", scheduled=False)]
        p = build(audit=rows, monitors=mons)
        w = p["window_24h"]
        assert (w["digest_runs"], w["digest_delivered"], w["digest_empty"], w["rate_limited_runs"],
                w["late_runs"]) == (4, 2, 1, 1, 1)
        assert p["monitors"]["scheduled"] == 3 and p["monitors"]["next_run"] == NOW + 10

    def test_connection_split_scheduled_only_is_quiet(self):
        sched_only = [smon()]
        for status, detail in (("auth_failed", "invalid_auth"), ("transport_unavailable", None)):
            assert build(status=status, detail=detail, monitors=sched_only)["attention"] == []
        assert build(status="backoff", since=-500, monitors=sched_only)["attention"] == []
        # live monitors keep the 3d behaviour; a relay without monitors is unchanged
        assert codes(build(status="auth_failed", monitors=[smon(scheduled=False)])) == ["auth_failed"]
        assert codes(build(status="auth_failed")) == ["auth_failed"]
        assert codes(build(status="auth_failed", monitors=[smon(), smon("l", scheduled=False)])) == ["auth_failed"]

    def test_inbound_off_split(self):
        for detail, scheduled_flagged in (("disabled_by_owner", True), ("no_user_token", True),
                                          ("no_app_token", False)):
            p = build(status="disabled", detail=detail, monitors=[smon()])
            assert ("inbound_off_with_monitors" in codes(p)) is scheduled_flagged
            live = build(status="disabled", detail=detail, monitors=[smon(scheduled=False)])
            assert "inbound_off_with_monitors" in codes(live)

    def test_connection_codes_defaults_keep_old_signature(self):
        assert connection_codes("auth_failed", None, None, NOW, has_live_monitors=True) == ["auth_failed"]


class TestSessionHelper:
    def att(self, monitors, *, status="connected", detail=None, since=-1000.0, eligible=True):
        return attention_for_session(monitors, status=status, detail=detail, status_since=NOW + since,
                                     now=NOW, digest_eligible=eligible)

    def test_scheduled_rules_light_the_chip(self):
        assert self.att([smon(run_failures=3)]) is True
        assert self.att([smon(next_run=NOW - 700)]) is True
        assert self.att([smon(next_run=NOW - 700)], eligible=False) is False
        assert self.att([smon(run_failures=1)]) is False

    def test_scheduled_only_session_ignores_socket_state(self):
        assert self.att([smon()], status="auth_failed") is False
        assert self.att([smon()], status="transport_unavailable") is False
        assert self.att([smon(scheduled=False)], status="auth_failed") is True
        assert self.att([smon()], status="disabled", detail="disabled_by_owner") is True
        assert self.att([smon()], status="disabled", detail="no_app_token") is False

    def test_counts_include_scheduled(self):
        assert monitor_summary_counts([smon(), smon("l", scheduled=False), smon("u", kind=UNREAD)])["scheduled"] == 2

    @pytest.mark.parametrize("status,detail", [
        ("connected", None), ("auth_failed", "x"), ("transport_unavailable", None), ("backoff", None),
        ("disabled", "disabled_by_owner"), ("disabled", "no_app_token"), ("disabled", "no_user_token"),
    ])
    @pytest.mark.parametrize("monitors", [
        [], [smon()], [smon(scheduled=False)], [smon(), smon("l", scheduled=False)],
        [smon(run_failures=3)], [smon(next_run=NOW - 700)], [smon(paused=True)],
        [smon(status="suspended", reason="flood")],
    ])
    def test_parity_with_library_health(self, status, detail, monitors):
        """The chip dot is lit exactly when the Library tab has an attention item (audit-only items aside).
        A relay with ONLY scheduled monitors reads the same on both."""
        library = [a for a in build(status=status, detail=detail, monitors=monitors)["attention"]
                   if a["severity"] == ATTENTION]
        assert self.att(monitors, status=status, detail=detail) is (bool(monitors) and bool(library))


# ------------------------------------------------------------------ real service + routes

@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.t = await started(e)
    e.relay_service.policies[CHANNEL] = "auto"
    e.clock.advance(const.DIGEST_STARTUP_DELAY_SECONDS + 1)
    yield e
    await e.service.stop()


async def make_digest(env, kind=CH, minutes=60, **kw):
    rec, _ = await env.service.create_monitor(
        SESSION, kind, CHANNEL if kind == CH else None, thread_ts=None,
        filters={"keywords": ["deploy"] if kind == CH else [], "users": []}, expires_at=None,
        schedule=sched.parse_schedule({"every_minutes": minutes}, owner_tz=None, now=env.clock.now()), **kw)
    return rec


def client_for(env):
    async def get_relay(rid):
        return object() if rid == "relay1" else None

    async def require_attached(session_id, relay_id):
        if session_id == "nosession":
            raise SessionNotFoundError(session_id)
        if relay_id != "relay1":
            raise RelayNotFoundError(relay_id)
        if session_id == "detached":
            raise RelayNotAttachedError("no")

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay),
        relay_service=SimpleNamespace(require_attached=require_attached),
        relay_inbound=SimpleNamespace(service_for=lambda rid: env.service if rid == "relay1" else None)))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    return TestClient(app)


async def settle(env):
    for _ in range(300):
        if not env.service.digest.running:
            return
        await asyncio.sleep(0.01)


class TestRunRoutes:
    async def test_run_is_async_audited_owner_and_keeps_next_run(self, env):
        rec = await make_digest(env)
        slot = env.service.store.get_monitor(rec["id"])["next_run"]
        client = client_for(env)
        r = await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")
        assert r.status_code == 200 and r.json()["started"] is True
        assert r.json()["monitor"]["id"] == rec["id"] and r.json()["monitor"]["mode"] == "digest"
        await settle(env)
        assert env.service.store.get_monitor(rec["id"])["next_run"] == slot
        rows = [x for x in audit_events(env, "monitor_digest") if x["source"] == "owner"]
        assert [x["outcome"] for x in rows][:1] == ["accepted"] and rows[0]["code"] == "run_now"

    async def test_session_qualified_and_foreign_session(self, env):
        rec = await make_digest(env)
        client = client_for(env)
        ok = await asyncio.to_thread(client.post, f"/api/sessions/{SESSION}/relays/relay1/monitors/{rec['id']}/run")
        assert ok.status_code == 200
        await settle(env)
        assert (await asyncio.to_thread(client.post, f"/api/sessions/other/relays/relay1/monitors/{rec['id']}/run")
                ).status_code == 404
        assert (await asyncio.to_thread(client.post, f"/api/sessions/nosession/relays/relay1/monitors/{rec['id']}/run")
                ).status_code == 404
        assert (await asyncio.to_thread(client.post, f"/api/sessions/detached/relays/relay1/monitors/{rec['id']}/run")
                ).status_code == 409

    async def test_unknown_relay_monitor_and_inbound_down(self, env):
        client = client_for(env)
        assert (await asyncio.to_thread(client.post, "/api/relays/nope/monitors/x/run")).status_code == 404
        assert (await asyncio.to_thread(client.post, "/api/relays/relay1/monitors/x/run")).status_code == 404

    async def test_live_monitor_is_422(self, env):
        live, _ = await env.service.create_monitor(
            SESSION, CH, "C0LIVE0001", thread_ts=None, filters={"keywords": ["a"], "users": []}, expires_at=None)
        r = await asyncio.to_thread(client_for(env).post, f"/api/relays/relay1/monitors/{live['id']}/run")
        assert r.status_code == 422 and r.json()["detail"]["code"] == "not_a_digest_monitor"

    async def test_paused_and_suspended_are_409_with_reason(self, env):
        rec = await make_digest(env)
        client = client_for(env)
        await env.service.set_paused(rec["id"], True)
        r = await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "monitor_paused"
        await env.service.set_paused(rec["id"], False)
        await env.service.store.set_monitor_status(rec["id"], "suspended", "channel_blocked")
        r = await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")
        assert r.status_code == 409 and "channel_blocked" in r.json()["detail"]["message"]
        refused = [x["code"] for x in audit_events(env, "monitor_digest") if x["outcome"] == "refused"]
        assert refused == ["monitor_paused", "monitor_suspended"]

    async def test_too_soon_is_429_with_retry_after(self, env):
        rec = await make_digest(env)
        client = client_for(env)
        assert (await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")).status_code == 200
        await settle(env)
        r = await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")
        assert r.status_code == 429 and r.json()["detail"]["code"] == "run_too_soon"
        assert int(r.headers["Retry-After"]) == r.json()["detail"]["retry_after_seconds"] > 0
        env.clock.advance(const.RUN_NOW_OWNER_MIN_SECONDS + 1)
        assert (await asyncio.to_thread(client.post, f"/api/relays/relay1/monitors/{rec['id']}/run")).status_code == 200
        await settle(env)

    async def test_inbound_unavailable_is_409(self, env):
        rec = await make_digest(env)
        env.service.record.inbound_enabled = False
        r = await asyncio.to_thread(client_for(env).post, f"/api/relays/relay1/monitors/{rec['id']}/run")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "inbound_unavailable"


class TestListFilterAndRows:
    async def test_mode_filter_and_row_fields(self, env):
        live, _ = await env.service.create_monitor(
            SESSION, CH, "C0LIVE0001", thread_ts=None, filters={"keywords": ["a"], "users": []}, expires_at=None)
        dig = await make_digest(env)
        client = client_for(env)

        def ids(q):
            return sorted(m["id"] for m in client.get(f"/api/relays/relay1/monitors{q}").json()["monitors"])

        assert ids("?mode=live") == [live["id"]] and ids("?mode=digest") == [dig["id"]]
        assert ids("?mode=unread") == [] and len(ids("")) == 2
        assert client.get("/api/relays/relay1/monitors?mode=weird").status_code == 422
        row = client.get("/api/relays/relay1/monitors?mode=digest").json()["monitors"][0]
        assert row["schedule_text"] == "every 1 hour" and row["next_run"] and row["run_failures"] == 0

    async def test_last_run_after_rate_limit_carries_retry_at(self, env):
        rec = await make_digest(env)
        env.adapter.fail_methods["read_history"] = "ratelimited"
        target = float(env.service.store.get_monitor(rec["id"])["next_run"]) + 1
        env.clock.advance(target - env.clock.now())
        await env.service.digest.tick()
        lr = env.service.store.get_monitor(rec["id"])["last_run"]
        assert lr["outcome"] == "rate_limited" and lr["code"] == "rate_limited_rescheduled"
        assert lr["retry"] == 1 and lr["retry_at"] == pytest.approx(env.clock.now() + 300, abs=1)
        row = await env.service.public_one(rec["id"])
        assert row["last_run"]["retry_at"] == lr["retry_at"]
        audit = [x for x in audit_events(env, "monitor_digest") if x["code"] == "rate_limited_rescheduled"]
        assert audit and audit[0]["retry_at"]

    async def test_empty_run_ends_a_failure_streak(self, env):
        rec = await make_digest(env)
        env.service.store.touch_monitor(rec["id"], run_failures=2)
        target = float(env.service.store.get_monitor(rec["id"])["next_run"]) + 1
        env.clock.advance(target - env.clock.now())
        await env.service.digest.tick()
        m = env.service.store.get_monitor(rec["id"])
        assert m["last_run"]["outcome"] == "empty" and m["run_failures"] == 0
        assert BASE_TIME and OWNER  # fixtures imported for parity with the digest suites
