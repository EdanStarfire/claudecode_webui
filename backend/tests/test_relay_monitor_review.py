"""Regression tests for the 3b-i review findings (issue #2103): comm-id retries, cursor floor after
failures, lost buffers, cancel-on-block, per-monitor dedupe, flood accounting, one flush at a time."""

from __future__ import annotations

import asyncio

import pytest

from backend.relay.events import RelayEvent
from backend.relay.inbound import constants as const
from backend.relay.inbound.manager import InboundManager
from backend.tests.relay_inbound_support import (
    BASE_TIME,
    OWNER,
    SELF_DM,
    SESSION,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
)
from backend.tests.relay_monitor_support import (
    CHANNEL,
    emit,
    envelope_entries,
    fire_window,
    history_msg,
    monitor,
    started,
)


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies[CHANNEL] = "read_only"
    e.t = await started(e)
    yield e
    await e.service.stop()


def got(env, rec):
    return env.service.store.get_monitor(rec["id"])


class TestRetryAndCursorFloor:
    async def test_retry_of_a_failed_envelope_is_a_new_comm_not_swallowed(self, env):
        rec = await monitor(env)
        ts = await emit(env, env.t, "deploy now", seq=1)
        await fire_window(env)
        first = env.port.calls[0]["comm_id"]
        env.port.set_status(first, "failed")
        await env.service.watcher.poll_once()
        env.adapter.histories[CHANNEL] = [history_msg(ts, "deploy now")]
        await env.service.backfill_monitors()
        await fire_window(env)
        assert len(env.port.calls) == 2 and env.port.calls[1]["comm_id"] != first
        env.port.set_status(env.port.calls[1]["comm_id"], "delivered")
        await env.service.watcher.poll_once()
        got_rec = got(env, rec)
        assert got_rec["last_seen_ts"] == ts and got_rec["failures"] == 0 and got_rec["status"] == "active"

    async def test_identical_pending_envelope_is_the_same_comm(self, env):
        await monitor(env)
        await emit(env, env.t, "deploy now", seq=1)
        await fire_window(env)
        first = env.port.calls[0]["comm_id"]
        assert env.port.item_status(SESSION, first) == "pending"
        from backend.relay.inbound.service import monitor_comm_id
        assert monitor_comm_id("relay1", SESSION, ["a:1"]) == monitor_comm_id("relay1", SESSION, ["a:1"])
        assert monitor_comm_id("relay1", SESSION, ["a:1"], 1) != monitor_comm_id("relay1", SESSION, ["a:1"])

    async def test_backfill_after_failure_starts_at_the_durable_cursor_even_after_new_live_traffic(self, env):
        rec = await monitor(env)
        ts1 = await emit(env, env.t, "deploy one", seq=1)
        await fire_window(env)
        env.port.set_status(env.port.calls[-1]["comm_id"], "failed")
        await env.service.watcher.poll_once()
        await emit(env, env.t, "deploy two", seq=2)  # live traffic raises the in-memory high water
        env.adapter.histories[CHANNEL] = [history_msg(ts1, "deploy one")]
        await env.service.backfill_monitors()
        assert env.adapter.history_calls[-1]["oldest"] == f"{BASE_TIME:.6f}"
        assert env.service._high_water[rec["id"]] >= ts1

    async def test_a_lost_buffer_resets_high_water_and_freezes_the_cursor(self, env):
        rec = await monitor(env)
        await emit(env, env.t, "deploy", seq=1)
        assert env.service._high_water[rec["id"]] > f"{BASE_TIME:.6f}"
        env.service.coalescer.discard_session(SESSION)
        await asyncio.sleep(0.05)
        assert env.service._high_water[rec["id"]] == f"{BASE_TIME:.6f}" and rec["id"] in env.service._hold

    async def test_an_all_flooded_window_still_moves_the_cursor(self, env):
        env.service.coalescer._monitor_limits._args = (0, 600.0, env.clock.mono)
        rec = await monitor(env)
        last = await emit(env, env.t, "deploy a", seq=1)
        last = await emit(env, env.t, "deploy b", seq=2)
        await fire_window(env)
        assert env.port.calls == []
        assert got(env, rec)["last_seen_ts"] == last and got(env, rec)["counters"]["dropped"] == 2


class TestFloodAccounting:
    async def test_relay_wide_budget_never_suspends_a_monitor(self, env):
        env.service.coalescer._relay_limit._limit = 0
        rec = await monitor(env)
        for w in range(4):
            await emit(env, env.t, "deploy", seq=w + 1)
            await fire_window(env)
            env.clock.advance(601)
        assert got(env, rec)["status"] == "active"
        assert audit_events(env, "monitor_flood")  # the drop is still audited

    async def test_per_monitor_flood_still_suspends(self, env):
        env.service.coalescer._monitor_limits._args = (0, 600.0, env.clock.mono)
        rec = await monitor(env)
        for w in range(3):
            await emit(env, env.t, "deploy", seq=w + 1)
            await fire_window(env)
            env.clock.advance(601)
        assert got(env, rec)["suspended_reason"] == const.SUSPEND_FLOOD


class TestPerMonitorDedupe:
    async def test_backfill_of_one_monitor_does_not_hide_the_message_from_another(self, env):
        env.sessions.add("s2")
        a = await monitor(env, session=SESSION)
        b = await monitor(env, session="s2")
        ts = env.clock.ts(seq=1)
        env.adapter.histories[CHANNEL] = [history_msg(ts, "deploy it")]
        await env.service.backfill_monitors(SESSION)  # only monitor A is backfilled
        await emit(env, env.t, "deploy it", seq=1)  # the live event reaches B, and not A a second time
        await fire_window(env)
        by_session = {c["session_id"]: envelope_entries(c) for c in env.port.calls}
        assert len(by_session[SESSION]) == 1 and len(by_session["s2"]) == 1
        assert a["id"] != b["id"]


class TestCancelOnBlock:
    async def deliver(self, env, *keywords):
        recs = [await monitor(env, keywords=(k,)) for k in keywords]
        await emit(env, env.t, " ".join(keywords) + " now", seq=1)
        await fire_window(env)
        return recs, env.port.calls[-1]["comm_id"]

    async def test_blocking_cancels_the_queued_envelope_without_a_failure(self, env):
        (rec,), comm_id = await self.deliver(env, "deploy")
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate(SESSION)
        assert env.port.item_status(SESSION, comm_id) == "cancelled"
        await env.service.watcher.poll_once()
        got_rec = got(env, rec)
        assert got_rec["failures"] == 0 and got_rec["suspended_reason"] == const.SUSPEND_CHANNEL_BLOCKED
        assert not audit_events(env, "monitor_delivery_failed")
        await env.service.reevaluate(SESSION)

    async def test_unmonitor_cancels_the_queued_envelope(self, env):
        (rec,), comm_id = await self.deliver(env, "deploy")
        assert await env.service.remove_monitor(SESSION, rec["id"])
        assert env.port.item_status(SESSION, comm_id) == "cancelled"

    async def test_other_monitors_of_the_cancelled_envelope_recollect(self, env):
        other = "C0CHAN0002"
        env.relay_service.policies[other] = "read_only"
        m1 = await monitor(env, channel=CHANNEL, keywords=("deploy",))
        await monitor(env, channel=other, keywords=("deploy",))
        ts1 = await emit(env, env.t, "deploy a", seq=1)
        await emit(env, env.t, "deploy b", seq=2, channel=other)
        await fire_window(env)
        env.relay_service.policies[other] = "blocked"
        env.adapter.histories[CHANNEL] = [history_msg(ts1, "deploy a")]
        await env.service.reevaluate(SESSION)
        await env.service.watcher.poll_once()
        await fire_window(env)
        assert len(env.port.calls) == 2
        assert [e["text"] for e in envelope_entries(env.port.calls[1])] == ["deploy a"]  # not the blocked one
        assert got(env, m1)["failures"] == 0

    async def test_flood_suspension_does_not_cancel(self, env):
        (rec,), comm_id = await self.deliver(env, "deploy")
        await env.service._suspend(got(env, rec), const.SUSPEND_FLOOD)
        assert env.port.item_status(SESSION, comm_id) == "pending"

    async def test_a_monitor_removed_while_the_envelope_is_being_enqueued_is_undone(self, env):
        rec = await monitor(env)
        await emit(env, env.t, "deploy now", seq=1)

        async def remove_midway():
            await env.service.store.remove_monitor(rec["id"])

        env.port.on_enqueue = remove_midway
        await fire_window(env)
        assert env.port.item_status(SESSION, env.port.calls[0]["comm_id"]) == "cancelled"


class TestFlushDiscipline:
    async def test_stuck_session_never_accumulates_more_than_the_pending_cap(self, env):
        await monitor(env)
        env.port.pending_monitor = const.MAX_PENDING_ENVELOPES
        await emit(env, env.t, "deploy", seq=1)
        await env.window.fire()
        for _ in range(40):  # far beyond the 10 minute postponement bound
            await env.window.fire()
        await asyncio.sleep(0.05)
        assert env.port.calls == []
        env.port.pending_monitor = False
        await fire_window(env)
        assert len(env.port.calls) == 1

    async def test_one_flush_per_session_at_a_time(self, env):
        await monitor(env)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def slow():
            entered.set()
            await release.wait()

        env.port.on_enqueue = slow
        await emit(env, env.t, "deploy a", seq=1)
        await env.window.fire()
        await asyncio.wait_for(entered.wait(), 3)
        env.port.on_enqueue = None
        await emit(env, env.t, "deploy b", seq=2)
        await env.window.fire()  # the second window elapses while the first flush is still running
        await env.window.fire()
        await asyncio.sleep(0.05)
        assert len(env.port.calls) == 1
        release.set()
        await fire_window(env)
        assert len(env.port.calls) == 2

    async def test_stop_cancels_a_flush_in_progress(self, env):
        await monitor(env)
        entered = asyncio.Event()

        async def hang():
            entered.set()
            await asyncio.sleep(3600)

        env.port.on_enqueue = hang
        await emit(env, env.t, "deploy", seq=1)
        await env.window.fire()
        await asyncio.wait_for(entered.wait(), 3)
        await env.service.stop()
        assert env.service.coalescer._flushing == {}


class TestRingAndPersistence:
    async def test_unmatched_traffic_schedules_no_state_write(self, env):
        await monitor(env)
        env.service.state._flush_task = None
        for i in range(1, 20):
            await emit(env, env.t, "nothing", seq=i, channel="C0OTHER001")
        assert env.service.state._flush_task is None

    async def test_owner_self_dm_message_that_is_not_an_instruction_is_remembered(self, env):
        ts = env.clock.ts(seq=1)
        await env.t.emit(envelope(message_payload("just a note to self", ts, user=OWNER), "E1"))
        assert env.service.state.seen(SELF_DM, ts)  # a trust-rule candidate: remembered, like in 3a

    async def test_other_users_self_dm_style_events_are_not_remembered(self, env):
        ts = env.clock.ts(seq=2)
        await env.service.accept_event(RelayEvent("relay1", "C0NOISE001", ts, user="U0X", text="hi",
                                                  channel_type="channel"))
        assert not env.service.state.seen("C0NOISE001", ts)


class TestMiscReview:
    def test_owner_app_posts_are_echo_even_before_the_app_id_is_known(self):
        from backend.relay.inbound.matcher import echo_reason
        e = RelayEvent("r", CHANNEL, "1.1", user=OWNER, text="deploy", bot_id="B1")
        assert echo_reason(e, None, OWNER) == "echo_owner_app"
        assert echo_reason(RelayEvent("r", CHANNEL, "1.1", user=OWNER, text="x"), None, OWNER) is None
        assert echo_reason(RelayEvent("r", CHANNEL, "1.1", user="U0BOT", text="x", bot_id="B1"), None, OWNER) is None

    async def test_reevaluate_trigger_is_a_noop_without_monitors(self, tmp_path):
        e = make_inbound(tmp_path)
        mgr = InboundManager(
            relay_manager=e.manager, relay_service=e.relay_service, session_manager=e.sessions,
            audit=e.audit, relays_root=tmp_path / "relays",
        )
        await mgr.start()
        try:
            mgr.schedule_reevaluate("s1")
            assert mgr._tasks == set()
        finally:
            await mgr.stop()
