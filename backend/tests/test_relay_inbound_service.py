"""Inbound pipeline end to end with a fake Socket Mode transport (issue #2103 stage 3a)."""

from __future__ import annotations

import asyncio

import pytest

from backend.relay.adapter import RelayMessage
from backend.relay.inbound import constants as const
from backend.relay.inbound.service import DeliveryOutcome, comm_id_for
from backend.relay.inbound.transport import (
    RawEnvelope,
    TransportAuthError,
    TransportUnavailableError,
)
from backend.relay.safety import contains_trigger_syntax, has_agent_marker
from backend.session_manager import SessionState
from backend.tests.relay_inbound_support import (
    OTHER_USER,
    OWNER,
    RELAY_ID,
    SELF_DM,
    SESSION,
    FakeSocketTransport,
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
    return ts


class TestDelivery:
    async def test_instruction_delivered_and_thread_owned(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent run the nightly checks")
        assert len(env.sink.calls) == 1
        call = env.sink.calls[0]
        assert call["session_id"] == SESSION
        assert call["comm_id"] == comm_id_for(RELAY_ID, SELF_DM, ts)
        assert call["content"] == "run the nightly checks"
        assert f"channel={SELF_DM} thread_ts={ts}" in call["dispatch_content"]
        assert "[OWNER INSTRUCTION id=" in call["dispatch_content"]
        assert call["metadata"]["relay_inbound"]["thread_ts"] == ts
        assert env.service.store.has_active_thread(SESSION, SELF_DM, ts)
        assert t.acked == ["E1"]
        assert not env.adapter.posts  # no reply needed on success
        assert env.service.state.inflight == {}

    async def test_follow_up_in_thread_without_handle(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent start")
        await send(env, t, "and now the report", seq=2, thread_ts=ts)
        assert [c["content"] for c in env.sink.calls] == ["start", "and now the report"]
        assert env.sink.calls[1]["metadata"]["relay_inbound"]["thread_ts"] == ts

    async def test_other_handle_in_owned_thread_is_not_routed(self, env):
        env.index.rebind("s2", "deploy")
        env.sessions.add("s2")
        t = await started(env)
        ts = await send(env, t, "!my-agent start")
        await send(env, t, "!deploy now", seq=2, thread_ts=ts)
        assert [c["session_id"] for c in env.sink.calls] == [SESSION, SESSION]

    async def test_unowned_thread_and_no_handle_ignored(self, env):
        t = await started(env)
        await send(env, t, "just a note")
        await send(env, t, "reply", seq=2, thread_ts="1700000000.000100")
        assert not env.sink.calls and not env.adapter.posts

    async def test_file_count_is_noted_not_delivered(self, env):
        t = await started(env)
        await send(env, t, "!my-agent see file", subtype="file_share", files=[{"id": "F1"}])
        assert "1 file(s) attached in Slack were not delivered" in env.sink.calls[0]["dispatch_content"]


class TestRefusals:
    async def test_unknown_handle_gets_one_marked_reply(self, env):
        t = await started(env)
        ts = await send(env, t, "!nobody do something")
        assert not env.sink.calls
        [post] = env.adapter.posts
        assert post["channel"] == SELF_DM and post["thread_ts"] == ts
        assert has_agent_marker(post["text"]) and "nobody" not in post["text"]
        assert not contains_trigger_syntax(post["text"])
        assert post["metadata"]["event_type"] == "cc_webui_agent_message"

    async def test_ambiguous_and_empty(self, env):
        env.index.rebind("s2", "deploy")
        t = await started(env)
        await send(env, t, "!my-agent !deploy hi")
        await send(env, t, "!my-agent", seq=2)
        assert not env.sink.calls and len(env.adapter.posts) == 2
        assert "more than one" in env.adapter.posts[0]["text"]
        assert "empty" in env.adapter.posts[1]["text"]

    @pytest.mark.parametrize("setup,code_text", [
        (lambda e: e.sessions.infos.clear(), "no session with that handle"),  # handle self-heals away
        (lambda e: e.sessions.add(ephemeral=True), "cannot take instructions"),
        (lambda e: e.sessions.add(state=SessionState.ERROR), "error state"),
        (lambda e: e.sessions.add(relay_id=None), "no longer has this relay"),
        (lambda e: e.manager.tokens.update(user=False), "no usable user token"),
    ])
    async def test_session_cannot_take_instruction(self, env, setup, code_text):
        t = await started(env)
        setup(env)
        await send(env, t, "!my-agent do it")
        assert not env.sink.calls
        [post] = env.adapter.posts
        assert code_text in post["text"]
        assert not env.service.store.find_thread(SELF_DM, env.clock.ts(seq=1))

    async def test_sink_failure_replies_once(self, env):
        env.sink.outcome = DeliveryOutcome("failed", "x")
        t = await started(env)
        await send(env, t, "!my-agent do it")
        [post] = env.adapter.posts
        assert "could not accept" in post["text"]

    async def test_sink_exception_is_contained(self, env):
        env.sink.raise_error = True
        t = await started(env)
        await send(env, t, "!my-agent do it")
        assert len(env.adapter.posts) == 1
        assert env.service.state.inflight == {}

    async def test_reply_failure_never_blocks(self, env):
        env.adapter.fail_post = True
        t = await started(env)
        await send(env, t, "!nobody x")
        await send(env, t, "!my-agent ok", seq=2)
        assert len(env.sink.calls) == 1


class TestNeverInstructions:
    @pytest.mark.parametrize("kw", [
        {"user": OTHER_USER},
        {"channel": "D0OTHER0001"},
        {"channel": "C0CHAN0001", "channel_type": "channel"},
        {"bot_id": "B1"},
        {"app_id": "A1"},
        {"metadata": {"event_type": "cc_webui_agent_message"}},
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"team": "T0OTHER"},
        {"channel_type": "mpim"},
    ])
    async def test_dropped_before_delivery(self, env, kw):
        t = await started(env)
        await send(env, t, "!my-agent attack", **kw)
        assert not env.sink.calls and not env.adapter.posts

    @pytest.mark.parametrize("marker", [":robot_face: [cc-agent]", "\U0001f916 [cc-agent]"])
    async def test_typed_marker_is_untrusted(self, env, marker):
        t = await started(env)
        await send(env, t, f"{marker} !my-agent attack")
        assert not env.sink.calls and not env.adapter.posts

    async def test_edit_after_the_fact_does_nothing(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent first")
        edit = message_payload("", env.clock.ts(seq=9), subtype="message_changed")
        edit["event"]["message"] = {"text": "!my-agent edited", "ts": ts, "user": OWNER}
        await t.emit(envelope(edit, "E9"))
        await settle(env)
        assert len(env.sink.calls) == 1

    async def test_own_reply_does_not_loop(self, env):
        t = await started(env)
        await send(env, t, "!nobody x")
        [post] = env.adapter.posts
        # the relay's own marked reply comes back as an event: bot/app id, metadata and marker
        await send(env, t, post["text"], seq=2, app_id="A0APP", metadata=post["metadata"])
        await send(env, t, post["text"], seq=3)  # even with ids stripped the marker demotes it
        assert len(env.adapter.posts) == 1 and not env.sink.calls


class TestDedupe:
    async def test_message_and_app_mention_twin_deliver_once(self, env):
        t = await started(env)
        ts = env.clock.ts()
        payload = message_payload("!my-agent once", ts)
        await t.emit(envelope(payload, "E1"))
        twin = message_payload("!my-agent once", ts, event_type="app_mention")
        await t.emit(envelope(twin, "E2"))
        await t.emit(envelope(payload, "E1", retry=1))
        await settle(env)
        assert len(env.sink.calls) == 1
        assert t.acked == ["E1", "E2", "E1"]

    async def test_replay_after_restart_delivers_once(self, tmp_path):
        a = make_inbound(tmp_path)
        t = await started(a)
        ts = await send(a, t, "!my-agent once")
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        t2 = await started(b)
        await t2.emit(envelope(message_payload("!my-agent once", ts), "E1", retry=2))
        await settle(b)
        assert not b.sink.calls
        await b.service.stop()


class TestAckAndRecovery:
    async def test_accepted_event_is_durable_before_ack(self, env):
        gate = asyncio.Event()

        async def slow_sink(session_id, **kw):
            await gate.wait()
            return DeliveryOutcome("delivered")

        env.service._sink = slow_sink
        t = await started(env)
        ts = env.clock.ts()
        await t.emit(envelope(message_payload("!my-agent hi", ts), "E1"))
        assert t.acked == ["E1"]
        state_file = env.root / "inbound_state.json"
        assert f"{SELF_DM}:{ts}" in state_file.read_text()  # in-flight persisted at ack time
        gate.set()
        await settle(env)
        assert env.service.state.inflight == {}

    async def test_unfinished_instruction_rerun_at_startup_exactly_once(self, tmp_path):
        a = make_inbound(tmp_path)
        gate = asyncio.Event()

        async def hang(session_id, **kw):
            await gate.wait()
            return DeliveryOutcome("delivered")

        a.service._sink = hang
        t = await started(a)
        ts = a.clock.ts()
        await t.emit(envelope(message_payload("!my-agent hi", ts), "E1"))
        await settle(a, rounds=20)
        await a.service.stop()  # crash window: acked, not yet enqueued
        b = make_inbound(tmp_path, clock=a.clock)
        await started(b)
        await settle(b)
        assert [c["comm_id"] for c in b.sink.calls] == [comm_id_for(RELAY_ID, SELF_DM, ts)]
        await b.service.stop()
        c = make_inbound(tmp_path, clock=a.clock)
        await started(c)
        await settle(c)
        assert not c.sink.calls  # completed: not repeated

    async def test_crash_after_enqueue_before_removal_does_not_duplicate(self, tmp_path):
        a = make_inbound(tmp_path)
        t = await started(a)
        ts = await send(a, t, "!my-agent hi")
        # simulate: delivered but the in-flight removal never reached disk
        a.service.state.inflight[f"{SELF_DM}:{ts}"] = {
            "key": f"{SELF_DM}:{ts}", "reply_done": False, "accepted_at": 0,
            "event": {"relay_id": RELAY_ID, "channel": SELF_DM, "ts": ts, "user": OWNER,
                      "text": "!my-agent hi", "channel_type": "im"},
            "decision": {"kind": "instruction", "session_id": SESSION, "handle": "my-agent",
                         "text": "hi", "thread_ts": ts, "top_level": True},
        }
        await a.service.state.flush()
        await a.service.stop()
        b = make_inbound(tmp_path, clock=a.clock)
        b.sink.seen_comm_ids = set(a.sink.seen_comm_ids)  # the ledger still holds the comm
        await started(b)
        await settle(b)
        assert [c["comm_id"] for c in b.sink.calls] == [comm_id_for(RELAY_ID, SELF_DM, ts)]
        assert not b.adapter.posts


class TestInstructionWindow:
    async def test_late_instruction_not_executed_one_reply(self, env):
        t = await started(env)
        old_ts = env.clock.ts(offset=-(const.INSTRUCTION_MAX_AGE_SECONDS + 60))
        payload = message_payload("!my-agent too old", old_ts)
        await t.emit(envelope(payload, "E1"))
        await settle(env)
        await t.emit(envelope(payload, "E1", retry=1))
        await settle(env)
        assert not env.sink.calls
        [post] = env.adapter.posts
        assert "received late" in post["text"] and post["thread_ts"] == old_ts
        await env.service.stop()
        late = [r for r in audit_events(env, "inbound_instruction") if r["outcome"] == "late"]
        assert len(late) == 1

    async def test_within_window_is_delivered(self, env):
        t = await started(env)
        ts = env.clock.ts(offset=-(const.INSTRUCTION_MAX_AGE_SECONDS - 30))
        await t.emit(envelope(message_payload("!my-agent ok", ts), "E1"))
        await settle(env)
        assert len(env.sink.calls) == 1

    async def test_first_start_sets_cursor_to_now(self, env):
        await started(env)
        assert float(env.service.state.cursors[SELF_DM]) == pytest.approx(env.clock.now(), abs=1)


class TestBackfill:
    def msg(self, env, text, offset, **kw):
        return RelayMessage(ts=env.clock.ts(offset=offset), text=text, user=OWNER, **kw)

    async def test_hello_fetches_history_since_cursor(self, env):
        t = await started(env)
        cursor = env.clock.ts(offset=-120)
        env.service.state.cursors[SELF_DM] = cursor
        env.adapter.history = [self.msg(env, "!my-agent while offline", -60)]
        await t.hello()
        await settle(env, until=lambda: bool(env.sink.calls))
        assert env.sink.calls[0]["content"] == "while offline"
        assert env.adapter.history_calls[0]["oldest"] == cursor

    async def test_first_start_has_no_backfill(self, env):
        t = await started(env)
        env.adapter.history = [self.msg(env, "!my-agent old", -3000)]
        await t.hello()
        await settle(env, rounds=50)
        assert not env.sink.calls

    async def test_old_items_get_one_late_reply_not_repeated(self, env):
        t = await started(env)
        env.service.state.cursors[SELF_DM] = env.clock.ts(offset=-5000)
        env.adapter.history = [self.msg(env, "!my-agent ancient", -3000)]
        await t.hello()
        await settle(env, until=lambda: bool(env.adapter.posts))
        await t.hello()
        await settle(env, rounds=60)
        assert not env.sink.calls and len(env.adapter.posts) == 1
        assert "received late" in env.adapter.posts[0]["text"]

    async def test_own_replies_in_history_are_ignored(self, env):
        t = await started(env)
        env.service.state.cursors[SELF_DM] = env.clock.ts(offset=-120)
        env.adapter.history = [self.msg(env, ":robot_face: [cc-agent] hi", -60, app_id="A1")]
        await t.hello()
        await settle(env, rounds=60)
        assert not env.sink.calls and not env.adapter.posts

    async def test_owned_thread_replies_are_backfilled(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        reply = self.msg(env, "follow up while down", 30, thread_ts=ts)
        env.adapter.threads[ts] = [RelayMessage(ts=ts, text="!my-agent go", user=OWNER), reply]
        env.clock.advance(60)
        await t.hello()
        await settle(env, until=lambda: len(env.sink.calls) == 2)
        assert env.sink.calls[1]["content"] == "follow up while down"

    async def test_page_cap_is_audited(self, env):
        t = await started(env)
        env.service.state.cursors[SELF_DM] = env.clock.ts(offset=-120)
        env.adapter.history_next = {None: "c1", "c1": "c2", "c2": "c3"}
        await t.hello()
        await settle(env, rounds=80)
        await asyncio.sleep(0.05)
        await env.service.stop()
        assert any(r["code"] == "backfill_truncated" for r in audit_events(env))


class TestRateLimit:
    async def test_burst_over_limit_dropped_without_reply(self, env):
        t = await started(env)
        for i in range(const.RELAY_BURST + 5):
            await send(env, t, f"!my-agent n{i}", seq=i)
        assert len(env.sink.calls) == const.RELAY_BURST
        assert not env.adapter.posts  # replying would amplify
        await asyncio.sleep(0.05)
        await env.service.stop()
        limited = [r for r in audit_events(env, "inbound_instruction") if r["outcome"] == "rate_limited"]
        assert len(limited) == 1

    async def test_thread_bucket_is_per_thread(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        for i in range(const.THREAD_BURST + 3):
            env.clock.advance(0.01)
            await send(env, t, f"more {i}", seq=2 + i, thread_ts=ts)
        thread_calls = len(env.sink.calls)
        assert thread_calls <= const.THREAD_BURST + 1


class TestSessionHooks:
    async def test_delete_drops_records_and_inflight(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        await env.service.on_session_deleted(SESSION)
        assert env.service.store.find_thread(SELF_DM, ts) is None
        await send(env, t, "late follow-up", seq=2, thread_ts=ts)
        assert len(env.sink.calls) == 1 and not env.adapter.posts  # unowned now: ignored

    async def test_dispose_suspends_followup_gets_one_reply_start_restores(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        await env.service.on_session_disposed(SESSION)
        assert not env.service.has_thread_grant(SESSION, SELF_DM, ts)
        await send(env, t, "one", seq=2, thread_ts=ts)
        await send(env, t, "two", seq=3, thread_ts=ts)
        assert len(env.sink.calls) == 1
        [post] = env.adapter.posts
        assert "no longer active" in post["text"]
        await env.service.on_session_started(SESSION)
        assert env.service.has_thread_grant(SESSION, SELF_DM, ts)
        await send(env, t, "three", seq=4, thread_ts=ts)
        assert len(env.sink.calls) == 2

    async def test_grant_off_when_inbound_disabled(self, env):
        t = await started(env)
        ts = await send(env, t, "!my-agent go")
        env.manager.relays[RELAY_ID].inbound_enabled = False
        assert not env.service.has_thread_grant(SESSION, SELF_DM, ts)


class TestSupervisor:
    async def test_connected_status_and_hello(self, env):
        t = await started(env)
        assert env.service.status == const.STATUS_CONNECTED
        await t.hello()
        assert env.service.status == const.STATUS_CONNECTED

    async def test_disabled_reasons(self, tmp_path):
        for mutate, reason in (
            (lambda e: setattr(e.manager.relays[RELAY_ID], "inbound_enabled", False), "disabled_by_owner"),
            (lambda e: e.manager.tokens.update(app=False), "no_app_token"),
            (lambda e: e.manager.tokens.update(user=False), "no_user_token"),
            (lambda e: setattr(e.manager.relays[RELAY_ID], "owner_user_id", None), "no_owner"),
        ):
            e = make_inbound(tmp_path / reason)
            mutate(e)
            await e.service.start()
            await settle(e, until=lambda e=e: e.service.status == const.STATUS_DISABLED)
            assert (e.service.status, e.service.detail) == (const.STATUS_DISABLED, reason)
            assert not e.transports
            await e.service.stop()

    async def test_reconnect_backoff_with_jitter_and_min_spacing(self, tmp_path):
        transports: list[FakeSocketTransport] = []

        def factory(relay_id):
            t = FakeSocketTransport(connect_error=TransportUnavailableError("network_error"))
            transports.append(t)
            return t

        e = make_inbound(tmp_path, factory=factory)
        await e.service.start()
        await settle(e, until=lambda: len(transports) >= 5)
        assert len(transports) >= 5
        assert e.service.status == const.STATUS_BACKOFF
        # rng()==1.0 -> full backoff: 1, 2, 4, 8 ... but never less than the 5 s connection spacing
        spacing = [d for d in e.delays if d >= const.MIN_CONNECT_SPACING_SECONDS - 1e-9]
        assert all(d <= const.BACKOFF_MAX_SECONDS + const.MIN_CONNECT_SPACING_SECONDS for d in e.delays)
        assert spacing and e.delays[0] == 1.0
        await e.service.stop()

    async def test_backoff_resets_after_stable_connection(self, tmp_path):
        transports: list[FakeSocketTransport] = []
        e = None

        def factory(relay_id):
            t = FakeSocketTransport()
            transports.append(t)
            return t

        e = make_inbound(tmp_path, factory=factory)
        await e.service.start()
        await settle(e, until=lambda: bool(transports) and transports[0].connected)
        e.clock.advance(const.STABLE_CONNECTION_SECONDS + 1)
        transports[0].drop()
        await settle(e, until=lambda: len(transports) >= 2)
        assert e.delays[-1] == const.BACKOFF_BASE_SECONDS  # reset, not doubled
        await e.service.stop()

    async def test_auth_failure_stops_retrying(self, tmp_path):
        transports: list[FakeSocketTransport] = []

        def factory(relay_id):
            t = FakeSocketTransport(connect_error=TransportAuthError("token_revoked"))
            transports.append(t)
            return t

        e = make_inbound(tmp_path, factory=factory)
        await e.service.start()
        await settle(e, until=lambda: e.service.status == const.STATUS_AUTH_FAILED)
        for _ in range(100):
            await asyncio.sleep(0)
        assert len(transports) == 1  # no retry storm
        assert (e.service.status, e.service.detail) == (const.STATUS_AUTH_FAILED, "token_revoked")
        await e.service.stop()

    async def test_auth_failure_after_connect(self, env):
        t = await started(env)
        t.auth_failed()
        await settle(env, until=lambda: env.service.status == const.STATUS_AUTH_FAILED)
        assert len(env.transports) == 1

    async def test_missing_library_is_a_status_not_a_crash(self, tmp_path):
        e = make_inbound(
            tmp_path,
            factory=lambda rid: FakeSocketTransport(connect_error=TransportUnavailableError("slack_sdk_missing")),
        )
        await e.service.start()
        await settle(e, until=lambda: e.service.status == const.STATUS_TRANSPORT_UNAVAILABLE)
        assert e.service.status == const.STATUS_TRANSPORT_UNAVAILABLE
        await e.service.stop()

    async def test_refresh_envelope_types_other_than_events_ignored(self, env):
        t = await started(env)
        await t.emit(RawEnvelope("X1", "other", {}))
        assert not env.sink.calls


class TestAuditAndDropSummary:
    async def test_events_and_drop_counts(self, env):
        t = await started(env)
        await send(env, t, "!my-agent go")
        await send(env, t, "chatter", seq=2, user=OTHER_USER)
        await send(env, t, "chatter 2", seq=3, user=OTHER_USER)
        await env.service._flush_drop_summary()
        await asyncio.sleep(0.05)
        await env.service.stop()
        delivered = [r for r in audit_events(env, "inbound_instruction") if r["outcome"] == "delivered"]
        assert delivered and delivered[0]["session_id"] == SESSION and delivered[0]["text_sha256"]
        [summary] = audit_events(env, "inbound_drop_summary")
        assert summary["reason_counts"] == {"not_owner": 2}
        assert any(r["inbound_status"] == "connected" for r in audit_events(env, "inbound_connection"))

    async def test_no_token_material_in_audit_or_status(self, env):
        t = await started(env)
        await send(env, t, "!my-agent go")
        await env.service.stop()
        text = (env.root / "audit.jsonl").read_text()
        assert "xapp-" not in text and "xoxp-" not in text
