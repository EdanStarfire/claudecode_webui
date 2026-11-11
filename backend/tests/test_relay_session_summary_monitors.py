"""relay_summary monitors / attention and its parity with the Inbound health rules (issue #2103 3d-ii)."""

from __future__ import annotations

import ast
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from backend.relay import service as relay_service_module
from backend.relay.handles import RelayHandleIndex
from backend.relay.inbound import constants as const
from backend.relay.inbound.health import (
    ATTENTION,
    ATTENTION_CODES,
    RECONNECTING_LONG_SECONDS,
    attention_for_session,
    build_health,
    monitor_summary_counts,
)
from backend.relay.inbound.manager import InboundManager
from backend.relay.service import RelayService
from backend.session_config import SessionConfig
from backend.session_manager import SessionManager
from backend.tests.relay_inbound_support import RELAY_ID, SESSION, make_inbound, started
from backend.tests.relay_monitor_support import monitor, wait_for
from backend.tests.test_relay_inbound_health import NOW, mon, scan

CONNECTED = dict(status="connected", detail=None, status_since=NOW - 1000.0, now=NOW)


def att(monitors, **conn):
    return attention_for_session(monitors, **{**CONNECTED, **conn})


# ------------------------------------------------------------------ the rules

class TestAttentionRules:
    def test_no_monitors_never_lit_even_when_the_connection_is_bad(self):
        for status, detail in (("auth_failed", None), ("transport_unavailable", None),
                               ("disabled", "no_app_token")):
            assert att([], status=status, detail=detail) is False

    def test_healthy_monitors_quiet(self):
        assert att([mon(), mon("m2", paused=True)]) is False

    @pytest.mark.parametrize("reason", ["flood", "delivery_failed", "policy_unset"])
    def test_attention_level_suspensions_light_the_dot(self, reason):
        assert att([mon(status="suspended", reason=reason)]) is True

    @pytest.mark.parametrize("reason", ["channel_blocked", "disposed", "session_inactive"])
    def test_owner_caused_suspensions_stay_quiet(self, reason):
        assert att([mon(status="suspended", reason=reason)]) is False

    def test_erroring_active_monitor_lights_the_dot_but_paused_one_does_not(self):
        assert att([mon(failures=2, last_error="boom")]) is True
        assert att([mon(failures=2, last_error="boom", paused=True)]) is False
        assert att([mon(failures=2, last_error=None)]) is False

    @pytest.mark.parametrize("status,detail", [
        ("auth_failed", None), ("transport_unavailable", None),
        ("disabled", "no_app_token"), ("disabled", "no_user_token"), ("disabled", "disabled_by_owner"),
    ])
    def test_connection_attention_lights_a_session_with_live_monitors(self, status, detail):
        assert att([mon()], status=status, detail=detail) is True

    def test_off_with_only_suspended_monitors_matches_the_library_rule(self):
        # the Library tab reports inbound_off_with_monitors for active + paused monitors only
        m = [mon(status="suspended", reason="channel_blocked")]
        assert att(m, status="disabled", detail="disabled_by_owner") is False

    def test_owner_toggle_with_no_detail_stays_quiet(self):
        assert att([mon()], status="disabled", detail=None) is False
        assert att([mon()], status="stopped") is False
        assert att([mon()], status="connected") is False

    def test_reconnecting_only_after_the_threshold(self):
        short = NOW - (RECONNECTING_LONG_SECONDS - 1)
        long_ = NOW - (RECONNECTING_LONG_SECONDS + 1)
        for status in ("backoff", "connecting"):
            assert att([mon()], status=status, status_since=short) is False
            assert att([mon()], status=status, status_since=long_) is True

    def test_counts(self):
        counts = monitor_summary_counts([
            mon("a"), mon("b", paused=True), mon("c", status="suspended", reason="flood"),
            mon("d", status="suspended", reason="disposed"),
        ])
        assert counts == {"active": 1, "paused": 1, "suspended": 2}


# ------------------------------------------------------------------ parity with build_health

CONNECTIONS = [
    dict(status="connected", detail=None, since=-1000.0),
    dict(status="auth_failed", detail="invalid_auth", since=-10.0),
    dict(status="transport_unavailable", detail=None, since=-10.0),
    dict(status="backoff", detail=None, since=-30.0),
    dict(status="backoff", detail=None, since=-500.0),
    dict(status="connecting", detail=None, since=-500.0),
    dict(status="disabled", detail="no_app_token", since=-10.0),
    dict(status="disabled", detail="disabled_by_owner", since=-10.0),
    dict(status="disabled", detail=None, since=-10.0),
    dict(status="stopped", detail=None, since=-10.0),
]
MONITOR_SETS = [
    [],
    [mon()],
    [mon(paused=True)],
    [mon(status="suspended", reason="flood")],
    [mon(status="suspended", reason="delivery_failed")],
    [mon(status="suspended", reason="policy_unset")],
    [mon(status="suspended", reason="channel_blocked")],
    [mon(status="suspended", reason="disposed")],
    [mon(status="suspended", reason="session_inactive")],
    [mon(failures=3, last_error="x")],
    [mon(), mon("m2", paused=True), mon("m3", status="suspended", reason="channel_blocked")],
    [mon(expires_at=NOW + 60)],
]


@pytest.mark.parametrize("conn", CONNECTIONS, ids=lambda c: f"{c['status']}-{c['detail']}-{c['since']}")
@pytest.mark.parametrize("monitors", MONITOR_SETS, ids=lambda m: "+".join(
    f"{x['status']}:{x['suspended_reason']}:{x['paused']}:{x['failures']}" for x in m) or "none")
def test_session_attention_agrees_with_library_health(conn, monitors):
    """One session holding every monitor of the relay: the chip dot is lit exactly when the Library
    tab reports an attention-level monitor / connection item (the 24 h audit items are audit-only)."""
    health = build_health(
        relay_id="r1", enabled=True, status=conn["status"], detail=conn["detail"],
        status_since=NOW + conn["since"], connected_at=None, inflight=0, monitors=list(monitors),
        last_backfill=None, scan=scan(), now=NOW,
    )
    library = [a for a in health["attention"] if a["severity"] == ATTENTION]
    expected = bool(monitors) and bool(library)
    assert att(monitors, status=conn["status"], detail=conn["detail"],
               status_since=NOW + conn["since"]) is expected


def test_every_attention_code_is_covered_by_a_session_rule_or_is_audit_only():
    audit_only = {"backfill_gap_24h", "backfill_cap_24h", "instruction_delivery_failed_24h"}
    assert {c for c, s in ATTENTION_CODES.items() if s == ATTENTION} - audit_only == {
        "auth_failed", "transport_unavailable", "reconnecting_long", "inbound_off_with_monitors",
        "monitor_error", "monitors_flood", "monitors_delivery_failed", "monitors_policy_unset",
    }


# ------------------------------------------------------------------ RelayService.compute_summary

class _Templates:
    async def get_template(self, tid):
        return None

    async def list_templates(self):
        return []


class _Profiles:
    async def get_profile(self, pid):
        return None


@pytest.fixture
async def rs_env(tmp_path):
    sessions = SessionManager(tmp_path)
    await sessions.initialize()
    relays = {"r1": SimpleNamespace(adapter_type="slack", name="Slack")}
    service = RelayService(
        SimpleNamespace(relays=relays), RelayHandleIndex(), sessions, _Templates(), _Profiles()
    )
    workdir = tmp_path / "w"
    workdir.mkdir()
    await sessions.create_session("s1", SessionConfig(working_directory=str(workdir), relay_id="r1"), name="A")
    return SimpleNamespace(sessions=sessions, service=service)


async def test_summary_defaults_to_no_monitors_and_no_attention(rs_env):
    await rs_env.service.refresh_summary("s1")
    s = (await rs_env.sessions.get_session_info("s1")).relay_summary
    assert s["monitors"] == {"active": 0, "paused": 0, "suspended": 0} and s["attention"] is False


async def test_summary_uses_the_injected_monitor_summary_and_broadcasts_only_on_change(rs_env):
    calls = []
    rs_env.service.monitor_summary = lambda rid, sid: (
        calls.append((rid, sid)) or {"active": 2, "paused": 1, "suspended": 0, "attention": True}
    )
    broadcasts = []

    async def cb(session_id):
        broadcasts.append(session_id)

    rs_env.sessions._notify_metadata_change_callbacks = cb  # counts the broadcasts
    await rs_env.service.refresh_summary("s1")
    s = (await rs_env.sessions.get_session_info("s1")).relay_summary
    assert s["monitors"] == {"active": 2, "paused": 1, "suspended": 0} and s["attention"] is True
    assert calls == [("r1", "s1")]
    n = len(broadcasts)
    for _ in range(5):  # a refresh storm with unchanged facts changes nothing
        assert await rs_env.sessions.set_relay_summary("s1", dict(s)) is False
        await rs_env.service.refresh_summary("s1")
    assert len(broadcasts) == n


def test_relay_service_stays_free_of_inbound_imports():
    tree = ast.parse(inspect.getsource(relay_service_module))
    modules = [
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    ]
    assert not [m for m in modules if "inbound" in m]


# ------------------------------------------------------------------ service wiring + triggers

@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies["C0AAAAAAA1"] = "read_only"
    e.t = await started(e)
    e.relay_service.refreshed.clear()
    yield e
    await e.service.stop()


async def refreshed(env, *, expect=True):
    await asyncio.sleep(0.02)
    return SESSION in env.relay_service.refreshed


class TestRefreshTriggers:
    async def test_create_pause_resume_remove_each_refresh(self, env):
        rec = await monitor(env)
        assert await refreshed(env)
        for action in ("pause", "resume", "remove"):
            env.relay_service.refreshed.clear()
            if action == "remove":
                await env.service.delete_monitor(rec["id"])
            else:
                await env.service.set_paused(rec["id"], action == "pause")
            assert await refreshed(env), action

    async def test_suspend_and_policy_resume_refresh(self, env):
        rec = await monitor(env)
        env.relay_service.refreshed.clear()
        await env.service._suspend(rec, const.SUSPEND_FLOOD)
        assert await refreshed(env)
        env.relay_service.refreshed.clear()
        await env.service.store.set_monitor_status(rec["id"], "active")
        assert await refreshed(env)

    async def test_expiry_refreshes(self, env):
        await monitor(env, expires_at=env.clock.now() + 10)
        env.relay_service.refreshed.clear()
        env.clock.advance(60)
        await env.service.run_housekeeping()
        assert await refreshed(env)

    async def test_failure_counter_refreshes(self, env):
        rec = await monitor(env)
        env.relay_service.refreshed.clear()
        env.service.store.touch_monitor(rec["id"], failures=1, last_error="boom")
        assert await refreshed(env)

    async def test_ordinary_bookkeeping_does_not_refresh(self, env):
        rec = await monitor(env)
        env.relay_service.refreshed.clear()
        env.service.store.touch_monitor(rec["id"], last_activity_at=1.0, flood_windows=0)
        env.service.store.bump_counters(rec["id"], delivered=1)
        await asyncio.sleep(0.02)
        assert env.relay_service.refreshed == []

    async def test_unchanged_failure_fields_and_spell_flips_do_not_refresh(self, env):
        rec = await monitor(env)
        env.service.store.touch_monitor(rec["id"], failures=0, last_error=None, last_delivered_at=1.0)
        env.service._set_status(const.STATUS_BACKOFF, "a")
        await asyncio.sleep(0.02)
        env.relay_service.refreshed.clear()
        env.service._set_status(const.STATUS_CONNECTING, "b")  # same reconnecting spell
        env.service._set_status(const.STATUS_BACKOFF, "c")
        await asyncio.sleep(0.02)
        assert env.relay_service.refreshed == []

    async def test_status_change_refreshes_only_sessions_with_monitors(self, env):
        env.sessions.add("other")
        await monitor(env)
        env.relay_service.refreshed.clear()
        env.service._set_status(const.STATUS_AUTH_FAILED, "invalid_auth")
        await asyncio.sleep(0.02)
        assert env.relay_service.refreshed == [SESSION]

    async def test_status_change_without_monitors_refreshes_nothing(self, env):
        env.service._set_status(const.STATUS_AUTH_FAILED, "invalid_auth")
        await asyncio.sleep(0.02)
        assert env.relay_service.refreshed == []

    async def test_reconnect_threshold_triggers_a_late_refresh(self, tmp_path):
        e = make_inbound(tmp_path)
        released = asyncio.Event()

        async def attention_sleep(seconds):
            assert seconds > RECONNECTING_LONG_SECONDS
            await released.wait()

        e.service._attention_sleep = attention_sleep
        await started(e)
        await monitor(e)
        e.relay_service.refreshed.clear()
        e.service._set_status(const.STATUS_BACKOFF, None)
        await asyncio.sleep(0.02)
        e.relay_service.refreshed.clear()
        released.set()
        await wait_for(lambda: SESSION in e.relay_service.refreshed)
        await e.service.stop()

    async def test_session_delete_clears_the_monitors(self, env):
        await monitor(env)
        await env.service.on_session_deleted(SESSION)
        assert env.service.session_monitor_summary(SESSION)["active"] == 0

    async def test_start_refreshes_sessions_with_persisted_monitors(self, tmp_path):
        a = make_inbound(tmp_path)
        await started(a)
        await monitor(a)
        await a.service.store.close()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        await started(b)
        await asyncio.sleep(0.05)
        assert SESSION in b.relay_service.refreshed
        await b.service.stop()


class TestServiceSummary:
    async def test_counts_and_attention_follow_the_connection(self, env):
        rec = await monitor(env)
        await monitor(env, keywords=("other",))
        await env.service.set_paused(rec["id"], True)
        s = env.service.session_monitor_summary(SESSION)
        assert s == {"active": 1, "paused": 1, "suspended": 0, "attention": False}
        env.service._set_status(const.STATUS_AUTH_FAILED, "invalid_auth")
        assert env.service.session_monitor_summary(SESSION)["attention"] is True

    async def test_session_without_monitors_is_never_lit(self, env):
        env.service._set_status(const.STATUS_AUTH_FAILED, "invalid_auth")
        assert env.service.session_monitor_summary(SESSION)["attention"] is False

    async def test_manager_returns_zeros_without_a_service(self, tmp_path):
        mgr = InboundManager(
            relay_manager=SimpleNamespace(relays={}), relay_service=SimpleNamespace(),
            session_manager=SimpleNamespace(), audit=SimpleNamespace(), relays_root=tmp_path,
        )
        assert mgr.session_monitor_summary(RELAY_ID, SESSION) == {
            "active": 0, "paused": 0, "suspended": 0, "attention": False}

    async def test_manager_delegates_to_the_service(self, env):
        await monitor(env)
        mgr = InboundManager(
            relay_manager=SimpleNamespace(relays={}), relay_service=SimpleNamespace(),
            session_manager=SimpleNamespace(), audit=SimpleNamespace(), relays_root=env.root,
        )
        mgr.services[RELAY_ID] = env.service
        assert mgr.session_monitor_summary(RELAY_ID, SESSION)["active"] == 1
