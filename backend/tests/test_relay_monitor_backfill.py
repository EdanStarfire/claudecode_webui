"""Scope-monitor backfill: DMs/MPIMs per conversation, mentions via search + hydration
(issue #2103 stage 3b-ii). Fake transport + clock + adapter; real store, coalescer and ports."""

from __future__ import annotations

import pytest

from backend.relay.adapter import ConversationInfo, RelayMessage, SearchHit
from backend.relay.inbound import constants as const
from backend.relay.safety import SELF_DM_MARKER
from backend.tests.relay_inbound_support import (
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
    fire_window,
    started,
    wait_for,
)

M, D = const.KIND_MONITOR_MENTIONS, const.KIND_MONITOR_DMS
DM1, DM2, MPIM = "D0ALICE001", "D0BOB00001", "G0MPIM0001"
OTHER_CH = "C0CHAN0002"


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
        expires_at=None)
    return rec


def conv(cid, *, im=False, mpim=False, latest=None, member=True, private=False):
    return ConversationInfo(id=cid, is_im=im, is_mpim=mpim, is_channel=not (im or mpim),
                            is_private=private, latest_ts=latest, is_member=member)


def msg(env, offset, text="hello", user=OTHER_USER, channel=None, **kw):
    return RelayMessage(ts=env.clock.ts(offset), text=text, user=user, channel=channel, **kw)


def hit(m, channel=CHANNEL, **kw):
    m.channel = channel
    m.permalink = m.permalink or f"https://x.slack.com/archives/{channel}/p1"
    return SearchHit(m, **kw)


def history_calls(env, channel=None):
    return [c for c in env.adapter.call_log if c[0] == "history" and (channel is None or c[1] == channel)]


async def flush(env):
    await fire_window(env)
    return env.port.calls


class TestDmBackfill:
    async def test_only_conversations_with_newer_messages_are_fetched(self, env):
        a = env.adapter
        await scope(env, D)
        a.conversations = {
            DM1: conv(DM1, im=True, latest=env.clock.ts(30)),
            DM2: conv(DM2, im=True, latest=env.clock.ts(-1000)),  # older than every cursor
            SELF_DM: conv(SELF_DM, im=True, latest=env.clock.ts(30)),
            MPIM: conv(MPIM, mpim=True, latest=env.clock.ts(40)),
        }
        a.histories = {
            DM1: [msg(env, 10, "from alice")],
            MPIM: [msg(env, 20, "group hello")],
            SELF_DM: [msg(env, 5, "!my-agent x", user=OWNER)],
        }
        await env.service.backfill_monitors()
        calls = await flush(env)
        assert len(calls) == 1
        entries = envelope_entries(calls[0])
        assert {e["channel"] for e in entries} == {DM1, MPIM}
        assert history_calls(env, DM2) == [] and history_calls(env, SELF_DM) == []
        assert ("info", SELF_DM) not in a.call_log  # the self-DM is never even looked at

    async def test_own_messages_and_echoes_are_not_delivered(self, env):
        await scope(env, D)
        env.adapter.conversations = {DM1: conv(DM1, im=True, latest=env.clock.ts(30))}
        env.adapter.histories = {DM1: [
            msg(env, 10, "mine", user=OWNER),
            msg(env, 11, f"{SELF_DM_MARKER} agent text"),
            msg(env, 12, "agent", metadata={"event_type": "cc_webui_agent_message"}),
            msg(env, 13, "real"),
        ]}
        expected = env.clock.ts(13)
        await env.service.backfill_monitors()
        entries = envelope_entries((await flush(env))[0])
        assert [e["ts"] for e in entries] == [expected]

    async def test_unknown_latest_still_fetches(self, env):
        await scope(env, D)
        env.adapter.conversations = {DM1: conv(DM1, im=True, latest=None)}
        env.adapter.histories = {DM1: [msg(env, 10)]}
        await env.service.backfill_monitors()
        assert len(await flush(env)) == 1

    async def test_conversation_cap_and_gap_notice(self, env):
        await scope(env, D)
        env.adapter.conversations = {
            f"D0CONV{i:04d}": conv(f"D0CONV{i:04d}", im=True, latest=env.clock.ts(-1000)) for i in range(60)}
        env.adapter.conversations["D0CONV0000"].latest_ts = env.clock.ts(10)
        env.adapter.histories = {"D0CONV0000": [msg(env, 10)]}
        await env.service.backfill_monitors()
        calls = await flush(env)
        infos = [c for c in env.adapter.call_log if c[0] == "info"]
        assert len(infos) == const.DM_BACKFILL_MAX_CONVERSATIONS
        text = calls[0]["dispatch_content"]
        assert "Slack backfill was incomplete for the period" in text
        assert text.index("incomplete") < text.index("[UNTRUSTED EXTERNAL CONTENT")  # outside the fence
        row = [r for r in audit_events(env, "monitor_backfill") if r["code"] == "gap_notice"][0]
        assert row["reason_counts"]["conversation_cap"] == 10

    async def test_rate_limit_stops_the_run_with_gap_notice(self, env):
        await scope(env, D)
        env.adapter.conversations = {DM1: conv(DM1, im=True, latest=env.clock.ts(30)),
                                     DM2: conv(DM2, im=True, latest=env.clock.ts(30))}
        env.adapter.histories = {DM1: [msg(env, 10)], DM2: [msg(env, 11)]}
        env.adapter.fail_methods["read_history"] = "ratelimited"
        await env.service.backfill_monitors()
        assert len(history_calls(env)) == 1  # stopped at the first rate limit
        assert env.service._gaps[SESSION]
        env.adapter.fail_methods.clear()
        await env.service.backfill_monitors()
        calls = await flush(env)
        assert "incomplete" in calls[-1]["dispatch_content"]

    async def test_gone_conversation_is_skipped_without_gap_notice(self, env):
        # seen live: a DM with a deactivated user answers channel_not_found to conversations.info
        await scope(env, D)
        env.adapter.conversations = {DM1: conv(DM1, im=True, latest=env.clock.ts(30)),
                                     DM2: conv(DM2, im=True, latest=env.clock.ts(30))}
        env.adapter.histories = {DM1: [msg(env, 10)]}
        env.adapter.fail_channels = {DM2: "channel_not_found"}
        await env.service.backfill_monitors()
        calls = await flush(env)
        assert len(calls) == 1 and "incomplete" not in calls[0]["dispatch_content"]
        assert env.service._gaps.get(SESSION) is None
        assert env.service._drops["monitor_conversation_gone"] == 1

    async def test_idempotent_with_live_twin_and_repeat(self, env):
        rec = await scope(env, D)
        env.adapter.conversations = {DM1: conv(DM1, im=True, latest=env.clock.ts(30))}
        env.adapter.histories = {DM1: [msg(env, 10)]}
        await env.service.backfill_monitors()
        await env.service.backfill_monitors()
        calls = await flush(env)
        assert len(calls) == 1 and len(envelope_entries(calls[0])) == 1
        assert rec["id"] in env.service._recent_keys


class TestMentionsBackfill:
    async def setup_member(self, env, *channels):
        env.adapter.conversations = {c: conv(c) for c in (CHANNEL, OTHER_CH, *channels)}

    async def test_human_hit_delivered_with_hydrated_text_and_query_shape(self, env):
        await scope(env, M)
        await self.setup_member(env)
        m = msg(env, 10, "stale search text")
        env.adapter.search_hits = [hit(m)]
        env.adapter.histories = {CHANNEL: [RelayMessage(ts=m.ts, text=f"<@{OWNER}> hydrated", user=OTHER_USER)]}
        await env.service.backfill_monitors()
        entries = envelope_entries((await flush(env))[0])
        assert [e["text"] for e in entries] == [f"<@{OWNER}> hydrated"]
        call = env.adapter.search_calls[0]
        assert call == {"query": f"<@{OWNER}>", "page": 1, "sort": "timestamp", "sort_dir": "desc"}
        h = env.adapter.history_calls[0]
        assert h["oldest"] == h["latest"] == m.ts and h["inclusive"] is True

    @pytest.mark.parametrize("kw", [
        {"metadata": {"event_type": "cc_webui_agent_message"}},
        {"user": OWNER, "app_id": "A0X"},
        {"text": f"{SELF_DM_MARKER} <@{OWNER}> hi"},
    ])
    async def test_own_agent_post_mentioning_owner_is_dropped_after_hydration(self, env, kw):
        await scope(env, M)
        await self.setup_member(env)
        m = msg(env, 10, f"<@{OWNER}> from agent")  # the search metadata looks human
        env.adapter.search_hits = [hit(m)]
        base = {"ts": m.ts, "text": f"<@{OWNER}> from agent", "user": OTHER_USER}
        env.adapter.histories = {CHANNEL: [RelayMessage(**{**base, **kw})]}
        env.adapter.search_hits[0].message.user = OTHER_USER
        await env.service.backfill_monitors()
        assert await flush(env) == []

    async def test_other_bot_delivered_flagged(self, env):
        await scope(env, M)
        await self.setup_member(env)
        m = msg(env, 10)
        env.adapter.search_hits = [hit(m)]
        env.adapter.histories = {CHANNEL: [RelayMessage(
            ts=m.ts, text=f"<@{OWNER}> ci failed", user=None, bot_id="B0CI", app_id="A0CI")]}
        await env.service.backfill_monitors()
        entries = envelope_entries((await flush(env))[0])
        assert entries[0]["bot"] is True

    async def test_non_member_selfdm_and_owner_hits_dropped_without_calls(self, env):
        await scope(env, M)
        await self.setup_member(env)
        hits = [hit(msg(env, 10), channel="C0OUTSIDE1"), hit(msg(env, 11), channel=SELF_DM),
                hit(msg(env, 12, user=OWNER))]
        env.adapter.search_hits = hits
        await env.service.backfill_monitors()
        assert await flush(env) == [] and history_calls(env) == []
        assert env.service._drops["monitor_non_member"] == 1

    async def test_thread_reply_hit_hydrated_through_replies(self, env):
        await scope(env, M)
        await self.setup_member(env)
        root = "1799999000.000100"
        m = msg(env, 10, "x", thread_ts=root)
        h = hit(m)
        h.message.permalink = f"https://x.slack.com/archives/{CHANNEL}/p1?thread_ts={root}&cid={CHANNEL}"
        env.adapter.search_hits = [h]
        env.adapter.thread_parents = {root: RelayMessage(ts=root, text="parent", user=OTHER_USER)}
        env.adapter.threads = {root: [RelayMessage(ts=m.ts, text=f"<@{OWNER}> reply", user=OTHER_USER, thread_ts=root)]}
        await env.service.backfill_monitors()
        entries = envelope_entries((await flush(env))[0])
        assert [e["text"] for e in entries] == [f"<@{OWNER}> reply"]  # the parent was skipped

    async def test_hit_that_cannot_be_hydrated_is_dropped(self, env):
        await scope(env, M)
        await self.setup_member(env)
        env.adapter.search_hits = [hit(msg(env, 10))]  # nothing in history, no thread permalink
        await env.service.backfill_monitors()
        assert await flush(env) == []
        assert env.service._drops["monitor_hydration_failed"] == 1

    async def test_hydration_error_and_ratelimit_fail_closed(self, env):
        await scope(env, M)
        await self.setup_member(env)
        env.adapter.search_hits = [hit(msg(env, 10)), hit(msg(env, 11))]
        env.adapter.fail_methods["read_history"] = "internal_error"
        await env.service.backfill_monitors()
        assert await flush(env) == []
        env.adapter.fail_methods["read_history"] = "ratelimited"
        await env.service.backfill_monitors()
        assert await flush(env) == [] and env.service._gaps.get(SESSION)

    async def test_hydration_cap_is_20_serialized_and_nothing_unhydrated_is_delivered(self, env):
        await scope(env, M)
        await self.setup_member(env)
        hits, hist = [], []
        for i in range(25, 0, -1):  # newest first
            m = msg(env, i, "t")
            hits.append(hit(m))
            hist.append(RelayMessage(ts=m.ts, text=f"<@{OWNER}> n{i}", user=OTHER_USER))
        env.adapter.search_hits = hits
        env.adapter.histories = {CHANNEL: hist}
        await env.service.backfill_monitors()
        calls = await flush(env)
        assert len(history_calls(env)) == const.HYDRATION_CAP
        assert len(envelope_entries(calls[0])) == const.HYDRATION_CAP
        assert "incomplete" in calls[0]["dispatch_content"]
        assert env.service._drops["monitor_hydration_cap"] == 5

    async def test_known_hits_cost_no_call_and_duplicates_hydrate_once(self, env):
        await scope(env, M)
        await self.setup_member(env)
        m = msg(env, 10)
        env.adapter.search_hits = [hit(m), hit(m)]  # the same hit twice (across pages)
        env.adapter.histories = {CHANNEL: [RelayMessage(ts=m.ts, text=f"<@{OWNER}> x", user=OTHER_USER)]}
        await env.service.backfill_monitors()
        assert len(history_calls(env)) == 1
        await flush(env)
        await env.service.backfill_monitors()
        assert len(history_calls(env)) == 1  # already seen: no new call

    async def test_stops_at_cursor_minus_margin_and_search_is_spaced(self, env):
        await scope(env, M)
        await self.setup_member(env)
        old = msg(env, -const.BACKFILL_MARGIN_SECONDS - 5)
        env.adapter.search_hits = [hit(msg(env, 10)), hit(old)]
        env.adapter.histories = {CHANNEL: [RelayMessage(ts=env.clock.ts(10), text=f"<@{OWNER}> x", user=OTHER_USER)]}
        await env.service.backfill_monitors()
        assert len(history_calls(env)) == 1  # the older hit was never examined
        assert env.service.search_limiter is not None

    async def test_index_lag_rerun_delivers_late_hit_once(self, env):
        await scope(env, M)
        await self.setup_member(env)
        early, late = msg(env, 10), msg(env, 20)
        env.adapter.search_hits = [hit(late), hit(early)]
        env.adapter.search_visible = 1  # the index lags: only the OLDER... first N newest-first
        env.adapter.histories = {CHANNEL: [
            RelayMessage(ts=early.ts, text=f"<@{OWNER}> e", user=OTHER_USER),
            RelayMessage(ts=late.ts, text=f"<@{OWNER}> l", user=OTHER_USER)]}
        await env.service.backfill_monitors()
        await wait_for(lambda: env.rerun.requested)
        assert len(env.rerun.waiting) == 1 and env.rerun.requested == [const.MENTION_RERUN_DELAY_SECONDS]
        env.adapter.search_visible = None  # the index caught up
        await env.rerun.fire()
        await fire_window(env)
        texts = [e["text"] for c in env.port.calls for e in envelope_entries(c)]
        assert sorted(texts) == sorted([f"<@{OWNER}> e", f"<@{OWNER}> l"])
        assert len(texts) == len(set(texts))

    async def test_search_failure_gap_notice_and_failed_membership_fails_closed(self, env):
        await scope(env, M)
        await self.setup_member(env)
        env.adapter.fail_methods["list_conversations"] = "internal_error"
        env.adapter.search_hits = [hit(msg(env, 10))]
        await env.service.backfill_monitors()
        assert env.adapter.search_calls == [] and env.service._gaps.get(SESSION)
