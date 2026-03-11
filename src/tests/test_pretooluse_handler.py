"""Tests for internal permission handler (issue #707).

Tests the suggestion-based auto-approval logic that evaluates CLI permission
suggestions against internal path rules (history, plans, skills).
"""

from dataclasses import dataclass

import pytest

from src.hooks.pretooluse_handler import InternalPermissionHandler, _extract_dir_from_rule_content


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary directory structure for testing."""
    session_dir = tmp_path / "sessions" / "test-session"
    history_dir = session_dir / "history"
    history_dir.mkdir(parents=True)

    plans_dir = tmp_path / ".cc_webui" / "plans"
    plans_dir.mkdir(parents=True)

    skills_dir1 = tmp_path / ".claude" / "skills"
    skills_dir1.mkdir(parents=True)
    skills_dir2 = tmp_path / ".config" / "cc_webui" / "skills"
    skills_dir2.mkdir(parents=True)

    return {
        "session_dir": session_dir,
        "history_dir": history_dir,
        "plans_dir": plans_dir,
        "skills_dirs": [skills_dir1, skills_dir2],
    }


@pytest.fixture
def handler_km_enabled(tmp_dirs):
    """Handler with knowledge management enabled."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        skills_dirs=tmp_dirs["skills_dirs"],
        knowledge_mgmt_enabled=True,
    )


@pytest.fixture
def handler_km_disabled(tmp_dirs):
    """Handler with knowledge management disabled."""
    return InternalPermissionHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        skills_dirs=tmp_dirs["skills_dirs"],
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

    def test_issue_707_read_skill_allowed(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Read on skills should be auto-approved."""
        skills_dir = str(tmp_dirs["skills_dirs"][0])
        result = handler_km_enabled.evaluate_suggestion(
            "Read", f"//{skills_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "allow"

    def test_issue_707_write_skill_denied(self, handler_km_enabled, tmp_dirs):
        """CLI suggestion to allow Write on skills should be denied."""
        skills_dir = str(tmp_dirs["skills_dirs"][0])
        result = handler_km_enabled.evaluate_suggestion(
            "Write", f"//{skills_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "deny"

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

    def test_issue_707_read_second_skills_dir(self, handler_km_enabled, tmp_dirs):
        """Suggestion for Read on second skills dir should be auto-approved."""
        skills_dir = str(tmp_dirs["skills_dirs"][1])
        result = handler_km_enabled.evaluate_suggestion(
            "Read", f"//{skills_dir}/**", "allow"
        )
        assert result is not None
        assert result[0] == "allow"


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
            skills_dirs=tmp_dirs["skills_dirs"],
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
            skills_dirs=tmp_dirs["skills_dirs"],
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
            skills_dirs=tmp_dirs["skills_dirs"],
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
