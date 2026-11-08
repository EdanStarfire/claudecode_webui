"""Issue #2346: adapter registry, tool-name parsing, Slack policy model, policy normalisation."""

import logging
from types import SimpleNamespace

import pytest

from backend.relay.adapters import SLACK_SPEC, get_spec, is_relay_tool, parse_relay_tool
from backend.relay.policy import normalize_policies
from backend.relay.service import RelayService
from backend.relay.slack_policy import SLACK_POLICY_MODEL

CH = "C0123456789"


def test_issue_2346_parse_relay_tool():
    assert parse_relay_tool("mcp__relay_slack__post") == (SLACK_SPEC, "post")
    assert is_relay_tool("mcp__relay_slack__read_thread")
    assert SLACK_SPEC.tool_prefix == "mcp__relay_slack__"
    # removed alias, unknown adapters and non-relay tools are not relay tools
    for name in ("mcp__relay__post", "mcp__relay_linear__post", "mcp__legion__send_comm", "post"):
        assert parse_relay_tool(name) is None and not is_relay_tool(name)
    assert get_spec("slack") is SLACK_SPEC and get_spec("nope") is None and get_spec(None) is None


def test_issue_2346_slack_spec_tool_map_and_model():
    assert SLACK_SPEC.server_name == "relay_slack"
    assert SLACK_SPEC.tool_actions["search"] is None and SLACK_SPEC.tool_actions["react"] == "send"
    assert "read_thread" not in SLACK_SPEC.write_tools and "post" in SLACK_SPEC.write_tools
    model = SLACK_POLICY_MODEL
    assert model.validate_target("C0123456") == "C0123456"
    for bad in ("general", "c0123456", "C123", None):
        with pytest.raises(ValueError):
            model.validate_target(bad)
    with pytest.raises(ValueError):
        model.validate_value("yes")
    assert model.describe()["values"] == ["blocked", "read_only", "ask", "auto"]


def test_issue_2346_normalize_policies():
    nested = {"r1": {CH: "auto"}}
    assert normalize_policies(nested, "r1") == nested
    assert normalize_policies({CH: "ask"}, "r1") == {"r1": {CH: "ask"}}
    assert normalize_policies(None, "r1") == {} and normalize_policies("junk", "r1") == {}


def test_issue_2346_flat_policies_dropped_with_warning_when_relay_ambiguous(caplog):
    with caplog.at_level(logging.WARNING, logger="backend.relay.policy"):
        assert normalize_policies({CH: "ask"}, None) == {}
    assert "Dropped 1 legacy flat" in caplog.text


class _Templates:
    async def get_template(self, _):
        return SimpleNamespace(
            profile_ids={"relay": "p1"},
            config={"relay_channel_policies": {"r1": {CH: "ask"}}},
        )


class _Profiles:
    async def get_profile(self, _):
        # legacy flat profile tier
        return SimpleNamespace(config={"relay_channel_policies": {CH: "read_only", "C0999999999": "blocked"}})


@pytest.mark.asyncio
async def test_issue_2346_effective_policies_merge_per_relay_and_ignore_dangling():
    relays = {"r1": SimpleNamespace(adapter_type="slack")}
    service = RelayService(
        SimpleNamespace(relays=relays), None, None, _Templates(), _Profiles()
    )
    info = SimpleNamespace(
        template_id="t1",
        config={"relay_channel_policies": {"r1": {CH: "auto"}, "gone": {CH: "auto"}}},
    )
    eff = await service.effective_policies(info)
    assert set(eff) == {"r1"}  # dangling relay ignored
    assert {k: (v.policy, v.source) for k, v in eff["r1"].items()} == {
        CH: ("auto", "session"),  # session > template > flat profile (wrapped under sole relay)
        "C0999999999": ("blocked", "profile"),
    }
    assert await service.effective_relay_policies(info, "gone") == {}


@pytest.mark.asyncio
async def test_issue_2346_session_start_snapshot_ignores_stored_policies():
    """Legacy flat and dangling-relay stored policies must never fail effective-config resolution."""
    from backend.config_resolution import resolve_effective_config

    for stored in ({CH: "blocked"}, {"deleted-relay": {CH: "auto"}}):
        info = SimpleNamespace(template_id=None, config={"relay_id": "r1", "relay_channel_policies": stored})
        cfg = await resolve_effective_config(info, None)
        assert cfg.relay_id == "r1" and cfg.relay_channel_policies is None
