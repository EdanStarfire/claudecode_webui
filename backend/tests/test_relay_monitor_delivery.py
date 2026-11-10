"""Cursor-on-confirm, failure handling, backfill and instruction failure replies
(issue #2103 stage 3b-i). Fake transport + clock + port; real JsonSubscriptionStore."""

from __future__ import annotations

import pytest

from backend.relay.inbound import constants as const
from backend.tests.relay_inbound_support import (
    BASE_TIME,
    SELF_DM,
    SESSION,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
    settle,
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


async def make_env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies[CHANNEL] = "read_only"
    e.t = await started(e)
    return e


@pytest.fixture
async def env(tmp_path):
    e = await make_env(tmp_path)
    yield e
    await e.service.stop()


async def deliver_one(env, text="deploy now", seq=1):
    rec = await monitor(env)
    ts = await emit(env, env.t, text, seq=seq)
    await fire_window(env)
    return rec, ts, env.port.calls[-1]["comm_id"]


class TestCursorOnConfirm:
    async def test_durable_cursor_advances_only_when_delivered(self, env):
        rec, ts, comm_id = await deliver_one(env)
        store = env.service.store
        await env.service.watcher.poll_once()  # still pending
        assert store.get_monitor(rec["id"])["last_seen_ts"] == f"{BASE_TIME:.6f}"
        assert env.service._high_water[rec["id"]] == ts  # in-memory high water moved at buffering
        env.port.set_status(comm_id, "delivered")
        await env.service.watcher.poll_once()
        got = store.get_monitor(rec["id"])
        assert got["last_seen_ts"] == ts and got["counters"]["delivered"] == 1
        assert got["last_delivered_at"] and got["failures"] == 0
        ev = audit_events(env, "monitor_delivered")
        assert len(ev) == 1 and ev[0]["envelope_id"] == comm_id and ev[0]["count"] == 1

    async def test_cursor_is_monotonic(self, env):
        rec, ts, comm_id = await deliver_one(env)
        env.service.store.advance_cursor(rec["id"], "1900000000.000000")
        env.port.set_status(comm_id, "delivered")
        await env.service.watcher.poll_once()
        assert env.service.store.get_monitor(rec["id"])["last_seen_ts"] == "1900000000.000000"

    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    async def test_failed_or_cancelled_leaves_cursor_and_counts(self, env, status):
        rec, ts, comm_id = await deliver_one(env)
        env.port.set_status(comm_id, status)
        await env.service.watcher.poll_once()
        got = env.service.store.get_monitor(rec["id"])
        assert got["last_seen_ts"] == f"{BASE_TIME:.6f}" and got["failures"] == 1 and got["last_error"]
        assert got["status"] == "active"  # no suspension on the first
        assert env.service._high_water[rec["id"]] == f"{BASE_TIME:.6f}"
        assert audit_events(env, "monitor_delivery_failed")

    async def test_three_consecutive_failures_suspend(self, env):
        rec = await monitor(env)
        for i in range(3):
            await emit(env, env.t, "deploy", seq=i + 1)
            await fire_window(env)
            env.port.set_status(env.port.calls[-1]["comm_id"], "failed")
            await env.service.watcher.poll_once()
        got = env.service.store.get_monitor(rec["id"])
        assert got["status"] == "suspended" and got["suspended_reason"] == const.SUSPEND_DELIVERY_FAILED

    async def test_success_resets_the_failure_streak(self, env):
        rec = await monitor(env)
        for i, status in enumerate(("failed", "failed", "delivered", "failed")):
            await emit(env, env.t, "deploy", seq=i + 1)
            await fire_window(env)
            env.port.set_status(env.port.calls[-1]["comm_id"], status)
            await env.service.watcher.poll_once()
        assert env.service.store.get_monitor(rec["id"])["status"] == "active"

    async def test_enqueue_failure_counts_and_keeps_cursor(self, env):
        rec = await monitor(env)
        env.port.fail_enqueue = True
        await emit(env, env.t, "deploy", seq=1)
        await fire_window(env)
        got = env.service.store.get_monitor(rec["id"])
        assert got["failures"] == 1 and got["last_seen_ts"] == f"{BASE_TIME:.6f}"
        assert env.service.watcher.entries == {}

    async def test_vanished_item_is_unknown_not_a_failure(self, env):
        rec, ts, comm_id = await deliver_one(env)
        del env.port.statuses[(SESSION, comm_id)]  # history cleared
        await env.service.watcher.poll_once()
        got = env.service.store.get_monitor(rec["id"])
        assert got["failures"] == 0 and got["last_error"] == "delivery_unknown"
        assert got["last_seen_ts"] == f"{BASE_TIME:.6f}"

    async def test_frozen_cursor_after_failure_until_a_backfill_recollects(self, env):
        rec = await monitor(env)
        ts1 = await emit(env, env.t, "deploy a", seq=1)
        await fire_window(env)
        env.port.set_status(env.port.calls[-1]["comm_id"], "failed")
        await env.service.watcher.poll_once()
        ts2 = await emit(env, env.t, "deploy b", seq=2)
        await fire_window(env)
        env.port.set_status(env.port.calls[-1]["comm_id"], "delivered")
        await env.service.watcher.poll_once()
        # the later delivery must not move the cursor past the failed envelope's messages
        assert env.service.store.get_monitor(rec["id"])["last_seen_ts"] == f"{BASE_TIME:.6f}"
        assert ts2 > ts1

    async def test_session_delete_drops_watches(self, env):
        await deliver_one(env)
        await env.service.on_session_deleted(SESSION)
        assert env.service.watcher.entries == {}


class TestBackfill:
    async def test_restart_with_a_lost_queue_redelivers(self, tmp_path):
        env = await make_env(tmp_path)
        rec, ts, comm_id = await deliver_one(env)
        await env.service.stop()  # enqueued but never confirmed; the queue is not recovered
        env2 = await make_env(tmp_path)
        try:
            assert env2.service._high_water[rec["id"]] == f"{BASE_TIME:.6f}"  # = durable cursor
            env2.adapter.histories[CHANNEL] = [history_msg(ts)]
            await env2.t.hello()
            await wait_for(lambda: env2.service.coalescer.buffered(SESSION) is not None)
            await fire_window(env2)
            assert len(env2.port.calls) == 1
            assert envelope_entries(env2.port.calls[0])[0]["text"] == "deploy it"
        finally:
            await env2.service.stop()

    async def test_reconnect_backfill_skips_what_is_already_buffered(self, env):
        rec = await monitor(env)
        ts = await emit(env, env.t, "deploy live", seq=1)
        env.adapter.histories[CHANNEL] = [history_msg(ts, "deploy live")]
        await env.t.hello()
        await settle(env, until=lambda: bool(env.adapter.history_calls))
        await fire_window(env)
        assert len(envelope_entries(env.port.calls[0])) == 1

    async def test_live_twin_of_a_backfilled_message_is_a_duplicate(self, env):
        rec = await monitor(env)
        ts = env.clock.ts(seq=1)
        env.adapter.histories[CHANNEL] = [history_msg(ts, "deploy it")]
        await env.service.backfill_monitors()
        await emit(env, env.t, "deploy it", seq=1)  # the same message arrives live afterwards
        await fire_window(env)
        assert len(env.port.calls) == 1 and len(envelope_entries(env.port.calls[0])) == 1

    async def test_backfill_recollects_after_a_failure(self, env):
        rec, ts, comm_id = await deliver_one(env)
        env.port.set_status(comm_id, "failed")
        await env.service.watcher.poll_once()
        env.adapter.histories[CHANNEL] = [history_msg(ts)]
        await env.service.backfill_monitors()
        await fire_window(env)
        assert len(env.port.calls) == 2  # cursor never moved: the same message is re-fetched

    async def test_thread_monitor_backfill_uses_the_thread_and_skips_the_root(self, env):
        rec = await monitor(env, kind=const.KIND_MONITOR_THREAD, thread_ts=ROOT, keywords=())
        reply = env.clock.ts(seq=1)
        env.adapter.threads[ROOT] = [history_msg(ROOT, "root"), history_msg(reply, "a reply")]
        await env.service.backfill_monitors()
        await fire_window(env)
        entries = envelope_entries(env.port.calls[0])
        assert [e["text"] for e in entries] == ["a reply"] and entries[0]["thread_ts"] == ROOT
        assert env.adapter.thread_calls[0]["oldest"] == f"{BASE_TIME:.6f}"

    async def test_backfill_applies_the_echo_rule_and_subtype_filter(self, env):
        await monitor(env)
        ok = env.clock.ts(seq=1)
        env.adapter.histories[CHANNEL] = [
            history_msg(env.clock.ts(seq=2), "deploy echo", user="U0OWNER001",
                        metadata={"event_type": "cc_webui_agent_message"}),
            history_msg(env.clock.ts(seq=3), "deploy join", subtype="channel_join"),
            history_msg(ok, "deploy fine"),
        ]
        await env.service.backfill_monitors()
        await fire_window(env)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == ["deploy fine"]

    async def test_old_cursor_is_capped_at_24h_with_older_omitted(self, env):
        rec = await monitor(env)
        env.clock.advance(3 * 24 * 3600)
        env.adapter.histories[CHANNEL] = [history_msg(env.clock.ts(seq=1), "deploy recent")]
        await env.service.backfill_monitors()
        await fire_window(env)
        oldest = float(env.adapter.history_calls[-1]["oldest"])
        assert oldest == pytest.approx(env.clock.t - 24 * 3600 - 0.0, abs=100)  # the 24 h floor
        assert "older content omitted (backfill limit)" in env.port.calls[0]["dispatch_content"]
        assert audit_events(env, "monitor_backfill")[0]["code"] == "backfill_truncated"

    async def test_paused_and_suspended_monitors_are_not_backfilled(self, env):
        rec = await monitor(env)
        await env.service.store.set_paused(rec["id"], True)
        env.adapter.histories[CHANNEL] = [history_msg(env.clock.ts(seq=1))]
        await env.service.backfill_monitors()
        assert env.adapter.history_calls == []

    async def test_backfill_error_is_recorded_not_raised(self, env):
        from backend.relay.adapter import RelayError
        rec = await monitor(env)

        async def boom(*a, **k):
            raise RelayError("channel_not_found", "conversations.history")

        env.adapter.read_history = boom
        await env.service.backfill_monitors()
        assert env.service.store.get_monitor(rec["id"])["last_error"] == "channel_not_found"


class TestInstructionFailureReplies:
    async def instruct(self, env):
        ts = env.clock.ts(seq=1)
        await env.t.emit(envelope(message_payload("!my-agent run it", ts), "EI"))
        await settle(env)
        return ts, env.sink.calls[0]["comm_id"]

    async def test_failed_gets_one_marked_reply_in_the_thread(self, env):
        ts, comm_id = await self.instruct(env)
        env.port.set_status(comm_id, "pending")
        await env.service.watcher.poll_once()
        assert env.adapter.posts == []
        env.port.set_status(comm_id, "failed")
        await env.service.watcher.poll_once()
        await env.service.watcher.poll_once()
        assert len(env.adapter.posts) == 1
        post = env.adapter.posts[0]
        assert post["thread_ts"] == ts and post["channel"] == SELF_DM
        assert "could not accept" in post["text"] and "[cc-agent]" in post["text"]
        rows = audit_events(env, "inbound_instruction")
        assert rows[-1]["outcome"] == "delivery_failed" and rows[-1]["message_ts"] == ts
        assert env.adapter.reactions[0][0] == "add"  # the received reaction is left in place
        assert not any(r[0] == "remove" for r in env.adapter.reactions)

    async def test_cancelled_gets_one_reply(self, env):
        ts, comm_id = await self.instruct(env)
        env.port.set_status(comm_id, "cancelled")
        await env.service.watcher.poll_once()
        assert len(env.adapter.posts) == 1 and "cancelled" in env.adapter.posts[0]["text"]

    async def test_delivered_gets_none(self, env):
        ts, comm_id = await self.instruct(env)
        env.port.set_status(comm_id, "delivered")
        await env.service.watcher.poll_once()
        assert env.adapter.posts == []

    async def test_once_guard_even_if_the_same_comm_is_watched_again(self, env):
        ts, comm_id = await self.instruct(env)
        env.port.set_status(comm_id, "failed")
        await env.service.watcher.poll_once()
        from backend.relay.inbound.watcher import WatchEntry
        env.service.watcher.watch(WatchEntry("instruction", SESSION, comm_id, env.clock.now(), info={
            "channel": SELF_DM, "thread_ts": ts, "message_ts": ts, "handle": "my-agent"}))
        await env.service.watcher.poll_once()
        assert len(env.adapter.posts) == 1

    async def test_no_reply_after_a_restart(self, tmp_path):
        env = await make_env(tmp_path)
        ts, comm_id = await TestInstructionFailureReplies().instruct(env)
        await env.service.stop()
        env2 = await make_env(tmp_path)
        try:
            env2.port.set_status(comm_id, "failed")
            await env2.service.watcher.poll_once()
            assert env2.service.watcher.entries == {} and env2.adapter.posts == []
        finally:
            await env2.service.stop()

    async def test_watcher_ignores_a_monitor_comm_for_replies(self, env):
        rec, ts, comm_id = await deliver_one(env)
        env.port.set_status(comm_id, "cancelled")
        await env.service.watcher.poll_once()
        assert env.adapter.posts == []  # monitored content never produces a Slack post
