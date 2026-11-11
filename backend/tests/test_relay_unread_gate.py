"""Unread envelopes through the REAL DeliveryGate / PendingStore (issue #2103 stage 3c-ii): a stopped
session starts only when something is newly unread, the card / queue label / metadata, a user PIVOT does
not cancel it, `notified` moves on confirmation only."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from backend.models.legion_models import Comm, DeliveryPolicy
from backend.relay.inbound import constants as const
from backend.relay.inbound.monitor_delivery import CoordinatorPort
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import make_inbound
from backend.tests.relay_unread_support import (
    CHANNEL,
    SESSION,
    add_unread,
    at,
    create,
    dm,
    msg,
    started,
)
from backend.tests.test_relay_inbound_integration import _fast_sleep, _wait_for, live_coordinator

DM1 = "D0ALICE001"


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
    env.rec = await create(env)
    return env


async def run_due(env):
    env.clock.advance(float(env.service.store.get_monitor(env.rec["id"])["next_run"]) - env.clock.now() + 1)
    return await env.service.digest.tick()


async def finish(env):
    env.coord.delivery_gate.stop_when_idle("s1")
    await env.service.stop()


class TestRealGate:
    async def test_nothing_unread_never_starts_a_stopped_session(self, tmp_path, monkeypatch):
        env = await build(tmp_path, monkeypatch, state=SessionState.TERMINATED)
        try:
            add_unread(env, dm(DM1, unread_count=0, latest=100, last_read=100))
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                await _wait_for(lambda: False, 1)
            env.coord.start_session.assert_not_called()
            assert env.sent == [] and env.coord.pending_store.get_pending("s1") == []
        finally:
            await finish(env)

    async def test_unread_starts_the_session_and_notified_moves_on_confirm(self, tmp_path, monkeypatch):
        env = await build(tmp_path, monkeypatch, state=SessionState.TERMINATED)
        try:
            add_unread(env, dm(DM1, unread_count=1, latest=100), msg(100, "ping"))
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                assert await _wait_for(lambda: bool(env.sent))
                await env.service.watcher.poll_once()
            env.coord.start_session.assert_awaited_once()
            message, metadata, inject = env.sent[0]
            assert message.startswith("📬 Slack unread — 1 conversation") and inject is False
            assert metadata["comm"]["from_display_name"] == "Slack unread"  # collapsed CommCard
            assert metadata["relay_inbound"]["mode"] == "unread"
            rec = env.service.store.get_monitor(env.rec["id"])
            assert rec["notified"] == {DM1: at(100)} and rec["last_run"]["outcome"] == "delivered"
            assert env.adapter.posts == [] and env.adapter.reactions == []
        finally:
            await finish(env)

    async def test_busy_session_waits_and_user_pivot_does_not_cancel_it(self, tmp_path, monkeypatch):
        env = await build(tmp_path, monkeypatch, processing=True)
        try:
            add_unread(env, dm(DM1, unread_count=1, latest=100), msg(100))
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                await run_due(env)
                comm_id = next(iter(env.service.watcher.entries))
                assert env.sent == []
                item = env.coord.pending_store.get_item("s1", comm_id)
                assert item.status == "pending" and item.comm.content == "Slack unread: 1 conversation"
                pivot = Comm(comm_id="p", from_user=True, to_minion_id="s1", delivery=DeliveryPolicy.PIVOT)
                assert await env.coord.delivery_gate.flush_sender("s1", pivot) == []
                assert env.coord.pending_store.get_item("s1", comm_id).status == "pending"
                env.info.is_processing = False
                assert await _wait_for(lambda: bool(env.sent))
            assert env.service.store.get_monitor(env.rec["id"])["notified"] == {}  # not yet confirmed
        finally:
            await finish(env)


_ = SESSION
