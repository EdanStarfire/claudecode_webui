"""D3: implicit auto for owned self-DM instruction threads (issue #2103 stage 3a)."""

from __future__ import annotations

import json

import pytest

from backend.relay.safety import has_agent_marker
from backend.tests.relay_support import CH_AUTO, OTHER_DM, SELF_DM, make_env, result_text

THREAD = "1700000000.000100"
OTHER_THREAD = "1700000000.000200"


def grants_env(tmp_path, policies=None, owned=None):
    env = make_env(tmp_path, policies if policies is not None else {CH_AUTO: "auto"})
    env.manager = env.gate.relay_manager
    env.manager.relays["relay1"].self_dm_channel_id = SELF_DM
    owned = owned if owned is not None else {("s1", "relay1", SELF_DM, THREAD)}
    env.lookups = []

    def lookup(session_id, relay_id, channel, thread_ts):
        env.lookups.append((session_id, relay_id, channel, thread_ts))
        return (session_id, relay_id, channel, thread_ts) in owned

    env.gate.thread_grants = lookup
    return env


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


async def audit_rows(env):
    return await env.audit.query("relay1", limit=50)


class TestThreadExceptionAllows:
    async def test_reply_in_owned_thread_needs_no_prompt(self, tmp_path):
        env = grants_env(tmp_path, policies={})  # self-DM unlisted
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "done"}
        )
        assert not out.get("is_error"), result_text(out)
        [call] = [c for c in env.adapter.calls if c[0] == "post"]
        assert call[1] == SELF_DM and call[4] == THREAD
        assert has_agent_marker(call[2])  # the handler still marks self-DM messages
        [row] = await audit_rows(env)
        assert row["approval"] == "thread_grant" and row["outcome"] == "ok"

    async def test_ask_policy_is_upgraded(self, tmp_path):
        env = grants_env(tmp_path, policies={SELF_DM: "ask"})
        out = await env.tools.handle_post(
            "s1", "post", {"channel": SELF_DM, "thread_ts": THREAD, "text": "done"}
        )
        assert not out.get("is_error")
        assert (await audit_rows(env))[0]["approval"] == "thread_grant"

    async def test_explicit_auto_is_not_relabelled(self, tmp_path):
        env = grants_env(tmp_path, policies={SELF_DM: "auto"})
        await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "done"}
        )
        assert (await audit_rows(env))[0]["approval"] == "auto"

    async def test_read_thread_and_upload_in_owned_thread(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        (env.workdir / "a.txt").write_text("x")
        read = await env.tools.handle_read_thread("s1", {"channel": SELF_DM, "thread_ts": THREAD})
        assert not read.get("is_error")
        up = await env.tools.handle_upload(
            "s1", {"channel": SELF_DM, "thread_ts": THREAD, "path": str(env.workdir / "a.txt")}
        )
        assert not up.get("is_error"), result_text(up)

    async def test_owner_user_id_as_channel_resolves_to_same_self_dm(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": "U0OWNER001", "thread_ts": THREAD, "text": "done"}
        )
        assert not out.get("is_error")


class TestThreadExceptionDoesNotApply:
    @pytest.mark.parametrize("args,why", [
        ({"channel": SELF_DM, "thread_ts": OTHER_THREAD, "text": "x"}, "other thread"),
        ({"channel": SELF_DM, "text": "x"}, "no thread_ts"),
        ({"channel": OTHER_DM, "thread_ts": THREAD, "text": "x"}, "other channel"),
        ({"channel": CH_AUTO, "thread_ts": THREAD, "text": "x"}, "listed channel keeps its policy"),
    ])
    async def test_other_targets_use_normal_policy(self, tmp_path, args, why):
        env = grants_env(tmp_path, policies={CH_AUTO: "read_only"})
        out = await env.tools.handle_post("s1", "post", args)
        assert out["is_error"] is True, why
        assert not [c for c in env.adapter.calls if c[0] == "post"]

    async def test_different_session_is_not_covered(self, tmp_path):
        env = grants_env(tmp_path, policies={}, owned={("s2", "relay1", SELF_DM, THREAD)})
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "x"}
        )
        assert out["is_error"] is True
        assert ("s1", "relay1", SELF_DM, THREAD) in env.lookups

    async def test_other_relay_record_is_not_covered(self, tmp_path):
        env = grants_env(tmp_path, policies={}, owned={("s1", "relay2", SELF_DM, THREAD)})
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "x"}
        )
        assert out["is_error"] is True

    async def test_feature_off_without_lookup(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        env.gate.thread_grants = None
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "x"}
        )
        assert out["is_error"] is True

    @pytest.mark.parametrize("policy", ["blocked", "read_only"])
    async def test_explicit_policy_on_self_dm_wins(self, tmp_path, policy):
        env = grants_env(tmp_path, policies={SELF_DM: policy})
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "x"}
        )
        assert code_of(out) in ("channel_blocked", "send_not_permitted")
        assert not [c for c in env.adapter.calls if c[0] == "post"]

    async def test_react_mark_read_search_untouched(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        react = await env.tools.handle_react(
            "s1", {"channel": SELF_DM, "ts": THREAD, "name": "thumbsup", "thread_ts": THREAD}
        )
        mark = await env.tools.handle_mark_read(
            "s1", {"channel": SELF_DM, "ts": THREAD, "thread_ts": THREAD}
        )
        assert react["is_error"] is True and mark["is_error"] is True

    async def test_trigger_syntax_still_refused_in_owned_thread(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        out = await env.tools.handle_post(
            "s1", "reply", {"channel": SELF_DM, "thread_ts": THREAD, "text": "run !my-agent"}
        )
        assert code_of(out) == "trigger_syntax_refused"


class TestPermissionHookAgrees:
    async def test_pre_hook_allows_owned_thread_reply_without_prompt(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        result = await env.bridge.pre_hook(
            "s1", "mcp__relay_slack__reply",
            {"channel": SELF_DM, "thread_ts": THREAD, "text": "done"},
        )
        assert result.kind == "allow" and result.approval == "thread_grant"

    async def test_pre_hook_prompts_for_unowned_thread(self, tmp_path):
        env = grants_env(tmp_path, policies={})
        result = await env.bridge.pre_hook(
            "s1", "mcp__relay_slack__reply",
            {"channel": SELF_DM, "thread_ts": OTHER_THREAD, "text": "done"},
        )
        assert result.kind != "allow"


class TestNoWayToCreateRecordsFromTools:
    def test_store_requires_root_message(self):
        # the only creator (the inbound pipeline) passes origin_ts == thread_ts; nothing in the
        # relay tool surface, REST routes or config can create a record
        import inspect

        from backend.mcp import relay_mcp_tools

        assert "create_thread" not in inspect.getsource(relay_mcp_tools)
