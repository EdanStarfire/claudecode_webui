"""Unread monitors: evaluation of one run (issue #2103 stage 3c-ii). Fake clock / adapter, real store."""

from __future__ import annotations

import pytest

from backend.relay.adapter import RelayMessage
from backend.relay.inbound import constants as const
from backend.relay.inbound import unread
from backend.relay.inbound.service import comm_id_for  # noqa: F401
from backend.relay.safety import SELF_DM_MARKER
from backend.tests.relay_inbound_support import OTHER_DM, OTHER_USER, OWNER, SELF_DM, make_inbound
from backend.tests.relay_unread_support import (
    CHANNEL,
    add_unread,
    at,
    chan,
    create,
    dm,
    entries_of,
    mpim,
    msg,
    rows,
    started,
    tick_at,
)

DM1, DM2, GDM = "D0ALICE001", "D0BOB00001", "G0GROUP001"


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.t = await started(e)
    e.relay_service.policies[CHANNEL] = "auto"
    e.clock.advance(const.DIGEST_STARTUP_DELAY_SECONDS + 1)
    yield e
    await e.service.stop()


async def run(env, rec):
    assert await tick_at(env, rec) == 1
    return env.port.calls[-1] if env.port.calls else None


class TestCandidates:
    async def test_dm_with_unread_count(self, env):
        add_unread(env, dm(DM1, unread_count=2, latest=200), msg(100, "one"), msg(200, "two"))
        rec = await create(env)
        call = await run(env, rec)
        [e] = entries_of(call)
        assert (e["channel"], e["kind"], e["unread_count"], e["count_exact"]) == (DM1, "dm", 2, True)
        assert [m["text"] for m in e["messages"]] == ["one", "two"]
        assert env.adapter.history_calls[-1]["oldest"] == at(0)  # from the owner's last_read

    async def test_dm_without_count_uses_latest_vs_last_read(self, env):
        add_unread(env, dm(DM1, latest=100, last_read=50), msg(100))
        add_unread(env, dm(DM2, latest=100, last_read=100), msg(90))  # read: nothing newer than last_read
        rec = await create(env)
        [e] = entries_of(await run(env, rec))
        assert e["channel"] == DM1
        assert ("history", DM2) not in env.adapter.call_log

    async def test_group_dm_and_channel(self, env):
        add_unread(env, mpim(GDM, latest=300), msg(300, "group"))
        add_unread(env, chan(CHANNEL, latest=400), msg(400, "chan"))
        rec = await create(env, dms=True, channels=[CHANNEL])
        entries = entries_of(await run(env, rec))
        assert [(e["channel"], e["kind"]) for e in entries] == [(CHANNEL, "channel"), (GDM, "group_dm")]

    async def test_channel_without_last_read_is_skipped(self, env):
        add_unread(env, chan(CHANNEL, last_read=None, latest=400), msg(400))
        rec = await create(env, dms=False, channels=[CHANNEL])
        await run(env, rec)
        assert env.port.calls == [] and ("history", CHANNEL) not in env.adapter.call_log

    async def test_newest_first_and_five_messages_cut_at_500(self, env):
        add_unread(env, dm(DM1, unread_count=8, latest=108),
                   *[msg(100 + i, f"m{i}" + "x" * 600) for i in range(8)])
        add_unread(env, dm(DM2, unread_count=1, latest=900), msg(900, "newer"))
        rec = await create(env)
        entries = entries_of(await run(env, rec))
        assert [e["channel"] for e in entries] == [DM2, DM1]
        first = entries[1]
        assert len(first["messages"]) == 5 and first["messages"][-1]["text"].startswith("m7")
        assert "truncated" in first["messages"][0]["text"] and len(first["messages"][0]["text"]) < 560
        assert first["unread_count"] == 8

    async def test_conversation_cap_and_exact_tally(self, env):
        for i in range(const.UNREAD_MAX_CONVERSATIONS + 4):
            cid = f"D0N{i:07d}"
            add_unread(env, dm(cid, unread_count=1, latest=100 + i), msg(100 + i, f"c{i}"))
        rec = await create(env)
        call = await run(env, rec)
        assert len(entries_of(call)) == const.UNREAD_MAX_CONVERSATIONS
        assert "+4 more conversations with unread messages omitted" in call["dispatch_content"]
        assert call["metadata"]["relay_inbound"]["omitted"] == 4

    async def test_history_page_full_is_not_exact_and_dm_count_larger(self, env):
        add_unread(env, dm(DM1, unread_count=75, latest=200), msg(100), msg(200))
        add_unread(env, dm(DM2, unread_count=60, latest=100 + const.UNREAD_HISTORY_LIMIT),
                   *[msg(100 + i) for i in range(const.UNREAD_HISTORY_LIMIT)])
        rec = await create(env)
        entries = {e["channel"]: e for e in entries_of(await run(env, rec))}
        assert (entries[DM1]["unread_count"], entries[DM1]["count_exact"]) == (75, False)
        assert entries[DM2]["count_exact"] is False

    async def test_mentions_only(self, env):
        add_unread(env, dm(DM1, unread_count=2, latest=200), msg(100, "plain"), msg(200, f"hi <@{OWNER}>"))
        add_unread(env, dm(DM2, unread_count=1, latest=300), msg(300, "no mention"))
        rec = await create(env, mentions_only=True)
        entries = entries_of(await run(env, rec))
        assert [e["channel"] for e in entries] == [DM1]
        assert [m["text"] for m in entries[0]["messages"]] == [f"hi <@{OWNER}>"]
        assert entries[0]["unread_count"] == 1  # the mention count, not Slack's total
        assert mon(env, rec)["notified"][DM2] == at(300)  # nothing to say: not looked at again

    async def test_own_messages_ignored_and_conversation_dropped_when_nothing_survives(self, env):
        add_unread(env, dm(DM1, unread_count=1, latest=100), msg(100, "mine", user=OWNER))
        rec = await create(env)
        await run(env, rec)
        assert env.port.calls == []

    async def test_echo_rule_without_hydration(self, env):
        add_unread(
            env, dm(DM1, unread_count=5, latest=105),
            msg(101, "meta", metadata={"event_type": "cc_webui_agent_message"}),
            msg(102, "owner app", user=OWNER, app_id="A1"),
            msg(103, f"{SELF_DM_MARKER} hi", user=OWNER),
            msg(104, "other bot", user="U0BOT00001", bot_id="B1"),
            msg(105, "human"),
        )
        rec = await create(env)
        [e] = entries_of(await run(env, rec))
        assert [m["text"] for m in e["messages"]] == ["other bot", "human"]
        assert [m["bot"] for m in e["messages"]] == [True, False]
        assert {m for m, _ in env.adapter.call_log} <= {"info", "history"}  # no single-message fetches

    async def test_self_dm_never_listed_and_exclude(self, env):
        add_unread(env, dm(SELF_DM, unread_count=1, latest=100), msg(100, "note to self", user=OWNER))
        add_unread(env, dm(DM1, unread_count=1, latest=100), msg(100))
        add_unread(env, dm(DM2, unread_count=1, latest=100), msg(100))
        rec = await create(env, exclude=[DM2])
        [e] = entries_of(await run(env, rec))
        assert e["channel"] == DM1
        assert ("info", SELF_DM) not in env.adapter.call_log and ("info", DM2) not in env.adapter.call_log

    async def test_dm_cap_counts_and_flags_gap(self, env):
        add_unread(env, dm(DM1, unread_count=1, latest=100), msg(100))
        for i in range(const.DM_BACKFILL_MAX_CONVERSATIONS + 4):
            add_unread(env, dm(f"D0M{i:07d}", unread_count=0, latest=10))
        rec = await create(env)
        call = await run(env, rec)
        assert [e["channel"] for e in entries_of(call)] == [DM1]
        [row] = rows(env, outcome="delivered")
        assert row["reason_counts"]["conversation_cap"] == 5
        assert "5 conversations were not checked (conversation limit)" in call["dispatch_content"]


def mon(env, rec):
    return env.service.store.get_monitor(rec["id"])


class TestOptionParsing:
    def test_defaults_and_normalisation(self):
        dms, channels, exclude, mo, rep = unread.parse_option_values({})
        assert (dms, channels, exclude, mo, rep) == (True, [], [], False, False)
        a = unread.make_options(True, ["C0B00000001", "C0A00000001"], ["D0X0000001"], False, False)
        b = unread.make_options(True, ["C0A00000001", "C0B00000001"], ["D0X0000001"], False, False)
        assert a == b

    @pytest.mark.parametrize("args", [
        {"dms": "yes"}, {"channels": "C0A00000001"}, {"exclude": ["#general"]},
        {"channels": [f"C0A{i:07d}" for i in range(21)]}, {"repeat": 1},
    ])
    def test_invalid(self, args):
        with pytest.raises(unread.OptionError):
            unread.parse_option_values(args)

    def test_describe(self):
        text = unread.describe_options(unread.make_options(True, ["C0A00000001"], ["D0X0000001"], True, False))
        assert text == ("direct and group messages (all except 1), channels C0A00000001, "
                        "only messages that mention you, only newly unread")


_ = (RelayMessage, OTHER_DM, OTHER_USER)
