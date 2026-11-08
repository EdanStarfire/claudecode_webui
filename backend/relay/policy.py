"""Adapter-neutral policy engine pieces (issue #2103, #2346). Pure: no I/O.

Each adapter owns its policy model (values, target format, decision matrix) behind the
``PolicyModel`` protocol; Slack's lives in ``slack_policy.py``. This module keeps only what
every adapter shares: decision kinds, ``Decision``, ``EffectivePolicy`` and ``merge_policies``,
the single per-key tier merge (profile < template < session). It is deliberately NOT the
whole-dict key-replace the rest of the config cascade uses, so a session can promote one
target without losing the template's other entries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

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


class PolicyModel(Protocol):
    """An adapter's policy definition: value set, target format and decision matrix."""

    values: tuple[str, ...]
    target_pattern: str  # human-readable target format, for the UI

    def validate_target(self, target: object) -> str: ...

    def validate_value(self, value: object) -> str: ...

    def decide(self, policy: str | None, action: str) -> Decision: ...

    def describe(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class EffectivePolicy:
    policy: str
    source: str  # profile | template | session


def merge_policies(
    profile_policies: Iterable[Mapping[str, str] | None],
    template_policies: Mapping[str, str] | None,
    session_policies: Mapping[str, str] | None,
    value_ok: Callable[[object], bool] = lambda v: isinstance(v, str),
) -> dict[str, EffectivePolicy]:
    """Per-key merge: profile < template < session; the higher tier wins per target.

    *value_ok* is the adapter model's value guard; entries it rejects are ignored.
    """
    merged: dict[str, EffectivePolicy] = {}
    tiers: list[tuple[str, Mapping[str, str] | None]] = [
        (SOURCE_PROFILE, p) for p in profile_policies
    ]
    tiers.append((SOURCE_TEMPLATE, template_policies))
    tiers.append((SOURCE_SESSION, session_policies))
    for source, policies in tiers:
        for target, policy in (policies or {}).items():
            if value_ok(policy):
                merged[target] = EffectivePolicy(policy, source)
    return merged


def normalize_policies(raw: object, sole_relay_id: str | None) -> dict[str, dict[str, str]]:
    """Stored ``relay_channel_policies`` -> ``{relay_id: {target: policy}}`` (read-time).

    Nested entries pass through. A legacy flat ``{target: policy}`` entry (string value) is wrapped
    under *sole_relay_id* when the Backend has exactly one relay, else dropped with a warning
    (never guessed). Nothing is rewritten on disk by this function.
    """
    result: dict[str, dict[str, str]] = {}
    if not isinstance(raw, Mapping):
        return result
    dropped = 0
    for key, value in raw.items():
        if isinstance(value, Mapping):
            result.setdefault(key, {}).update(value)
        elif isinstance(value, str):
            if sole_relay_id is None:
                dropped += 1
            else:
                result.setdefault(sole_relay_id, {})[key] = value
    if dropped:
        logger.warning(
            "Dropped %d legacy flat relay policy entries: cannot attribute them to a single relay",
            dropped,
        )
    return result
