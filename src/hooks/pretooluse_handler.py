"""Internal permission handler for auto-approving/denying tool operations.

Evaluates CLI permission suggestions against internal path rules to auto-approve
or deny operations on managed directories (history, plans, skills) without
prompting the user.

The CLI infers permission mappings (e.g., Bash accessing history → Read rule for
that path) and exposes them via ToolPermissionContext.suggestions. This module
checks those suggestions against our internal rules.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_logger = logging.getLogger(__name__)

PermissionDecision = Literal["allow", "deny"]

_RESOLVE_CACHE: dict[str, str] = {}


def _get_field(obj: Any, snake_name: str, camel_name: str) -> Any:
    """Get a field from a dict (camelCase keys) or dataclass (snake_case attrs).

    The SDK types define snake_case attrs (e.g., tool_name, rule_content) but the
    CLI sends suggestions as plain dicts with camelCase keys (toolName, ruleContent).
    """
    if isinstance(obj, dict):
        return obj.get(camel_name) or obj.get(snake_name)
    return getattr(obj, snake_name, None)


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
    """A rule for internal tool access control."""

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


def _extract_dir_from_rule_content(rule_content: str) -> str | None:
    """Extract directory path from a CLI rule_content glob pattern.

    CLI rule_content uses patterns like '//var/home/.../history/**' or
    '/var/home/.../plans/**'. Strip leading double-slash and trailing glob.
    Returns None if the pattern doesn't look like a directory path.
    """
    if not rule_content:
        return None
    # Strip leading // (CLI convention) or keep single /
    path = rule_content.lstrip("/")
    if not path:
        return None
    path = "/" + path  # Re-add single leading slash
    # Strip trailing glob segments (e.g., /**/* or /**)
    while path.endswith("/*") or path.endswith("/**"):
        path = path.rsplit("/", 1)[0]
    return path if path else None


class InternalPermissionHandler:
    """Evaluates CLI permission suggestions against internal path rules.

    When the CLI prompts for tool permission, it includes suggestions — inferred
    permission rules based on the tool and its arguments. For example, a Bash
    command that reads history files produces a suggestion like:
        addRules: Read //path/to/history/** → allow

    This handler checks if the suggested path matches an internal managed directory
    (history, plans, skills) and returns the appropriate decision, avoiding user
    prompts for routine internal operations.
    """

    def __init__(
        self,
        session_data_dir: Path,
        plans_dir: Path,
        skills_dirs: list[Path],
        knowledge_mgmt_enabled: bool,
        memory_dir: Path | None = None,
    ):
        self._rules = self._build_rules(
            session_data_dir, plans_dir, skills_dirs, knowledge_mgmt_enabled, memory_dir
        )

    def _build_rules(
        self,
        session_data_dir: Path,
        plans_dir: Path,
        skills_dirs: list[Path],
        knowledge_mgmt_enabled: bool,
        memory_dir: Path | None = None,
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

        # Rule: Allow reading and writing session memory files (issue #709)
        if memory_dir:
            memory_prefixes = _resolve_prefixes([str(memory_dir)])
            rules.append(InternalRule(
                tool_names=frozenset(["Read", "Write", "Edit"]),
                path_prefixes=memory_prefixes,
                decision="allow",
                reason="Auto-approved: session memory file access",
                enabled=True,
            ))

        return rules

    def _matches_path(self, file_path: str, prefixes: list[str]) -> bool:
        """Check if resolved file_path starts with any pre-resolved prefix.

        Handles both files inside the directory and the directory itself.
        Prefixes include trailing os.sep so '/dir/'.startswith works for '/dir/file',
        but we also need to match '/dir' exactly (the directory path without trailing sep).
        """
        resolved = _resolve_path(file_path)
        for prefix in prefixes:
            if resolved.startswith(prefix) or resolved + os.sep == prefix:
                return True
        return False

    def evaluate_suggestion(
        self, tool_name: str, rule_content: str | None, behavior: str | None
    ) -> tuple[PermissionDecision, str] | None:
        """Evaluate a single CLI permission suggestion rule against internal rules.

        Args:
            tool_name: The tool name from the suggestion rule (e.g., "Read", "Write").
            rule_content: The glob pattern from the suggestion (e.g., "//path/to/history/**").
            behavior: The suggested behavior ("allow", "deny", "ask").

        Returns:
            (decision, reason) tuple if the suggestion matches an internal rule,
            or None if no match (let the normal permission flow handle it).
        """
        if not rule_content:
            return None

        dir_path = _extract_dir_from_rule_content(rule_content)
        if not dir_path:
            return None

        for rule in self._rules:
            if not rule.enabled:
                continue
            if tool_name not in rule.tool_names:
                continue
            if not self._matches_path(dir_path, rule.path_prefixes):
                continue

            # Use OUR decision regardless of CLI suggestion — we enforce deny on
            # read-only dirs even if CLI suggests allow.
            _logger.debug(
                "Suggestion matched internal rule: tool=%s path=%s decision=%s reason=%s",
                tool_name, dir_path, rule.decision, rule.reason,
            )
            return (rule.decision, rule.reason)

        return None

    def evaluate_suggestions(
        self, suggestions: list[Any]
    ) -> tuple[PermissionDecision, str] | None:
        """Evaluate all CLI suggestions against internal rules.

        Checks each suggestion's rules. If ANY rule matches a deny, returns deny.
        If all rules in a suggestion match allow, returns allow.
        Returns None if no suggestion matches internal rules.

        Args:
            suggestions: List of PermissionUpdate objects from context.suggestions.

        Returns:
            (decision, reason) if a matching decision was found, None otherwise.
        """
        for suggestion in suggestions:
            sug_type = _get_field(suggestion, "type", "type")
            if sug_type != "addRules":
                continue

            rules = _get_field(suggestion, "rules", "rules")
            if not rules:
                continue

            behavior = _get_field(suggestion, "behavior", "behavior")
            matched_allows: list[str] = []

            for rule in rules:
                tool_name = _get_field(rule, "tool_name", "toolName")
                rule_content = _get_field(rule, "rule_content", "ruleContent")
                if not tool_name:
                    continue

                result = self.evaluate_suggestion(tool_name, rule_content, behavior)
                if result is None:
                    continue

                decision, reason = result
                if decision == "deny":
                    return ("deny", reason)
                matched_allows.append(reason)

            if matched_allows:
                return ("allow", matched_allows[0])

        return None
