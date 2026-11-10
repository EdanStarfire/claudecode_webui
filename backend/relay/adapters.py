"""Adapter registry (issue #2103, #2346).

An adapter registers one ``AdapterSpec``: the MCP server name, the tool -> policy-action map,
the write tools and its policy model. The gate, permission bridge, launcher and MCP factory
read the spec instead of hardcoding Slack. Tool names are ``mcp__{server_name}__{tool}``
(e.g. ``mcp__relay_slack__post``); the former ``mcp__relay__*`` names are not recognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .policy import PolicyModel
from .slack_policy import (
    SLACK_DISCOVERY_TOOLS,
    SLACK_MONITOR_TOOLS,
    SLACK_POLICY_MODEL,
    SLACK_SCOPE_TOOLS,
    SLACK_TOOL_ACTIONS,
    SLACK_WRITE_TOOLS,
)


@dataclass(frozen=True)
class AdapterSpec:
    adapter_type: str  # "slack"
    server_name: str  # "relay_slack"
    tool_actions: dict[str, str | None]  # tool -> policy action (None = scope-filtered)
    write_tools: frozenset[str]
    policy_model: PolicyModel
    discovery_tools: frozenset[str] = frozenset()  # audited minimally (no contents)
    monitor_tools: frozenset[str] = frozenset()  # inbound monitors: audited, never send tools
    scope_tools: frozenset[str] = frozenset()  # monitors with no channel: one consent prompt (3b-ii)

    @property
    def tool_prefix(self) -> str:
        return f"mcp__{self.server_name}__"


SLACK_SPEC = AdapterSpec(
    adapter_type="slack",
    server_name="relay_slack",
    tool_actions=SLACK_TOOL_ACTIONS,
    write_tools=SLACK_WRITE_TOOLS,
    policy_model=SLACK_POLICY_MODEL,
    discovery_tools=SLACK_DISCOVERY_TOOLS,
    monitor_tools=SLACK_MONITOR_TOOLS,
    scope_tools=SLACK_SCOPE_TOOLS,
)

SPECS: dict[str, AdapterSpec] = {"slack": SLACK_SPEC}

_RELAY_TOOL_RE = re.compile(r"^mcp__(relay_[a-z0-9]+)__(.+)\Z")


def get_spec(adapter_type: str | None) -> AdapterSpec | None:
    return SPECS.get(adapter_type) if adapter_type else None


def parse_relay_tool(tool_name: str) -> tuple[AdapterSpec, str] | None:
    """``mcp__relay_slack__post`` -> ``(SLACK_SPEC, "post")``; None for anything that is not a
    registered adapter's relay tool (including the removed ``mcp__relay__*`` names)."""
    match = _RELAY_TOOL_RE.match(tool_name)
    if match is None:
        return None
    for spec in SPECS.values():
        if spec.server_name == match.group(1):
            return spec, match.group(2)
    return None


def is_relay_tool(tool_name: str) -> bool:
    """True for ``mcp__relay_{adapter}__*`` of a registered adapter (any tool name)."""
    return parse_relay_tool(tool_name) is not None
