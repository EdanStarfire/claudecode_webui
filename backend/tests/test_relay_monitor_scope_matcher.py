"""Mentions / DMs matching (issue #2103 stage 3b-ii). Pure."""

from __future__ import annotations

from backend.relay.inbound import constants as const
from backend.relay.inbound.matcher import match, mentions_owner
from backend.relay.safety import SELF_DM_MARKER
from backend.tests.test_relay_monitor_matcher import ALICE, CH, OWNER, SELF_DM, ctx, ev, mon

M = const.KIND_MONITOR_MENTIONS
D = const.KIND_MONITOR_DMS


def scope(kind, **kw):
    return mon(kind, channel=None, **kw)


def dm(text="hi", channel="D0ALICE001", ctype="im", **kw):
    e = ev(text=text, channel=channel, **kw)
    e.channel_type = ctype
    return e


class TestMentions:
    def test_forms(self):
        assert mentions_owner(f"hey <@{OWNER}> look", OWNER)
        assert mentions_owner(f"hey <@{OWNER}|owner> look", OWNER)
        assert not mentions_owner("<!here> <!channel> <!subteam^S1>", OWNER)
        assert not mentions_owner("<@U0OTHER001> and @owner", OWNER)
        assert not mentions_owner(f"<@{OWNER}X>", OWNER)
        assert not mentions_owner(f"<@{OWNER}>", None)

    def test_matches_in_any_conversation_type(self):
        m = scope(M)
        for channel, ctype in ((CH, "channel"), ("G0PRIV0001", "group"), ("D0ALICE001", "im"), ("G0MPIM0001", "mpim")):
            e = ev(text=f"<@{OWNER}> ping", channel=channel)
            e.channel_type = ctype
            assert match(e, [m], ctx()) == [m]

    def test_owner_self_mention_and_plain_text_do_not_match(self):
        m = scope(M)
        assert match(ev(text=f"<@{OWNER}> me", user=OWNER), [m], ctx()) == []
        assert match(ev(text="no mention"), [m], ctx()) == []

    def test_filters_apply(self):
        m = scope(M, keywords=["deploy"])
        assert match(ev(text=f"<@{OWNER}> Deploy"), [m], ctx()) == [m]
        assert match(ev(text=f"<@{OWNER}> lunch"), [m], ctx()) == []
        m = scope(M, users=["U0BOB00001"])
        assert match(ev(text=f"<@{OWNER}> hi"), [m], ctx()) == []

    def test_block_only_message_mention_via_user_element_text(self):
        from backend.relay.slack_adapter import _text_from_blocks
        blocks = [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
            {"type": "text", "text": "hi "}, {"type": "user", "user_id": OWNER}]}]}]
        text = _text_from_blocks(blocks)
        assert text == f"hi <@{OWNER}>"
        m = scope(M)
        assert match(ev(text=text), [m], ctx()) == [m]


class TestDms:
    def test_im_and_mpim(self):
        m = scope(D)
        assert match(dm(), [m], ctx()) == [m]
        assert match(dm(channel="G0MPIM0001", ctype="mpim"), [m], ctx()) == [m]

    def test_channels_owner_and_selfdm_excluded(self):
        m = scope(D)
        assert match(ev(), [m], ctx()) == []  # a channel message
        assert match(dm(user=OWNER), [m], ctx()) == []  # the owner's own message
        assert match(dm(channel=SELF_DM), [m], ctx()) == []  # the self-DM is the instruction channel
        assert match(dm(channel=SELF_DM, thread_ts="1800000000.000100"), [m], ctx()) == []

    def test_echo_rule_applies(self):
        m = scope(D)
        assert match(dm(metadata={"event_type": "cc_webui_agent_message"}), [m], ctx()) == []
        assert match(dm(text=f"{SELF_DM_MARKER} x"), [m], ctx()) == []
        assert match(dm(app_id="A0OWN"), [m], ctx()) == []
        # (the owner's own messages never match a scope monitor at all; the owner-with-app-id rule is
        # pinned in test_relay_monitor_matcher for the channel monitors)
        e = dm(bot_id="B0OTHER", user=ALICE)
        assert match(e, [m], ctx()) == [m]  # another bot is delivered (flagged by the entry)

    def test_one_message_feeds_mentions_dms_and_channel_monitors(self):
        e = dm(text=f"<@{OWNER}> deploy")
        a, b = scope(D, id="a"), scope(M, id="b")
        assert [x["id"] for x in match(e, [a, b], ctx())] == ["a", "b"]

    def test_paused_suspended_expired_ignored(self):
        e = dm()
        assert match(e, [scope(D, paused=True)], ctx()) == []
        assert match(e, [scope(D, status="suspended")], ctx()) == []
        assert match(e, [scope(D, expires_at=1.0)], ctx()) == []

    def test_no_owner_fails_closed(self):
        assert match(dm(), [scope(D)], ctx(owner_user_id=None)) == []
