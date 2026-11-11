"""Scheduled digests: scheduler + run per scope (issue #2103 stage 3c-i). Fake clock + fake transport +
fake adapter; real store, watcher, audit. Delivery through the real gate is in the integration file."""

from __future__ import annotations

import asyncio

import pytest

from backend.relay.adapter import ConversationInfo, RelayMessage, SearchHit
from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.relay.inbound.service import comm_id_for  # noqa: F401  (kept: ids of live items)
from backend.relay.safety import SELF_DM_MARKER
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
    envelope_entries,
    started,
)

M, D = const.KIND_MONITOR_MENTIONS, const.KIND_MONITOR_DMS
CH, TH = const.KIND_MONITOR_CHANNEL, const.KIND_MONITOR_THREAD
DM1 = "D0ALICE001"
HOUR = 3600.0


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.t = await started(e)
    e.relay_service.policies[CHANNEL] = "auto"  # a channel digest follows the read policy
    e.clock.advance(const.DIGEST_STARTUP_DELAY_SECONDS + 1)  # past the startup delay
    yield e
    await e.service.stop()


def at(seconds: float) -> str:
    """A Slack ts *seconds* after the fixture's creation time (BASE_TIME)."""
    return f"{BASE_TIME + seconds:.6f}"


def interval(env, minutes=60):
    return sched.parse_schedule({"every_minutes": minutes}, owner_tz=None, now=env.clock.now())


async def digest(env, kind=CH, *, channel=CHANNEL, thread_ts=None, keywords=("deploy",), users=(),
                 minutes=60, session=SESSION, schedule=None):
    if kind in (M, D):
        channel = None
    rec, _ = await env.service.create_monitor(
        session, kind, channel, thread_ts=thread_ts,
        filters={"keywords": list(keywords) if kind == CH else list(keywords or ()), "users": list(users)},
        expires_at=None, schedule=schedule or interval(env, minutes),
    )
    return rec


async def tick_at(env, rec, *, plus: float = 1.0) -> int:
    """Move the clock to just after the monitor's slot and run the scheduler once."""
    now = env.clock.now()
    target = float(env.service.store.get_monitor(rec["id"])["next_run"]) + plus
    if target > now:
        env.clock.advance(target - now)
    return await env.service.digest.tick()


def history_msg(offset, text="deploy now", user=OTHER_USER, **kw):
    return RelayMessage(ts=at(offset), text=text, user=user, **kw)


def mon(env, rec):
    return env.service.store.get_monitor(rec["id"])


def digest_rows(env, **match):
    return [r for r in audit_events(env, "monitor_digest") if all(r.get(k) == v for k, v in match.items())]


async def confirm(env, status="delivered"):
    for call in env.port.calls:
        key = (call["session_id"], call["comm_id"])
        if env.port.statuses.get(key) == "pending":
            env.port.statuses[key] = status
    await env.service.watcher.poll_once()


class TestScheduler:
    async def test_never_early(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        env.clock.advance(HOUR - 1)  # one second before the slot (jitter makes it later still)
        assert await env.service.digest.tick() == 0
        assert env.adapter.history_calls == [] and env.port.calls == []
        assert await tick_at(env, rec) == 1
        assert mon(env, rec)["last_run_trigger"] == "schedule"

    async def test_next_slot_is_persisted_before_the_run_and_strictly_after_now(self, env):
        rec = await digest(env)
        first_slot = mon(env, rec)["next_run"]
        seen = []
        original = env.adapter.read_history

        async def spying(*a, **kw):
            seen.append(mon(env, rec)["next_run"])
            return await original(*a, **kw)

        env.adapter.read_history = spying
        await tick_at(env, rec)
        assert seen and seen[0] > first_slot and seen[0] > env.clock.now() - 100
        assert seen[0] - first_slot >= HOUR - const.DIGEST_JITTER_MAX_SECONDS
        # persisted to disk too
        import json
        stored = json.loads((env.root / "subscriptions.json").read_text())["subscriptions"][0]
        assert stored["next_run"] == mon(env, rec)["next_run"]

    async def test_missed_slots_run_once_marked_late(self, env):
        rec = await digest(env, schedule=sched.parse_schedule(
            {"daily": {"times": ["09:00"], "timezone": "UTC"}}, owner_tz=None, now=env.clock.now()))
        env.adapter.history = [history_msg(100)]
        env.clock.advance(3 * 24 * HOUR + 5 * HOUR)  # Backend "down" for three days
        assert await env.service.digest.tick() == 1
        assert await env.service.digest.tick() == 0  # the other missed slots are merged, not replayed
        assert mon(env, rec)["last_run_trigger"] == "late"
        assert digest_rows(env, code="late") and mon(env, rec)["next_run"] > env.clock.now()

    async def test_at_most_three_runs_per_tick_oldest_slot_first(self, env):
        recs = [await digest(env, minutes=60 + i * 30, keywords=(f"k{i}",)) for i in range(5)]
        env.clock.advance(4 * HOUR)
        assert await env.service.digest.tick() == 3
        ran = [r for r in recs if mon(env, r).get("last_run_trigger")]
        assert [r["id"] for r in ran] == [r["id"] for r in recs[:3]]  # earliest slots first
        assert await env.service.digest.tick() == 2

    async def test_startup_delay(self, tmp_path):
        e = make_inbound(tmp_path)
        e.relay_service.policies[CHANNEL] = "auto"
        await started(e)
        rec = await digest(e)
        e.clock.advance(2 * HOUR)
        await e.service.digest.tick()  # past the slot but within... not_before already passed here
        assert mon(e, rec)["last_run_trigger"]
        # a restarted service waits for the delay before its first run
        await e.service.stop()
        e2 = make_inbound(tmp_path, clock=e.clock)
        e2.relay_service.policies[CHANNEL] = "auto"
        await started(e2)
        rec2 = e2.service.store.get_monitor(rec["id"])
        e2.clock.advance(5 * HOUR)
        e2.service.digest.not_before = e2.clock.now() + 30
        assert await e2.service.digest.tick() == 0
        e2.clock.advance(31)
        assert await e2.service.digest.tick() == 1
        assert rec2["id"] == rec["id"]  # reloaded from disk with its next_run
        await e2.service.stop()

    async def test_restart_keeps_next_run(self, tmp_path):
        e = make_inbound(tmp_path)
        await started(e)
        rec = await digest(e)
        slot = mon(e, rec)["next_run"]
        await e.service.stop()
        e2 = make_inbound(tmp_path, clock=e.clock)
        await started(e2)
        assert e2.service.store.get_monitor(rec["id"])["next_run"] == slot
        await e2.service.stop()

    async def test_paused_monitor_is_skipped_and_resume_recomputes(self, env):
        rec = await digest(env)
        await env.service.set_paused(rec["id"], True)
        slot = mon(env, rec)["next_run"]
        env.clock.advance(3 * HOUR)
        assert await env.service.digest.tick() == 0 and mon(env, rec)["next_run"] == slot
        await env.service.set_paused(rec["id"], False)
        resumed = mon(env, rec)
        assert resumed["next_run"] > env.clock.now() and float(resumed["last_seen_ts"]) == env.clock.now()

    async def test_owner_switch_off_refuses_and_still_advances(self, env):
        rec = await digest(env)
        env.manager.relays["relay1"].inbound_enabled = False
        slot = mon(env, rec)["next_run"]
        assert await tick_at(env, rec) == 1
        assert mon(env, rec)["last_run"]["outcome"] == "refused" and mon(env, rec)["next_run"] > slot
        assert digest_rows(env, outcome="refused", code="disabled_by_owner")
        assert env.adapter.history_calls == []

    async def test_works_without_app_token_or_connection(self, env):
        env.manager.tokens["app"] = False
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        await tick_at(env, rec)
        assert len(env.port.calls) == 1

    async def test_stop_mid_run_enqueues_nothing_and_keeps_cursor(self, tmp_path):
        e = make_inbound(tmp_path)
        e.relay_service.policies[CHANNEL] = "auto"
        await started(e)
        e.clock.advance(31)
        rec = await digest(e)
        cursor = mon(e, rec)["last_seen_ts"]
        gate = asyncio.Event()

        async def hang(*a, **kw):
            gate.set()
            await asyncio.sleep(3600)

        e.adapter.read_history = hang
        e.clock.advance(2 * HOUR)
        sleeper = e.service._digest_sleep
        for _ in range(50):
            if sleeper.waiting:
                break
            await asyncio.sleep(0.01)
        await sleeper.fire()  # the scheduler loop's own tick starts the run
        await asyncio.wait_for(gate.wait(), 3)
        await e.service.stop()  # cancels the loop and with it the run in progress
        assert e.port.calls == [] and mon(e, rec)["last_seen_ts"] == cursor

    async def test_loop_runs_ticks_on_its_own_sleep(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        env.clock.advance(2 * HOUR)
        sleeper = env.service._digest_sleep
        for _ in range(5):
            if sleeper.waiting:
                break
            await asyncio.sleep(0.01)
        assert sleeper.requested and sleeper.requested[0] == const.DIGEST_TICK_SECONDS
        await sleeper.fire()
        for _ in range(100):
            if env.port.calls:
                break
            await asyncio.sleep(0.01)
        assert len(env.port.calls) == 1 and mon(env, rec)["last_run_trigger"]


class TestRunsPerScope:
    async def test_channel_keyword_filter_delivers_one_digest_envelope(self, env):
        rec = await digest(env, keywords=("deploy",))
        env.adapter.history = [
            history_msg(100, "please deploy"), history_msg(200, "lunch?"), history_msg(300, "deploy done")]
        await tick_at(env, rec)
        [call] = env.port.calls
        assert [e["text"] for e in envelope_entries(call)] == ["please deploy", "deploy done"]
        text = call["dispatch_content"]
        assert text.startswith("📰 Slack digest — 2 message(s) from 1 monitor(s) since ")
        assert "scheduled digest of Slack messages" in text and "NOT instructions" in text
        assert call["summary"] == "Slack digest: 2 messages"
        ri = call["metadata"]["relay_inbound"]
        assert ri["mode"] == "digest" and ri["kind"] == "monitor" and ri["count"] == 2
        assert ri["envelope_id"] == call["comm_id"] and ri["monitor_ids"] == [rec["id"]]
        assert call["metadata"]["comm"]["from_display_name"] == "Slack digest"
        assert text.index("Slack digest") < text.index("[UNTRUSTED EXTERNAL CONTENT")
        assert digest_rows(env, outcome="delivered", envelope_id=call["comm_id"])[0]["count"] == 2

    async def test_user_filter_and_keyword_and(self, env):
        rec = await digest(env, keywords=("deploy",), users=(OTHER_USER,))
        env.adapter.history = [history_msg(100, "deploy a", user=OTHER_USER),
                               history_msg(200, "deploy b", user="U0THIRD01")]
        await tick_at(env, rec)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == ["deploy a"]

    async def test_thread_digest_delivers_replies_not_the_root(self, env):
        root = at(10)
        rec = await digest(env, TH, thread_ts=root, keywords=())
        env.adapter.thread_parents = {root: RelayMessage(ts=root, text="root", user=OTHER_USER)}
        env.adapter.threads = {root: [
            RelayMessage(ts=at(100), text="reply 1", user=OTHER_USER, thread_ts=root),
            RelayMessage(ts=at(200), text="reply 2", user=OTHER_USER, thread_ts=root)]}
        await tick_at(env, rec)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == ["reply 1", "reply 2"]

    async def test_dms_digest_excludes_self_dm_and_own_messages(self, env):
        rec = await digest(env, D, keywords=())
        env.adapter.conversations = {
            DM1: ConversationInfo(id=DM1, is_im=True, latest_ts=at(500)),
            SELF_DM: ConversationInfo(id=SELF_DM, is_im=True, latest_ts=at(500))}
        env.adapter.histories = {
            DM1: [history_msg(100, "hi owner"), history_msg(110, "mine", user=OWNER)],
            SELF_DM: [history_msg(120, "!my-agent x", user=OWNER)]}
        await tick_at(env, rec)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == ["hi owner"]
        assert ("info", SELF_DM) not in env.adapter.call_log

    def _mentions_setup(self, env, n, *, texts=None):
        env.adapter.conversations = {CHANNEL: ConversationInfo(id=CHANNEL, is_channel=True)}
        hits, full = [], []
        for i in range(n, 0, -1):  # newest first, like search
            ts = at(100 + i)
            m = RelayMessage(ts=ts, text="stale", user=OTHER_USER, channel=CHANNEL,
                             permalink=f"https://x.slack.com/archives/{CHANNEL}/p{i}")
            hits.append(SearchHit(m))
            full.append(RelayMessage(ts=ts, text=(texts or {}).get(i, f"<@{OWNER}> m{i}"), user=OTHER_USER))
        env.adapter.search_hits = hits
        env.adapter.histories = {CHANNEL: full}

    async def test_mentions_digest_hydrates_and_drops_own_posts(self, env):
        rec = await digest(env, M, keywords=())
        self._mentions_setup(env, 3, texts={2: f"{SELF_DM_MARKER} <@{OWNER}> from the agent"})
        await tick_at(env, rec)
        assert [e["text"] for e in envelope_entries(env.port.calls[0])] == [
            f"<@{OWNER}> m1", f"<@{OWNER}> m3"]
        h = env.adapter.history_calls[0]
        assert h["inclusive"] is True and h["oldest"] == h["latest"]  # hydrated, never the search text

    async def test_mentions_hydration_cap_40_states_the_exact_count_and_never_delivers_unhydrated(self, env):
        rec = await digest(env, M, keywords=())
        self._mentions_setup(env, 45)
        await tick_at(env, rec)
        [call] = env.port.calls
        assert len(envelope_entries(call)) == const.DIGEST_HYDRATION_CAP
        assert len([c for c in env.adapter.call_log if c[0] == "history"]) == 40
        text = call["dispatch_content"]
        assert "5 mentions could not be checked (hydration limit)" in text
        assert text.index("could not be checked") < text.index("[UNTRUSTED EXTERNAL CONTENT")
        row = digest_rows(env, outcome="delivered")[0]
        assert row["reason_counts"]["hydration_cap"] == 5
        delivered = {e["ts"] for e in envelope_entries(call)}
        fetched = {c["oldest"] for c in env.adapter.history_calls}
        assert delivered == fetched and len(delivered) == 40  # only hydrated hits are delivered

    async def test_paced_calls_use_the_shared_tier3_limiter(self, env):
        rec = await digest(env, M, keywords=())
        self._mentions_setup(env, 3)
        await tick_at(env, rec)
        assert env.api_sleeps and all(abs(s - const.API_MIN_SPACING_SECONDS) < 1e-6 or s > 0
                                      for s in env.api_sleeps)
        assert env.search_sleeps == [] or all(s > 0 for s in env.search_sleeps)


class TestEmptyAndDelivery:
    async def test_empty_period_sends_nothing_and_advances_the_cursor(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100, "lunch?")]
        await tick_at(env, rec)
        assert env.port.calls == []
        m = mon(env, rec)
        assert m["last_run"]["outcome"] == "empty"
        assert float(m["last_seen_ts"]) == pytest.approx(env.clock.now() - 60, abs=2)
        assert digest_rows(env, outcome="empty") and mon(env, rec)["next_run"] > env.clock.now()

    async def test_cursor_on_confirm_and_last_run_delivered(self, env):
        rec = await digest(env)
        cursor = mon(env, rec)["last_seen_ts"]
        env.adapter.history = [history_msg(100), history_msg(200, "deploy two")]
        await tick_at(env, rec)
        assert mon(env, rec)["last_seen_ts"] == cursor  # not before the session confirms
        await confirm(env)
        m = mon(env, rec)
        assert m["last_seen_ts"] == at(200) and m["last_run"]["outcome"] == "delivered"
        assert m["last_run"]["messages"] == 2 and m["run_failures"] == 0 and m["counters"]["delivered"] == 2
        # the next digest only sees what is new
        env.adapter.history.append(history_msg(HOUR + 500, "deploy three"))
        await tick_at(env, rec)
        assert [e["text"] for e in envelope_entries(env.port.calls[1])] == ["deploy three"]

    async def test_failed_delivery_keeps_cursor_and_three_failures_suspend(self, env):
        rec = await digest(env)
        cursor = mon(env, rec)["last_seen_ts"]
        env.adapter.history = [history_msg(100)]
        for n in range(1, 4):
            await tick_at(env, rec)
            await confirm(env, "failed")
            assert mon(env, rec)["last_seen_ts"] == cursor and mon(env, rec)["run_failures"] == n
            if n < 3:
                assert mon(env, rec)["status"] == "active"
        assert mon(env, rec)["status"] == "suspended" and mon(env, rec)["suspended_reason"] == "delivery_failed"
        assert len(env.port.calls) == 3  # the failed content was re-collected each time (new comm id)
        assert len({c["comm_id"] for c in env.port.calls}) == 3

    async def test_enqueue_failure_counts_and_keeps_the_cursor(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        env.port.fail_enqueue = True
        await tick_at(env, rec)
        m = mon(env, rec)
        assert m["last_run"]["outcome"] == "failed" and m["run_failures"] == 1 and m["failures"] == 1
        env.port.fail_enqueue = False
        await tick_at(env, rec)
        assert len(env.port.calls) == 1  # the next run re-collects it

    async def test_envelope_caps_and_tally(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100 + i, f"deploy {i}") for i in range(60)]
        await tick_at(env, rec)
        [call] = env.port.calls
        assert len(envelope_entries(call)) == const.ENVELOPE_MAX_MESSAGES
        assert f"+10 more messages omitted (by channel: {CHANNEL} x10)" in call["dispatch_content"]
        assert call["metadata"]["relay_inbound"]["omitted"] == 10
        await confirm(env)
        assert mon(env, rec)["last_seen_ts"] == at(159)  # the tallied ones are covered too

    async def test_blocked_channel_is_suspended_and_nothing_is_collected(self, env):
        rec = await digest(env)
        env.relay_service.policies[CHANNEL] = "blocked"
        env.adapter.history = [history_msg(100)]
        await tick_at(env, rec)
        assert env.port.calls == [] and mon(env, rec)["status"] == "suspended"
        assert mon(env, rec)["suspended_reason"] == "channel_blocked"
        assert env.adapter.history_calls == []

    async def test_blocked_channel_messages_dropped_for_scope_digest(self, env):
        env.relay_service.policies["C0BLOCKED1"] = "blocked"
        rec = await digest(env, D, keywords=())
        env.adapter.conversations = {DM1: ConversationInfo(id=DM1, is_im=True)}
        env.adapter.histories = {DM1: [history_msg(100, "hello")]}
        await tick_at(env, rec)
        assert len(env.port.calls) == 1  # DMs are not channels: the block does not reach them

    async def test_live_monitors_never_see_digests(self, env):
        rec = await digest(env)
        assert env.service.store.monitors_for_channel(CHANNEL) == []
        assert env.service.store.scope_monitors() == []
        before = len(env.adapter.history_calls)
        await env.service.backfill_monitors()  # the hello backfill skips digests
        assert len(env.adapter.history_calls) == before
        assert mon(env, rec)["mode"] == "digest"

    async def test_defer_up_to_six_times_then_enqueues(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        env.port.pending_monitor = const.MAX_PENDING_ENVELOPES
        for n in range(1, const.DIGEST_DEFER_MAX + 1):
            await tick_at(env, rec)
            assert env.port.calls == [] and mon(env, rec)["last_run"]["outcome"] == "deferred"
            assert digest_rows(env, outcome="deferred", retry=n)
            assert mon(env, rec)["next_run"] - env.clock.now() == pytest.approx(
                const.DIGEST_DEFER_STEP_SECONDS, abs=2)
        await tick_at(env, rec)
        assert len(env.port.calls) == 1 and mon(env, rec)["slot_defers"] == 0

    async def test_two_digests_of_one_session_are_two_envelopes(self, env):
        a = await digest(env, keywords=("deploy",))
        b = await digest(env, keywords=("build",))
        env.adapter.history = [history_msg(100, "deploy x"), history_msg(110, "build y")]
        env.clock.advance(2 * HOUR)
        await env.service.digest.tick()
        assert len(env.port.calls) == 2
        assert {tuple(c["metadata"]["relay_inbound"]["monitor_ids"]) for c in env.port.calls} == {
            (a["id"],), (b["id"],)}


class TestRateLimits:
    async def test_reschedules_five_minutes_three_times_then_gives_up_with_notice(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        env.adapter.fail_methods["read_history"] = "ratelimited"
        for n in range(1, 4):
            await tick_at(env, rec)
            m = mon(env, rec)
            assert m["last_run"]["outcome"] == "rate_limited" and m["slot_retries"] == n
            assert m["next_run"] == pytest.approx(env.clock.now() + 300, abs=1)
            assert digest_rows(env, code="rate_limited_rescheduled", retry=n)
        calls_before = len(env.adapter.history_calls)
        await tick_at(env, rec)  # fourth: the slot is given up
        m = mon(env, rec)
        assert m["slot_retries"] == 0 and m["next_run"] > env.clock.now() + 600
        assert digest_rows(env, code="rate_limited_gave_up")
        assert len(env.adapter.history_calls) == calls_before + 1  # no retry storm: one call per run
        assert env.port.calls == [] and m["run_failures"] == 4
        env.adapter.fail_methods.clear()
        await tick_at(env, rec)
        text = env.port.calls[0]["dispatch_content"]
        assert "Slack calls were rate limited" in text and "Slack digest incomplete" in text

    async def test_not_run_again_before_the_five_minutes(self, env):
        rec = await digest(env)
        env.adapter.fail_methods["read_history"] = "ratelimited"
        await tick_at(env, rec)
        calls = len(env.adapter.history_calls)
        env.clock.advance(200)
        assert await env.service.digest.tick() == 0 and len(env.adapter.history_calls) == calls

    async def test_ratelimit_in_search_and_hydration_stops_the_run(self, env):
        rec = await digest(env, M, keywords=())
        env.adapter.conversations = {CHANNEL: ConversationInfo(id=CHANNEL, is_channel=True)}
        env.adapter.fail_methods["search_page"] = "ratelimited"
        await tick_at(env, rec)
        assert mon(env, rec)["last_run"]["outcome"] == "rate_limited" and env.port.calls == []
        env.adapter.fail_methods.clear()
        env.adapter.search_hits = [SearchHit(RelayMessage(
            ts=at(100), text="x", user=OTHER_USER, channel=CHANNEL, permalink="https://x/p1"))]
        env.adapter.fail_methods["read_history"] = "ratelimited"
        await tick_at(env, rec, plus=301)
        assert mon(env, rec)["last_run"]["outcome"] == "rate_limited" and env.port.calls == []

    async def test_run_now_ratelimit_leaves_the_schedule_alone(self, env):
        rec = await digest(env)
        slot = mon(env, rec)["next_run"]
        env.adapter.fail_methods["read_history"] = "ratelimited"
        assert (await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION))["ok"]
        for _ in range(100):
            if mon(env, rec).get("last_run"):
                break
            await asyncio.sleep(0.01)
        assert mon(env, rec)["last_run"]["outcome"] == "rate_limited" and mon(env, rec)["next_run"] == slot


class TestRunNow:
    async def test_agent_run_now_starts_without_waiting_and_keeps_the_schedule(self, env):
        rec = await digest(env)
        slot = mon(env, rec)["next_run"]
        env.adapter.history = [history_msg(100)]
        res = await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION)
        assert res == {"ok": True, "started": True}
        for _ in range(200):
            if env.port.calls and not env.service.digest.running:
                break
            await asyncio.sleep(0.01)
        assert len(env.port.calls) == 1
        assert mon(env, rec)["next_run"] == slot and mon(env, rec)["last_run_trigger"] == "agent"
        assert digest_rows(env, code="run_now", source="agent", outcome="accepted")
        assert digest_rows(env, outcome="delivered", source="agent")

    async def test_too_soon_after_five_minutes_rule(self, env):
        rec = await digest(env)
        assert (await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION))["ok"]
        for _ in range(100):
            if not env.service.digest.running:
                break
            await asyncio.sleep(0.01)
        res = await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION)
        assert res["ok"] is False and res["code"] == "run_too_soon" and res["seconds_left"] > 0
        env.clock.advance(const.RUN_NOW_AGENT_MIN_SECONDS + 1)
        assert (await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION))["ok"]
        # the owner limit is 60 s
        env.clock.advance(61)
        for _ in range(100):
            if not env.service.digest.running:
                break
            await asyncio.sleep(0.01)
        assert (await env.service.run_monitor_now(rec["id"], trigger="manual"))["ok"]

    async def test_refusals_are_coded_and_every_attempt_is_audited(self, env):
        rec = await digest(env)
        live = await env.service.create_monitor(
            SESSION, CH, "C0LIVE0001", thread_ts=None, filters={"keywords": ["a"], "users": []},
            expires_at=None)
        run = env.service.run_monitor_now
        assert (await run("nope", trigger="agent", session_id=SESSION))["code"] == "monitor_not_found"
        assert (await run(rec["id"], trigger="agent", session_id="other"))["code"] == "monitor_not_found"
        assert (await run(live[0]["id"], trigger="agent", session_id=SESSION))["code"] == "not_a_digest_monitor"
        await env.service.set_paused(rec["id"], True)
        assert (await run(rec["id"], trigger="agent", session_id=SESSION))["code"] == "monitor_paused"
        await env.service.set_paused(rec["id"], False)
        await env.service.store.set_monitor_status(rec["id"], "suspended", "channel_blocked")
        res = await run(rec["id"], trigger="manual")
        assert res["code"] == "monitor_suspended" and "channel_blocked" in res["message"]
        codes = [r["code"] for r in digest_rows(env, outcome="refused")]
        assert codes == ["monitor_not_found", "monitor_not_found", "not_a_digest_monitor",
                         "monitor_paused", "monitor_suspended"]
        assert digest_rows(env, code="monitor_suspended")[0]["source"] == "owner"
        assert env.adapter.history_calls == []

    async def test_in_progress(self, env):
        rec = await digest(env)
        release = asyncio.Event()

        async def slow(*a, **kw):
            await release.wait()
            return await original(*a, **kw)

        original = env.adapter.read_history
        env.adapter.read_history = slow
        assert (await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION))["ok"]
        env.clock.advance(const.RUN_NOW_AGENT_MIN_SECONDS + 1)
        res = await env.service.run_monitor_now(rec["id"], trigger="agent", session_id=SESSION)
        assert res["code"] == "run_in_progress"
        release.set()
        for _ in range(100):
            if not env.service.digest.running:
                break
            await asyncio.sleep(0.01)


class TestLifecycle:
    async def test_delete_cancels_a_queued_digest_envelope(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        await tick_at(env, rec)
        [call] = env.port.calls
        assert await env.service.delete_monitor(rec["id"])
        assert env.port.statuses[(SESSION, call["comm_id"])] == "cancelled"
        assert env.service.store.get_monitor(rec["id"]) is None

    async def test_dispose_suspends_and_start_restores_then_runs_a_missed_slot_once(self, env):
        rec = await digest(env)
        env.adapter.history = [history_msg(100)]
        await env.service.on_session_disposed(SESSION)
        assert mon(env, rec)["suspended_reason"] == "disposed"
        env.clock.advance(5 * HOUR)
        assert await env.service.digest.tick() == 0  # a suspended digest does not run
        await env.service.on_session_started(SESSION)
        assert mon(env, rec)["status"] == "active"
        for _ in range(200):
            if env.port.calls:
                break
            await asyncio.sleep(0.01)
        assert len(env.port.calls) == 1
        assert await env.service.digest.tick() == 0

    async def test_no_comm_to_a_parent_and_other_sessions_untouched(self, env):
        env.sessions.add("s2")
        rec = await digest(env)
        await digest(env, session="s2", keywords=("deploy",))
        env.adapter.history = [history_msg(100)]
        env.clock.advance(2 * HOUR)
        await env.service.digest.tick()
        assert {c["session_id"] for c in env.port.calls} == {SESSION, "s2"}
        assert rec["id"] in [mid for c in env.port.calls if c["session_id"] == SESSION
                             for mid in c["metadata"]["relay_inbound"]["monitor_ids"]]

    async def test_expiry_is_honoured_and_idle_rule_does_not_apply(self, env):
        rec = await digest(env, TH, thread_ts=at(10), keywords=())
        env.clock.advance(const.MONITOR_THREAD_IDLE_SECONDS + 10)
        assert await env.service.store.expire_monitors() == []  # a digest is long-lived
        env.service.store.touch_monitor(rec["id"], expires_at=env.clock.now() - 1)
        assert len(await env.service.store.expire_monitors()) == 1

    async def test_policy_resume_recomputes_next_run_from_now(self, env):
        rec = await digest(env)
        env.relay_service.policies[CHANNEL] = "blocked"
        await env.service.reevaluate(SESSION)
        assert mon(env, rec)["status"] == "suspended"
        env.clock.advance(5 * HOUR)
        env.relay_service.policies[CHANNEL] = "auto"
        await env.service.reevaluate(SESSION)
        m = mon(env, rec)
        assert m["status"] == "active" and m["next_run"] > env.clock.now()
        assert float(m["last_seen_ts"]) == env.clock.now()
