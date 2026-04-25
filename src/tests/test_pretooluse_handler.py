"""Tests for internal permission handler (issue #707, #750).

Tests the suggestion-based auto-approval logic that evaluates CLI permission
suggestions against internal path rules (history, plans) and the dangerous
Bash command guard.
"""

from dataclasses import dataclass

import pytest

from src.hooks.pretooluse_handler import (
    InternalPermissionHandler,
    _extract_dir_from_rule_content,
    _is_dangerous_bash,
)


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary directory structure for testing."""
    session_dir = tmp_path / "sessions" / "test-session"
    history_dir = session_dir / "history"
    history_dir.mkdir(parents=True)

    plans_dir = tmp_path / ".cc_webui" / "plans"
    plans_dir.mkdir(parents=True)

    return {
        "session_dir": session_dir,
        "history_dir": history_dir,
        "plans_dir": plans_dir,
    }


@pytest.fixture
def handler_km_enabled(tmp_dirs):
    """Handler with knowledge management enabled."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        knowledge_mgmt_enabled=True,
    )


@pytest.fixture
def handler_km_disabled(tmp_dirs):
    """Handler with knowledge management disabled."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        knowledge_mgmt_enabled=False,
    )


@dataclass
class FakePermissionRuleValue:
    """Minimal stand-in for claude_agent_sdk.types.PermissionRuleValue."""

    tool_name: str
    rule_content: str | None = None


@dataclass
class FakePermissionUpdate:
    """Minimal stand-in for claude_agent_sdk.types.PermissionUpdate."""

    type: str
    rules: list[FakePermissionRuleValue] | None = None
    behavior: str | None = None
    destination: str = "session"


class TestExtractDirFromRuleContent:
    """Tests for _extract_dir_from_rule_content helper."""

    def test_double_slash_glob(self):
        assert _extract_dir_from_rule_content("//var/home/user/history/**") == "/var/home/user/history"

    def test_single_slash_glob(self):
        assert _extract_dir_from_rule_content("/var/home/user/plans/**") == "/var/home/user/plans"

    def test_no_glob(self):
        assert _extract_dir_from_rule_content("/var/home/user/plans") == "/var/home/user/plans"

    def test_empty(self):
        assert _extract_dir_from_rule_content("") is None

    def test_only_slashes(self):
        assert _extract_dir_from_rule_content("//") is None

    def test_nested_glob(self):
        assert _extract_dir_from_rule_content("//home/user/.claude/skills/**/*") == "/home/user/.claude/skills"


class TestSuggestionEvaluation:
    """Tests for evaluate_suggestion (single rule evaluation)."""

    def test_issue_707_read_history_allowed(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Read on history path should be auto-approved."""
        history_dir = str(tmp_dirs["history_dir"])
        result = handler_km_enabled.evaluate_suggestion(
            "Read", f"//{history_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_write_history_denied(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Write on history should be denied (our rule overrides)."""
        history_dir = str(tmp_dirs["history_dir"])
        result = handler_km_enabled.evaluate_suggestion(
            "Write", f"//{history_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "deny"

    def test_issue_707_read_plan_allowed(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Read on plans should be auto-approved."""
        plans_dir = str(tmp_dirs["plans_dir"])
        result = handler_km_enabled.evaluate_suggestion(
            "Read", f"//{plans_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_write_plan_allowed(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Write on plans should be auto-approved."""
        plans_dir = str(tmp_dirs["plans_dir"])
        result = handler_km_enabled.evaluate_suggestion(
            "Write", f"//{plans_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_unrelated_path_no_match(self, handler_km_enabled):
        """Suggestion for unrelated path should return None (no opinion)."""
        result = handler_km_enabled.evaluate_suggestion(
            "Read", "//home/user/project/src/**", "allow"
        )
        assert result is None

    def test_issue_707_no_rule_content(self, handler_km_enabled):
        """Suggestion without rule_content should return None."""
        result = handler_km_enabled.evaluate_suggestion("Read", None, "allow")
        assert result is None

    def test_issue_707_read_history_km_disabled(self, handler_km_disabled, tmp_dirs):
        """Suggestion for Read on history should return None when KM disabled."""
        history_dir = str(tmp_dirs["history_dir"])
        result = handler_km_disabled.evaluate_suggestion(
            "Read", f"//{history_dir}/**", "allow"
        )
        assert result is None


class TestEvaluateSuggestions:
    """Tests for evaluate_suggestions (full suggestion list evaluation)."""

    def test_issue_707_allow_single_rule(self, handler_km_enabled, tmp_dirs):
        """Single addRules suggestion matching allow should return allow."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{history_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_deny_overrides_allow(self, handler_km_enabled, tmp_dirs):
        """If any rule matches deny, entire result should be deny."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[
                    FakePermissionRuleValue("Read", f"//{history_dir}/**"),
                    FakePermissionRuleValue("Write", f"//{history_dir}/**"),
                ],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "deny"

    def test_issue_707_non_addrules_skipped(self, handler_km_enabled):
        """Non-addRules suggestions should be skipped."""
        suggestions = [
            FakePermissionUpdate(type="setMode", behavior="allow")
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_707_no_match(self, handler_km_enabled):
        """Suggestions with no matching paths should return None."""
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", "//home/user/project/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_707_empty_list(self, handler_km_enabled):
        """Empty suggestions list should return None."""
        result = handler_km_enabled.evaluate_suggestions([])
        assert result is None

    def test_issue_707_multiple_suggestions_first_match_wins(self, handler_km_enabled, tmp_dirs):
        """First matching suggestion should win."""
        plans_dir = str(tmp_dirs["plans_dir"])
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{plans_dir}/**")],
                behavior="allow",
            ),
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{history_dir}/**")],
                behavior="allow",
            ),
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"
        assert "plan" in result[1].lower()

    def test_issue_707_suggestion_with_empty_rules(self, handler_km_enabled):
        """Suggestion with empty rules list should be skipped."""
        suggestions = [
            FakePermissionUpdate(type="addRules", rules=[], behavior="allow")
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_707_suggestion_with_none_rules(self, handler_km_enabled):
        """Suggestion with None rules should be skipped."""
        suggestions = [
            FakePermissionUpdate(type="addRules", rules=None, behavior="allow")
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None


class TestDictFormatSuggestions:
    """Tests for dict-format suggestions (as sent by CLI on the wire)."""

    def test_issue_707_dict_allow_history_read(self, handler_km_enabled, tmp_dirs):
        """Dict suggestion with camelCase keys should be auto-approved."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [{
            "type": "addRules",
            "rules": [{"toolName": "Read", "ruleContent": f"//{history_dir}/**"}],
            "behavior": "allow",
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_dict_deny_history_write(self, handler_km_enabled, tmp_dirs):
        """Dict suggestion for Write on history should be denied."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [{
            "type": "addRules",
            "rules": [
                {"toolName": "Read", "ruleContent": f"//{history_dir}/**"},
                {"toolName": "Write", "ruleContent": f"//{history_dir}/**"},
            ],
            "behavior": "allow",
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "deny"

    def test_issue_707_dict_unrelated_path_no_match(self, handler_km_enabled):
        """Dict suggestion for unrelated path should return None."""
        suggestions = [{
            "type": "addRules",
            "rules": [{"toolName": "Read", "ruleContent": "//home/user/project/**"}],
            "behavior": "allow",
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_707_dict_plan_write_allowed(self, handler_km_enabled, tmp_dirs):
        """Dict suggestion for Write on plans should be auto-approved."""
        plans_dir = str(tmp_dirs["plans_dir"])
        suggestions = [{
            "type": "addRules",
            "rules": [{"toolName": "Write", "ruleContent": f"//{plans_dir}/**"}],
            "behavior": "allow",
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"


class TestAddDirectoriesSuggestion:
    """Tests for addDirectories suggestion auto-approval (issue #709)."""

    def test_issue_709_add_memory_dir_auto_approved(self, tmp_dirs):
        """addDirectories for session memory dir should be auto-approved."""
        memory_dir = tmp_dirs["session_dir"] / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        handler = InternalPermissionHandler(
            session_data_dir=tmp_dirs["session_dir"],
            plans_dir=tmp_dirs["plans_dir"],
            knowledge_mgmt_enabled=True,
            memory_dir=memory_dir,
        )
        suggestions = [{
            "type": "addDirectories",
            "directories": [str(memory_dir)],
            "destination": "session",
        }]
        result = handler.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"
        assert result[1] == "Auto-approved: session memory file access"

    def test_issue_709_add_unmanaged_dir_not_approved(self, tmp_dirs):
        """addDirectories for an unmanaged dir should not be auto-approved."""
        memory_dir = tmp_dirs["session_dir"] / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        handler = InternalPermissionHandler(
            session_data_dir=tmp_dirs["session_dir"],
            plans_dir=tmp_dirs["plans_dir"],
            knowledge_mgmt_enabled=True,
            memory_dir=memory_dir,
        )
        suggestions = [{
            "type": "addDirectories",
            "directories": ["/some/random/directory"],
            "destination": "session",
        }]
        result = handler.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_709_add_mixed_dirs_not_approved(self, tmp_dirs):
        """addDirectories with mix of managed and unmanaged dirs should not auto-approve."""
        memory_dir = tmp_dirs["session_dir"] / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        handler = InternalPermissionHandler(
            session_data_dir=tmp_dirs["session_dir"],
            plans_dir=tmp_dirs["plans_dir"],
            knowledge_mgmt_enabled=True,
            memory_dir=memory_dir,
        )
        suggestions = [{
            "type": "addDirectories",
            "directories": [str(memory_dir), "/some/random/directory"],
            "destination": "session",
        }]
        result = handler.evaluate_suggestions(suggestions)
        assert result is None

    def test_issue_709_add_plans_dir_auto_approved(self, handler_km_enabled, tmp_dirs):
        """addDirectories for plans dir should also be auto-approved."""
        suggestions = [{
            "type": "addDirectories",
            "directories": [str(tmp_dirs["plans_dir"])],
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"
        assert result[1] == "Auto-approved: internal plan file access"

    def test_issue_709_add_dir_without_memory_configured(self, handler_km_enabled, tmp_dirs):
        """addDirectories for a session memory path should not auto-approve when memory not configured."""
        memory_dir = tmp_dirs["session_dir"] / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        # handler_km_enabled has no memory_dir configured
        suggestions = [{
            "type": "addDirectories",
            "directories": [str(memory_dir)],
            "destination": "session",
        }]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is None


class TestIsDangerousBash:
    """Tests for _is_dangerous_bash helper (issue #750)."""

    def test_rm_rf(self):
        assert _is_dangerous_bash("rm -rf /home/user/.claude/skills/") is True

    def test_rm_simple(self):
        assert _is_dangerous_bash("rm file.txt") is True

    def test_rmdir(self):
        assert _is_dangerous_bash("rmdir /some/dir") is True

    def test_chmod(self):
        assert _is_dangerous_bash("chmod 777 /etc/passwd") is True

    def test_chown(self):
        assert _is_dangerous_bash("chown root:root /etc/passwd") is True

    def test_mv(self):
        assert _is_dangerous_bash("mv important.txt /dev/null") is True

    def test_redirect_overwrite(self):
        assert _is_dangerous_bash("echo bad > /etc/config") is True

    def test_truncate(self):
        assert _is_dangerous_bash("truncate -s 0 /var/log/syslog") is True

    def test_shred(self):
        assert _is_dangerous_bash("shred /var/data/secrets") is True

    def test_dd(self):
        assert _is_dangerous_bash("dd if=/dev/zero of=/dev/sda") is True

    def test_mkfs(self):
        assert _is_dangerous_bash("mkfs.ext4 /dev/sda1") is True

    def test_safe_cat(self):
        assert _is_dangerous_bash("cat /home/user/file.txt") is False

    def test_safe_ls(self):
        assert _is_dangerous_bash("ls -la /home/user") is False

    def test_safe_grep(self):
        assert _is_dangerous_bash("grep -r 'pattern' /src") is False

    def test_safe_echo(self):
        assert _is_dangerous_bash("echo hello world") is False


class TestBashDenyPatternGuard:
    """Tests for Bash deny-pattern guard in evaluate_suggestions (issue #750)."""

    def test_issue_750_bash_rm_on_plan_dir_no_auto_approve(self, handler_km_enabled, tmp_dirs):
        """Bash rm -rf on plans dir should NOT be auto-approved despite matching plan rule."""
        plans_dir = str(tmp_dirs["plans_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{plans_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(
            suggestions,
            actual_tool="Bash",
            tool_input={"command": f"rm -rf {plans_dir}"},
        )
        # Should return None (fall through to user prompt), not allow
        assert result is None

    def test_issue_750_read_tool_on_plan_dir_still_approved(self, handler_km_enabled, tmp_dirs):
        """Read tool on plans dir should still be auto-approved (not Bash)."""
        plans_dir = str(tmp_dirs["plans_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{plans_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(
            suggestions,
            actual_tool="Read",
            tool_input={"file_path": f"{plans_dir}/issue-1.md"},
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_750_bash_cat_on_plan_dir_still_approved(self, handler_km_enabled, tmp_dirs):
        """Bash cat (non-dangerous) on plans dir should still be auto-approved."""
        plans_dir = str(tmp_dirs["plans_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{plans_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(
            suggestions,
            actual_tool="Bash",
            tool_input={"command": f"cat {plans_dir}/issue-1.md"},
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_750_bash_rm_on_history_dir_no_auto_approve(self, handler_km_enabled, tmp_dirs):
        """Bash rm on history dir should NOT be auto-approved."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{history_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(
            suggestions,
            actual_tool="Bash",
            tool_input={"command": f"rm -rf {history_dir}"},
        )
        assert result is None

    def test_issue_750_no_actual_tool_still_works(self, handler_km_enabled, tmp_dirs):
        """Without actual_tool param, existing behavior is preserved."""
        plans_dir = str(tmp_dirs["plans_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Read", f"//{plans_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(suggestions)
        assert result is not None
        assert result[0] == "allow"

    def test_issue_750_deny_rules_still_deny_regardless(self, handler_km_enabled, tmp_dirs):
        """Deny rules should still deny even for non-Bash tools."""
        history_dir = str(tmp_dirs["history_dir"])
        suggestions = [
            FakePermissionUpdate(
                type="addRules",
                rules=[FakePermissionRuleValue("Write", f"//{history_dir}/**")],
                behavior="allow",
            )
        ]
        result = handler_km_enabled.evaluate_suggestions(
            suggestions,
            actual_tool="Write",
            tool_input={"file_path": f"{history_dir}/test.md"},
        )
        assert result is not None
        assert result[0] == "deny"

    def test_issue_750_bash_chmod_on_add_directories_no_auto_approve(self, tmp_dirs):
        """Bash chmod via addDirectories should NOT be auto-approved."""
        memory_dir = tmp_dirs["session_dir"] / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        handler = InternalPermissionHandler(
            session_data_dir=tmp_dirs["session_dir"],
            plans_dir=tmp_dirs["plans_dir"],
            knowledge_mgmt_enabled=True,
            memory_dir=memory_dir,
        )
        suggestions = [{
            "type": "addDirectories",
            "directories": [str(memory_dir)],
            "destination": "session",
        }]
        result = handler.evaluate_suggestions(
            suggestions,
            actual_tool="Bash",
            tool_input={"command": f"chmod -R 777 {memory_dir}"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# Issue #1133: TestToolBlock — evaluate_tool_block matrix
# ---------------------------------------------------------------------------

@pytest.fixture
def handler_legion(tmp_dirs):
    """Handler configured for a Legion session (is_legion=True)."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        knowledge_mgmt_enabled=False,
        is_legion=True,
    )


@pytest.fixture
def handler_non_legion(tmp_dirs):
    """Handler configured for a non-Legion session (is_legion=False)."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        knowledge_mgmt_enabled=False,
        is_legion=False,
    )


class TestToolBlock:
    """Tests for evaluate_tool_block — the Legion-scoped tool deny list (issue #1133)."""

    def test_sendmessage_denied_in_legion(self, handler_legion):
        """SendMessage is denied in Legion sessions and reason mentions send_comm."""
        result = handler_legion.evaluate_tool_block("SendMessage", {})
        assert result is not None
        decision, reason = result
        assert decision == "deny"
        assert "mcp__legion__send_comm" in reason

    def test_sendmessage_not_blocked_outside_legion(self, handler_non_legion):
        """SendMessage passes through (returns None) in non-Legion sessions."""
        result = handler_non_legion.evaluate_tool_block("SendMessage", {})
        assert result is None

    def test_agent_background_true_denied_in_legion(self, handler_legion):
        """Agent with run_in_background=True is denied in Legion sessions."""
        result = handler_legion.evaluate_tool_block("Agent", {"run_in_background": True})
        assert result is not None
        decision, reason = result
        assert decision == "deny"
        assert "mcp__legion__spawn_minion" in reason

    def test_agent_background_false_not_blocked(self, handler_legion):
        """Agent with run_in_background=False is NOT blocked (foreground call)."""
        result = handler_legion.evaluate_tool_block("Agent", {"run_in_background": False})
        assert result is None

    def test_agent_background_absent_not_blocked(self, handler_legion):
        """Agent with no run_in_background key is NOT blocked (foreground call)."""
        result = handler_legion.evaluate_tool_block("Agent", {"prompt": "do something"})
        assert result is None

    def test_agent_background_truthy_string_not_blocked(self, handler_legion):
        """Agent with run_in_background='true' (string) is NOT blocked — strict is True check."""
        result = handler_legion.evaluate_tool_block("Agent", {"run_in_background": "true"})
        assert result is None

    def test_agent_not_blocked_outside_legion_even_with_background(self, handler_non_legion):
        """Background Agent in non-Legion session returns None (passes to normal flow)."""
        result = handler_non_legion.evaluate_tool_block("Agent", {"run_in_background": True})
        assert result is None

    def test_other_tools_pass_through(self, handler_legion):
        """Read, Write, Bash with arbitrary inputs return None in Legion session."""
        assert handler_legion.evaluate_tool_block("Read", {"file_path": "/some/file"}) is None
        assert handler_legion.evaluate_tool_block("Write", {"file_path": "/out", "content": "x"}) is None
        assert handler_legion.evaluate_tool_block("Bash", {"command": "ls"}) is None
