"""Digest records in the monitor store (issue #2103 stage 3c-i): schema, identity, caps, indexing."""

from __future__ import annotations

import json

import pytest

from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.relay.inbound.manager import InboundManager
from backend.relay.inbound.store import JsonSubscriptionStore, MonitorLimitError

NOW = 1_800_000_000.0
F = {"keywords": ["deploy"], "users": []}


def hourly(minutes=60):
    return sched.parse_schedule({"every_minutes": minutes}, owner_tz=None, now=NOW)


@pytest.fixture
def store(tmp_path):
    return JsonSubscriptionStore(tmp_path, "relay1", clock=lambda: NOW)


class TestRecord:
    async def test_digest_fields_and_live_defaults(self, store):
        rec, outcome = await store.create_monitor(
            "s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F, schedule=hourly())
        assert outcome == "created" and rec["mode"] == "digest"
        assert rec["next_run"] > NOW and rec["last_run"] is None and rec["run_failures"] == 0
        assert rec["slot_retries"] == 0 and rec["last_seen_ts"] == f"{NOW:.6f}"
        live, _ = await store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F)
        assert live["id"] != rec["id"] and "mode" not in live  # older shape: mode defaults to live

    async def test_persisted_and_reloaded(self, store, tmp_path):
        rec, _ = await store.create_monitor("s1", const.KIND_MONITOR_DMS, None, filters=F, schedule=hourly())
        again = JsonSubscriptionStore(tmp_path, "relay1", clock=lambda: NOW)
        again.load()
        assert again.get_monitor(rec["id"])["next_run"] == rec["next_run"]
        assert json.loads((tmp_path / "subscriptions.json").read_text())["version"] == 1

    async def test_identity_includes_schedule(self, store):
        a, _ = await store.create_monitor("s1", const.KIND_MONITOR_MENTIONS, None, filters=F, schedule=hourly(60))
        same, outcome = await store.create_monitor(
            "s1", const.KIND_MONITOR_MENTIONS, None, filters=F, schedule=hourly(60))
        assert outcome == "existing" and same["id"] == a["id"]
        other, outcome = await store.create_monitor(
            "s1", const.KIND_MONITOR_MENTIONS, None, filters=F, schedule=hourly(90))
        assert outcome == "created" and other["id"] != a["id"]
        live, outcome = await store.create_monitor("s1", const.KIND_MONITOR_MENTIONS, None, filters=F)
        assert outcome == "created"
        assert store.find_monitor("s1", const.KIND_MONITOR_MENTIONS, None, None, F) is live

    async def test_digests_are_not_in_the_live_indexes(self, store):
        await store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F, schedule=hourly())
        await store.create_monitor("s1", const.KIND_MONITOR_MENTIONS, None, filters=F, schedule=hourly())
        assert store.monitors_for_channel("C0CHAN0001") == [] and store.scope_monitors() == []
        live, _ = await store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F)
        assert store.monitors_for_channel("C0CHAN0001") == [live]

    async def test_caps(self, store):
        for i in range(const.MAX_SCHEDULED_PER_RELAY):
            await store.create_monitor(
                f"s{i}", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F, schedule=hourly())
        with pytest.raises(MonitorLimitError) as e:
            await store.create_monitor("s99", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F,
                                       schedule=hourly())
        assert e.value.scope == "scheduled"
        await store.create_monitor("s99", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F)  # live still fits
        assert store.count_scheduled() == const.MAX_SCHEDULED_PER_RELAY

    async def test_due_never_early_and_ordered(self, store):
        a, _ = await store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0001", filters=F, schedule=hourly(60))
        b, _ = await store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, "C0CHAN0002", filters=F, schedule=hourly(30))
        assert store.due_digests(NOW + 1000) == []
        due = store.due_digests(NOW + 7200)
        assert [r["id"] for r in due] == [b["id"], a["id"]]
        await store.set_paused(a["id"], True)
        assert [r["id"] for r in store.due_digests(NOW + 7200)] == [b["id"]]


class TestScopeLookupForTheConsentHook:
    def _manager(self, store, tz="Europe/Paris"):
        service = type("S", (), {})()
        service.store, service.owner_tz = store, tz
        mgr = object.__new__(InboundManager)
        mgr.services = {"relay1": service}
        return mgr

    async def test_identical_schedule_is_known_without_a_prompt(self, store):
        raw = {"daily": {"times": ["09:00"]}}
        parsed = sched.parse_schedule(raw, owner_tz="Europe/Paris", now=NOW)
        await store.create_monitor("s1", const.KIND_MONITOR_MENTIONS, None, filters={"keywords": [], "users": []},
                                   schedule=parsed)
        mgr = self._manager(store)
        assert mgr.scope_monitor_exists("s1", "relay1", "monitor_mentions", {"schedule": raw})
        assert not mgr.scope_monitor_exists("s1", "relay1", "monitor_mentions", {})  # live is another monitor
        assert not mgr.scope_monitor_exists(
            "s1", "relay1", "monitor_mentions", {"schedule": {"daily": {"times": ["10:00"]}}})
        assert not mgr.scope_monitor_exists("s1", "relay1", "monitor_mentions", {"schedule": {"bad": 1}})

    async def test_unknown_owner_timezone_prompts(self, store):
        mgr = self._manager(store, tz=None)
        assert not mgr.scope_monitor_exists(
            "s1", "relay1", "monitor_mentions", {"schedule": {"daily": {"times": ["09:00"]}}})


class TestOwnerTimezone:
    def test_users_info_tz_is_parsed(self):
        from backend.relay.slack_adapter import _parse_user

        assert _parse_user({"id": "U1", "tz": "Europe/Paris", "profile": {}}, "U1").tz == "Europe/Paris"
        assert _parse_user({"id": "U1", "profile": {}}, "U1").tz is None
        assert _parse_user({"id": "U1", "tz": "", "profile": {}}, "U1").tz is None

    async def test_resolve_owner_tz_is_cached_and_falls_back_to_none(self, tmp_path):
        from types import SimpleNamespace

        from backend.relay.adapter import UserInfo
        from backend.tests.relay_inbound_support import make_inbound

        env = make_inbound(tmp_path)
        calls = []

        async def user_info(uid):
            calls.append(uid)
            return UserInfo(id=uid, tz="Asia/Kolkata")

        env.adapter.user_info = user_info
        assert await env.service.resolve_owner_tz() == "Asia/Kolkata"
        assert await env.service.resolve_owner_tz() == "Asia/Kolkata" and len(calls) == 1
        env2 = make_inbound(tmp_path / "x")

        async def boom(uid):
            raise RuntimeError("slack down")

        env2.adapter.user_info = boom
        assert await env2.service.resolve_owner_tz() is None
        env2.manager.relays["relay1"] = SimpleNamespace(owner_user_id=None)
        assert await env2.service.resolve_owner_tz() is None

    async def test_audit_keeps_the_retry_field(self, tmp_path):
        from backend.relay.audit import RelayAuditLog

        audit = RelayAuditLog(tmp_path)
        await audit.record("relay1", event="monitor_digest", retry=2, code="rate_limited_rescheduled")
        row = json.loads((tmp_path / "relays" / "relay1" / "audit.jsonl").read_text().splitlines()[0])
        assert row["retry"] == 2
