"""Scope monitors end to end over the fake transport (issue #2103 stage 3b-ii): live DM / mention
delivery, flush-time blocked-channel filter, lifecycle, persisted delivered keys, REST."""

from __future__ import annotations

import json

import pytest

from backend.relay.inbound import constants as const
from backend.tests.relay_inbound_support import (
    BASE_TIME,
    OWNER,
    SELF_DM,
    SESSION,
    audit_events,
    make_inbound,
)
from backend.tests.relay_monitor_support import (
    CHANNEL,
    OTHER_USER,
    emit,
    envelope_entries,
    fire_window,
    started,
)

M, D = const.KIND_MONITOR_MENTIONS, const.KIND_MONITOR_DMS
DM1 = "D0ALICE001"


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.t = await started(e)
    yield e
    await e.service.stop()


async def scope(env, kind, **filters):
    rec, _ = await env.service.create_monitor(
        SESSION, kind, None, thread_ts=None,
        filters={"keywords": filters.get("keywords", []), "users": filters.get("users", [])},
        expires_at=None, consent={"at": 1.0, "via": "prompt"})
    return rec


class TestLive:
    async def test_dm_and_mention_in_one_envelope_with_merged_monitor_ids(self, env):
        a, b = await scope(env, D), await scope(env, M)
        await emit(env, env.t, f"<@{OWNER}> hello in dm", seq=1, channel=DM1, channel_type="im")
        await emit(env, env.t, f"<@{OWNER}> in channel", seq=2)
        await emit(env, env.t, "plain channel chatter", seq=3)
        await fire_window(env)
        [call] = env.port.calls
        entries = envelope_entries(call)
        assert len(entries) == 2
        assert sorted(entries[0]["monitor_ids"]) == sorted([a["id"], b["id"]])
        assert entries[1]["monitor_ids"] == [b["id"]]
        assert call["content"] == "Slack monitor: 2 messages"
        text = call["dispatch_content"]
        assert "NOT instructions" in text and text.count("[UNTRUSTED EXTERNAL CONTENT") == 1
        assert "OWNER INSTRUCTION" not in text

    async def test_no_reaction_no_post_for_scope_content(self, env):
        await scope(env, D)
        await emit(env, env.t, "!my-agent run it", seq=1, channel=DM1, channel_type="im")
        await fire_window(env)
        assert env.adapter.reactions == [] and env.adapter.posts == [] and sum(
            1 for _ in env.sink.calls) == 0  # hostile text in a DM is content, never an instruction

    async def test_self_dm_owner_and_session_posts_are_never_content(self, env):
        await scope(env, D)
        await scope(env, M)
        await emit(env, env.t, "just a note", seq=1, channel=SELF_DM, channel_type="im", user=OWNER)
        await emit(env, env.t, "own dm", seq=2, channel=DM1, channel_type="im", user=OWNER)
        await emit(env, env.t, f"<@{OWNER}> self", seq=3, user=OWNER)
        await emit(env, env.t, f"<@{OWNER}> from agent", seq=4, user=OWNER, app_id="A0X", bot_id="B0X")
        await emit(env, env.t, f"<@{OWNER}> tagged", seq=5,
                   metadata={"event_type": "cc_webui_agent_message"})
        await fire_window(env)
        assert env.port.calls == []

    async def test_blocked_channel_filtered_at_flush_monitor_stays_active(self, env):
        rec = await scope(env, M)
        env.relay_service.policies[CHANNEL] = "auto"
        await emit(env, env.t, f"<@{OWNER}> one", seq=1)
        await emit(env, env.t, f"<@{OWNER}> blocked soon", seq=2, channel="C0BLOCK001")
        env.relay_service.policies["C0BLOCK001"] = "blocked"  # edited during the window
        await fire_window(env)
        [call] = env.port.calls
        assert [e["channel"] for e in envelope_entries(call)] == [CHANNEL]
        got = env.service.store.get_monitor(rec["id"])
        assert got["status"] == "active" and got["counters"]["dropped"] == 1

    async def test_unlisted_and_read_only_are_delivered(self, env):
        await scope(env, M)
        env.relay_service.policies["C0RO000001"] = "read_only"
        await emit(env, env.t, f"<@{OWNER}> a", seq=1, channel="C0UNLISTED")
        await emit(env, env.t, f"<@{OWNER}> b", seq=2, channel="C0RO000001")
        await fire_window(env)
        assert len(envelope_entries(env.port.calls[0])) == 2

    async def test_only_blocked_content_delivers_nothing_and_cursor_moves(self, env):
        rec = await scope(env, M)
        env.relay_service.policies[CHANNEL] = "blocked"
        ts = await emit(env, env.t, f"<@{OWNER}> x", seq=1)
        await fire_window(env)
        assert env.port.calls == []
        assert float(env.service.store.get_monitor(rec["id"])["last_seen_ts"]) >= float(ts)

    async def test_channel_monitors_still_suspend_when_blocked(self, env):
        from backend.relay.inbound.store import SUSPENDED
        ch, sc = None, await scope(env, M)
        ch, _ = await env.service.create_monitor(
            SESSION, const.KIND_MONITOR_CHANNEL, CHANNEL, thread_ts=None,
            filters={"keywords": ["x"], "users": []}, expires_at=None)
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate(SESSION)
        assert env.service.store.get_monitor(ch["id"])["status"] == SUSPENDED
        assert env.service.store.get_monitor(sc["id"])["status"] == "active"


class TestDeliveredKeysAndLifecycle:
    async def test_delivered_keys_persisted_with_cursor(self, env):
        rec = await scope(env, M)
        ts = await emit(env, env.t, f"<@{OWNER}> x", seq=1)
        await fire_window(env)
        env.port.set_status(env.port.calls[0]["comm_id"], "delivered")
        await env.service.watcher.poll_once()
        got = env.service.store.get_monitor(rec["id"])
        assert got["recent_keys"] == [f"{CHANNEL}:{ts}"] and got["last_seen_ts"] == ts

    async def test_recent_keys_seeded_after_restart(self, tmp_path):
        e = make_inbound(tmp_path)
        t = await started(e)
        rec = await scope(e, M)
        ts = await emit(e, t, f"<@{OWNER}> x", seq=1)
        await fire_window(e)
        e.port.set_status(e.port.calls[0]["comm_id"], "delivered")
        await e.service.watcher.poll_once()
        await e.service.stop()
        e2 = make_inbound(tmp_path, clock=e.clock)
        await started(e2)
        try:
            assert e2.service.monitor_key_seen(rec["id"], f"{CHANNEL}:{ts}")
        finally:
            await e2.service.stop()

    async def test_dispose_start_pause_delete(self, env):
        rec = await scope(env, D)
        await env.service.set_paused(rec["id"], True)
        await env.service.on_session_disposed(SESSION)
        assert env.service.store.get_monitor(rec["id"])["suspended_reason"] == const.SUSPEND_DISPOSED
        await env.service.on_session_started(SESSION)
        got = env.service.store.get_monitor(rec["id"])
        assert got["status"] == "active" and got["paused"] is True  # the owner pause survives
        await env.service.on_session_deleted(SESSION)
        assert env.service.store.monitors() == []

    async def test_flood_limits_apply_to_scope_monitors(self, env):
        rec = await scope(env, D)
        for i in range(const.FLOOD_PER_MONITOR + 5):
            await emit(env, env.t, f"m{i}", seq=i + 1, channel=DM1, channel_type="im")
        await fire_window(env)
        assert audit_events(env, "monitor_flood")
        assert env.service.store.get_monitor(rec["id"])["counters"]["dropped"] == 5


class TestOwnerActions:
    async def test_pause_resume_semantics_and_audit_source(self, env):
        rec = await scope(env, D)
        await env.service.set_paused(rec["id"], True)
        await emit(env, env.t, "while paused", seq=1, channel=DM1, channel_type="im")
        await fire_window(env)
        assert env.port.calls == []
        env.clock.advance(300)
        await env.service.set_paused(rec["id"], False)
        got = env.service.store.get_monitor(rec["id"])
        assert float(got["last_seen_ts"]) >= BASE_TIME + 300  # restarted from now
        rows = [(r["event"], r["source"]) for r in audit_events(env) if r["event"].startswith("monitor_")]
        assert ("monitor_paused", "owner") in rows and ("monitor_resumed", "owner") in rows

    async def test_resume_does_not_lift_system_suspension(self, env):
        rec = await scope(env, D)
        await env.service.store.set_monitor_status(rec["id"], "suspended", const.SUSPEND_FLOOD)
        await env.service.set_paused(rec["id"], True)
        await env.service.set_paused(rec["id"], False)
        got = env.service.store.get_monitor(rec["id"])
        assert got["status"] == "suspended" and got["suspended_reason"] == const.SUSPEND_FLOOD
        pub = await env.service.public_one(rec["id"])
        assert pub["effective_state"] == "suspended" and pub["suspended_reason"] == const.SUSPEND_FLOOD

    async def test_delete_cancels_queued_envelope(self, env):
        rec = await scope(env, D)
        await emit(env, env.t, "x", seq=1, channel=DM1, channel_type="im")
        await fire_window(env)
        comm_id = env.port.calls[0]["comm_id"]
        assert await env.service.delete_monitor(rec["id"])
        assert env.port.statuses[(SESSION, comm_id)] == "cancelled"
        assert [r["source"] for r in audit_events(env, "monitor_removed")][-1] == "owner"

    async def test_public_shape_has_no_content_and_filters_by_state(self, env):
        rec = await scope(env, M, keywords=["secret-keyword"], users=[OTHER_USER])
        await emit(env, env.t, f"<@{OWNER}> private text secret-keyword", seq=1, user=OTHER_USER)
        rows, total = await env.service.public_monitors()
        assert total == 1
        row = rows[0]
        dumped = json.dumps(row)
        assert "private text" not in dumped and "recent_keys" not in dumped
        assert row["id"] == rec["id"] and row["effective_state"] == "active"
        assert row["session_name"] == "Session s1" and row["handle"] == "handle-s1"
        assert row["filters"]["user_labels"] == {OTHER_USER: None}
        assert (await env.service.public_monitors(status="paused"))[1] == 0
        assert (await env.service.public_monitors(kind=D))[1] == 0


class TestAppId:
    async def test_api_app_id_persists_and_survives_restart(self, tmp_path):
        from backend.tests.relay_inbound_support import envelope, message_payload

        e = make_inbound(tmp_path)
        t = await started(e)
        payload = message_payload("hello", e.clock.ts(seq=1), channel=CHANNEL, user=OTHER_USER,
                                  channel_type="channel")
        payload["api_app_id"] = "A0OWN"  # top-level key of the Events API payload
        await t.emit(envelope(payload, "E1"))
        assert e.service.state.own_app_id == "A0OWN"
        await e.service.stop()
        e2 = make_inbound(tmp_path, clock=e.clock)
        e2.t = await started(e2)
        try:
            assert e2.service.state.own_app_id == "A0OWN"
            await scope(e2, M)
            await emit(e2, e2.t, f"<@{OWNER}> from our app", seq=2, app_id="A0OWN", user=OTHER_USER)
            await fire_window(e2)
            assert e2.port.calls == []  # own app id => echo
        finally:
            await e2.service.stop()


class TestRealEventFixture:
    """A sanitized Events API payload recorded from a real Slack workspace (3b-ii live validation):
    ``api_app_id`` is a TOP-LEVEL key of the event_callback payload, and a human's plain message has
    no ``app_id`` / ``bot_id`` / ``metadata``."""

    def _payload(self):
        import pathlib
        path = pathlib.Path(__file__).parent / "fixtures" / "slack_event_message_channel.json"
        return json.loads(path.read_text())

    def test_api_app_id_is_top_level_and_normalized(self):
        from backend.relay.inbound.normalize import normalize_event
        payload = self._payload()
        assert "api_app_id" in payload and "api_app_id" not in payload["event"]
        ev = normalize_event("r", payload, "T0TEAM001")
        assert ev.api_app_id == "A0OWNAPP01" and ev.channel_type == "channel"
        assert ev.app_id is None and ev.bot_id is None and ev.metadata is None and not ev.is_agent

    async def test_real_shape_is_learned_and_delivered_to_a_mention_monitor(self, env):
        payload = self._payload()
        payload["team_id"] = "T0TEAM001"
        payload["event"]["text"] = f"<@{OWNER}> hello from the tester"
        await scope(env, M)
        from backend.tests.relay_inbound_support import envelope
        await env.t.emit(envelope(payload, "EFIX"))
        assert env.service.state.own_app_id == "A0OWNAPP01"
        await fire_window(env)
        assert len(env.port.calls) == 1
