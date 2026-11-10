"""Monitor envelopes through the REAL DeliveryGate / PendingStore (issue #2103 stage 3b-i): comm
shape, authority consequences, end-to-end cursor-on-confirm and instruction failure replies."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.legion.comm_router import CommRouter
from backend.models.legion_models import (
    SYSTEM_MINION_ID,
    Comm,
    CommType,
    DeliveryPolicy,
    SenderType,
)
from backend.relay.inbound.monitor_delivery import CoordinatorPort, enqueue_monitor_comm
from backend.relay.inbound.sink import enqueue_external_comm
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import (
    BASE_TIME,
    SELF_DM,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
    settle,
)
from backend.tests.relay_monitor_support import CHANNEL, emit, fire_window, monitor, started
from backend.tests.test_relay_inbound_integration import (
    _fast_sleep,
    _wait_for,
    live_coordinator,
)


def enqueue_monitor(coord, comm_id="m1", text="DISPATCH monitor"):
    return enqueue_monitor_comm(
        coord, "s1", comm_id=comm_id, summary="Slack monitor: 2 messages",
        content="Slack monitor: 2 messages", dispatch_content=text,
        metadata={"comm": {"from_display_name": "Slack monitor", "comm_type": "info"},
                  "relay_inbound": {"kind": "monitor", "count": 2}}, relay_id="relay1",
    )


def enqueue_instruction(coord, comm_id="i1"):
    return enqueue_external_comm(
        coord, "s1", comm_id=comm_id, summary="Slack instruction: x", content="x",
        dispatch_content="DISPATCH instruction", metadata={"relay_inbound": {"kind": "instruction"}},
        relay_id="relay1",
    )


class TestCommShape:
    async def test_shape_y(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        assert (await enqueue_monitor(coord)).status == "delivered"
        item = coord.pending_store.get_item("s1", "m1")
        comm = item.comm
        assert comm.from_minion_id == SYSTEM_MINION_ID and comm.from_user is False
        assert comm.sender_type == SenderType.EXTERNAL and comm.sender_id == "relay1"
        assert comm.comm_type == CommType.INFO and comm.delivery == DeliveryPolicy.WHEN_IDLE
        assert comm.auto_start is True and comm.reset_first is False
        assert comm.to_minion_id == "s1"  # the monitoring session only, never a parent / overseer
        comm.validate()
        assert comm.content == "Slack monitor: 2 messages"  # counts only
        assert item.dispatch_content == "DISPATCH monitor" and item.inject_timestamp is False
        coord.delivery_gate.stop_when_idle("s1")

    async def test_same_comm_id_not_enqueued_twice(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue_monitor(coord)
        assert (await enqueue_monitor(coord)).status == "already_delivered"
        coord.delivery_gate.stop_when_idle("s1")

    async def test_system_source_can_never_halt_pivot_or_resume(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue_monitor(coord)
        comm = coord.pending_store.get_item("s1", "m1").comm
        router = object.__new__(CommRouter)
        router.system = SimpleNamespace(session_port=SimpleNamespace(session_manager=coord.session_manager))
        assert await router.is_authorized_sender(comm) is False  # SYSTEM_MINION_ID is nobody's ancestor
        assert comm.comm_type not in (CommType.TASK, CommType.QUESTION, CommType.PIVOT)
        coord.delivery_gate.stop_when_idle("s1")

    async def test_user_pivot_and_halt_do_not_cancel_monitor_content_but_cancel_an_instruction(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue_monitor(coord, "m1")
        await enqueue_instruction(coord, "i1")
        gate = coord.delivery_gate
        user_pivot = Comm(comm_id="p", from_user=True, to_minion_id="s1", delivery=DeliveryPolicy.PIVOT)
        cancelled = await gate.flush_sender("s1", user_pivot)
        assert [c.comm.comm_id for c in cancelled] == ["i1"]  # the 3a instruction is a USER item
        assert coord.pending_store.get_item("s1", "m1").status == "pending"
        assert coord.pending_store.get_item("s1", "i1").status == "cancelled"
        minion_pivot = Comm(comm_id="q", from_minion_id="boss", to_minion_id="s1")
        assert await gate.flush_sender("s1", minion_pivot) == []
        assert coord.pending_store.get_item("s1", "m1").status == "pending"
        gate.stop_when_idle("s1")

    async def test_stale_pivot_note_never_names_the_user_for_monitor_content(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue_monitor(coord)
        comm = coord.pending_store.get_item("s1", "m1").comm
        assert coord.delivery_gate._sender_label(comm) == "Slack monitor"
        coord.delivery_gate.stop_when_idle("s1")


class TestDeliverySemantics:
    async def test_busy_session_waits_then_receives_the_dispatch_text_and_comm_metadata(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        coord, info, sent = live_coordinator(tmp_path, processing=True)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue_monitor(coord)
            for _ in range(10):
                await asyncio.sleep(0)
            assert sent == []
            info.is_processing = False
            assert await _wait_for(lambda: bool(sent))
        message, metadata, inject = sent[0]
        assert message == "DISPATCH monitor" and inject is False
        assert metadata["comm"]["from_display_name"] == "Slack monitor"  # collapsed CommCard
        assert "from_minion_id" not in metadata["comm"]  # no jump link
        assert coord.pending_store.get_item("s1", "m1").status == "delivered"
        coord.delivery_gate.stop_when_idle("s1")

    async def test_stopped_session_auto_starts_because_there_is_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        coord, info, sent = live_coordinator(tmp_path, state=SessionState.TERMINATED)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue_monitor(coord)
            assert await _wait_for(lambda: bool(sent))
        coord.start_session.assert_awaited_once()
        coord.delivery_gate.stop_when_idle("s1")

    async def test_paused_queue_keeps_it_pending_and_never_resumes(self, tmp_path):
        coord, info, sent = live_coordinator(tmp_path, paused=True)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue_monitor(coord)
            for _ in range(20):
                await asyncio.sleep(0)
        assert sent == [] and coord.pending_store.get_item("s1", "m1").status == "pending"
        coord.session_manager.update_session.assert_not_called()
        coord.delivery_gate.stop_when_idle("s1")

    async def test_port_reports_status_and_pending_monitors(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        port = CoordinatorPort(coord)
        assert port.item_status("s1", "m1") is None and not port.has_pending_monitor("s1", "relay1")
        await enqueue_monitor(coord)
        await enqueue_instruction(coord)
        assert port.item_status("s1", "m1") == "pending" and port.has_pending_monitor("s1", "relay1")
        assert not port.has_pending_monitor("s1", "other-relay")
        coord.delivery_gate.stop_when_idle("s1")


class TestEndToEnd:
    """Real gate + PendingStore behind the service: window -> envelope -> sent -> cursor."""

    async def build(self, tmp_path, monkeypatch, **kw):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        env = make_inbound(tmp_path)
        env.relay_service.policies[CHANNEL] = "read_only"
        coord, info, sent = live_coordinator(tmp_path / "coord", **kw)
        port = CoordinatorPort(coord)
        env.service.port = port
        env.service.watcher._probe = port
        env.coord, env.info, env.sent = coord, info, sent
        env.t = await started(env)
        return env

    async def test_cursor_advances_only_after_the_real_gate_delivered(self, tmp_path, monkeypatch):
        (tmp_path / "coord").mkdir()
        env = await self.build(tmp_path, monkeypatch, processing=True)
        try:
            with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
                rec = await monitor(env)
                ts = await emit(env, env.t, "deploy now", seq=1)
                await fire_window(env)
                comm_id = next(iter(env.service.watcher.entries))
                await env.service.watcher.poll_once()  # session busy: still pending
                assert env.service.store.get_monitor(rec["id"])["last_seen_ts"] == f"{BASE_TIME:.6f}"
                env.info.is_processing = False
                assert await _wait_for(lambda: bool(env.sent))
                await env.service.watcher.poll_once()
            assert env.service.store.get_monitor(rec["id"])["last_seen_ts"] == ts
            assert comm_id not in env.service.watcher.entries
            assert "deploy now" in env.sent[0][0] and "NOT instructions" in env.sent[0][0]
            assert env.adapter.posts == [] and env.adapter.reactions == []
        finally:
            env.coord.delivery_gate.stop_when_idle("s1")
            await env.service.stop()

    async def test_clearing_the_queue_cancels_it_and_the_cursor_stays(self, tmp_path, monkeypatch):
        (tmp_path / "coord").mkdir()
        env = await self.build(tmp_path, monkeypatch, processing=True)
        try:
            rec = await monitor(env)
            await emit(env, env.t, "deploy now", seq=1)
            await fire_window(env)
            await env.coord.pending_store.clear_pending("s1", tmp_path / "coord")
            await env.service.watcher.poll_once()
            got = env.service.store.get_monitor(rec["id"])
            assert got["last_seen_ts"] == f"{BASE_TIME:.6f}" and got["failures"] == 1
            assert audit_events(env, "monitor_delivery_failed")
        finally:
            env.coord.delivery_gate.stop_when_idle("s1")
            await env.service.stop()

    async def test_instruction_cancelled_before_delivery_gets_one_marked_reply(self, tmp_path, monkeypatch):
        (tmp_path / "coord").mkdir()
        env = await self.build(tmp_path, monkeypatch, processing=True)
        env.service._sink = lambda session_id, **kw: enqueue_external_comm(env.coord, session_id, **kw)
        try:
            ts = env.clock.ts(seq=1)
            await env.t.emit(envelope(message_payload("!my-agent run it", ts), "EI"))
            await settle(env)
            await env.service.watcher.poll_once()
            assert env.adapter.posts == []  # still pending: nothing yet
            await env.coord.pending_store.clear_pending("s1", tmp_path / "coord")
            await env.service.watcher.poll_once()
            await env.service.watcher.poll_once()
            replies = [p for p in env.adapter.posts]
            assert len(replies) == 1 and "cancelled" in replies[0]["text"]
            assert replies[0]["thread_ts"] == ts and replies[0]["channel"] == SELF_DM
            assert [a["outcome"] for a in audit_events(env, "inbound_instruction")][-1] == "cancelled"
        finally:
            env.coord.delivery_gate.stop_when_idle("s1")
            await env.service.stop()
