"""Received reaction on dispatched self-DM instructions + shortened dispatch text (issue #2364)."""

from __future__ import annotations

import asyncio

import pytest

from backend.relay.adapter import RelayMessage
from backend.relay.inbound import constants as const
from backend.relay.inbound.dispatch import format_instruction
from backend.relay.inbound.service import DeliveryOutcome
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import (
    OTHER_USER,
    OWNER,
    RELAY_ID,
    SELF_DM,
    SESSION,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
    settle,
    started,
)


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    yield e
    await e.service.stop()


async def send(env, transport, text, *, seq=1, eid=None, **kw):
    ts = env.clock.ts(seq=seq)
    await transport.emit(envelope(message_payload(text, ts, **kw), eid or f"E{seq}"))
    await settle(env)
    await asyncio.sleep(0.02)  # the reaction is a background task
    return ts


def adds(env):
    return [r for r in env.adapter.reactions if r[0] == "add"]


class TestReactionAdded:
    async def test_constant(self):
        assert const.INBOUND_ACK_REACTION == "flying_saucer"

    async def test_top_level_instruction_gets_reaction_ac1_t1(self, env):  # AC1, AC4, T1
        t = await started(env)
        ts = await send(env, t, "!my-agent ping")
        assert env.adapter.reactions == [("add", SELF_DM, ts, "flying_saucer")]
        assert len(env.sink.calls) == 1
        await env.service.stop()
        [rec] = audit_events(env, "inbound_ack")
        assert rec["outcome"] == "ok" and rec["channel_id"] == SELF_DM and rec["message_ts"] == ts
        assert rec["session_id"] == SESSION and rec["handle"] == "my-agent"

    async def test_thread_follow_up_gets_reaction_ac2_t2(self, env):  # AC2, T2
        t = await started(env)
        ts = await send(env, t, "!my-agent start")
        ts2 = await send(env, t, "and the report", seq=2, thread_ts=ts)
        assert [r[2] for r in adds(env)] == [ts, ts2]
        assert len(env.sink.calls) == 2

    async def test_stopped_or_busy_session_still_reacts_ac4(self, env):
        env.sessions.add(state=SessionState.TERMINATED)
        t = await started(env)
        ts = await send(env, t, "!my-agent wake up")
        assert [r[2] for r in adds(env)] == [ts]


class TestNoReaction:
    async def test_refusals_and_ignored_ac3_t3(self, env):  # AC3, T3
        env.index.rebind("s2", "deploy")
        t = await started(env)
        await send(env, t, "!nobody hi", seq=1)  # unknown handle
        await send(env, t, "!my-agent !deploy hi", seq=2)  # ambiguous
        await send(env, t, "!my-agent", seq=3)  # empty
        await send(env, t, ":robot_face: [cc-agent] !my-agent x", seq=4)  # typed marker
        await send(env, t, "just a note", seq=5)  # no handle
        edit = message_payload("", env.clock.ts(seq=6), subtype="message_changed")
        edit["event"]["message"] = {"text": "!my-agent edited", "ts": env.clock.ts(seq=1), "user": OWNER}
        await t.emit(envelope(edit, "E6"))
        await settle(env)
        assert env.adapter.reactions == []
        assert not env.sink.calls

    @pytest.mark.parametrize("kw", [
        {"user": OTHER_USER}, {"channel": "D0OTHER0001"},
        {"channel": "C0CHAN0001", "channel_type": "channel"}, {"bot_id": "B1"}, {"app_id": "A1"},
        {"team": "T0OTHER"}, {"channel_type": "mpim"},
    ])
    async def test_other_conversations_t4(self, env, kw):  # T4
        t = await started(env)
        await send(env, t, "!my-agent attack", **kw)
        assert env.adapter.reactions == []

    @pytest.mark.parametrize("setup", [
        lambda e: e.sessions.infos.clear(),
        lambda e: e.sessions.add(ephemeral=True),
        lambda e: e.sessions.add(state=SessionState.ERROR),
        lambda e: e.sessions.add(relay_id=None),
        lambda e: e.manager.tokens.update(user=False),
    ])
    async def test_session_unavailable(self, env, setup):
        t = await started(env)
        setup(env)
        await send(env, t, "!my-agent do it")
        assert env.adapter.reactions == []
        assert len(env.adapter.posts) == 1  # the refusal reply still goes out

    async def test_late_instruction(self, env):
        t = await started(env)
        old = env.clock.ts(offset=-(const.INSTRUCTION_MAX_AGE_SECONDS + 60))
        await t.emit(envelope(message_payload("!my-agent old", old), "E1"))
        await settle(env)
        assert env.adapter.reactions == [] and len(env.adapter.posts) == 1

    async def test_rate_limited_gets_none(self, env):
        t = await started(env)
        for i in range(const.RELAY_BURST + 4):
            await send(env, t, f"!my-agent n{i}", seq=i)
        assert len(env.sink.calls) == const.RELAY_BURST
        assert len(adds(env)) == const.RELAY_BURST

    async def test_duplicate_delivery_reacts_once_t6(self, env):  # AC6, T6
        t = await started(env)
        ts = env.clock.ts()
        payload = message_payload("!my-agent once", ts)
        await t.emit(envelope(payload, "E1"))
        await t.emit(envelope(message_payload("!my-agent once", ts, event_type="app_mention"), "E2"))
        await t.emit(envelope(payload, "E1", retry=1))
        await settle(env)
        await asyncio.sleep(0.02)
        assert len(adds(env)) == 1

    async def test_content_branch_never_reacts(self, env):
        """Future monitored content (3b) must not call the ack: the guard rejects anything that is
        not a self-DM instruction decision."""
        await started(env)
        from backend.relay.events import RelayEvent

        base = {"relay_id": RELAY_ID, "ts": env.clock.ts(), "user": OTHER_USER, "text": "x",
                "channel_type": "channel"}
        for kind, channel in (("content", "C0CHAN0001"), ("content", SELF_DM),
                              ("instruction", "C0CHAN0001"), ("reply", SELF_DM)):
            event = RelayEvent.from_dict({**base, "channel": channel})
            entry = {"key": f"{channel}:{base['ts']}", "event": event.to_dict()}
            await env.service._ack_received(entry, event, {"kind": kind, "session_id": SESSION})
            assert "acked" not in entry
        await asyncio.sleep(0.02)
        assert env.adapter.reactions == []


class TestFailuresNeverBlock:
    async def test_hanging_reaction_does_not_delay_delivery_ac5(self, env):
        env.adapter.react_hang = True
        t = await started(env)
        ts = env.clock.ts()
        await t.emit(envelope(message_payload("!my-agent go", ts), "E1"))
        await settle(env, until=lambda: bool(env.sink.calls))
        await asyncio.sleep(0.05)
        assert len(env.sink.calls) == 1  # enqueued while the reaction call is still hanging
        assert len(adds(env)) == 1

    @pytest.mark.parametrize("code", ["message_not_found", "missing_scope", "ratelimited", "internal_error"])
    async def test_slack_error_is_audited_t5(self, env, code):  # AC5, AC10, T5
        env.adapter.react_error = code
        t = await started(env)
        await send(env, t, "!my-agent go")
        assert len(env.sink.calls) == 1 and len(adds(env)) == 1  # no retry
        await env.service.stop()
        [rec] = audit_events(env, "inbound_ack")
        assert rec["outcome"] == "error" and rec["code"] == code

    async def test_error_code_is_sanitised(self, env):
        env.adapter.react_error = "Bad Code!<script>"
        t = await started(env)
        await send(env, t, "!my-agent go")
        await env.service.stop()
        assert audit_events(env, "inbound_ack")[0]["code"] == "unknown_error"

    async def test_already_reacted_is_success(self, env):
        env.adapter.react_error = "already_reacted"
        t = await started(env)
        await send(env, t, "!my-agent go")
        await env.service.stop()
        [rec] = audit_events(env, "inbound_ack")
        assert rec["outcome"] == "ok" and rec["code"] == "already_reacted"

    async def test_timeout_is_audited(self, env, monkeypatch):
        monkeypatch.setattr(const, "ACK_TIMEOUT_SECONDS", 0.05)
        env.adapter.react_hang = True
        t = await started(env)
        await send(env, t, "!my-agent go")
        await asyncio.sleep(0.15)
        await env.service.stop()
        assert audit_events(env, "inbound_ack")[0]["code"] == "timeout"


class TestRetraction:
    async def test_delivery_failure_removes_reaction(self, env):
        env.sink.outcome = DeliveryOutcome("failed", "x")
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        assert env.adapter.reactions == [("add", SELF_DM, ts, "flying_saucer"), ("remove", SELF_DM, ts, "flying_saucer")]
        await env.service.stop()
        assert [r["outcome"] for r in audit_events(env, "inbound_ack")] == ["ok", "removed"]
        assert len(env.adapter.posts) == 1  # the refusal reply is unchanged

    async def test_sink_exception_removes_reaction(self, env):
        env.sink.raise_error = True
        t = await started(env)
        await send(env, t, "!my-agent go")
        assert [r[0] for r in env.adapter.reactions] == ["add", "remove"]

    async def test_failed_add_is_not_followed_by_remove(self, env):
        env.adapter.react_error = "message_not_found"
        env.sink.outcome = DeliveryOutcome("failed", "x")
        t = await started(env)
        await send(env, t, "!my-agent go")
        assert [r[0] for r in env.adapter.reactions] == ["add"]

    async def test_remove_failure_is_audited_and_contained(self, env):
        env.adapter.unreact_error = "message_not_found"
        env.sink.outcome = DeliveryOutcome("failed", "x")
        t = await started(env)
        await send(env, t, "!my-agent go")
        await env.service.stop()
        last = audit_events(env, "inbound_ack")[-1]
        assert last["outcome"] == "error" and last["code"] == "message_not_found"


class TestRetractionEdges:
    async def test_error_after_successful_delivery_keeps_reaction(self, env, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("store down")

        monkeypatch.setattr(env.service.store, "touch_thread", boom)
        t = await started(env)
        await send(env, t, "!my-agent go")
        assert len(env.sink.calls) == 1
        assert [r[0] for r in env.adapter.reactions] == ["add"]  # delivered: never retracted

    async def test_restart_rerun_failure_removes_only_our_reaction(self, tmp_path):
        a = make_inbound(tmp_path)
        ts = a.clock.ts()
        await started(a)
        base = TestAtMostOnce()._entry(ts, acked=True)
        a.service.state.inflight[base["key"]] = {**base, "ack_added": True}
        await a.service.state.flush()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        b.sink.outcome = DeliveryOutcome("failed", "x")
        await started(b)
        await settle(b)
        assert [r[0] for r in b.adapter.reactions] == ["remove"]
        await b.service.stop()

    async def test_restart_rerun_failure_without_our_add_removes_nothing(self, tmp_path):
        a = make_inbound(tmp_path)
        ts = a.clock.ts()
        await started(a)
        base = TestAtMostOnce()._entry(ts, acked=True)  # add never confirmed / already present
        a.service.state.inflight[base["key"]] = base
        await a.service.state.flush()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        b.sink.outcome = DeliveryOutcome("failed", "x")
        await started(b)
        await settle(b)
        assert b.adapter.reactions == []
        await b.service.stop()


class TestAtMostOnce:
    def _entry(self, ts, **extra):
        return {
            "key": f"{SELF_DM}:{ts}", "reply_done": False, "accepted_at": 0, **extra,
            "event": {"relay_id": RELAY_ID, "channel": SELF_DM, "ts": ts, "user": OWNER,
                      "text": "!my-agent hi", "channel_type": "im"},
            "decision": {"kind": "instruction", "session_id": SESSION, "handle": "my-agent",
                         "text": "hi", "thread_ts": ts, "top_level": True},
        }

    async def test_inflight_rerun_does_not_react_again(self, tmp_path):
        a = make_inbound(tmp_path)
        ts = a.clock.ts()
        await started(a)
        a.service.state.inflight[f"{SELF_DM}:{ts}"] = self._entry(ts, acked=True)  # crashed pre-enqueue
        await a.service.state.flush()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        await started(b)
        await settle(b)
        await asyncio.sleep(0.02)
        assert len(b.sink.calls) == 1  # delivered
        assert b.adapter.reactions == []  # the persisted flag: no second (or re-added) reaction
        await b.service.stop()

    async def test_unacked_inflight_rerun_reacts_once(self, tmp_path):
        a = make_inbound(tmp_path)
        ts = a.clock.ts()
        await started(a)
        a.service.state.inflight[f"{SELF_DM}:{ts}"] = self._entry(ts)
        await a.service.state.flush()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        await started(b)
        await settle(b)
        await asyncio.sleep(0.02)
        assert len(adds(b)) == 1
        await b.service.stop()

    async def test_backfill_replay_after_restart_does_not_react_again(self, tmp_path):
        a = make_inbound(tmp_path)
        t = await started(a)
        ts = await send(a, t, "!my-agent once")
        assert len(adds(a)) == 1
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        b.adapter.history = [RelayMessage(
            ts=ts, user=OWNER, text="!my-agent once", channel=SELF_DM, thread_ts=None,
        )]
        t2 = await started(b)
        await t2.emit(envelope(message_payload("!my-agent once", ts), "E1", retry=3))
        await t2.hello()
        await settle(b)
        await asyncio.sleep(0.02)
        assert b.adapter.reactions == [] and not b.sink.calls
        await b.service.stop()


class TestDispatchText:
    def test_shortened_block_ac8_t7(self):  # AC8, T7
        text = format_instruction("Slack", SELF_DM, "1.5", "do it", nonce="abc")
        tail = text.split("---\n", 1)[1]
        assert tail == (
            f"Slack thread: channel={SELF_DM} thread_ts=1.5. Answer with the relay `reply` tool "
            "using exactly this channel and thread_ts; replies in this thread need no approval."
        )
        assert "Further messages" not in text and "exclamation" not in text
        assert text.startswith("📥 Slack instruction from the owner")
        assert "[OWNER INSTRUCTION id=abc" in text
