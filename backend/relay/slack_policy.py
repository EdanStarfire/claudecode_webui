"""Slack's policy model (issue #2103, #2346): the adapter-specific half of the policy engine.

Values (``blocked | read_only | ask | auto``), the channel-ID target format and the decision
matrix belong to the Slack adapter; another adapter defines its own ``PolicyModel``.
"""

from __future__ import annotations

import re
from typing import Any

from .policy import ALLOW, CHOOSE_POLICY, DENY, PROMPT, Decision

POLICY_VALUES = ("blocked", "read_only", "ask", "auto")
CHANNEL_ID_RE = re.compile(r"^[CGDW][A-Z0-9]{6,}\Z")
CHANNEL_ID_PATTERN = "^[CGDW][A-Z0-9]{6,}$"

ACTIONS = ("read", "mark_read", "send", "search")

# tool -> policy action (None: scope-filtered per channel inside the handler)
SLACK_TOOL_ACTIONS: dict[str, str | None] = {
    "read_thread": "read",
    "mark_read": "mark_read",
    "post": "send",
    "reply": "send",
    "upload_file": "send",
    "react": "send",
    "search": None,
    # Identity and discovery are read-only metadata: no policy, no prompt, never a send right.
    "get_identity": None,
    "find_channels": None,
    # Monitors (inbound 3b-i): creating one reads like read_thread (blocked refused, unlisted ->
    # choose-policy prompt, don't-ask / unattended refusals); removing / listing needs no policy.
    "monitor_thread": "read",
    "monitor_channel": "read",
    "unmonitor": None,
    "list_monitors": None,
    # Scope monitors (3b-ii): no channel; ONE plain-language consent prompt, never a channel policy.
    "monitor_mentions": "monitor_scope",
    "monitor_dms": "monitor_scope",
    "run_monitor_now": None,  # 3c-i: an immediate digest run of the caller's own monitor, no policy
}
SLACK_WRITE_TOOLS = frozenset({"post", "reply", "upload_file", "react", "mark_read"})
SLACK_DISCOVERY_TOOLS = frozenset({"get_identity", "find_channels"})
SLACK_SCOPE_TOOLS = frozenset({"monitor_mentions", "monitor_dms"})
SLACK_MONITOR_TOOLS = frozenset({
    "monitor_thread", "monitor_channel", "unmonitor", "list_monitors", "run_monitor_now",
    *SLACK_SCOPE_TOOLS,
})

_ALLOW = Decision(ALLOW)
_PROMPT = Decision(PROMPT)
_CHOOSE = Decision(CHOOSE_POLICY)


def decide(policy: str | None, action: str) -> Decision:
    """Return the Decision for *action* on a channel whose policy is *policy* (None = unlisted)."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown relay action '{action}'")
    if policy is not None and policy not in POLICY_VALUES:
        raise ValueError(f"Unknown relay policy '{policy}'")

    if action == "search":
        # Search scope: unlisted channels are excluded without a prompt.
        if policy is None:
            return Decision(DENY, "policy_unset")
        if policy == "blocked":
            return Decision(DENY, "channel_blocked")
        return _ALLOW

    if policy is None:
        return _CHOOSE
    if policy == "blocked":
        return Decision(DENY, "channel_blocked")
    if action in ("read", "mark_read"):
        return _ALLOW
    # send
    if policy == "read_only":
        return Decision(DENY, "send_not_permitted")
    if policy == "ask":
        return _PROMPT
    return _ALLOW


def validate_channel_id(channel_id: object) -> str:
    if not isinstance(channel_id, str) or not CHANNEL_ID_RE.match(channel_id):
        raise ValueError(
            f"Invalid channel id {channel_id!r}: must be a Slack ID matching {CHANNEL_ID_PATTERN}"
        )
    return channel_id


def validate_policy_value(policy: object) -> str:
    if policy not in POLICY_VALUES:
        raise ValueError(f"Invalid policy {policy!r}: must be one of {', '.join(POLICY_VALUES)}")
    return policy  # type: ignore[return-value]


VALUE_HELP = {
    "blocked": "Not readable or writable, and hidden from the agent.",
    "read_only": "Read freely; sending is refused.",
    "ask": "Read freely; each send asks for approval.",
    "auto": "Read and send freely.",
}


VALUE_ICONS = {"blocked": "🚫", "read_only": "👁", "ask": "❓", "auto": "⚡"}


class SlackPolicyModel:
    values = POLICY_VALUES
    target_pattern = CHANNEL_ID_PATTERN

    def validate_target(self, target: object) -> str:
        return validate_channel_id(target)

    def validate_value(self, value: object) -> str:
        return validate_policy_value(value)

    def decide(self, policy: str | None, action: str) -> Decision:
        return decide(policy, action)

    def describe(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "target_pattern": self.target_pattern,
            "target_label": "Slack channel ID",
            "actions": list(ACTIONS),
            "value_help": dict(VALUE_HELP),
            "value_icons": dict(VALUE_ICONS),
        }


SLACK_POLICY_MODEL = SlackPolicyModel()
