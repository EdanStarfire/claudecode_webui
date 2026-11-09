"""Inbound integration seams: RelayManager hooks, InboundManager, coordinator sink, queue
limitation (issue #2103 stage 3a)."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.relay_manager as relay_manager_module
from backend.delivery_gate import DeliveryGate
from backend.models.legion_models import DeliveryPolicy, SenderType
from backend.models.relay import RelayRecord
from backend.pending_store import PendingStore
from backend.relay.adapter import AuthInfo
from backend.relay.inbound import constants as const
from backend.relay.inbound.manager import InboundManager
from backend.relay.inbound.service import comm_id_for
from backend.relay_manager import RelayManager
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionInfo, SessionState
from backend.tests.relay_inbound_support import (
    FakeSocketTransport,
    RecordingSink,
    make_inbound,
)


class FakeVault:
    def secret_exists(self, name):
        return True

    async def delete_secret(self, name):
        pass


class AuthAdapter:
    async def auth_test(self):
        return AuthInfo(user_id="U0OWNER001", team_id="T0TEAM001", team="Team", user="owner")

    async def open_dm(self, user_id):
        return "D0SELF0001"


def relay_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_manager_module, "get_secret_value", lambda name: {
        "user-secret": "xoxp-user", "app-secret": "xapp-app"}.get(name))
    return RelayManager(tmp_path, FakeVault(), adapter_factory=lambda provider: AuthAdapter())


class TestRelayManagerHooks:
    async def test_record_defaults_and_roundtrip(self):
        r = RelayRecord(relay_id="r", name="Slack")
        assert r.inbound_enabled is True
        assert RelayRecord.from_dict(r.to_dict()).inbound_enabled is True
        r.inbound_enabled = False
        assert RelayRecord.from_dict(r.to_dict()).inbound_enabled is False
        legacy = r.to_dict()
        del legacy["inbound_enabled"]
        assert RelayRecord.from_dict(legacy).inbound_enabled is True  # existing relays default on

    async def test_listeners_fire_on_changes(self, tmp_path, monkeypatch):
        m = relay_manager(tmp_path, monkeypatch)
        seen: list[str] = []
        m.change_listeners.append(seen.append)
        m.inbound_status_provider = lambda rid: {"status": "connected", "detail": None}
        record = await m.create_relay("Slack")
        assert seen == []
        await m.update_relay(record.relay_id, name="Renamed")
        assert seen == []  # a rename does not restart inbound
        await m.update_relay(record.relay_id, inbound_enabled=False)
        assert seen == [record.relay_id] and record.inbound_enabled is False
        await m.update_relay(record.relay_id, inbound_enabled=False)
        assert len(seen) == 1  # unchanged value: no restart
        await m.set_secret_refs(record.relay_id, "user-secret", "app-secret")
        assert len(seen) == 2 and record.owner_user_id == "U0OWNER001"
        await m.test_relay(record.relay_id)
        assert len(seen) == 3
        public = m.public_dict(record)
        assert public["inbound_status"] == "connected" and public["inbound_enabled"] is False
        assert "xapp-" not in str(public)
        await m.delete_relay(record.relay_id)
        assert len(seen) == 4

    async def test_failing_listener_never_breaks_the_change(self, tmp_path, monkeypatch):
        m = relay_manager(tmp_path, monkeypatch)
        m.change_listeners.append(lambda rid: 1 / 0)
        record = await m.create_relay("Slack")
        await m.update_relay(record.relay_id, inbound_enabled=False)
        assert record.inbound_enabled is False


def inbound_manager(tmp_path):
    e = make_inbound(tmp_path)
    transports: list[FakeSocketTransport] = []

    def factory(rid):
        t = FakeSocketTransport()
        transports.append(t)
        return t

    mgr = InboundManager(
        relay_manager=e.manager, relay_service=e.service._relay_service,
        session_manager=e.sessions, audit=e.audit, relays_root=tmp_path / "relays",
        transport_factory=factory,
    )
    mgr.sink = RecordingSink()
    return mgr, e, transports


class TestInboundManager:
    async def test_start_status_stop(self, tmp_path):
        mgr, e, transports = inbound_manager(tmp_path)
        assert mgr.status_for("relay1")["status"] == const.STATUS_STOPPED
        await mgr.start()
        await asyncio.sleep(0.2)
        assert mgr.status_for("relay1")["status"] == const.STATUS_CONNECTED
        await mgr.stop()
        assert mgr.status_for("relay1")["status"] == const.STATUS_STOPPED
        assert transports[0].disconnect_count >= 1

    async def test_refresh_restarts_and_delete_removes_state(self, tmp_path):
        mgr, e, transports = inbound_manager(tmp_path)
        await mgr.start()
        await asyncio.sleep(0.2)
        e.manager.relays["relay1"].inbound_enabled = False
        await mgr.refresh("relay1")
        await asyncio.sleep(0.2)
        st = mgr.status_for("relay1")
        assert (st["status"], st["detail"]) == (const.STATUS_DISABLED, "disabled_by_owner")
        assert (tmp_path / "relays" / "relay1" / "inbound_state.json").exists()
        del e.manager.relays["relay1"]
        await mgr.refresh("relay1")
        assert "relay1" not in mgr.services
        assert not (tmp_path / "relays" / "relay1" / "inbound_state.json").exists()
        await mgr.stop()

    async def test_schedule_refresh_is_sync_and_safe(self, tmp_path):
        mgr, e, _ = inbound_manager(tmp_path)
        await mgr.start()
        mgr.schedule_refresh("relay1")
        await asyncio.sleep(0.3)
        assert mgr.status_for("relay1")["status"] == const.STATUS_CONNECTED
        await mgr.stop()
        mgr.schedule_refresh("relay1")  # after stop: nothing started
        await asyncio.sleep(0.05)
        assert "relay1" not in mgr.services

    async def test_thread_grant_lookup_and_session_hooks(self, tmp_path):
        mgr, e, _ = inbound_manager(tmp_path)
        await mgr.start()
        svc = mgr.services["relay1"]
        rec = await svc.store.create_thread("s1", "D0SELF0001", "1800000000.000100", "1800000000.000100")
        assert mgr.has_thread_grant("s1", "relay1", "D0SELF0001", "1800000000.000100")
        assert not mgr.has_thread_grant("s1", "nope", "D0SELF0001", "1800000000.000100")
        await mgr.on_session_disposed("s1")
        assert not mgr.has_thread_grant("s1", "relay1", "D0SELF0001", "1800000000.000100")
        await mgr.on_session_started("s1")
        assert mgr.has_thread_grant("s1", "relay1", "D0SELF0001", "1800000000.000100")
        await mgr.on_session_deleted("s1")
        assert svc.store.find_thread("D0SELF0001", rec["thread_ts"]) is None
        await mgr.stop()


def fake_coordinator(tmp_path, *, processing=True, paused=False):
    info = MagicMock(spec=SessionInfo)
    info.session_id = "s1"
    info.state = SessionState.ACTIVE
    info.is_processing = processing
    info.queue_config = None
    info.error_message = None
    info.queue_paused = paused
    coord = MagicMock()
    coord.session_manager = AsyncMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=info)
    coord.session_manager.get_session_directory = AsyncMock(return_value=tmp_path)
    coord.session_manager.update_session = AsyncMock(return_value=True)
    coord.legion_system = MagicMock()
    coord.legion_system.comm_router = AsyncMock(is_authorized_sender=AsyncMock(return_value=False))
    coord.pending_store = PendingStore()
    coord.delivery_gate = DeliveryGate(coord)
    coord.delivery_gate.ensure_when_idle_running = MagicMock()
    return coord


def enqueue(coord, comm_id=None, text="do it"):
    return SessionCoordinator.enqueue_external_comm(
        coord, "s1", comm_id=comm_id or str(uuid.uuid4()), summary="Slack instruction: do it",
        content=text, dispatch_content=f"DISPATCH {text}",
        metadata={"relay_inbound": {"thread_ts": "1.1"}}, relay_id="relay1",
    )


class TestEnqueueExternalComm:
    async def test_busy_session_item_is_queued_as_external_when_idle(self, tmp_path):
        coord = fake_coordinator(tmp_path, processing=True)
        outcome = await enqueue(coord, "c1")
        assert outcome.status == "delivered"
        item = coord.pending_store.get_item("s1", "c1")
        assert item.status == "pending"
        assert item.comm.sender_type == SenderType.EXTERNAL and item.comm.sender_id == "relay1"
        assert item.comm.delivery == DeliveryPolicy.WHEN_IDLE and item.comm.auto_start
        assert not item.comm.reset_first
        assert item.dispatch_content == "DISPATCH do it" and item.inject_timestamp is False
        coord.delivery_gate.ensure_when_idle_running.assert_called_with("s1")

    async def test_same_comm_id_is_not_enqueued_twice(self, tmp_path):
        coord = fake_coordinator(tmp_path)
        cid = comm_id_for("relay1", "D0SELF0001", "1800000000.000100")
        assert (await enqueue(coord, cid)).status == "delivered"
        assert (await enqueue(coord, cid)).status == "already_delivered"
        assert len(coord.pending_store.get_all("s1")) == 1

    async def test_paused_queue_keeps_instruction_pending(self, tmp_path):
        coord = fake_coordinator(tmp_path, paused=True)
        await enqueue(coord, "c1")
        assert coord.pending_store.get_item("s1", "c1").status == "pending"
        coord.session_manager.update_session.assert_not_called()  # an external sender cannot resume

    async def test_failure_outcome_when_session_directory_missing(self, tmp_path):
        coord = fake_coordinator(tmp_path)
        coord.session_manager.get_session_directory = AsyncMock(return_value=None)
        outcome = await enqueue(coord, "c1")
        assert outcome.status == "failed" and outcome.error


class TestQueueLimitationIsPinned:
    """Issue #2362 (pre-existing, carved out of #2103 stage 3a): the pending queue is not
    recovered across a Backend restart. An instruction queued for a busy or stopped session is
    lost if the Backend restarts before it is drained; the owner may need to resend."""

    async def test_enqueue_never_loads_the_ledger(self, tmp_path):
        coord = fake_coordinator(tmp_path)
        coord.pending_store.load = AsyncMock()
        await enqueue(coord, "c1")
        coord.pending_store.load.assert_not_called()

    async def test_restart_loses_a_queued_instruction(self, tmp_path):
        coord = fake_coordinator(tmp_path, processing=True)
        await enqueue(coord, "c1")
        assert coord.pending_store.get_item("s1", "c1") is not None
        restarted = fake_coordinator(tmp_path, processing=False)  # fresh in-memory store
        assert restarted.pending_store.get_item("s1", "c1") is None
        assert restarted.pending_store.get_pending("s1") == []
        restarted.delivery_gate.ensure_when_idle_running.assert_not_called()  # nothing re-armed

    def test_no_recovery_machinery_was_added(self):
        assert not hasattr(SessionCoordinator, "recover_pending")
        assert not hasattr(PendingStore, "recover_pending")


class TestStatusRoute:
    def _client(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.routers import relay as relay_router

        record = SimpleNamespace(inbound_enabled=True)

        async def get_relay(rid):
            return record if rid == "relay1" else None

        inbound = SimpleNamespace(status_for=lambda rid: {
            "status": "auth_failed", "detail": "token_revoked", "connected_at": None})
        webui = SimpleNamespace(service=SimpleNamespace(
            relay_manager=SimpleNamespace(get_relay=get_relay), relay_inbound=inbound,
            relay_service=None,
        ))
        app = FastAPI()
        app.include_router(relay_router.build_router(webui))
        return TestClient(app)

    def test_status_shape_and_404(self, tmp_path):
        client = self._client(tmp_path)
        body = client.get("/api/relays/relay1/inbound/status").json()
        assert body == {"relay_id": "relay1", "enabled": True, "status": "auth_failed",
                        "detail": "token_revoked", "connected_at": None}
        assert client.get("/api/relays/nope/inbound/status").status_code == 404


# --- What from_user=True / CommType.SYSTEM mean for an external instruction comm -------------
# Comm.validate() demands exactly one source (a minion or the user), so the plan's
# from_user=False was not constructible; the owner's own Slack instruction is "from the user",
# tagged sender_type=EXTERNAL. These tests pin the consequences (see CLAUDE.md, "Relay inbound").

from unittest.mock import patch  # noqa: E402

from backend.models.legion_models import Comm, CommType  # noqa: E402

_real_sleep = asyncio.sleep


async def _fast_sleep(_seconds):
    await _real_sleep(0)


def live_coordinator(tmp_path, *, state=SessionState.ACTIVE, processing=False, paused=False):
    coord = fake_coordinator(tmp_path, processing=processing, paused=paused)
    info = coord.session_manager.get_session_info.return_value
    info.state = state
    coord.delivery_gate = DeliveryGate(coord)  # the real when-idle drain loop
    sent: list[tuple] = []

    async def send_message(session_id, message, metadata=None, inject_timestamp=True):
        sent.append((message, metadata, inject_timestamp))
        return True

    async def start_session(session_id, permission_callback=None):
        info.state = SessionState.ACTIVE
        return True

    coord.send_message = AsyncMock(side_effect=send_message)
    coord.start_session = AsyncMock(side_effect=start_session)
    coord.wait_for_session_ready = AsyncMock(return_value=True)
    coord._permission_callback_factory = None
    coord._message_callback_registrar = None
    return coord, info, sent


async def _wait_for(predicate, rounds=100):
    for _ in range(rounds):
        await asyncio.sleep(0)
        if predicate():
            return True
    return False


class TestExternalCommDeliverySemantics:
    async def test_busy_session_waits_for_idle_then_delivers_dispatch_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        coord, info, sent = live_coordinator(tmp_path, processing=True)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            assert (await enqueue(coord, "c1")).status == "delivered"
            for _ in range(10):
                await asyncio.sleep(0)
            assert sent == []  # busy: held
            info.is_processing = False
            assert await _wait_for(lambda: bool(sent))
        message, metadata, inject = sent[0]
        assert message == "DISPATCH do it"
        assert metadata == {"relay_inbound": {"thread_ts": "1.1"}}  # no "comm" key: not a CommCard
        assert inject is False
        assert coord.pending_store.get_item("s1", "c1").status == "delivered"
        coord.delivery_gate.stop_when_idle("s1")

    async def test_stopped_session_is_auto_started(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        coord, info, sent = live_coordinator(tmp_path, state=SessionState.TERMINATED)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue(coord, "c1")
            assert await _wait_for(lambda: bool(sent))
        coord.start_session.assert_awaited_once()
        assert sent[0][0] == "DISPATCH do it"
        coord.delivery_gate.stop_when_idle("s1")

    async def test_paused_queue_stays_paused_and_nothing_is_sent(self, tmp_path):
        coord, info, sent = live_coordinator(tmp_path, paused=True)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue(coord, "c1")
            for _ in range(20):
                await asyncio.sleep(0)
        assert sent == []
        assert coord.pending_store.get_item("s1", "c1").status == "pending"
        coord.session_manager.update_session.assert_not_called()  # queue_paused untouched
        coord.delivery_gate.stop_when_idle("s1")

    async def test_no_session_state_written_on_the_normal_path(self, tmp_path):
        """from_user=True adds no user-input bookkeeping: the gate never writes session fields
        (last-input timestamps, queue_paused, ...) for this comm; only send_message runs."""
        coord, info, sent = live_coordinator(tmp_path)
        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await enqueue(coord, "c1")
            assert await _wait_for(lambda: bool(sent))
        coord.session_manager.update_session.assert_not_called()
        coord.delivery_gate.stop_when_idle("s1")

    async def test_comm_shape(self, tmp_path):
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue(coord, "c1")
        comm = coord.pending_store.get_item("s1", "c1").comm
        assert comm.from_user is True and comm.from_minion_id is None
        assert comm.sender_type == SenderType.EXTERNAL and comm.comm_type == CommType.SYSTEM
        comm.validate()
        coord.delivery_gate.stop_when_idle("s1")

    def test_a_non_user_non_minion_source_is_not_constructible(self):
        # why the plan's from_user=False could not be used without changing Comm.validate()
        comm = Comm(comm_id="x", from_user=False, to_minion_id="s1", sender_type=SenderType.EXTERNAL)
        with pytest.raises(ValueError, match="exactly one source"):
            comm.validate()

    async def test_user_authority_consequence_pivot_flush_treats_it_as_the_user(self, tmp_path):
        """Pinned consequence of from_user=True: sender-scoped flush (_same_sender) groups it with
        other user-originated items, so a USER pivot cancels a still-pending Slack instruction
        (like a queued manual message); a minion's pivot does not."""
        coord, _, _ = live_coordinator(tmp_path, processing=True)
        await enqueue(coord, "c1")
        gate = coord.delivery_gate
        external = coord.pending_store.get_item("s1", "c1").comm
        user_pivot = Comm(comm_id="p", from_user=True, to_minion_id="s1")
        minion_pivot = Comm(comm_id="m", from_minion_id="boss", to_minion_id="s1")
        assert gate._same_sender(external, user_pivot) is True
        assert gate._same_sender(external, minion_pivot) is False
        assert gate._sender_label(external) == "the user"
        gate.stop_when_idle("s1")

    async def test_external_comm_never_implicitly_resumes_a_paused_queue(self, tmp_path):
        coord, info, sent = live_coordinator(tmp_path, paused=True)
        # SYSTEM is not a resume trigger even though the user is "authorized"
        await enqueue(coord, "c1")
        item = coord.pending_store.get_item("s1", "c1")
        assert item.comm.comm_type not in (CommType.TASK, CommType.QUESTION, CommType.PIVOT)
        coord.delivery_gate.stop_when_idle("s1")
