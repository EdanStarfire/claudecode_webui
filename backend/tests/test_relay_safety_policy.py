"""Issue #2103 outbound-1a: trigger syntax check, policy engine, footer, handles, validation."""

import pytest

from backend.relay.adapter import RelayMessage
from backend.relay.events import RELAY_EVENT_TYPE, is_agent_message
from backend.relay.footer import render_footer, validate_footer_template
from backend.relay.handles import HandleConflictError, RelayHandleIndex, default_handle_base
from backend.relay.policy import (
    POLICY_VALUES,
    decide,
    merge_policies,
    validate_channel_id,
    validate_channel_policies,
)
from backend.relay.safety import (
    HANDLE_PATTERN,
    HANDLE_RE,
    TRIGGER_RE,
    contains_trigger_syntax,
    validate_handle,
)
from backend.relay.validation import validate_assigned_secrets, validate_relay_config

# ------------------------------------------------------------------ trigger check

REFUSED = [
    "!deploy",
    "please run !deploy now",
    "end with !deploy",
    "line one\n!deploy",
    "after punct, !deploy",
    "`!deploy`",
    "```\n!deploy\n```",
    "<!here>",
    "<!channel> hi",
    "<!subteam^S123>",
    "&lt;!here&gt;",
    "！deploy",  # full-width exclamation mark (NFKC -> '!')
    "!d2",
    "!Deploy",
    "!DEPLOY now",
    "run !Deploy now",
    "end with !DeployProd",
    "line\n!MixedCase",
]
ALLOWED = [
    "Great! thanks",
    "Done! ok",
    "wow!great",
    "a != b",
    "!=",
    "!",
    "https://x.com/a!b",
    "Hello!",
    "Done! OK",
    "WOW!Great",
    "! Slug",
    "! slug",
    "",
    None,
]


@pytest.mark.parametrize("text", REFUSED)
def test_issue_2103_trigger_refused(text):
    assert contains_trigger_syntax(text)


@pytest.mark.parametrize("text", ALLOWED)
def test_issue_2103_trigger_allowed(text):
    assert not contains_trigger_syntax(text)


def test_issue_2103_handle_pattern_single_source():
    assert HANDLE_PATTERN in TRIGGER_RE.pattern
    assert HANDLE_PATTERN in HANDLE_RE.pattern
    assert not HANDLE_RE.match("a_b")  # no underscore anywhere
    assert not TRIGGER_RE.search("!a_b")
    assert not HANDLE_RE.match("Abc")  # handles stay lowercase-only


def test_issue_2103_validate_handle():
    assert validate_handle("my-agent-2") == "my-agent-2"
    for bad in ("A", "a", "-ab", "a_b", "x" * 33, "UPPER", ""):
        with pytest.raises(ValueError):
            validate_handle(bad)
    for reserved in ("all", "here", "channel", "everyone", "subteam"):
        with pytest.raises(ValueError, match="reserved"):
            validate_handle(reserved)


# ------------------------------------------------------------------ policy matrix

MATRIX = {
    # action: (unlisted, blocked, read_only, ask, auto)
    "read": ("choose_policy", "deny:channel_blocked", "allow", "allow", "allow"),
    "mark_read": ("choose_policy", "deny:channel_blocked", "allow", "allow", "allow"),
    "send": ("choose_policy", "deny:channel_blocked", "deny:send_not_permitted", "prompt", "allow"),
    "search": ("deny:policy_unset", "deny:channel_blocked", "allow", "allow", "allow"),
}


@pytest.mark.parametrize("action", MATRIX)
@pytest.mark.parametrize("idx,policy", list(enumerate([None, *POLICY_VALUES])))
def test_issue_2103_policy_matrix_cell(action, idx, policy):
    expected = MATRIX[action][idx]
    d = decide(policy, action)
    got = f"deny:{d.code}" if d.kind == "deny" else d.kind
    assert got == expected


def test_issue_2103_decide_rejects_unknown():
    with pytest.raises(ValueError):
        decide("auto", "delete")
    with pytest.raises(ValueError):
        decide("everything", "read")


def test_issue_2103_merge_policies_per_key_precedence():
    merged = merge_policies(
        [{"C1111111": "blocked", "C2222222": "read_only"}],
        {"C2222222": "ask", "C3333333": "ask"},
        {"C3333333": "auto"},
    )
    assert {k: (v.policy, v.source) for k, v in merged.items()} == {
        "C1111111": ("blocked", "profile"),  # kept: not replaced by the template dict
        "C2222222": ("ask", "template"),
        "C3333333": ("auto", "session"),
    }


def test_issue_2103_merge_policies_handles_none_tiers():
    assert merge_policies([None], None, None) == {}


def test_issue_2103_channel_id_and_policy_validation():
    for ok in ("C0123456789", "G0ABCDEFG", "D01234567", "W0123456"):
        assert validate_channel_id(ok) == ok
    for bad in ("general", "c0123456", "C123", "#C0123456", None):
        with pytest.raises(ValueError):
            validate_channel_id(bad)
    assert validate_channel_policies({"C0123456": "auto"}) == {"C0123456": "auto"}
    with pytest.raises(ValueError):
        validate_channel_policies({"C0123456": "yes"})
    with pytest.raises(ValueError):
        validate_channel_policies(["C0123456"])


# ------------------------------------------------------------------------- footer

def test_issue_2103_footer_validation_uses_trigger_check():
    assert validate_footer_template("Sent by {session_name} ({session_handle}) {session_link}")
    assert validate_footer_template(None) is None
    with pytest.raises(ValueError, match="trigger"):
        validate_footer_template("via !bot")
    with pytest.raises(ValueError, match="at most"):
        validate_footer_template("x" * 501)
    with pytest.raises(ValueError, match="unknown variables"):
        validate_footer_template("{nope}")


def test_issue_2103_footer_render():
    out = render_footer("{session_name} / {session_handle} {other}", {"session_name": "A", "session_handle": "a1"})
    assert out == "A / a1 {other}"


# ------------------------------------------------------------------------- handles

def test_issue_2103_default_handle_base():
    assert default_handle_base("Fix Auth Bug") == "fix-auth-bug"
    assert default_handle_base("", "abcdef123456").startswith("session-abcdef")
    assert default_handle_base("!!!", "abcdef123456").startswith("session-")
    assert default_handle_base("here", "abcdef123456").startswith("session-")  # reserved


@pytest.mark.asyncio
async def test_issue_2103_handle_assignment_collision_suffix_and_free_on_delete():
    idx = RelayHandleIndex()
    assert await idx.assign_default("s1", "Planner") == "planner"
    assert await idx.assign_default("s2", "Planner") == "planner-2"
    assert await idx.assign_default("s3", "Planner") == "planner-3"
    assert await idx.assign_default("s1", "Other") == "planner"  # never re-derived
    idx.remove_session("s1")
    assert idx.get_session("planner") is None
    assert await idx.assign_default("s4", "Planner") == "planner"


@pytest.mark.asyncio
async def test_issue_2103_handle_set_duplicate_and_reserved():
    idx = RelayHandleIndex()
    await idx.set_handle("s1", "alpha")
    with pytest.raises(HandleConflictError):
        await idx.set_handle("s2", "alpha")
    await idx.set_handle("s1", "alpha")  # same session: idempotent
    await idx.set_handle("s1", "beta")  # edit frees the old handle
    assert idx.get_session("alpha") is None
    await idx.set_handle("s2", "alpha")
    with pytest.raises(ValueError):
        await idx.set_handle("s2", "here")


def test_issue_2103_handle_index_build_skips_duplicates():
    idx = RelayHandleIndex()
    idx.build([("s1", "a1"), ("s2", "a1"), ("s3", None)])
    assert idx.get_session("a1") == "s1"
    assert idx.get_handle("s2") is None


# ------------------------------------------------------------------- validation

def test_issue_2103_assigned_secrets_rejects_relay_prefix():
    validate_assigned_secrets(["github-token", None])
    for name in ("relay-abc-user-token", "RELAY-x"):
        with pytest.raises(ValueError, match="reserved"):
            validate_assigned_secrets([name])


def test_issue_2103_relay_handle_is_session_tier_only():
    validate_relay_config({"relay_handle": "ok-handle"}, "session")
    for tier in ("profile", "template"):
        with pytest.raises(ValueError, match="session"):
            validate_relay_config({"relay_handle": "ok-handle"}, tier)


def test_issue_2103_validate_relay_config_fields():
    validate_relay_config({"relay_id": "r", "relay_footer_enabled": True,
                           "relay_channel_policies": {"C0123456": "ask"}}, "profile")
    with pytest.raises(ValueError):
        validate_relay_config({"relay_footer_enabled": "yes"}, "profile")
    with pytest.raises(ValueError):
        validate_relay_config({"relay_footer_template": "!bad"}, "template")
    with pytest.raises(ValueError):
        validate_relay_config({"assigned_secrets": ["relay-x-user-token"]}, "template")


# ----------------------------------------------------------------- is_agent_message

def test_issue_2103_is_agent_message():
    assert is_agent_message(RelayMessage(ts="1", app_id="A1"))
    assert is_agent_message(RelayMessage(ts="1", bot_id="B1"))
    assert is_agent_message(RelayMessage(ts="1", metadata={"event_type": RELAY_EVENT_TYPE}))
    assert not is_agent_message(RelayMessage(ts="1", metadata={"event_type": "other"}))
    assert not is_agent_message(RelayMessage(ts="1", user="U1"))
    assert is_agent_message({"app_id": "A1"})
    assert not is_agent_message({"user": "U1"})


def test_issue_2103_trailing_newline_rejected_in_handle_and_channel_id():
    with pytest.raises(ValueError):
        validate_handle("ab\n")
    with pytest.raises(ValueError):
        validate_channel_id("C1234567\n")


def test_issue_2103_footer_variable_cannot_complete_a_trigger():
    for template in ("!{session_handle}", "run !{session_name} now"):
        with pytest.raises(ValueError, match="rendered"):
            validate_footer_template(template)
    assert validate_footer_template("Sent! by {session_name}")
