"""Digest envelopes through the REAL DeliveryGate / PendingStore (issue #2103 stage 3c-i): a digest
wakes a stopped session only when it has content, waits for a busy one, is a collapsed CommCard,
survives a user PIVOT, is cursor-on-confirm, and shows the #2362 restart limitation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from backend.models.legion_models import Comm, DeliveryPolicy
from backend.relay.adapter import RelayMessage
from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.relay.inbound.monitor_delivery import CoordinatorPort
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import BASE_TIME, SESSION, audit_events, make_inbound
from backend.tests.relay_monitor_support import CHANNEL, OTHER_USER, started
from backend.tests.test_relay_inbound_integration import _fast_sleep, _wait_for, live_coordinator

HOUR = 3600.0


async def build(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
    (tmp_path / "coord").mkdir()
    env = make_inbound(tmp_path)
    env.relay_service.policies[CHANNEL] = "read_only"
    coord, info, sent = live_coordinator(tmp_path / "coord", **kw)
    port = CoordinatorPort(coord)
    env.service.port = port
    env.service.watcher._probe = port
    env.coord, env.info, env.sent = coord, info, sent
    env.t = await started(env)
    env.clock.advance(const.DIGEST_STARTUP_DELAY_SECONDS + 1)
    rec, _ = await env.service.create_monitor(
        SESSION, const.KIND_MONITOR_CHANNEL, CHANNEL, thread_ts=None,
        filters={"keywords": ["deploy"], "users": []}, expires_at=None,
        schedule=sched.parse_schedule({"every_minutes": 60}, owner_tz=None, now=env.clock.now()))
    env.rec = rec
    return env


async def run_due(env):
    env.clock.advance(float(env.service.store.get_monitor(env.rec["id"])["next_run"]) - env.clock.now() + 1)
    return await env.service.digest.tick()


async def finish(env):
    env.coord.delivery_gate.stop_when_idle("s1")
    await env.service.stop()


class TestRealGate:
    async def test_empty_digest_never_starts_a_stopped_session(self, tmp_path, monkeypatch):
        env = await build(tmp_path, monkeypatch, state=SessionState.TERMINATED)
        try:
            env.adapter.history = [RelayMessage(ts=f"{BASE_TIME + 100:.6f}", text="lunch", user=OTHER_USER)]
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                for _ in range(30):
                    await _wait_for(lambda: False, 3)
            env.coord.start_session.assert_not_called()
            assert env.sent == [] and env.coord.pending_store.get_pending("s1") == []
            assert env.service.store.get_monitor(env.rec["id"])["last_run"]["outcome"] == "empty"
        finally:
            await finish(env)

    async def test_digest_with_content_starts_the_session_and_cursor_moves_on_confirm(
        self, tmp_path, monkeypatch
    ):
        env = await build(tmp_path, monkeypatch, state=SessionState.TERMINATED)
        try:
            ts = f"{BASE_TIME + 100:.6f}"
            env.adapter.history = [RelayMessage(ts=ts, text="deploy it", user=OTHER_USER)]
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                assert await _wait_for(lambda: bool(env.sent))
                await env.service.watcher.poll_once()
            env.coord.start_session.assert_awaited_once()
            message, metadata, inject = env.sent[0]
            assert message.startswith("📰 Slack digest — 1 message(s)") and inject is False
            assert metadata["comm"]["from_display_name"] == "Slack digest"  # collapsed CommCard
            assert metadata["relay_inbound"]["mode"] == "digest"
            assert env.service.store.get_monitor(env.rec["id"])["last_seen_ts"] == ts
            assert env.service.store.get_monitor(env.rec["id"])["last_run"]["outcome"] == "delivered"
            assert env.adapter.posts == [] and env.adapter.reactions == []
        finally:
            await finish(env)

    async def test_busy_session_waits_and_user_pivot_does_not_cancel_it(self, tmp_path, monkeypatch):
        env = await build(tmp_path, monkeypatch, processing=True)
        try:
            env.adapter.history = [RelayMessage(ts=f"{BASE_TIME + 100:.6f}", text="deploy it", user=OTHER_USER)]
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                comm_id = next(iter(env.service.watcher.entries))
                assert env.sent == []
                pivot = Comm(comm_id="p", from_user=True, to_minion_id="s1", delivery=DeliveryPolicy.PIVOT)
                assert await env.coord.delivery_gate.flush_sender("s1", pivot) == []
                assert env.coord.pending_store.get_item("s1", comm_id).status == "pending"
                env.info.is_processing = False
                assert await _wait_for(lambda: bool(env.sent))
        finally:
            await finish(env)

    async def test_lost_queue_after_restart_is_collected_again_by_the_next_digest(
        self, tmp_path, monkeypatch
    ):
        """#2362: the pending queue is not recovered across a restart. The cursor moved only on
        confirmed delivery, so the next digest re-collects what was enqueued but lost."""
        env = await build(tmp_path, monkeypatch, processing=True)
        try:
            ts = f"{BASE_TIME + 100:.6f}"
            env.adapter.history = [RelayMessage(ts=ts, text="deploy it", user=OTHER_USER)]
            cursor = env.service.store.get_monitor(env.rec["id"])["last_seen_ts"]
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
            assert env.service.store.get_monitor(env.rec["id"])["last_seen_ts"] == cursor
        finally:
            await finish(env)
        env2 = make_inbound(tmp_path, clock=env.clock)
        env2.relay_service.policies[CHANNEL] = "read_only"
        env2.adapter.history = env.adapter.history
        await started(env2)
        try:
            rec = env2.service.store.get_monitor(env.rec["id"])
            env2.service.digest.not_before = 0
            env2.clock.advance(float(rec["next_run"]) - env2.clock.now() + 1)
            await env2.service.digest.tick()
            assert len(env2.port.calls) == 1 and "deploy it" in env2.port.calls[0]["dispatch_content"]
            assert audit_events(env2, "monitor_digest")
        finally:
            await env2.service.stop()
