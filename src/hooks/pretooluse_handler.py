"""PreToolUse hook handler for internal tool access control.

Evaluates hardcoded rules derived from session configuration to auto-approve
or deny tool operations for internal features (history, plans, skills).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk.types import (
    HookContext,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
    SyncHookJSONOutput,
)

PermissionDecision = Literal["allow", "deny"]

_RESOLVE_CACHE: dict[str, str] = {}


def _resolve_path(path_str: str) -> str:
    """Resolve path, expanding ~ and resolving symlinks. Results are cached."""
    cached = _RESOLVE_CACHE.get(path_str)
    if cached is not None:
        return cached
    resolved = str(Path(path_str).expanduser().resolve())
    _RESOLVE_CACHE[path_str] = resolved
    return resolved


@dataclass
class InternalRule:
    """A hardcoded rule for internal tool access control."""

    tool_names: frozenset[str]
    path_prefixes: list[str]  # Pre-resolved path prefixes with trailing os.sep
    decision: PermissionDecision
    reason: str
    enabled: bool = True


def _resolve_prefixes(dirs: list[str]) -> list[str]:
    """Resolve directory paths and append os.sep for safe prefix matching."""
    prefixes = []
    for d in dirs:
        resolved = _resolve_path(d)
        if not resolved.endswith(os.sep):
            resolved += os.sep
        prefixes.append(resolved)
    return prefixes


class PreToolUseHandler:
    """Internal PreToolUse hook handler for auto-approving/denying built-in feature operations."""

    def __init__(
        self,
        session_data_dir: Path,
        plans_dir: Path,
        skills_dirs: list[Path],
        knowledge_mgmt_enabled: bool,
    ):
        self._rules = self._build_rules(
            session_data_dir, plans_dir, skills_dirs, knowledge_mgmt_enabled
        )

    def _build_rules(
        self,
        session_data_dir: Path,
        plans_dir: Path,
        skills_dirs: list[Path],
        knowledge_mgmt_enabled: bool,
    ) -> list[InternalRule]:
        """Build internal rule set from session configuration. Paths are resolved once here."""
        rules: list[InternalRule] = []
        history_prefixes = _resolve_prefixes([str(session_data_dir / "history")])

        # Rule: Allow reading history files (when knowledge management enabled)
        rules.append(InternalRule(
            tool_names=frozenset(["Read"]),
            path_prefixes=history_prefixes,
            decision="allow",
            reason="Auto-approved: internal history file read (knowledge management)",
            enabled=knowledge_mgmt_enabled,
        ))

        # Rule: Deny writing to history folder (always — it's read-only)
        rules.append(InternalRule(
            tool_names=frozenset(["Write", "Edit"]),
            path_prefixes=history_prefixes,
            decision="deny",
            reason="Denied: history folder is read-only",
            enabled=True,
        ))

        # Rule: Allow reading/writing plan files
        plan_prefixes = _resolve_prefixes([str(plans_dir)])
        rules.append(InternalRule(
            tool_names=frozenset(["Read", "Write"]),
            path_prefixes=plan_prefixes,
            decision="allow",
            reason="Auto-approved: internal plan file access",
            enabled=True,
        ))

        # Rule: Allow reading skill files
        skill_prefixes = _resolve_prefixes([str(d) for d in skills_dirs])
        rules.append(InternalRule(
            tool_names=frozenset(["Read"]),
            path_prefixes=skill_prefixes,
            decision="allow",
            reason="Auto-approved: internal skill file read",
            enabled=True,
        ))

        # Rule: Deny writing to skill directories (managed by SkillManager)
        rules.append(InternalRule(
            tool_names=frozenset(["Write", "Edit"]),
            path_prefixes=skill_prefixes,
            decision="deny",
            reason="Denied: skill files are managed by SkillManager",
            enabled=True,
        ))

        return rules

    def _extract_file_path(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Extract file path from tool input based on tool type."""
        if tool_name in ("Read", "Write", "Edit"):
            return tool_input.get("file_path")
        return None

    def _matches_path(self, file_path: str, prefixes: list[str]) -> bool:
        """Check if resolved file_path starts with any pre-resolved prefix."""
        resolved = _resolve_path(file_path)
        for prefix in prefixes:
            if resolved.startswith(prefix):
                return True
        return False

    async def handle(
        self,
        hook_input: PreToolUseHookInput,
        matcher: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """Evaluate internal rules against tool call. Returns empty output if no rule matches."""
        tool_name = hook_input["tool_name"]
        tool_input = hook_input["tool_input"]

        file_path = self._extract_file_path(tool_name, tool_input)
        if file_path is None:
            return SyncHookJSONOutput()

        for rule in self._rules:
            if not rule.enabled:
                continue
            if tool_name not in rule.tool_names:
                continue
            if not self._matches_path(file_path, rule.path_prefixes):
                continue

            return SyncHookJSONOutput(
                hookSpecificOutput=PreToolUseHookSpecificOutput(
                    hookEventName="PreToolUse",
                    permissionDecision=rule.decision,
                    permissionDecisionReason=rule.reason,
                ),
                reason=rule.reason,
            )

        return SyncHookJSONOutput()
