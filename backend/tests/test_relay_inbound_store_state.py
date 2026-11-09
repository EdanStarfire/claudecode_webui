"""Inbound persistence: subscription store and state file (issue #2103 stage 3a, D4/D7)."""

from __future__ import annotations

import json

import pytest

from backend.relay.inbound.constants import MAX_THREAD_RECORDS, THREAD_TTL_SECONDS
from backend.relay.inbound.state import STATE_FILE, InboundState
from backend.relay.inbound.store import ACTIVE, SUSPENDED, JsonSubscriptionStore

TS = "1800000000.000100"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def store_at(tmp_path, clock=None):
    return JsonSubscriptionStore(tmp_path, "r1", clock=clock or Clock())


class TestStore:
    async def test_thread_record_roundtrip(self, tmp_path):
        s = store_at(tmp_path)
        rec = await s.create_thread("s1", "D1", TS, TS)
        assert rec["kind"] == "instruction_thread" and rec["status"] == ACTIVE
        reloaded = store_at(tmp_path)
        reloaded.load()
        assert reloaded.find_thread("D1", TS)["session_id"] == "s1"
        assert reloaded.has_active_thread("s1", "D1", TS)
        assert not reloaded.has_active_thread("s2", "D1", TS)  # other session
        assert not reloaded.has_active_thread("s1", "D2", TS)  # other channel
        assert not reloaded.has_active_thread("s1", "D1", "1.1")  # other thread

    async def test_record_only_from_its_own_root(self, tmp_path):
        with pytest.raises(ValueError):
            await store_at(tmp_path).create_thread("s1", "D1", TS, "1800000000.000200")

    async def test_create_is_idempotent(self, tmp_path):
        s = store_at(tmp_path)
        a = await s.create_thread("s1", "D1", TS, TS)
        b = await s.create_thread("s1", "D1", TS, TS)
        assert a["id"] == b["id"] and len(await s.list("r1")) == 1

    async def test_delete_drops_dispose_suspends_start_restores(self, tmp_path):
        s = store_at(tmp_path)
        await s.create_thread("s1", "D1", TS, TS)
        await s.set_status_by_session("s1", SUSPENDED, reason="disposed")
        assert not s.has_active_thread("s1", "D1", TS)
        assert s.find_thread("D1", TS)["status"] == SUSPENDED
        # only dispose-suspended records are restored
        await s.set_status_by_session("s1", ACTIVE, only_reason="other")
        assert not s.has_active_thread("s1", "D1", TS)
        await s.set_status_by_session("s1", ACTIVE, only_reason="disposed")
        assert s.has_active_thread("s1", "D1", TS)
        assert await s.remove_by_session("s1") == 1
        assert s.find_thread("D1", TS) is None

    async def test_ttl_expiry_and_cap(self, tmp_path):
        clock = Clock()
        s = store_at(tmp_path, clock)
        await s.create_thread("s1", "D1", TS, TS)
        clock.t += THREAD_TTL_SECONDS + 1
        assert await s.housekeeping() == (1, 0)
        assert s.find_thread("D1", TS) is None
        for i in range(MAX_THREAD_RECORDS + 5):
            clock.t += 1
            ts = f"1800000{i:03d}.000100"
            await s.create_thread("s1", "D1", ts, ts)
        expired, evicted = await s.housekeeping()
        assert (expired, evicted) == (0, 5)
        assert s.find_thread("D1", "1800000000.000100") is None  # oldest idle evicted
        assert s.find_thread("D1", f"1800000{MAX_THREAD_RECORDS + 4:03d}.000100") is not None

    async def test_activity_keeps_a_thread_alive(self, tmp_path):
        clock = Clock()
        s = store_at(tmp_path, clock)
        rec = await s.create_thread("s1", "D1", TS, TS)
        clock.t += THREAD_TTL_SECONDS - 10
        await s.touch_thread(rec["id"], last_seen_ts="1800000050.000100")
        clock.t += 100
        assert await s.housekeeping() == (0, 0)

    async def test_corrupt_file_moved_aside(self, tmp_path):
        (tmp_path / "subscriptions.json").write_text("{not json")
        s = store_at(tmp_path)
        s.load()
        assert await s.list("r1") == []
        assert list(tmp_path.glob("subscriptions.json.corrupt-*"))

    async def test_interrupted_write_leaves_previous_file(self, tmp_path):
        s = store_at(tmp_path)
        await s.create_thread("s1", "D1", TS, TS)
        (tmp_path / "subscriptions.json.tmp").write_text("{partial")  # crash mid-write leftover
        again = store_at(tmp_path)
        again.load()
        assert again.find_thread("D1", TS) is not None
        await again.create_thread("s1", "D1", "1800000009.000100", "1800000009.000100")
        assert json.loads((tmp_path / "subscriptions.json").read_text())["version"] == 1


class TestState:
    async def test_roundtrip_dedupe_cursor_inflight(self, tmp_path):
        st = InboundState(tmp_path)
        st.initialize_first_start(1.0)
        st.mark_seen("D1", TS)
        st.advance_cursor("D1", TS)
        st.advance_cursor("D1", "1700000000.000100")  # never moves back
        st.inflight["D1:" + TS] = {"key": "D1:" + TS, "event": {}, "decision": {}}
        await st.flush()
        again = InboundState(tmp_path)
        again.load()
        assert again.existed and again.seen("D1", TS) and again.cursors == {"D1": TS}
        assert list(again.inflight) == ["D1:" + TS]

    def test_dedupe_ring_is_bounded(self, tmp_path):
        st = InboundState(tmp_path, ring_size=5000)
        for i in range(5100):
            st.mark_seen("D1", f"1800000000.{i:06d}")
        assert st.dedupe_size == 5000
        assert not st.seen("D1", "1800000000.000000") and st.seen("D1", "1800000000.005099")

    async def test_corrupt_state_starts_empty(self, tmp_path):
        (tmp_path / STATE_FILE).write_text("[]]")
        st = InboundState(tmp_path)
        st.load()
        assert not st.existed and st.inflight == {}
        assert list(tmp_path.glob(f"{STATE_FILE}.corrupt-*"))

    async def test_unknown_version_ignored(self, tmp_path):
        (tmp_path / STATE_FILE).write_text(json.dumps({"version": 9, "cursors": {"D": "1.1"}}))
        st = InboundState(tmp_path)
        st.load()
        assert st.cursors == {}
