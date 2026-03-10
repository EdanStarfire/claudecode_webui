"""PreToolUse hook handler for internal tool access control.

Evaluates hardcoded rules derived from session configuration to auto-approve
or deny tool operations for internal features (history, plans, skills).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk.types import (
    HookContext,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
    SyncHookJSONOutput,
)


@dataclass
class InternalRule:
    """A hardcoded rule for internal tool access control."""

    tool_names: list[str]
    path_patterns: list[str]
    decision: str  # "allow" or "deny"
    reason: str
    enabled: bool = True


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
        """Build internal rule set from session configuration."""
        rules: list[InternalRule] = []
        history_dir = str(session_data_dir / "history")

        # Rule: Allow reading history files (when knowledge management enabled)
        rules.append(InternalRule(
            tool_names=["Read"],
            path_patterns=[history_dir],
            decision="allow",
            reason="Auto-approved: internal history file read (knowledge management)",
            enabled=knowledge_mgmt_enabled,
        ))

        # Rule: Deny writing to history folder (always — it's read-only)
        rules.append(InternalRule(
            tool_names=["Write", "Edit"],
            path_patterns=[history_dir],
            decision="deny",
            reason="Denied: history folder is read-only",
            enabled=True,
        ))

        # Rule: Allow reading/writing plan files
        plans_str = str(plans_dir)
        rules.append(InternalRule(
            tool_names=["Read", "Write"],
            path_patterns=[plans_str],
            decision="allow",
            reason="Auto-approved: internal plan file access",
            enabled=True,
        ))

        # Rule: Allow reading skill files
        skill_strs = [str(d) for d in skills_dirs]
        rules.append(InternalRule(
            tool_names=["Read"],
            path_patterns=skill_strs,
            decision="allow",
            reason="Auto-approved: internal skill file read",
            enabled=True,
        ))

        # Rule: Deny writing to skill directories (managed by SkillManager)
        rules.append(InternalRule(
            tool_names=["Write", "Edit"],
            path_patterns=skill_strs,
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

    def _resolve_path(self, path_str: str) -> str:
        """Resolve path, expanding ~ and resolving symlinks."""
        return str(Path(path_str).expanduser().resolve())

    def _matches_path(self, file_path: str, patterns: list[str]) -> bool:
        """Check if file_path starts with any of the path patterns."""
        resolved = self._resolve_path(file_path)
        for pattern in patterns:
            resolved_pattern = self._resolve_path(pattern)
            if resolved.startswith(resolved_pattern):
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
            if not self._matches_path(file_path, rule.path_patterns):
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
