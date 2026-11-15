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


PRIV = "G0PRIVATE01"


class TestRealSlackShapes:
    """Issue #2374: group DMs and channels carry only ``last_read`` (no count, no ``latest``)."""

    async def test_group_dm_and_channels_from_history(self, env):
        add_unread(env, mpim(GDM), msg(300, "group"))
        add_unread(env, chan(CHANNEL), msg(400, "chan"))
        add_unread(env, chan(PRIV, is_private=True), msg(350, "priv"))
        env.relay_service.policies[PRIV] = "auto"
        rec = await create(env, channels=[CHANNEL, PRIV])
        entries = entries_of(await run(env, rec))
        assert [(e["channel"], e["kind"], e["unread_count"], e["count_exact"]) for e in entries] == [
            (CHANNEL, "channel", 1, True), (PRIV, "private_channel", 1, True), (GDM, "group_dm", 1, True)]
        assert env.adapter.history_calls[-1]["oldest"] == at(0)

    async def test_empty_history_is_no_unread_and_consumed(self, env):
        add_unread(env, mpim(GDM))
        rec = await create(env)
        await run(env, rec)
        assert env.port.calls == []
        [row] = rows(env, outcome="empty")
        assert row["reason_counts"] == {"no_unread": 1}

    async def test_filtered_messages_still_advance_consumed(self, env):
        add_unread(env, mpim(GDM), msg(100, "mine", user=OWNER), msg(120, f"{SELF_DM_MARKER} x", user=OWNER))
        rec = await create(env)
        await run(env, rec)
        assert env.port.calls == []
        assert mon(env, rec)["notified"][GDM] == at(120)

    async def test_mentions_only(self, env):
        add_unread(env, mpim(GDM), msg(100, "plain"), msg(110, f"yo <@{OWNER}>"))
        rec = await create(env, mentions_only=True)
        [e] = entries_of(await run(env, rec))
        assert [m["text"] for m in e["messages"]] == [f"yo <@{OWNER}>"]

    async def test_full_page_not_exact(self, env):
        add_unread(env, mpim(GDM), *[msg(100 + i) for i in range(const.UNREAD_HISTORY_LIMIT)])
        rec = await create(env)
        [e] = entries_of(await run(env, rec))
        assert e["count_exact"] is False

    async def test_already_notified_and_repeat(self, env):
        add_unread(env, mpim(GDM), msg(300))
        rec = await create(env)
        await run(env, rec)
        from backend.tests.relay_unread_support import confirm
        await confirm(env)
        assert mon(env, rec)["notified"][GDM] == at(300)
        n = len(env.port.calls)
        await run(env, rec)
        assert len(env.port.calls) == n and rows(env, outcome="empty")[-1]["reason_counts"] == {"already_notified": 1}
        env.adapter.histories[GDM].append(msg(310, "new"))
        [e] = entries_of(await run(env, rec))
        assert e["messages"][-1]["text"] == "new" and len(env.port.calls) == n + 1

    async def test_repeat_lists_again(self, env):
        from backend.tests.relay_unread_support import confirm
        add_unread(env, mpim(GDM), msg(300))
        rec = await create(env, repeat=True)
        await run(env, rec)
        await confirm(env)
        [e] = entries_of(await run(env, rec))
        assert e["channel"] == GDM

    async def test_im_with_count_unchanged_and_no_history_when_zero(self, env):
        add_unread(env, dm(DM1, unread_count=0, latest=100))
        rec = await create(env)
        await run(env, rec)
        assert ("history", DM1) not in env.adapter.call_log

    async def test_latest_without_count_defensive(self, env):
        add_unread(env, chan(CHANNEL, last_read=100, latest=100), msg(90))
        add_unread(env, mpim(GDM, last_read=100, latest=200), msg(200))
        rec = await create(env, channels=[CHANNEL])
        [e] = entries_of(await run(env, rec))
        assert e["channel"] == GDM and ("history", CHANNEL) not in env.adapter.call_log

    async def test_blocked_unset_exclude_make_no_calls(self, env):
        add_unread(env, chan("C0BLOCKED01"), msg(100))
        add_unread(env, chan("C0UNSET0001"), msg(100))
        add_unread(env, mpim(GDM), msg(100))
        env.relay_service.policies["C0BLOCKED01"] = "blocked"
        rec = await create(env, dms=True, channels=["C0BLOCKED01", "C0UNSET0001"], exclude=[GDM])
        await run(env, rec)
        assert env.adapter.call_log == [] or all(c not in ("C0BLOCKED01", "C0UNSET0001", GDM)
                                                  for _, c in env.adapter.call_log)

    async def test_mixed_run_one_envelope_newest_first(self, env):
        add_unread(env, dm(DM1, unread_count=1, latest=500), msg(500, "dm"))
        add_unread(env, mpim(GDM), msg(600, "g"))
        add_unread(env, chan(CHANNEL), msg(550, "c"))
        rec = await create(env, channels=[CHANNEL])
        entries = entries_of(await run(env, rec))
        assert [e["channel"] for e in entries] == [GDM, CHANNEL, DM1]
        assert len(env.port.calls) == 1


class TestUnknownReadState:
    @pytest.mark.parametrize("last_read", [None, "", "0", "0.000000"])
    async def test_unknown_never_no_unread_and_no_history(self, env, last_read):
        info = chan(CHANNEL)
        info.last_read = last_read
        add_unread(env, info, msg(100))
        rec = await create(env, dms=False, channels=[CHANNEL])
        call = await run(env, rec)
        assert ("history", CHANNEL) not in env.adapter.call_log
        assert "could not be checked" in call["dispatch_content"]
        assert "read state could not be determined for 1 conversation;" in call["dispatch_content"]
        assert CHANNEL not in call["dispatch_content"]
        [row] = rows(env, outcome="delivered")
        assert row["count"] == 0 and row["reason_counts"]["read_state_unknown"] == 1

    async def test_im_without_anything_is_unknown(self, env):
        from backend.relay.adapter import ConversationInfo
        add_unread(env, ConversationInfo(id=DM1, is_im=True))
        rec = await create(env)
        await run(env, rec)
        assert rows(env, outcome="delivered")[0]["reason_counts"]["read_state_unknown"] == 1

    @pytest.mark.parametrize("code", ["missing_scope", "not_in_channel", "weird"])
    async def test_history_error_is_unknown(self, env, code):
        add_unread(env, mpim(GDM), msg(100))
        env.adapter.fail_methods["read_history"] = code
        rec = await create(env)
        await run(env, rec)
        assert rows(env)[-1]["reason_counts"] == {"read_state_unknown": 1}

    async def test_history_gone(self, env):
        add_unread(env, mpim(GDM), msg(100))
        env.adapter.fail_methods["read_history"] = "channel_not_found"
        rec = await create(env)
        await run(env, rec)
        assert rows(env)[-1]["reason_counts"] == {"conversation_gone": 1}

    async def test_history_ratelimited_stops_run(self, env):
        add_unread(env, mpim(GDM), msg(100))
        env.adapter.fail_methods["read_history"] = "ratelimited"
        rec = await create(env)
        await run(env, rec)
        assert rows(env)[-1]["outcome"] == "rate_limited"

    async def test_suppression_same_set_then_changed_then_repeat(self, env):
        from backend.tests.relay_unread_support import confirm
        info = chan(CHANNEL)
        info.last_read = None
        add_unread(env, info, msg(100))
        rec = await create(env, dms=False, channels=[CHANNEL])
        await run(env, rec)
        assert mon(env, rec).get("unknown_notice_hash") is None  # not before confirmation
        await confirm(env, "failed")
        assert mon(env, rec).get("unknown_notice_hash") is None
        await run(env, rec)  # failed earlier: notifies again
        assert len(env.port.calls) == 2
        await confirm(env)
        h = mon(env, rec)["unknown_notice_hash"]
        assert h and CHANNEL not in h and len(h) == 16
        await run(env, rec)  # unchanged: suppressed
        assert len(env.port.calls) == 2
        last = rows(env)[-1]
        assert last["outcome"] == "empty" and last["code"] == "read_state_unknown"
        info.last_read = at(0)  # recovered: hash cleared
        await run(env, rec)
        assert mon(env, rec)["unknown_notice_hash"] is None

    async def test_repeat_notifies_every_run(self, env):
        from backend.tests.relay_unread_support import confirm
        info = chan(CHANNEL)
        info.last_read = None
        add_unread(env, info)
        rec = await create(env, dms=False, channels=[CHANNEL], repeat=True)
        await run(env, rec)
        await confirm(env)
        await run(env, rec)
        assert len(env.port.calls) == 2
        assert env.port.calls[0]["comm_id"] != env.port.calls[1]["comm_id"]
        assert mon(env, rec).get("notified", {}) == {}

    async def test_mixed_unknown_and_listed(self, env):
        info = chan(CHANNEL)
        info.last_read = None
        add_unread(env, info)
        add_unread(env, mpim(GDM), msg(100, "hello"))
        rec = await create(env, channels=[CHANNEL])
        call = await run(env, rec)
        assert [e["channel"] for e in entries_of(call)] == [GDM]
        assert "read state could not be determined for 1 conversation;" in call["dispatch_content"]

    def test_hash_order_independent(self):
        assert unread.unknown_hash(["a", "b"]) == unread.unknown_hash(["b", "a"])


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
