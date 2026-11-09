"""Inbound trust rule, handle scanning and normalization (issue #2103 stage 3a)."""

from __future__ import annotations

import pytest

from backend.relay.events import RelayEvent
from backend.relay.inbound.normalize import Drop, normalize_event
from backend.relay.inbound.router import (
    KIND_IGNORE,
    KIND_INSTRUCTION,
    KIND_REPLY,
    classify,
    find_handles,
    remove_handle,
)
from backend.tests.relay_inbound_support import (
    CHANNEL,
    OTHER_DM,
    OTHER_USER,
    OWNER,
    RELAY_ID,
    SELF_DM,
    TEAM,
    message_payload,
)

HANDLES = {"my-agent": "s1", "deploy": "s2"}
THREADS = {(SELF_DM, "1800000000.000100"): {"id": "t1", "session_id": "s1", "status": "active"}}


def ev(text="", ts="1800000001.000100", **kw) -> RelayEvent:
    base = dict(relay_id=RELAY_ID, channel=SELF_DM, ts=ts, user=OWNER, text=text, channel_type="im")
    base.update(kw)
    return RelayEvent(**base)


def decide(event):
    return classify(
        event, self_dm=SELF_DM, owner=OWNER,
        find_thread=lambda c, t: THREADS.get((c, t)),
        resolve_handle=HANDLES.get,
    )


class TestTrustRule:
    def test_owner_top_level_handle_is_instruction(self):
        d = decide(ev("!my-agent run the nightly checks"))
        assert d.kind == KIND_INSTRUCTION
        assert (d.session_id, d.handle, d.text) == ("s1", "my-agent", "run the nightly checks")
        assert d.thread_ts == "1800000001.000100" and d.top_level

    @pytest.mark.parametrize("override,reason", [
        ({"user": OTHER_USER}, "not_owner"),
        ({"user": None}, "not_owner"),
        ({"channel": OTHER_DM}, "not_self_dm"),
        ({"channel": CHANNEL, "channel_type": "channel"}, "not_self_dm"),
        ({"channel_type": "mpim"}, "not_self_dm"),
        ({"channel_type": None}, "not_self_dm"),
        ({"bot_id": "B1"}, "agent_message"),
        ({"app_id": "A1"}, "agent_message"),
        ({"is_agent": True}, "agent_message"),
        ({"text": ":robot_face: [cc-agent] !my-agent hi"}, "marker"),
        ({"text": "\U0001f916 [cc-agent] !my-agent hi"}, "marker"),
        ({"text": "\U0001f916️[CC-agent] !my-agent hi"}, "marker"),
    ])
    def test_never_an_instruction(self, override, reason):
        d = decide(ev(**{"text": "!my-agent hi", **override}))
        assert d.kind == KIND_IGNORE and d.reason == reason

    def test_unknown_self_dm_or_owner_fails_closed(self):
        e = ev("!my-agent hi")
        for self_dm, owner in ((None, OWNER), (SELF_DM, None)):
            d = classify(e, self_dm=self_dm, owner=owner, find_thread=lambda c, t: None,
                         resolve_handle=HANDLES.get)
            assert d.kind == KIND_IGNORE


class TestHandles:
    def test_uppercase_matches_and_is_removed(self):
        d = decide(ev("!My-Agent do it !MY-AGENT now"))
        assert d.kind == KIND_INSTRUCTION and d.text == "do it now"

    def test_zero_handles_ignored(self):
        assert decide(ev("hello there")).reason == "no_handle"

    def test_reserved_and_angle_tokens_ignored(self):
        assert decide(ev("<!here> please <!channel>")).reason == "no_handle"
        assert decide(ev("!here and !everyone")).reason == "no_handle"
        assert find_handles("<!subteam^S123|@team> !my-agent x") == ["my-agent"]

    def test_unknown_handle_replies(self):
        d = decide(ev("!nobody do it"))
        assert (d.kind, d.reason) == (KIND_REPLY, "unknown_handle")
        assert d.thread_ts == "1800000001.000100"

    def test_two_distinct_handles_ambiguous(self):
        d = decide(ev("!my-agent and !deploy"))
        assert (d.kind, d.reason) == (KIND_REPLY, "ambiguous_handle")

    def test_same_handle_twice_is_one(self):
        assert decide(ev("!deploy a !deploy b")).kind == KIND_INSTRUCTION

    def test_empty_after_removal(self):
        d = decide(ev("!my-agent  "))
        assert (d.kind, d.reason) == (KIND_REPLY, "empty_instruction")

    def test_handle_inside_word_or_after_bang_not_matched(self):
        assert find_handles("hey!deploy") == []
        assert find_handles("!!deploy") == []

    def test_remove_handle_keeps_angle_tokens(self):
        assert remove_handle("<@U1> !deploy go", "deploy") == "<@U1> go"


class TestThreads:
    def test_reply_in_owned_thread_routes_without_handle(self):
        d = decide(ev("and then?", ts="1800000002.000100", thread_ts="1800000000.000100"))
        assert d.kind == KIND_INSTRUCTION and d.session_id == "s1"
        assert d.text == "and then?" and not d.top_level and d.thread_record_id == "t1"

    def test_other_handle_inside_owned_thread_is_plain_text(self):
        d = decide(ev("!deploy now", ts="1800000002.000100", thread_ts="1800000000.000100"))
        assert d.kind == KIND_INSTRUCTION and d.session_id == "s1" and d.text == "!deploy now"

    def test_unowned_thread_ignored(self):
        d = decide(ev("hi", ts="1800000002.000100", thread_ts="1799999999.000100"))
        assert (d.kind, d.reason) == (KIND_IGNORE, "unowned_thread")

    def test_thread_root_itself_is_top_level(self):
        d = decide(ev("!my-agent x", thread_ts="1800000001.000100"))
        assert d.top_level and d.kind == KIND_INSTRUCTION


class TestNormalize:
    def norm(self, **kw):
        text = kw.pop("text", "hi")
        return normalize_event(RELAY_ID, message_payload(text, "1800000001.000100", **kw), TEAM)

    def test_plain_message(self):
        e = self.norm()
        assert isinstance(e, RelayEvent) and e.channel == SELF_DM and not e.is_agent

    @pytest.mark.parametrize("kw,reason", [
        ({"subtype": "message_changed"}, "subtype"),
        ({"subtype": "message_deleted"}, "subtype"),
        ({"subtype": "bot_message"}, "subtype"),
        ({"subtype": "channel_join"}, "subtype"),
        ({"event_type": "reaction_added"}, "not_message"),
        ({"team": "T0OTHER"}, "wrong_workspace"),
    ])
    def test_drops(self, kw, reason):
        assert self.norm(**kw) == Drop(reason)

    def test_allowed_subtypes(self):
        assert isinstance(self.norm(subtype="file_share", files=[{"id": "F1"}]), RelayEvent)
        assert isinstance(self.norm(subtype="thread_broadcast"), RelayEvent)

    def test_agent_flags(self):
        assert self.norm(app_id="A1").is_agent
        assert self.norm(metadata={"event_type": "cc_webui_agent_message"}).is_agent
        assert not self.norm(metadata={"event_type": "other"}).is_agent

    def test_blocks_text_fallback_and_malformed(self):
        payload = message_payload("", "1800000001.000100")
        payload["event"]["blocks"] = [{"type": "rich_text", "elements": [
            {"type": "rich_text_section", "elements": [{"type": "text", "text": "!my-agent hi"}]}]}]
        assert normalize_event(RELAY_ID, payload, TEAM).text == "!my-agent hi"
        assert normalize_event(RELAY_ID, {"event": {"type": "message"}}, TEAM) == Drop("malformed")
        assert normalize_event(RELAY_ID, "x", TEAM) == Drop("malformed")  # type: ignore[arg-type]
