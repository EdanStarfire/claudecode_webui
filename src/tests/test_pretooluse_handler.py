"""Tests for PreToolUse hook handler (issue #707)."""

import pytest

from src.hooks.pretooluse_handler import PreToolUseHandler


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
    return PreToolUseHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        skills_dirs=tmp_dirs["skills_dirs"],
        knowledge_mgmt_enabled=True,
    )


@pytest.fixture
def handler_km_disabled(tmp_dirs):
    """Handler with knowledge management disabled."""
    return PreToolUseHandler(
        session_data_dir=tmp_dirs["session_dir"],
        plans_dir=tmp_dirs["plans_dir"],
        skills_dirs=tmp_dirs["skills_dirs"],
        knowledge_mgmt_enabled=False,
    )


def _make_hook_input(tool_name: str, tool_input: dict) -> dict:
    """Create a minimal PreToolUseHookInput-compatible dict."""
    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "test-id",
        "session_id": "test-session",
        "cwd": "/tmp",
        "permission_mode": "default",
    }


class TestHistoryRules:
    """Tests for history file access rules."""

    @pytest.mark.asyncio
    async def test_issue_707_history_read_allowed(self, handler_km_enabled, tmp_dirs):
        """Read tool on history path should be auto-approved when KM enabled."""
        history_file = str(tmp_dirs["history_dir"] / "2024-01-01.md")
        hook_input = _make_hook_input("Read", {"file_path": history_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    @pytest.mark.asyncio
    async def test_issue_707_history_read_fallthrough_when_km_disabled(self, handler_km_disabled, tmp_dirs):
        """Read on history path should fall through when KM disabled."""
        history_file = str(tmp_dirs["history_dir"] / "2024-01-01.md")
        hook_input = _make_hook_input("Read", {"file_path": history_file})
        result = await handler_km_disabled.handle(hook_input, None, {})
        # No hookSpecificOutput means no opinion — falls through to normal permissions
        assert "hookSpecificOutput" not in result or result.get("hookSpecificOutput") is None

    @pytest.mark.asyncio
    async def test_issue_707_history_write_denied(self, handler_km_enabled, tmp_dirs):
        """Write tool on history path should be denied (read-only)."""
        history_file = str(tmp_dirs["history_dir"] / "2024-01-01.md")
        hook_input = _make_hook_input("Write", {"file_path": history_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    @pytest.mark.asyncio
    async def test_issue_707_history_edit_denied(self, handler_km_enabled, tmp_dirs):
        """Edit tool on history path should be denied (read-only)."""
        history_file = str(tmp_dirs["history_dir"] / "2024-01-01.md")
        hook_input = _make_hook_input("Edit", {"file_path": history_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    @pytest.mark.asyncio
    async def test_issue_707_history_write_denied_even_when_km_disabled(self, handler_km_disabled, tmp_dirs):
        """Write to history should be denied even when KM is disabled."""
        history_file = str(tmp_dirs["history_dir"] / "2024-01-01.md")
        hook_input = _make_hook_input("Write", {"file_path": history_file})
        result = await handler_km_disabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestPlanRules:
    """Tests for plan file access rules."""

    @pytest.mark.asyncio
    async def test_issue_707_plan_read_allowed(self, handler_km_enabled, tmp_dirs):
        """Read on plan file should be auto-approved."""
        plan_file = str(tmp_dirs["plans_dir"] / "issue-42.md")
        hook_input = _make_hook_input("Read", {"file_path": plan_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    @pytest.mark.asyncio
    async def test_issue_707_plan_write_allowed(self, handler_km_enabled, tmp_dirs):
        """Write on plan file should be auto-approved."""
        plan_file = str(tmp_dirs["plans_dir"] / "issue-42.md")
        hook_input = _make_hook_input("Write", {"file_path": plan_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


class TestSkillRules:
    """Tests for skill file access rules."""

    @pytest.mark.asyncio
    async def test_issue_707_skill_read_allowed(self, handler_km_enabled, tmp_dirs):
        """Read on skill file should be auto-approved."""
        skill_file = str(tmp_dirs["skills_dirs"][0] / "plan-manager" / "SKILL.md")
        hook_input = _make_hook_input("Read", {"file_path": skill_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    @pytest.mark.asyncio
    async def test_issue_707_skill_read_second_dir(self, handler_km_enabled, tmp_dirs):
        """Read on skill file in second skills dir should be auto-approved."""
        skill_file = str(tmp_dirs["skills_dirs"][1] / "custom-skill" / "SKILL.md")
        hook_input = _make_hook_input("Read", {"file_path": skill_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    @pytest.mark.asyncio
    async def test_issue_707_skill_write_denied(self, handler_km_enabled, tmp_dirs):
        """Write on skill file should be denied."""
        skill_file = str(tmp_dirs["skills_dirs"][0] / "plan-manager" / "SKILL.md")
        hook_input = _make_hook_input("Write", {"file_path": skill_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    @pytest.mark.asyncio
    async def test_issue_707_skill_edit_denied(self, handler_km_enabled, tmp_dirs):
        """Edit on skill file should be denied."""
        skill_file = str(tmp_dirs["skills_dirs"][0] / "plan-manager" / "SKILL.md")
        hook_input = _make_hook_input("Edit", {"file_path": skill_file})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestFallthrough:
    """Tests for non-matching cases (normal permission flow)."""

    @pytest.mark.asyncio
    async def test_issue_707_unrelated_path_fallthrough(self, handler_km_enabled):
        """Read on unrelated path should produce empty output (no opinion)."""
        hook_input = _make_hook_input("Read", {"file_path": "/home/user/project/src/main.py"})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert "hookSpecificOutput" not in result or result.get("hookSpecificOutput") is None

    @pytest.mark.asyncio
    async def test_issue_707_bash_tool_fallthrough(self, handler_km_enabled):
        """Bash tool should fall through (no file_path extracted)."""
        hook_input = _make_hook_input("Bash", {"command": "ls -la"})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert "hookSpecificOutput" not in result or result.get("hookSpecificOutput") is None

    @pytest.mark.asyncio
    async def test_issue_707_no_file_path_in_read(self, handler_km_enabled):
        """Read without file_path should fall through."""
        hook_input = _make_hook_input("Read", {})
        result = await handler_km_enabled.handle(hook_input, None, {})
        assert "hookSpecificOutput" not in result or result.get("hookSpecificOutput") is None
