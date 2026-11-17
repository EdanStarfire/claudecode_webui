"""Startup recovery of the pending delivery ledger (issue #2362)."""

import asyncio
import json
import time
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

from backend.delivery_gate import DeliveryGate
from backend.models.legion_models import Comm, DeliveryPolicy, SenderType
from backend.pending_boot import PendingBoot
from backend.pending_recovery import (
    ERROR_EXPIRED,
    ERROR_NOT_REPLAYED,
    recover_session,
)
from backend.pending_store import PendingStore
from backend.relay.inbound.monitor_delivery import classify_replayed, replay_ttl
from backend.session_manager import SessionInfo, SessionState

NOW = time.time()
H = 3600.0


def make_comm(sid, *, delivery=DeliveryPolicy.WHEN_IDLE, auto_start=True, **kw):
    kw.setdefault("comm_id", str(uuid.uuid4()))
    kw.setdefault("content", "hi")
    return Comm(from_user=True, to_minion_id=sid, delivery=delivery,
                auto_start=auto_start, **kw)


async def seed(store, sid, d, comm, age=0.0):
    item = await store.enqueue(sid, d, comm)
    item.created_at = NOW - age
    # rewrite the persisted created_at
    path = d / "pending.jsonl"
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    for line in lines:
        if line.get("type") == "enqueue" and line["comm"]["comm_id"] == comm.comm_id:
            line["created_at"] = NOW - age
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return item


async def recover(d, sid="s1", **kw):
    store = PendingStore()
    report = await recover_session(store, sid, d, now=NOW, **kw)
    return store, report


class TestRecoverRules:
    async def test_non_when_idle_fails_exact_error_and_when_idle_kept(self, tmp_path):
        s = PendingStore()
        a = make_comm("s1", delivery=DeliveryPolicy.IMMEDIATE)
        b = make_comm("s1", delivery=DeliveryPolicy.PIVOT)
        c = make_comm("s1")
        for x in (a, b, c):
            await seed(s, "s1", tmp_path, x)
        store, rep = await recover(tmp_path)
        assert [i.comm.comm_id for i in rep.failed_not_replayed] == [a.comm_id, b.comm_id]
        assert store.get_item("s1", a.comm_id).error == ERROR_NOT_REPLAYED
        assert [i.comm.comm_id for i in rep.kept] == [c.comm_id]
        assert store.get_item("s1", c.comm_id).status == "pending"

    async def test_ttl_boundaries(self, tmp_path):
        s = PendingStore()
        fresh = make_comm("s1")
        old = make_comm("s1")
        sched_fresh = make_comm("s1", sender_type=SenderType.SCHEDULE)
        sched_old = make_comm("s1", sender_type=SenderType.SCHEDULE)
        await seed(s, "s1", tmp_path, fresh, age=23 * H)
        await seed(s, "s1", tmp_path, old, age=25 * H)
        await seed(s, "s1", tmp_path, sched_fresh, age=59 * 60)
        await seed(s, "s1", tmp_path, sched_old, age=61 * 60)
        store, rep = await recover(tmp_path)
        assert {i.comm.comm_id for i in rep.kept} == {fresh.comm_id, sched_fresh.comm_id}
        assert {i.comm.comm_id for i in rep.expired} == {old.comm_id, sched_old.comm_id}
        assert store.get_item("s1", old.comm_id).error == ERROR_EXPIRED

    async def test_classifier_cancel_persists(self, tmp_path):
        s = PendingStore()
        c = make_comm("s1")
        await seed(s, "s1", tmp_path, c)
        store, rep = await recover(tmp_path, classify=lambda i: "cancel")
        assert [i.comm.comm_id for i in rep.cancelled] == [c.comm_id]
        store2 = PendingStore()
        await store2.load("s1", tmp_path)
        assert store2.get_item("s1", c.comm_id).status == "cancelled"

    async def test_ttl_override_and_order_of_checks(self, tmp_path):
        s = PendingStore()
        c = make_comm("s1")
        await seed(s, "s1", tmp_path, c, age=2 * H)
        _, rep = await recover(tmp_path, ttl_override=lambda i: H, classify=lambda i: "cancel")
        assert len(rep.expired) == 1 and not rep.cancelled  # expiry before classifier

    async def test_order_by_position_and_terminal_untouched(self, tmp_path):
        s = PendingStore()
        comms = [make_comm("s1") for _ in range(3)]
        for x in comms:
            await seed(s, "s1", tmp_path, x)
        await s.mark_delivered("s1", tmp_path, comms[0].comm_id)
        await s.cancel("s1", tmp_path, comms[1].comm_id)
        store, rep = await recover(tmp_path)
        assert [i.comm.comm_id for i in rep.kept] == [comms[2].comm_id]
        assert store.get_item("s1", comms[0].comm_id).status == "delivered"
        assert store.get_item("s1", comms[1].comm_id).status == "cancelled"


class TestRelayClassifiers:
    def _item(self, kind, sender_id="relay1"):
        c = make_comm("s1", sender_type=SenderType.EXTERNAL, sender_id=sender_id,
                      metadata={"relay_inbound": {"kind": kind}})
        return types.SimpleNamespace(comm=c)

    def test_monitor_cancelled_instruction_replayed_with_1h_ttl(self):
        assert classify_replayed(self._item("monitor")) == "cancel"
        assert classify_replayed(self._item("instruction")) is None
        assert replay_ttl(self._item("instruction")) == 3600
        assert replay_ttl(self._item("monitor")) is None

    def test_non_relay_items_untouched(self):
        plain = types.SimpleNamespace(comm=make_comm("s1"))
        assert classify_replayed(plain) is None and replay_ttl(plain) is None

    async def test_instruction_1h_vs_other_sender_24h(self, tmp_path):
        s = PendingStore()
        ins = make_comm("s1", sender_type=SenderType.EXTERNAL, sender_id="r",
                        metadata={"relay_inbound": {"kind": "instruction"}})
        other = make_comm("s1")
        await seed(s, "s1", tmp_path, ins, age=2 * H)
        await seed(s, "s1", tmp_path, other, age=2 * H)
        _, rep = await recover(tmp_path, classify=classify_replayed, ttl_override=replay_ttl)
        assert [i.comm.comm_id for i in rep.expired] == [ins.comm_id]
        assert [i.comm.comm_id for i in rep.kept] == [other.comm_id]


class TestStoreLoad:
    async def test_roundtrip_statuses_and_order(self, tmp_path):
        s = PendingStore()
        cs = [make_comm("s1") for _ in range(3)]
        for x in cs:
            await s.enqueue("s1", tmp_path, x)
        await s.mark_delivered("s1", tmp_path, cs[0].comm_id)
        await s.cancel("s1", tmp_path, cs[1].comm_id)
        s2 = PendingStore()
        items = await s2.load("s1", tmp_path)
        assert [i.status for i in items] == ["delivered", "cancelled", "pending"]
        assert [i.comm.comm_id for i in items] == [c.comm_id for c in cs]

    async def test_requeue_front_position_survives_load(self, tmp_path):
        s = PendingStore()
        a, b = make_comm("s1"), make_comm("s1")
        await s.enqueue("s1", tmp_path, a)
        await s.mark_delivered("s1", tmp_path, a.comm_id)
        await s.enqueue("s1", tmp_path, b)
        new = await s.requeue("s1", tmp_path, a.comm_id)
        s2 = PendingStore()
        await s2.load("s1", tmp_path)
        pend = s2.get_pending("s1")
        assert [i.comm.comm_id for i in pend] == [new.comm.comm_id, b.comm_id]
        assert pend[0].position < pend[1].position

    async def test_history_cleared_and_malformed_line(self, tmp_path):
        s = PendingStore()
        a, b = make_comm("s1"), make_comm("s1")
        await s.enqueue("s1", tmp_path, a)
        await s.mark_delivered("s1", tmp_path, a.comm_id)
        await s.clear_history("s1", tmp_path)
        await s.enqueue("s1", tmp_path, b)
        with open(tmp_path / "pending.jsonl", "a") as f:
            f.write("{not json\n")
        s2 = PendingStore()
        items = await s2.load("s1", tmp_path)
        assert [i.comm.comm_id for i in items] == [b.comm_id]


# ---------------------------------------------------------------------------
# Boot integration: real PendingStore + DeliveryGate + the real coordinator methods
# ---------------------------------------------------------------------------

_real_sleep = asyncio.sleep


def boot_coordinator(tmp_path, specs, monkeypatch):
    """specs: {sid: state}. Returns (coord, infos, sent, starts)."""
    coord = MagicMock()
    infos = {}
    for sid, state in specs.items():
        info = MagicMock(spec=SessionInfo)
        info.session_id, info.state, info.is_processing = sid, state, False
        info.queue_config, info.queue_paused, info.error_message = None, False, None
        info.name, info.config = sid, {}
        infos[sid] = info
        (tmp_path / sid).mkdir()
    sm = coord.session_manager = AsyncMock()
    sm.list_sessions = AsyncMock(return_value=list(infos.values()))
    sm.get_session_info = AsyncMock(side_effect=lambda sid: infos.get(sid))
    sm.get_session_directory = AsyncMock(side_effect=lambda sid: tmp_path / sid)
    sm.update_session = AsyncMock(return_value=True)
    coord.pending_store = PendingStore()
    coord.delivery_gate = DeliveryGate(coord)
    coord.legion_system = MagicMock()
    coord.legion_system.comm_router = AsyncMock(is_authorized_sender=AsyncMock(return_value=True))
    coord.queue_processor = MagicMock()
    coord.queue_processor._initialize_queues = AsyncMock()
    coord.relay = MagicMock()
    coord.relay.audit.record = AsyncMock()
    coord._permission_callback_factory = None
    coord._message_callback_registrar = None
    sent, starts = [], {"now": 0, "max": 0, "calls": []}

    async def send_message(session_id, message, metadata=None, inject_timestamp=True):
        sent.append(session_id)
        return True

    async def start_session(sid, cb=None):
        starts["now"] += 1
        starts["max"] = max(starts["max"], starts["now"])
        starts["calls"].append(sid)
        await _real_sleep(0.01)
        infos[sid].state = SessionState.ACTIVE
        starts["now"] -= 1
        return True

    coord.send_message = AsyncMock(side_effect=send_message)
    coord.start_session = AsyncMock(side_effect=start_session)
    coord.wait_for_session_ready = AsyncMock(return_value=True)
    coord.pending_boot = PendingBoot(coord)
    monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr("backend.pending_boot.BOOT_DRAIN_SPACING_SECONDS", 0)
    return coord, infos, sent, starts


async def seed_disk(tmp_path, sid, comm, age=0.0):
    store = PendingStore()
    await seed(store, sid, tmp_path / sid, comm, age=age)


async def boot(coord):
    await coord.pending_boot.initialize()
    await coord.pending_boot.arm()
    if coord.pending_boot._drain_task:
        await coord.pending_boot._drain_task


async def until(pred, rounds=300):
    for _ in range(rounds):
        await _real_sleep(0.01)
        if pred():
            return True
    return False


def stop_all(coord):
    for sid in list(coord.delivery_gate._when_idle_tasks):
        coord.delivery_gate.stop_when_idle(sid)


class TestBootDrain:
    async def test_terminated_session_started_and_delivered_once(self, tmp_path, monkeypatch):
        coord, infos, sent, starts = boot_coordinator(
            tmp_path, {"s1": SessionState.TERMINATED}, monkeypatch)
        c = make_comm("s1")
        await seed_disk(tmp_path, "s1", c, age=H)
        await boot(coord)
        assert await until(lambda: coord.pending_store.get_item("s1", c.comm_id).status == "delivered")
        assert sent == ["s1"] and starts["calls"] == ["s1"]
        stop_all(coord)

    async def test_order_preserved(self, tmp_path, monkeypatch):
        coord, _, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        cs = [make_comm("s1", content=str(i)) for i in range(3)]
        store = PendingStore()
        for x in cs:
            await seed(store, "s1", tmp_path / "s1", x)
        order = []
        async def record(session_id, message, metadata=None, inject_timestamp=True):
            order.append(message)
            return True

        coord.send_message = AsyncMock(side_effect=record)
        await boot(coord)
        assert await until(lambda: len(order) == 3)
        assert order == ["0", "1", "2"]
        assert [i.comm.comm_id for i in coord.pending_store.get_all("s1")] == [c.comm_id for c in cs]
        stop_all(coord)

    async def test_auto_start_false_not_started(self, tmp_path, monkeypatch):
        coord, _, sent, starts = boot_coordinator(
            tmp_path, {"s1": SessionState.TERMINATED}, monkeypatch)
        c = make_comm("s1", auto_start=False)
        await seed_disk(tmp_path, "s1", c)
        await boot(coord)
        assert await until(lambda: coord.pending_store.get_item("s1", c.comm_id).status == "failed")
        assert starts["calls"] == [] and sent == []
        assert "auto_start" in coord.pending_store.get_item("s1", c.comm_id).error

    async def test_paused_and_error_deferred_until_state_change(self, tmp_path, monkeypatch):
        coord, infos, sent, _ = boot_coordinator(
            tmp_path, {"p": SessionState.PAUSED, "e": SessionState.ERROR}, monkeypatch)
        cp, ce = make_comm("p"), make_comm("e")
        await seed_disk(tmp_path, "p", cp)
        await seed_disk(tmp_path, "e", ce)
        await boot(coord)
        await _real_sleep(0.05)
        assert sent == [] and not coord.delivery_gate._when_idle_tasks
        assert coord.pending_boot._deferred == {"p", "e"}
        infos["p"].state = SessionState.ACTIVE
        coord.pending_boot.on_state_change("p", SessionState.PAUSED)  # still paused: no-op
        assert "p" in coord.pending_boot._deferred
        coord.pending_boot.on_state_change("p", SessionState.ACTIVE)
        assert await until(lambda: sent == ["p"])
        assert coord.pending_store.get_item("e", ce.comm_id).status == "pending"
        stop_all(coord)

    async def test_queue_paused_not_armed_and_resume_arms(self, tmp_path, monkeypatch):
        coord, infos, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        infos["s1"].queue_paused = True
        c = make_comm("s1")
        await seed_disk(tmp_path, "s1", c)
        await boot(coord)
        await _real_sleep(0.05)
        assert sent == [] and not coord.delivery_gate._when_idle_tasks
        assert coord.pending_store.get_item("s1", c.comm_id).status == "pending"
        # resume path (pause_queue(False)) arms the loop
        infos["s1"].queue_paused = False
        coord.delivery_gate.ensure_when_idle_running("s1")
        assert await until(lambda: sent == ["s1"])
        stop_all(coord)

    async def test_batch_cap(self, tmp_path, monkeypatch):
        specs = {f"s{i}": SessionState.TERMINATED for i in range(6)}
        coord, _, sent, starts = boot_coordinator(tmp_path, specs, monkeypatch)
        for sid in specs:
            await seed_disk(tmp_path, sid, make_comm(sid))
        await boot(coord)
        assert await until(lambda: len(sent) == 6)
        assert starts["max"] <= 3 and len(starts["calls"]) == 6
        stop_all(coord)

    async def test_spacing_between_starts(self, tmp_path, monkeypatch):
        specs = {f"s{i}": SessionState.TERMINATED for i in range(3)}
        coord, _, sent, _ = boot_coordinator(tmp_path, specs, monkeypatch)
        monkeypatch.setattr("backend.pending_boot.BOOT_DRAIN_SPACING_SECONDS", 7)
        for sid in specs:
            await seed_disk(tmp_path, sid, make_comm(sid))
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
            await _real_sleep(0)

        monkeypatch.setattr("backend.pending_boot.asyncio.sleep", fake_sleep)
        await boot(coord)
        assert sleeps.count(7) == 3
        stop_all(coord)

    async def test_crash_window_resends_once(self, tmp_path, monkeypatch):
        """Item sent but never marked delivered (crash window): replayed exactly once."""
        coord, _, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        c = make_comm("s1")
        await seed_disk(tmp_path, "s1", c)  # on disk as pending, as if sent before the crash
        await boot(coord)
        assert await until(lambda: coord.pending_store.get_item("s1", c.comm_id).status == "delivered")
        await _real_sleep(0.05)
        assert sent == ["s1"]
        stop_all(coord)

    async def test_one_session_load_failure_does_not_abort_others(self, tmp_path, monkeypatch):
        coord, _, sent, _ = boot_coordinator(
            tmp_path, {"bad": SessionState.ACTIVE, "ok": SessionState.ACTIVE}, monkeypatch)
        await seed_disk(tmp_path, "ok", make_comm("ok"))
        real_load = coord.pending_store.load

        async def load(sid, d):
            if sid == "bad":
                raise RuntimeError("boom")
            return await real_load(sid, d)

        coord.pending_store.load = load
        await boot(coord)
        assert await until(lambda: sent == ["ok"])
        stop_all(coord)

    async def test_expired_and_non_when_idle_not_replayed(self, tmp_path, monkeypatch):
        coord, _, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        old, imm = make_comm("s1"), make_comm("s1", delivery=DeliveryPolicy.IMMEDIATE)
        await seed_disk(tmp_path, "s1", old, age=30 * H)
        await seed_disk(tmp_path, "s1", imm)
        await boot(coord)
        await _real_sleep(0.05)
        assert sent == []
        assert coord.pending_store.get_item("s1", old.comm_id).error == ERROR_EXPIRED
        assert coord.pending_store.get_item("s1", imm.comm_id).error == ERROR_NOT_REPLAYED

    async def test_relay_monitor_cancelled_audited_no_text_instruction_replayed(
        self, tmp_path, monkeypatch
    ):
        coord, _, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        env = dict(sender_type=SenderType.EXTERNAL, sender_id="relay1")
        mon = make_comm("s1", metadata={"relay_inbound": {"kind": "monitor", "count": 4, "omitted": 1}}, **env)
        ins = make_comm("s1", metadata={"relay_inbound": {"kind": "instruction"}}, **env)
        await seed_disk(tmp_path, "s1", mon)
        await seed_disk(tmp_path, "s1", ins)
        await boot(coord)
        assert await until(lambda: sent == ["s1"])
        assert coord.pending_store.get_item("s1", mon.comm_id).status == "cancelled"
        assert coord.pending_store.get_item("s1", ins.comm_id).status == "delivered"
        coord.relay.audit.record.assert_awaited_once()
        kwargs = coord.relay.audit.record.await_args.kwargs
        assert kwargs["event"] == "monitor_replay_cancelled" and kwargs["code"] == "backend_restart"
        assert kwargs["envelope_id"] == mon.comm_id and kwargs["count"] == 4 and kwargs["omitted"] == 1
        assert "content" not in kwargs and "text" not in kwargs
        stop_all(coord)

    async def test_stop_cancels_drain_and_loops(self, tmp_path, monkeypatch):
        coord, infos, sent, _ = boot_coordinator(tmp_path, {"s1": SessionState.ACTIVE}, monkeypatch)
        infos["s1"].is_processing = True  # busy: the loop keeps waiting
        await seed_disk(tmp_path, "s1", make_comm("s1"))
        await boot(coord)
        await coord.pending_boot.stop()
        assert not coord.delivery_gate._when_idle_tasks
