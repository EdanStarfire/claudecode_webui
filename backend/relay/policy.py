"""Channel policy engine (issue #2103). Pure: no I/O.

``decide(policy, action)`` implements the policy matrix; ``merge_policies`` is the
single per-key tier merge (profile < template < session). It is deliberately NOT
the whole-dict key-replace the rest of the config cascade uses, so a session can
promote one channel without losing the template's other entries.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

POLICY_VALUES = ("blocked", "read_only", "ask", "auto")
CHANNEL_ID_RE = re.compile(r"^[CGDW][A-Z0-9]{6,}\Z")

ACTIONS = ("read", "mark_read", "send", "search")

# Decision kinds
ALLOW = "allow"
DENY = "deny"
PROMPT = "prompt"
CHOOSE_POLICY = "choose_policy"

# Source markers (tier the winning value came from)
SOURCE_PROFILE = "profile"
SOURCE_TEMPLATE = "template"
SOURCE_SESSION = "session"


@dataclass(frozen=True)
class Decision:
    kind: str  # allow | deny | prompt | choose_policy
    code: str | None = None  # deny code


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


def validate_channel_policies(policies: object) -> dict[str, str] | None:
    """Validate a channel_id -> policy mapping. Raises ValueError."""
    if policies is None:
        return None
    if not isinstance(policies, Mapping):
        raise ValueError("relay_channel_policies must be an object of channel_id -> policy")
    for channel_id, policy in policies.items():
        validate_channel_id(channel_id)
        validate_policy_value(policy)
    return dict(policies)


def validate_channel_id(channel_id: object) -> str:
    if not isinstance(channel_id, str) or not CHANNEL_ID_RE.match(channel_id):
        raise ValueError(
            f"Invalid channel id {channel_id!r}: must be a Slack ID matching ^[CGDW][A-Z0-9]{{6,}}$"
        )
    return channel_id


def validate_policy_value(policy: object) -> str:
    if policy not in POLICY_VALUES:
        raise ValueError(f"Invalid policy {policy!r}: must be one of {', '.join(POLICY_VALUES)}")
    return policy  # type: ignore[return-value]


@dataclass(frozen=True)
class EffectivePolicy:
    policy: str
    source: str  # profile | template | session


def merge_policies(
    profile_policies: Iterable[Mapping[str, str] | None],
    template_policies: Mapping[str, str] | None,
    session_policies: Mapping[str, str] | None,
) -> dict[str, EffectivePolicy]:
    """Per-key merge: profile < template < session; the higher tier wins per channel."""
    merged: dict[str, EffectivePolicy] = {}
    tiers: list[tuple[str, Mapping[str, str] | None]] = [
        (SOURCE_PROFILE, p) for p in profile_policies
    ]
    tiers.append((SOURCE_TEMPLATE, template_policies))
    tiers.append((SOURCE_SESSION, session_policies))
    for source, policies in tiers:
        for channel_id, policy in (policies or {}).items():
            if policy in POLICY_VALUES:
                merged[channel_id] = EffectivePolicy(policy, source)
    return merged
