"""Monitor lifecycle: dispose / start / delete / reparent / housekeeping (issue #2103 3b-i)."""

from __future__ import annotations

import pytest

from backend.relay.inbound import constants as const
from backend.tests.relay_inbound_support import (
    SESSION,
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
    wait_for,
)

ROOT = "1799999000.000100"


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies[CHANNEL] = "auto"
    e.t = await started(e)
    yield e
    await e.service.stop()


def got(env, rec):
    return env.service.store.get_monitor(rec["id"])


class TestDisposeAndStart:
    async def test_dispose_suspends_and_start_restores_only_disposed(self, env):
        a = await monitor(env)
        b = await monitor(env, keywords=("other",))
        c = await monitor(env, keywords=("third",))
        await env.service._suspend(got(env, b), const.SUSPEND_FLOOD)
        await env.service.store.set_paused(c["id"], True)
        thread = await env.service.store.create_thread(SESSION, "D0SELF0001", "1.1", "1.1")
        await env.service.on_session_disposed(SESSION)
        assert got(env, a)["suspended_reason"] == const.SUSPEND_DISPOSED
        assert got(env, b)["suspended_reason"] == const.SUSPEND_FLOOD  # not overwritten
        assert env.service.store.find_thread("D0SELF0001", "1.1")["status"] == "suspended"
        await env.service.on_session_started(SESSION)
        assert got(env, a)["status"] == "active"
        assert got(env, b)["suspended_reason"] == const.SUSPEND_FLOOD  # a start restores only disposed ones
        assert got(env, c)["paused"] is True and got(env, c)["status"] == "active"  # owner pause survives
        assert env.service.store.find_thread("D0SELF0001", "1.1")["status"] == "active"
        assert thread["id"]

    async def test_events_while_disposed_are_ignored_then_backfilled_on_start(self, env):
        rec = await monitor(env)
        await env.service.on_session_disposed(SESSION)
        await emit(env, env.t, "deploy while disposed", seq=1)
        assert env.service.coalescer.buffered(SESSION) is None
        env.adapter.histories[CHANNEL] = [history_msg(env.clock.ts(seq=1), "deploy while disposed")]
        await env.service.on_session_started(SESSION)
        await wait_for(lambda: env.service.coalescer.buffered(SESSION) is not None)
        await fire_window(env)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == ["deploy while disposed"]
        assert got(env, rec)["status"] == "active"

    async def test_dispose_drops_buffer_and_watches(self, env):
        await monitor(env)
        await emit(env, env.t, "deploy", seq=1)
        await env.service.on_session_disposed(SESSION)
        assert env.service.coalescer.buffered(SESSION) is None
        await fire_window(env)
        assert env.port.calls == []

    async def test_start_re_suspends_when_the_channel_was_blocked_meanwhile(self, env):
        rec = await monitor(env)
        await env.service.on_session_disposed(SESSION)
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.on_session_started(SESSION)
        assert got(env, rec)["suspended_reason"] == const.SUSPEND_CHANNEL_BLOCKED


class TestDelete:
    async def test_delete_drops_monitors_threads_buffers_watches_and_inflight(self, env):
        await monitor(env)
        await env.service.store.create_thread(SESSION, "D0SELF0001", "1.1", "1.1")
        await emit(env, env.t, "deploy", seq=1)
        await fire_window(env)
        assert env.service.watcher.entries
        await emit(env, env.t, "deploy again", seq=2)
        await env.service.on_session_deleted(SESSION)
        assert env.service.store.count() == 0
        assert env.service.store.find_thread("D0SELF0001", "1.1") is None
        assert env.service.watcher.entries == {} and env.service.coalescer.buffered(SESSION) is None
        assert audit_events(env, "monitor_removed")[-1]["source"] == "session"

    async def test_other_sessions_keep_their_monitors(self, env):
        env.sessions.add("s2")
        await monitor(env)
        keep = await monitor(env, session="s2")
        await env.service.on_session_deleted(SESSION)
        assert got(env, keep) is not None and env.service.store.count() == 1


class TestReparentAndOverseers:
    async def test_content_is_addressed_to_the_monitoring_session_only(self, env):
        env.sessions.infos[SESSION].parent_overseer_id = "overseer-1"
        await monitor(env)
        await emit(env, env.t, "deploy", seq=1)
        await fire_window(env)
        assert [c["session_id"] for c in env.port.calls] == [SESSION]
        assert "overseer-1" not in str(env.port.calls[0]["metadata"])

    async def test_monitor_record_carries_no_hierarchy_so_reparenting_changes_nothing(self, env):
        rec = await monitor(env)
        assert not any("parent" in k or "overseer" in k for k in got(env, rec))


class TestHousekeeping:
    async def test_expired_monitors_are_removed_and_audited(self, env):
        rec = await monitor(env, expires_at=env.clock.t + 30)
        env.clock.advance(60)
        await env.service.run_housekeeping()
        assert got(env, rec) is None
        ev = audit_events(env, "monitor_expired")
        assert ev[0]["code"] == "expired" and ev[0]["monitor_id"] == rec["id"]

    async def test_idle_thread_monitor_expires_after_14_days(self, env):
        rec = await monitor(env, kind=const.KIND_MONITOR_THREAD, thread_ts=ROOT, keywords=())
        env.clock.advance(const.MONITOR_THREAD_IDLE_SECONDS + 10)
        await env.service.run_housekeeping()
        assert got(env, rec) is None and audit_events(env, "monitor_expired")[0]["code"] == "idle"


class TestManagement:
    async def test_remove_only_own_monitor(self, env):
        env.sessions.add("s2")
        mine = await monitor(env)
        assert not await env.service.remove_monitor("s2", mine["id"])
        assert await env.service.remove_monitor(SESSION, mine["id"])
        assert got(env, mine) is None
        assert audit_events(env, "monitor_removed")[0]["source"] == "agent"

    async def test_monitor_audit_rows_name_the_session(self, env):
        rec = await monitor(env)
        await env.service.remove_monitor(SESSION, rec["id"])
        row = audit_events(env, "monitor_removed")[0]
        assert row["session_name"] == f"Session {SESSION}" and row["handle"] == f"handle-{SESSION}"
        assert row["session_id"] == SESSION

    async def test_audit_never_holds_message_text_or_keywords(self, env):
        await monitor(env, keywords=("super-secret-keyword",))
        await emit(env, env.t, "super-secret-keyword message body", seq=1)
        await fire_window(env)
        env.port.set_status(env.port.calls[0]["comm_id"], "delivered")
        await env.service.watcher.poll_once()
        raw = (env.root / "audit.jsonl").read_text()
        assert "super-secret-keyword" not in raw and "message body" not in raw
        created = audit_events(env, "monitor_created")[0]
        assert created["count"] == 1 and len(created["query_sha256"]) == 64
