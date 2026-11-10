"""Monitor records in JsonSubscriptionStore (issue #2103 stage 3b-i)."""

from __future__ import annotations

import pytest

from backend.relay.inbound import constants as const
from backend.relay.inbound.store import ACTIVE, SUSPENDED, JsonSubscriptionStore, MonitorLimitError

CH = "C0CHAN0001"
NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path):
    t = [NOW]
    s = JsonSubscriptionStore(tmp_path, "r1", clock=lambda: t[0])
    s.clock = t
    return s


async def mk(store, session="s1", channel=CH, **kw):
    kw.setdefault("filters", {"keywords": ["x"], "users": []})
    return await store.create_monitor(session, const.KIND_MONITOR_CHANNEL, channel, **kw)


class TestCreate:
    async def test_defaults_cursor_now_and_index(self, store):
        rec, outcome = await mk(store)
        assert outcome == "created" and rec["status"] == ACTIVE and rec["paused"] is False
        assert rec["last_seen_ts"] == f"{NOW:.6f}" and rec["counters"] == {
            "delivered": 0, "dropped": 0, "coalesced": 0}
        assert store.monitors_for_channel(CH) == [rec] and store.monitors_for_channel("C0NOPE0001") == []

    async def test_duplicate_returns_existing(self, store):
        a, _ = await mk(store)
        b, outcome = await mk(store)
        assert b["id"] == a["id"] and outcome == "existing" and store.count() == 1
        c, outcome = await mk(store, filters={"keywords": ["y"], "users": []})
        assert c["id"] != a["id"] and outcome == "created"

    async def test_flood_suspended_duplicate_resumes_from_now(self, store):
        a, _ = await mk(store)
        await store.set_monitor_status(a["id"], SUSPENDED, const.SUSPEND_FLOOD)
        store.clock[0] = NOW + 100
        b, outcome = await mk(store)
        assert outcome == "resumed" and b["status"] == ACTIVE and b["last_seen_ts"] == f"{NOW + 100:.6f}"

    async def test_caps(self, store):
        for i in range(const.MAX_MONITORS_PER_SESSION):
            await mk(store, channel=f"C0CHAN{i:04d}")
        with pytest.raises(MonitorLimitError):
            await mk(store, channel="C0EXTRA001")
        await mk(store, session="s2", channel="C0EXTRA001")  # another session is fine

    async def test_relay_cap(self, store, monkeypatch):
        monkeypatch.setattr(const, "MAX_MONITORS_PER_RELAY", 3)
        for i in range(3):
            await mk(store, session=f"s{i}")
        with pytest.raises(MonitorLimitError):
            await mk(store, session="s9", channel="C0OTHER001")

    async def test_unknown_kind_rejected(self, store):
        with pytest.raises(ValueError):
            await store.create_monitor("s1", "instruction_thread", CH)


class TestPersistence:
    async def test_reload_tolerates_unknown_fields_and_keeps_threads(self, tmp_path):
        s = JsonSubscriptionStore(tmp_path, "r1")
        rec, _ = await mk(s)
        await s.create_thread("s1", "C0CHAN0009", "1.1", "1.1")
        s.advance_cursor(rec["id"], "1800000500.000000")
        await s.close()
        s2 = JsonSubscriptionStore(tmp_path, "r1")
        s2.load()
        got = s2.get_monitor(rec["id"])
        assert got["last_seen_ts"] == "1800000500.000000" and s2.find_thread("C0CHAN0009", "1.1")
        assert s2.monitors_for_channel(CH)[0]["id"] == rec["id"]

    async def test_cursor_is_monotonic(self, store):
        rec, _ = await mk(store)
        assert store.advance_cursor(rec["id"], "1800000100.000000")
        assert not store.advance_cursor(rec["id"], "1800000050.000000")
        assert store.get_monitor(rec["id"])["last_seen_ts"] == "1800000100.000000"


class TestStatusAndPause:
    async def test_paused_survives_dispose_and_start(self, store):
        rec, _ = await mk(store)
        await store.set_paused(rec["id"], True)
        await store.set_status_by_session("s1", SUSPENDED, reason="disposed", only_status=ACTIVE)
        assert store.get_monitor(rec["id"])["status"] == SUSPENDED
        await store.set_status_by_session("s1", ACTIVE, only_reason="disposed")
        got = store.get_monitor(rec["id"])
        assert got["status"] == ACTIVE and got["paused"] is True

    async def test_dispose_does_not_overwrite_other_reason(self, store):
        rec, _ = await mk(store)
        await store.set_monitor_status(rec["id"], SUSPENDED, const.SUSPEND_CHANNEL_BLOCKED)
        await store.set_status_by_session("s1", SUSPENDED, reason="disposed", only_status=ACTIVE)
        assert store.get_monitor(rec["id"])["suspended_reason"] == const.SUSPEND_CHANNEL_BLOCKED
        await store.set_status_by_session("s1", ACTIVE, only_reason="disposed")
        assert store.get_monitor(rec["id"])["status"] == SUSPENDED  # a start restores only disposed ones

    async def test_kinds_filter(self, store):
        await mk(store)
        await store.create_thread("s1", "C0CHAN0009", "1.1", "1.1")
        await store.set_status_by_session("s1", SUSPENDED, reason="x", kinds=frozenset({const.THREAD_KIND}))
        assert [m["status"] for m in store.monitors()] == [ACTIVE]
        assert store.find_thread("C0CHAN0009", "1.1")["status"] == SUSPENDED

    async def test_resume_from_restarts_cursor(self, store):
        rec, _ = await mk(store)
        await store.set_monitor_status(rec["id"], SUSPENDED, const.SUSPEND_POLICY_UNSET)
        got = await store.set_monitor_status(rec["id"], ACTIVE, resume_from=NOW + 500)
        assert got["last_seen_ts"] == f"{NOW + 500:.6f}" and got["suspended_reason"] is None


class TestExpiry:
    async def test_expires_at_and_thread_idle(self, store):
        a, _ = await mk(store, expires_at=NOW + 10)
        b, _ = await store.create_monitor("s1", const.KIND_MONITOR_THREAD, CH, thread_ts="1.1")
        c, _ = await mk(store, channel="C0CHAN0002")
        store.clock[0] = NOW + 11
        gone = await store.expire_monitors()
        assert [g["id"] for g in gone] == [a["id"]] and gone[0]["_expired_reason"] == "expired"
        store.touch_monitor(c["id"], last_activity_at=store.clock[0])
        store.clock[0] = NOW + const.MONITOR_THREAD_IDLE_SECONDS + 100
        gone = await store.expire_monitors()
        assert [g["id"] for g in gone] == [b["id"]] and gone[0]["_expired_reason"] == "idle"
        assert store.get_monitor(c["id"]) is not None  # channel monitors do not idle out

    async def test_remove_by_session(self, store):
        await mk(store)
        await mk(store, session="s2")
        assert await store.remove_by_session("s1") == 1
        assert store.count() == 1 and store.monitors_for_channel(CH)[0]["session_id"] == "s2"
