"""Issue #2353: RelayCallRegistry (hook -> handler tool_use_id correlation) and SDK hook option."""

from __future__ import annotations

from unittest.mock import patch

from backend.claude_sdk import ClaudeSDK
from backend.config_manager import AppConfig
from backend.relay.adapters import SPECS
from backend.relay.call_registry import ENTRY_TTL_SECONDS, RelayCallRegistry, hook_matcher
from backend.session_config import SessionConfig

TOOL = "mcp__relay_slack__post"
ARGS = {"channel": "C0ASK00001", "text": "hello"}


def test_fifo_claim_for_identical_calls():
    reg = RelayCallRegistry()
    reg.record("s1", TOOL, ARGS, "tu1")
    reg.record("s1", TOOL, ARGS, "tu2", "agent-1")
    assert reg.claim("s1", TOOL, ARGS).tool_use_id == "tu1"
    second = reg.claim("s1", TOOL, ARGS)
    assert (second.tool_use_id, second.agent_id) == ("tu2", "agent-1")
    assert reg.claim("s1", TOOL, ARGS) is None


def test_digest_session_and_tool_must_match():
    reg = RelayCallRegistry()
    reg.record("s1", TOOL, ARGS, "tu1")
    assert reg.claim("s2", TOOL, ARGS) is None
    assert reg.claim("s1", "mcp__relay_slack__reply", ARGS) is None
    assert reg.claim("s1", TOOL, {**ARGS, "text": "other"}) is None
    assert reg.claim("s1", TOOL, ARGS).tool_use_id == "tu1"


def test_entries_expire():
    now = [0.0]
    reg = RelayCallRegistry(clock=lambda: now[0])
    reg.record("s1", TOOL, ARGS, "tu1")
    now[0] += ENTRY_TTL_SECONDS + 1
    assert reg.claim("s1", TOOL, ARGS) is None


def test_clear_session_only_clears_that_session():
    reg = RelayCallRegistry()
    reg.record("s1", TOOL, ARGS, "tu1")
    reg.record("s2", TOOL, ARGS, "tu2")
    reg.clear_session("s1")
    assert reg.claim("s1", TOOL, ARGS) is None
    assert reg.claim("s2", TOOL, ARGS).tool_use_id == "tu2"


async def test_hook_records_and_returns_empty_output():
    reg = RelayCallRegistry()
    hook = reg.make_hook("s1")
    out = await hook(
        {"hook_event_name": "PreToolUse", "tool_name": TOOL, "tool_input": ARGS,
         "tool_use_id": "tu1", "agent_id": "a1"}, "tu1", {"signal": None},
    )
    assert out == {}  # never allow / ask / deny
    claimed = reg.claim("s1", TOOL, ARGS)
    assert (claimed.tool_use_id, claimed.agent_id) == ("tu1", "a1")


async def test_hook_failures_never_raise_or_block():
    reg = RelayCallRegistry()
    hook = reg.make_hook("s1")
    assert await hook(None, None, None) == {}
    assert await hook({"tool_name": TOOL, "tool_input": "not-a-dict"}, "x", None) == {}
    with patch.object(reg, "record", side_effect=RuntimeError("boom")):
        assert await hook({"tool_name": TOOL, "tool_input": ARGS, "tool_use_id": "t"}, "t", None) == {}


def test_matcher_is_derived_from_adapter_specs_and_matches_only_relay_tools():
    import re

    matcher = hook_matcher()
    assert matcher == "|".join(f"{re.escape(s.tool_prefix)}.*" for s in SPECS.values())
    assert re.fullmatch(matcher, "mcp__relay_slack__post")
    for other in ("Bash", "mcp__legion__send_comm", "mcp__relay__post", "mcp__relay_slackx"):
        assert not re.fullmatch(matcher, other)


def test_sdk_registers_pre_tool_hooks_only_when_given(tmp_path):
    with patch("backend.config_manager.load_config", return_value=AppConfig()):
        plain = ClaudeSDK(session_id="s1", working_directory=str(tmp_path), config=SessionConfig())
        assert plain._get_sdk_options().hooks is None

        async def cb(*a):
            return {}

        sdk = ClaudeSDK(
            session_id="s1", working_directory=str(tmp_path), config=SessionConfig(),
            pre_tool_hooks=[(hook_matcher(), cb)],
        )
        hooks = sdk._get_sdk_options().hooks
    [matcher] = hooks["PreToolUse"]
    assert matcher.matcher == hook_matcher() and matcher.hooks == [cb]
    assert set(hooks) == {"PreToolUse"}
