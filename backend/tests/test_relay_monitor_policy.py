"""Policy at delivery: suspend / resume, fresh flush-time check, triggers (issue #2103 3b-i)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from backend.relay.inbound import constants as const
from backend.relay.inbound.manager import InboundManager
from backend.relay.service import RelayService
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import (
    RELAY_ID,
    SESSION,
    FakeSocketTransport,
    RecordingSink,
    audit_events,
    make_inbound,
)
from backend.tests.relay_monitor_support import (
    CHANNEL,
    emit,
    envelope_entries,
    fire_window,
    history_msg,
    monitor,
    started,
)


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies[CHANNEL] = "auto"
    e.t = await started(e)
    yield e
    await e.service.stop()


def rec_of(env, rec):
    return env.service.store.get_monitor(rec["id"])


class TestSuspendResume:
    @pytest.mark.parametrize("policy", ["read_only", "ask", "auto"])
    async def test_readable_policies_keep_monitors_active(self, env, policy):
        env.relay_service.policies[CHANNEL] = policy
        rec = await monitor(env)
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["status"] == "active"

    async def test_blocked_suspends_and_events_are_ignored(self, env):
        rec = await monitor(env)
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate(SESSION)
        got = rec_of(env, rec)
        assert got["status"] == "suspended" and got["suspended_reason"] == const.SUSPEND_CHANNEL_BLOCKED
        await emit(env, env.t, "deploy", seq=1)
        assert env.service.coalescer.buffered(SESSION) is None
        ev = audit_events(env, "monitor_suspended")
        assert ev[0]["code"] == "channel_blocked" and ev[0]["monitor_id"] == rec["id"]

    async def test_removed_policy_suspends_policy_unset(self, env):
        rec = await monitor(env)
        del env.relay_service.policies[CHANNEL]
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["suspended_reason"] == const.SUSPEND_POLICY_UNSET

    async def test_flush_time_check_is_fresh_and_removes_messages(self, env):
        rec = await monitor(env)
        await emit(env, env.t, "deploy", seq=1)
        env.relay_service.policies[CHANNEL] = "blocked"  # no trigger fired: the flush must still see it
        await fire_window(env)
        assert env.port.calls == []
        assert rec_of(env, rec)["suspended_reason"] == const.SUSPEND_CHANNEL_BLOCKED

    async def test_flush_keeps_messages_of_still_valid_monitors_only(self, env):
        other = "C0CHAN0002"
        env.relay_service.policies[other] = "auto"
        m1 = await monitor(env, channel=CHANNEL)
        m2 = await monitor(env, channel=other)
        await emit(env, env.t, "deploy a", seq=1)
        await emit(env, env.t, "deploy b", seq=2, channel=other)
        env.relay_service.policies[other] = "blocked"
        await fire_window(env)
        entries = envelope_entries(env.port.calls[0])
        assert [e["channel"] for e in entries] == [CHANNEL]
        assert rec_of(env, m2)["status"] == "suspended" and rec_of(env, m1)["status"] == "active"

    async def test_unblocking_resumes_from_now_without_backfilling_the_gap(self, env):
        rec = await monitor(env)
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate(SESSION)
        env.clock.advance(600)
        gap = env.clock.ts(seq=1)
        env.adapter.histories[CHANNEL] = [history_msg(gap, "deploy while blocked")]
        env.clock.advance(600)
        env.relay_service.policies[CHANNEL] = "read_only"
        await env.service.reevaluate(SESSION)
        got = rec_of(env, rec)
        assert got["status"] == "active" and got["last_seen_ts"] == f"{env.clock.t:.6f}"
        await env.service.backfill_monitors()
        await fire_window(env)
        assert env.port.calls == []  # nothing from the suspended period
        assert audit_events(env, "monitor_resumed")[-1]["source"] == "policy"
        await emit(env, env.t, "deploy after", seq=2)
        await fire_window(env)
        assert len(env.port.calls) == 1

    async def test_relay_detached_suspends_session_inactive_and_reattach_resumes(self, env):
        rec = await monitor(env)
        env.sessions.infos[SESSION].config["relay_id"] = None
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["suspended_reason"] == const.SUSPEND_SESSION_INACTIVE
        env.sessions.infos[SESSION].config["relay_id"] = RELAY_ID
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["status"] == "active"

    async def test_session_error_suspends(self, env):
        rec = await monitor(env)
        env.sessions.infos[SESSION].state = SessionState.ERROR
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["suspended_reason"] == const.SUSPEND_SESSION_INACTIVE

    async def test_flood_and_delivery_failed_are_not_lifted_by_policy(self, env):
        rec = await monitor(env)
        await env.service._suspend(rec_of(env, rec), const.SUSPEND_FLOOD)
        await env.service.reevaluate(SESSION)
        assert rec_of(env, rec)["suspended_reason"] == const.SUSPEND_FLOOD

    async def test_paused_monitor_is_not_resumed_into_matching(self, env):
        rec = await monitor(env)
        await env.service.store.set_paused(rec["id"], True)
        await emit(env, env.t, "deploy", seq=1)
        assert env.service.coalescer.buffered(SESSION) is None

    async def test_reevaluate_all_covers_every_session(self, env):
        env.sessions.add("s2")
        a = await monitor(env)
        b = await monitor(env, session="s2")
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate_all()
        assert rec_of(env, a)["status"] == rec_of(env, b)["status"] == "suspended"


class TestTriggers:
    def relay_service(self):
        return RelayService(MagicMock(relays={}), MagicMock(), MagicMock(), MagicMock(), MagicMock())

    async def test_session_policy_change_notifies_the_session(self):
        svc = self.relay_service()
        seen = []
        svc.change_listeners.append(seen.append)
        svc._on_policy_change("s1", [], "editor", None)
        assert seen == ["s1"]

    async def test_tier_policy_change_notifies_all(self):
        svc = self.relay_service()
        svc.relay_manager.relays = {"r1": MagicMock(adapter_type="slack")}
        seen = []
        svc.change_listeners.append(seen.append)
        svc.audit_tier_policy_change("template", "t1", "T", {}, {"relay_channel_policies": {"r1": {CHANNEL: "auto"}}})
        assert seen == [None]
        seen.clear()
        svc.audit_tier_policy_change("template", "t1", "T", {}, {})  # no change: no notification
        assert seen == []

    async def test_a_failing_listener_never_breaks_the_edit(self):
        svc = self.relay_service()
        svc.change_listeners.append(lambda sid: 1 / 0)
        seen = []
        svc.change_listeners.append(seen.append)
        svc._on_policy_change("s1", [], "editor", None)
        assert seen == ["s1"]

    async def test_template_refresh_notifies_all(self):
        svc = self.relay_service()
        svc._sessions.list_sessions = MagicMock(side_effect=lambda: asyncio.sleep(0, result=[]))
        seen = []
        svc.change_listeners.append(seen.append)
        await svc._refresh_for_templates({"t1"})
        assert seen == [None]

    async def test_manager_reevaluates_via_the_listener(self, tmp_path):
        e = make_inbound(tmp_path)
        e.relay_service.policies[CHANNEL] = "auto"
        transports = []

        def factory(rid):
            t = FakeSocketTransport()
            transports.append(t)
            return t

        mgr = InboundManager(
            relay_manager=e.manager, relay_service=e.relay_service, session_manager=e.sessions,
            audit=e.audit, relays_root=tmp_path / "relays", transport_factory=factory,
        )
        mgr.sink = RecordingSink()
        await mgr.start()
        try:
            svc = mgr.services[RELAY_ID]
            rec, _ = await svc.create_monitor(
                SESSION, const.KIND_MONITOR_CHANNEL, CHANNEL, thread_ts=None,
                filters={"keywords": ["x"], "users": []}, expires_at=None)
            e.relay_service.policies[CHANNEL] = "blocked"
            mgr.schedule_reevaluate(SESSION)
            for _ in range(100):
                await asyncio.sleep(0.01)
                if svc.store.get_monitor(rec["id"])["status"] == "suspended":
                    break
            assert svc.store.get_monitor(rec["id"])["suspended_reason"] == "channel_blocked"
            e.relay_service.policies[CHANNEL] = "auto"
            mgr.schedule_reevaluate(None)
            for _ in range(100):
                await asyncio.sleep(0.01)
                if svc.store.get_monitor(rec["id"])["status"] == "active":
                    break
            assert svc.store.get_monitor(rec["id"])["status"] == "active"
        finally:
            await mgr.stop()

    async def test_schedule_reevaluate_is_safe_without_a_loop(self):
        mgr = InboundManager(
            relay_manager=MagicMock(), relay_service=MagicMock(), session_manager=MagicMock(),
            audit=MagicMock(), relays_root=__import__("pathlib").Path("/nonexistent"),
        )
        mgr.schedule_reevaluate("s1")  # no running loop in a sync call: must not raise


class TestStartupReevaluation:
    async def test_start_resuspends_a_monitor_whose_channel_was_blocked_while_down(self, tmp_path):
        e = make_inbound(tmp_path)
        e.relay_service.policies[CHANNEL] = "auto"
        e.t = await started(e)
        rec = await monitor(e)
        await e.service.stop()
        e2 = make_inbound(tmp_path)
        e2.relay_service.policies[CHANNEL] = "blocked"
        e2.t = await started(e2)
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                if e2.service.store.get_monitor(rec["id"])["status"] == "suspended":
                    break
            assert e2.service.store.get_monitor(rec["id"])["suspended_reason"] == "channel_blocked"
        finally:
            await e2.service.stop()
