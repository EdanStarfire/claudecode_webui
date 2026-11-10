"""Issue #2103 outbound-1b: search scoping, audit log, permission bridge/hook."""

import json
from types import SimpleNamespace

import pytest

from backend.permission_service import PermissionService
from backend.relay.adapter import RelayMessage
from backend.relay.audit import RelayAuditLog
from backend.relay.safety import strip_trigger_syntax
from backend.tests.relay_support import (
    CH_ASK,
    CH_AUTO,
    CH_BLOCKED,
    CH_RO,
    CH_UNLISTED,
    make_env,
)

TS = "1700000000.000100"


def msg(channel, ts, text="hit"):
    return RelayMessage(ts=ts, text=text, channel=channel, user="U1", permalink=f"https://x/{ts}")


# ---------------------------------------------------------------------------- search


async def test_search_filters_and_post_filters(tmp_path):
    env = make_env(tmp_path)
    env.adapter.search_results = [
        msg(CH_AUTO, "1.1"), msg(CH_BLOCKED, "1.2", "secret"), msg(CH_UNLISTED, "1.3", "unl"),
        msg(CH_RO, "1.4"), msg("C0UNKNOWN01", "1.5"),
    ]
    result = await env.tools.handle_search("s1", {"query": "hit"})
    call = next(c for c in env.adapter.calls if c[0] == "search")
    assert set(call[2]) == {CH_AUTO, CH_ASK, CH_RO, "D0SELF0001"}  # no blocked, no unlisted
    body = result["content"][0]["text"]
    assert "secret" not in body and "unl" not in body
    assert {m["channel"] for m in json.loads(body.split("\n", 1)[1].rsplit("\n", 1)[0])} == {
        CH_AUTO, CH_RO
    }
    assert "excluded" in result["content"][1]["text"]  # unlisted note


async def test_search_empty_readable_set_errors(tmp_path):
    env = make_env(tmp_path, policies={CH_BLOCKED: "blocked"})
    result = await env.tools.handle_search("s1", {"query": "x"})
    assert result["is_error"]
    assert json.loads(result["content"][1]["text"])["code"] == "no_readable_channels"
    assert all(c[0] != "search" for c in env.adapter.calls)


async def test_search_chunks_filters_and_dedupes(tmp_path):
    policies = {f"C0CHAN{i:04d}": "auto" for i in range(23)}
    env = make_env(tmp_path, policies=policies)
    dup = msg("C0CHAN0001", "9.9")
    env.adapter.search_results = [dup]
    result = await env.tools.handle_search("s1", {"query": "x", "limit": 50})
    searches = [c for c in env.adapter.calls if c[0] == "search"]
    assert [len(c[2]) for c in searches] == [10, 10, 3]
    body = result["content"][0]["text"]
    assert body.count('"ts": "9.9"') == 1  # same hit from three chunks, one result


async def test_search_truncates_to_limit_and_text(tmp_path):
    env = make_env(tmp_path)
    env.adapter.search_results = [msg(CH_AUTO, f"1.{i}", "y" * 900) for i in range(5)]
    result = await env.tools.handle_search("s1", {"query": "x", "limit": 2})
    body = result["content"][0]["text"]
    assert body.count('"ts"') == 2 and "y" * 501 not in body


# ----------------------------------------------------------------------------- audit


async def test_audit_records_writes_and_denials_without_secrets(tmp_path):
    env = make_env(tmp_path)
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "hello world"})
    await env.tools.handle_post("s1", "post", {"channel": CH_RO, "text": "nope"})
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "!deploy"})
    await env.tools.handle_mark_read("s1", {"channel": CH_AUTO, "ts": TS})
    await env.tools.handle_react("s1", {"channel": CH_AUTO, "ts": TS, "name": "tada"})
    await env.tools.handle_read_thread("s1", {"channel": CH_AUTO, "thread_ts": TS})  # not audited
    recs = await env.audit.query("relay1")
    assert [(r["tool"], r["outcome"], r["code"]) for r in reversed(recs)] == [
        ("post", "ok", None), ("post", "denied", "send_not_permitted"),
        ("post", "denied", "trigger_syntax_refused"), ("mark_read", "ok", None),
        ("react", "ok", None),
    ]
    ok = recs[-1]
    assert ok["session_id"] == "s1" and ok["handle"] == "my-agent"
    assert ok["session_name"] == "My Session" and ok["channel_id"] == CH_AUTO
    assert ok["message_ts"] == "1700000000.000100" and ok["approval"] == "auto"
    assert len(ok["text_sha256"]) == 64 and ok["text_preview"] == "hello world"
    raw = (tmp_path / "relays" / "relay1" / "audit.jsonl").read_text()
    assert "xox" not in raw


async def test_audit_text_preview_is_bounded(tmp_path):
    env = make_env(tmp_path)
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "z" * 1000})
    assert len((await env.audit.query("relay1"))[0]["text_preview"]) == 200


async def test_audit_rotation_and_query_filters(tmp_path):
    audit = RelayAuditLog(tmp_path, max_bytes=400, backups=2)
    for i in range(12):
        await audit.record(
            "relay1", session_id="a" if i % 2 else "b", channel_id=f"C{i}",
            ts=f"2026-01-01T00:00:{i:02d}+00:00", tool="post", outcome="ok",
        )
    d = tmp_path / "relays" / "relay1"
    assert (d / "audit.jsonl.1").exists() and (d / "audit.jsonl.2").exists()
    assert not (d / "audit.jsonl.3").exists()
    recs = await audit.query("relay1", limit=100)
    assert recs[0]["channel_id"] == "C11"  # newest first across rotated files
    assert {r["session_id"] for r in await audit.query("relay1", session_id="a")} == {"a"}
    assert [r["channel_id"] for r in await audit.query("relay1", channel="C11")] == ["C11"]
    older = await audit.query("relay1", before="2026-01-01T00:00:11+00:00", limit=1)
    assert older[0]["channel_id"] == "C10"


async def test_audit_rejects_path_traversal_relay_id(tmp_path):
    audit = RelayAuditLog(tmp_path)
    await audit.record("../evil", tool="post")  # swallowed, never written
    assert not (tmp_path / "evil").exists()
    with pytest.raises(ValueError):
        await audit.query("../evil")


# ------------------------------------------------------------------- footer sanitizer


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("Fix !deploy", "Fix deploy"), ("Done! ok", "Done! ok"), ("Hello!", "Hello!"),
     ("！deploy", "deploy"), ("a !x-y z", "a x-y z"), ("a!!deploy", "a!!deploy")],
)
def test_strip_trigger_syntax(raw, clean):
    assert strip_trigger_syntax(raw) == clean


# ------------------------------------------------------------------- permission bridge


async def test_pre_hook_matrix(tmp_path):
    env = make_env(tmp_path)
    hook = env.bridge.pre_hook
    assert (await hook("s1", "mcp__relay_slack__read_thread", {"channel": CH_ASK, "thread_ts": TS})).kind == "allow"
    assert (await hook("s1", "mcp__relay_slack__search", {"query": "x"})).kind == "allow"
    assert (await hook("s1", "mcp__relay_slack__post", {"channel": CH_AUTO, "text": "x"})).kind == "allow"
    assert (await hook("s1", "mcp__relay_slack__post", {"channel": CH_RO, "text": "x"})).kind == "deny"
    assert (await hook("s1", "mcp__relay_slack__post", {"channel": CH_BLOCKED, "text": "x"})).kind == "deny"
    ask = await hook("s1", "mcp__relay_slack__post", {"channel": CH_ASK, "text": "hello there"})
    assert ask.kind == "prompt" and ask.prompt["kind"] == "ask_send"
    assert ask.prompt["channel_name"] == "ask-ch" and ask.prompt["text_preview"] == "hello there"
    unl = await hook("s1", "mcp__relay_slack__read_thread", {"channel": CH_UNLISTED, "thread_ts": TS})
    assert unl.kind == "choose_policy" and unl.prompt["kind"] == "choose_policy"
    trig = await hook("s1", "mcp__relay_slack__post", {"channel": CH_ASK, "text": "!deploy"})
    assert trig.kind == "deny" and trig.code == "trigger_syntax_refused"  # never prompts
    # AC9 (#2364): the refusal states the reason and what to do instead
    assert "exclamation mark directly followed by a handle" in trig.message
    assert "not allowed in Slack messages" in trig.message and "without the '!'" in trig.message
    assert env.adapter.network_calls() == []
    denied = await env.audit.query("relay1")
    assert {r["code"] for r in denied} == {"send_not_permitted", "channel_blocked", "trigger_syntax_refused"}


async def test_ask_prompt_allow_once_then_prompts_again(tmp_path):
    env = make_env(tmp_path)
    args = {"channel": CH_ASK, "text": "hello"}
    gr = await env.bridge.pre_hook("s1", "mcp__relay_slack__post", args)
    resp = await env.bridge.apply_response("s1", "mcp__relay_slack__post", args, gr, {"behavior": "allow"})
    assert resp["behavior"] == "allow"
    assert not (await env.tools.handle_post("s1", "post", args)).get("is_error")
    assert env.info.config["relay_channel_policies"]["relay1"][CH_ASK] == "ask"
    assert (await env.bridge.pre_hook("s1", "mcp__relay_slack__post", args)).kind == "prompt"
    rec = (await env.audit.query("relay1"))[0]
    assert rec["approval"] == "prompt_allow" and rec["outcome"] == "ok"


async def test_promote_persists_session_policy_and_next_post_needs_no_prompt(tmp_path):
    env = make_env(tmp_path)
    args = {"channel": CH_ASK, "text": "hello"}
    gr = await env.bridge.pre_hook("s1", "mcp__relay_slack__post", args)
    resp = await env.bridge.apply_response(
        "s1", "mcp__relay_slack__post", args, gr,
        {"behavior": "allow", "relay_choice": {"promote": True}},
    )
    assert resp["behavior"] == "allow"
    assert env.info.config["relay_channel_policies"]["relay1"][CH_ASK] == "auto"
    assert not (await env.tools.handle_post("s1", "post", args)).get("is_error")
    nxt = {"channel": CH_ASK, "text": "second"}
    assert (await env.bridge.pre_hook("s1", "mcp__relay_slack__post", nxt)).kind == "allow"
    assert not (await env.tools.handle_post("s1", "post", nxt)).get("is_error")
    events = [r for r in await env.audit.query("relay1") if r["event"] == "policy_change"]
    assert events and events[0]["policy"] == "auto"
    assert (await env.audit.query("relay1"))[-1]["approval"] == "prompt_promote"


async def test_choose_policy_flow(tmp_path):
    env = make_env(tmp_path)
    args = {"channel": CH_UNLISTED, "thread_ts": TS}
    gr = await env.bridge.pre_hook("s1", "mcp__relay_slack__read_thread", args)
    no_choice = await env.bridge.apply_response(
        "s1", "mcp__relay_slack__read_thread", args, gr, {"behavior": "allow"}
    )
    assert no_choice["behavior"] == "deny"
    assert CH_UNLISTED not in env.info.config["relay_channel_policies"]["relay1"]
    resp = await env.bridge.apply_response(
        "s1", "mcp__relay_slack__read_thread", args, gr,
        {"behavior": "allow", "relay_choice": {"policy": "read_only"}},
    )
    assert resp["behavior"] == "allow"
    assert env.info.config["relay_channel_policies"]["relay1"][CH_UNLISTED] == "read_only"
    assert not (await env.tools.handle_read_thread("s1", args)).get("is_error")


async def test_choose_blocked_or_read_only_for_a_send_denies_but_saves(tmp_path):
    env = make_env(tmp_path)
    args = {"channel": CH_UNLISTED, "text": "hi"}
    gr = await env.bridge.pre_hook("s1", "mcp__relay_slack__post", args)
    resp = await env.bridge.apply_response(
        "s1", "mcp__relay_slack__post", args, gr,
        {"behavior": "allow", "relay_choice": {"policy": "read_only"}},
    )
    assert resp["behavior"] == "deny"
    assert env.info.config["relay_channel_policies"]["relay1"][CH_UNLISTED] == "read_only"
    assert (await env.tools.handle_post("s1", "post", args))["is_error"]  # no grant: refused


async def test_invalid_policy_choice_denied(tmp_path):
    env = make_env(tmp_path)
    args = {"channel": CH_UNLISTED, "thread_ts": TS}
    gr = await env.bridge.pre_hook("s1", "mcp__relay_slack__read_thread", args)
    resp = await env.bridge.apply_response(
        "s1", "mcp__relay_slack__read_thread", args, gr,
        {"behavior": "allow", "relay_choice": {"policy": "root"}},
    )
    assert resp["behavior"] == "deny"


# ------------------------------------------------- permission callback pre-hook wiring


def _service(env):
    coordinator = SimpleNamespace(relay_bridge=env.bridge)
    return PermissionService(coordinator, {})


async def test_permission_callback_allows_and_denies_without_prompt_ui(tmp_path):
    env = make_env(tmp_path)
    cb = _service(env).create_permission_callback("s1")
    allow = await cb("mcp__relay_slack__post", {"channel": CH_AUTO, "text": "x"}, None)
    assert allow == {"behavior": "allow"}
    deny = await cb("mcp__relay_slack__post", {"channel": CH_RO, "text": "x"}, None)
    assert deny["behavior"] == "deny" and "read_only" in deny["message"]
    blocked = await cb("mcp__relay_slack__read_thread", {"channel": CH_BLOCKED, "thread_ts": TS}, None)
    assert blocked["behavior"] == "deny" and "not accessible" in blocked["message"]
    read = await cb("mcp__relay_slack__read_thread", {"channel": CH_ASK, "thread_ts": TS}, None)
    assert read == {"behavior": "allow"}


def test_relay_not_in_auto_allow_list():
    import inspect

    from backend import session_launcher

    src = inspect.getsource(session_launcher)
    assert 'mcp_tools_list.append("mcp__relay_slack")' not in src
    assert 'relay_mcp_tools.create_server_if_attached' in src


async def test_permission_callback_denies_relay_tool_when_bridge_missing():
    cb = PermissionService(SimpleNamespace(), {}).create_permission_callback("s1")
    result = await cb("mcp__relay_slack__post", {"channel": CH_AUTO, "text": "x"}, None)
    assert result["behavior"] == "deny"
