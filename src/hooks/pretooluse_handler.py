"""Internal permission handler for auto-approving/denying tool operations.

Evaluates CLI permission suggestions against internal path rules to auto-approve
or deny operations on managed directories (history, plans) without prompting the
user. Also guards against dangerous Bash commands being silently auto-approved.

The CLI infers permission mappings (e.g., Bash accessing history → Read rule for
that path) and exposes them via ToolPermissionContext.suggestions. This module
checks those suggestions against our internal rules.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_logger = logging.getLogger(__name__)

PermissionDecision = Literal["allow", "deny"]

_RESOLVE_CACHE: dict[str, str] = {}

# Issue #1133: Reason strings for Legion tool blocks.
# Tool names are hardcoded constants — if the SDK renames them, update here.
_SENDMESSAGE_REDIRECT_REASON = (
    "SendMessage is not available in Legion sessions. "
    "Use mcp__legion__send_comm to route communications to other minions."
)
_BACKGROUND_AGENT_REDIRECT_REASON = (
    "Agent with run_in_background=true is not available in Legion sessions. "
    "Use mcp__legion__spawn_minion to create observable child agents."
)

# Dangerous Bash command patterns that must never be silently auto-approved.
# The CLI may misclassify destructive Bash commands (e.g., `rm -rf ~/.claude/skills/`)
# as benign file reads, causing them to match internal allow rules. This deny list
# ensures such commands always fall through to the user permission prompt.
DANGEROUS_BASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\b"),         # file/directory deletion
    re.compile(r"\brmdir\b"),      # directory deletion
    re.compile(r"\bchmod\b"),      # permission changes
    re.compile(r"\bchown\b"),      # ownership changes
    re.compile(r"\bmv\b"),         # move/rename (can overwrite)
    re.compile(r">\s*/"),          # redirect overwrite to absolute path
    re.compile(r"\btruncate\b"),   # file truncation
    re.compile(r"\bshred\b"),      # secure deletion
    re.compile(r"\bmkfs\b"),       # filesystem creation
    re.compile(r"\bdd\b"),         # raw disk operations
]


def _is_dangerous_bash(command: str) -> bool:
    """Check if a Bash command matches any dangerous pattern."""
    return any(pattern.search(command) for pattern in DANGEROUS_BASH_PATTERNS)


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
        knowledge_mgmt_enabled: bool,
        memory_dir: Path | None = None,
        skill_creating_enabled: bool = False,
        working_directory: Path | None = None,
        is_legion: bool = False,
    ):
        self._skill_creating_enabled = skill_creating_enabled
        self._is_legion = is_legion
        self._rules = self._build_rules(
            session_data_dir, plans_dir, knowledge_mgmt_enabled, memory_dir,
            skill_creating_enabled, working_directory,
        )
        # Collect (prefix, reason) pairs from allow rules for addDirectories auto-approval
        self._managed_prefix_reasons: list[tuple[str, str]] = [
            (prefix, rule.reason)
            for rule in self._rules
            if rule.enabled and rule.decision == "allow"
            for prefix in rule.path_prefixes
        ]

    def _build_rules(
        self,
        session_data_dir: Path,
        plans_dir: Path,
        knowledge_mgmt_enabled: bool,
        memory_dir: Path | None = None,
        skill_creating_enabled: bool = False,
        working_directory: Path | None = None,
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

        # Rule: Allow reading/writing/editing skills directory (issue #749)
        if skill_creating_enabled and working_directory:
            skills_prefixes = _resolve_prefixes(
                [str(working_directory / ".claude" / "skills")]
            )
            rules.append(InternalRule(
                tool_names=frozenset(["Read", "Write", "Edit"]),
                path_prefixes=skills_prefixes,
                decision="allow",
                reason="Auto-approved: skill creating (issue #749)",
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

    def _check_directories_managed(self, directories: list[str]) -> str | None:
        """Check if all directories in an addDirectories suggestion are managed paths.

        Returns the reason from the first matched rule if every directory matches,
        or None if any directory is unmanaged.
        """
        first_reason = None
        for dir_path in directories:
            resolved = _resolve_path(dir_path)
            matched_reason = None
            for prefix, reason in self._managed_prefix_reasons:
                if resolved.startswith(prefix.rstrip(os.sep)):
                    matched_reason = reason
                    break
            if matched_reason is None:
                return None
            if first_reason is None:
                first_reason = matched_reason
        return first_reason

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

    def evaluate_direct(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> tuple[PermissionDecision, str] | None:
        """Evaluate a tool call directly against internal rules, without suggestions.

        Checks the tool name and its file_path input against path-based rules.
        Used as a fallback when the CLI provides no suggestions but the tool
        operates on a managed path (e.g., Write to .claude/skills/).

        Also handles non-path tools like Skill that are gated on feature flags.

        Returns:
            (decision, reason) if a matching rule was found, None otherwise.
        """
        # Issue #749: Auto-approve Skill tool when skill creating is enabled
        if self._skill_creating_enabled and tool_name == "Skill":
            return ("allow", "Auto-approved: skill invocation (skill creating enabled)")

        if not tool_input:
            return None

        file_path = tool_input.get("file_path")
        if not file_path:
            return None

        # Guard dangerous Bash commands even if path matches
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if _is_dangerous_bash(command):
                return None

        for rule in self._rules:
            if not rule.enabled:
                continue
            if tool_name not in rule.tool_names:
                continue
            if not self._matches_path(file_path, rule.path_prefixes):
                continue
            _logger.debug(
                "Direct match internal rule: tool=%s path=%s decision=%s reason=%s",
                tool_name, file_path, rule.decision, rule.reason,
            )
            return (rule.decision, rule.reason)

        return None

    def evaluate_tool_block(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None,
    ) -> tuple[PermissionDecision, str] | None:
        """Block specific tools unconditionally in Legion sessions (issue #1133).

        Returns (decision, reason) if the tool is blocked, None otherwise.
        Only active when is_legion=True — non-Legion sessions are unaffected.

        Checks run_in_background with `is True` (not truthiness) to avoid blocking
        foreground Agent calls that pass False, 0, or a truthy string.
        """
        if not self._is_legion:
            return None
        if tool_name == "SendMessage":
            return ("deny", _SENDMESSAGE_REDIRECT_REASON)
        if tool_name == "Agent" and tool_input and tool_input.get("run_in_background") is True:
            return ("deny", _BACKGROUND_AGENT_REDIRECT_REASON)
        return None

    def evaluate_suggestions(
        self,
        suggestions: list[Any],
        actual_tool: str | None = None,
        tool_input: dict[str, Any] | None = None,
    ) -> tuple[PermissionDecision, str] | None:
        """Evaluate all CLI suggestions against internal rules.

        Checks each suggestion's rules. If ANY rule matches a deny, returns deny.
        If all rules in a suggestion match allow, returns allow.
        Returns None if no suggestion matches internal rules.

        Before returning an allow decision, checks whether the actual tool is Bash
        with a dangerous command — if so, returns None to force a user prompt
        (issue #750).

        Args:
            suggestions: List of PermissionUpdate objects from context.suggestions.
            actual_tool: The real tool name (e.g., "Bash") — may differ from the
                suggestion's inferred tool name.
            tool_input: The tool's input parameters (e.g., {"command": "rm -rf /"}).

        Returns:
            (decision, reason) if a matching decision was found, None otherwise.
        """
        # Issue #749: Auto-approve all Skill tool invocations when skill creating is enabled
        if self._skill_creating_enabled and actual_tool == "Skill":
            return ("allow", "Auto-approved: skill invocation (skill creating enabled)")

        for suggestion in suggestions:
            sug_type = _get_field(suggestion, "type", "type")

            # Handle addDirectories: auto-approve if all directories are managed paths
            if sug_type == "addDirectories":
                directories = _get_field(suggestion, "directories", "directories")
                if directories:
                    reason = self._check_directories_managed(directories)
                    if reason:
                        # Issue #750: Guard against dangerous Bash commands
                        if actual_tool == "Bash" and tool_input:
                            command = tool_input.get("command", "")
                            if _is_dangerous_bash(command):
                                _logger.info(
                                    "Blocked auto-approve for dangerous Bash command: %s",
                                    command,
                                )
                                return None
                        return ("allow", reason)
                continue

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
                # Issue #750: Guard against dangerous Bash commands being auto-approved
                if actual_tool == "Bash" and tool_input:
                    command = tool_input.get("command", "")
                    if _is_dangerous_bash(command):
                        _logger.info(
                            "Blocked auto-approve for dangerous Bash command: %s", command
                        )
                        return None
                return ("allow", matched_allows[0])

        return None
