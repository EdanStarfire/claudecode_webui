"""Tests for SkillManager."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.skill_manager import SkillManager


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary directory structure for testing."""
    source_dir = tmp_path / "default_skills"
    global_dir = tmp_path / ".cc_webui"
    claude_dir = tmp_path / ".claude" / "skills"

    source_dir.mkdir(parents=True)
    # global_dir and claude_dir are created by _ensure_directories

    return source_dir, global_dir, claude_dir


@pytest.fixture
def manager(tmp_dirs):
    """Create a SkillManager with patched paths."""
    source_dir, global_dir, claude_dir = tmp_dirs
    mgr = SkillManager()
    mgr.source_dir = source_dir
    mgr.global_dir = global_dir
    mgr.global_skills_dir = global_dir / "skills"
    mgr.global_plans_dir = global_dir / "plans"
    mgr.claude_skills_dir = claude_dir
    return mgr


def _create_skill(source_dir: Path, name: str, content: str = "# Skill"):
    """Helper to create a skill directory with SKILL.md."""
    skill_dir = source_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


class TestEnsureDirectories:
    def test_creates_all_directories(self, manager):
        manager._ensure_directories()
        assert manager.global_skills_dir.exists()
        assert manager.global_plans_dir.exists()
        assert manager.claude_skills_dir.exists()

    def test_idempotent(self, manager):
        manager._ensure_directories()
        manager._ensure_directories()
        assert manager.global_skills_dir.exists()


class TestSyncSkills:
    def test_new_skill_added(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill", "# My Skill")

        added, updated, removed = manager._sync_skills()

        assert added == 1
        assert updated == 0
        assert removed == 0
        assert (manager.global_skills_dir / "my-skill" / "SKILL.md").exists()
        assert (manager.global_skills_dir / "my-skill" / "SKILL.md").read_text() == "# My Skill"

    def test_skill_updated(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill", "# Version 1")

        # First sync
        manager._sync_skills()

        # Update source
        (manager.source_dir / "my-skill" / "SKILL.md").write_text("# Version 2")

        added, updated, removed = manager._sync_skills()

        assert added == 0
        assert updated == 1
        assert removed == 0
        assert (manager.global_skills_dir / "my-skill" / "SKILL.md").read_text() == "# Version 2"

    def test_skill_removed(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "skill-a", "# A")
        _create_skill(manager.source_dir, "skill-b", "# B")

        manager._sync_skills()

        # Remove skill-b from source
        import shutil
        shutil.rmtree(manager.source_dir / "skill-b")

        added, updated, removed = manager._sync_skills()

        assert added == 0
        assert updated == 0
        assert removed == 1
        assert (manager.global_skills_dir / "skill-a").exists()
        assert not (manager.global_skills_dir / "skill-b").exists()

    def test_no_changes(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill", "# Stable")

        manager._sync_skills()
        added, updated, removed = manager._sync_skills()

        assert added == 0
        assert updated == 0
        assert removed == 0

    def test_missing_source_dir(self, manager):
        manager._ensure_directories()
        manager.source_dir = manager.source_dir.parent / "nonexistent"

        added, updated, removed = manager._sync_skills()

        assert added == 0
        assert updated == 0
        assert removed == 0

    def test_multiple_files_in_skill(self, manager):
        manager._ensure_directories()
        skill_dir = _create_skill(manager.source_dir, "complex-skill", "# Main")
        (skill_dir / "helper.sh").write_text("#!/bin/bash\necho hello")

        added, _, _ = manager._sync_skills()

        assert added == 1
        assert (manager.global_skills_dir / "complex-skill" / "SKILL.md").exists()
        assert (manager.global_skills_dir / "complex-skill" / "helper.sh").exists()


class TestManageSymlinks:
    def test_create_new_symlink(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill")
        manager._sync_skills()

        created, conflicts, cleaned = manager._manage_symlinks()

        assert created == 1
        assert conflicts == 0
        assert cleaned == 0

        link = manager.claude_skills_dir / "my-skill"
        assert link.is_symlink()
        assert link.resolve() == (manager.global_skills_dir / "my-skill").resolve()

    def test_conflict_with_user_directory(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill")
        manager._sync_skills()

        # Create a user-owned directory at the same path
        user_dir = manager.claude_skills_dir / "my-skill"
        user_dir.mkdir(parents=True)
        (user_dir / "user-file.md").write_text("User content")

        created, conflicts, cleaned = manager._manage_symlinks()

        assert created == 0
        assert conflicts == 1
        assert cleaned == 0
        # User directory preserved
        assert (user_dir / "user-file.md").read_text() == "User content"

    def test_conflict_with_user_symlink(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill")
        manager._sync_skills()

        # Create a user-owned symlink pointing elsewhere
        user_target = manager.claude_skills_dir.parent / "user-skill-target"
        user_target.mkdir(parents=True)
        link = manager.claude_skills_dir / "my-skill"
        link.symlink_to(user_target)

        created, conflicts, cleaned = manager._manage_symlinks()

        assert created == 0
        assert conflicts == 1
        # User symlink preserved
        assert link.is_symlink()

    def test_update_our_symlink(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill")
        manager._sync_skills()
        manager._manage_symlinks()

        # Symlink already points to correct target — no-op
        created, conflicts, cleaned = manager._manage_symlinks()

        assert created == 0
        assert conflicts == 0
        assert cleaned == 0

    def test_clean_orphaned_symlink(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "skill-a")
        _create_skill(manager.source_dir, "skill-b")
        manager._sync_skills()
        manager._manage_symlinks()

        # Remove skill-b from source and global
        import shutil
        shutil.rmtree(manager.source_dir / "skill-b")
        manager._sync_skills()  # removes from global

        created, conflicts, cleaned = manager._manage_symlinks()

        assert cleaned == 1
        assert not (manager.claude_skills_dir / "skill-b").exists()
        assert (manager.claude_skills_dir / "skill-a").is_symlink()

    def test_broken_symlink_replaced(self, manager):
        manager._ensure_directories()
        _create_skill(manager.source_dir, "my-skill")
        manager._sync_skills()

        # Create a broken symlink at the target path
        link = manager.claude_skills_dir / "my-skill"
        link.symlink_to("/nonexistent/path")

        # It's not our symlink (doesn't point into .cc_webui), so conflict
        created, conflicts, cleaned = manager._manage_symlinks()

        assert conflicts == 1


class TestIsOurSymlink:
    def test_our_symlink(self, manager):
        manager._ensure_directories()
        target = manager.global_skills_dir / "test-skill"
        target.mkdir()
        link = manager.claude_skills_dir / "test-skill"
        link.symlink_to(target)

        assert manager._is_our_symlink(link) is True

    def test_not_our_symlink(self, manager):
        manager._ensure_directories()
        target = manager.claude_skills_dir.parent / "other-dir"
        target.mkdir(parents=True)
        link = manager.claude_skills_dir / "test-skill"
        link.symlink_to(target)

        assert manager._is_our_symlink(link) is False

    def test_not_a_symlink(self, manager):
        manager._ensure_directories()
        regular = manager.claude_skills_dir / "regular-dir"
        regular.mkdir()

        assert manager._is_our_symlink(regular) is False

    def test_broken_symlink(self, manager):
        manager._ensure_directories()
        link = manager.claude_skills_dir / "broken"
        link.symlink_to("/nonexistent")

        assert manager._is_our_symlink(link) is False


class TestDirContentsMatch:
    def test_identical_dirs(self, manager, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file.txt").write_text("same")
        (dir_b / "file.txt").write_text("same")

        assert manager._dir_contents_match(dir_a, dir_b) is True

    def test_different_content(self, manager, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file.txt").write_text("version 1")
        (dir_b / "file.txt").write_text("version 2")

        assert manager._dir_contents_match(dir_a, dir_b) is False

    def test_extra_file(self, manager, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file.txt").write_text("same")
        (dir_b / "file.txt").write_text("same")
        (dir_a / "extra.txt").write_text("extra")

        assert manager._dir_contents_match(dir_a, dir_b) is False


class TestFullSync:
    @pytest.mark.asyncio
    async def test_full_sync_first_run(self, manager):
        _create_skill(manager.source_dir, "skill-a", "# A")
        _create_skill(manager.source_dir, "skill-b", "# B")

        await manager.sync()

        # Global dirs created
        assert manager.global_skills_dir.exists()
        assert manager.global_plans_dir.exists()

        # Skills copied
        assert (manager.global_skills_dir / "skill-a" / "SKILL.md").exists()
        assert (manager.global_skills_dir / "skill-b" / "SKILL.md").exists()

        # Symlinks created
        assert (manager.claude_skills_dir / "skill-a").is_symlink()
        assert (manager.claude_skills_dir / "skill-b").is_symlink()

    @pytest.mark.asyncio
    async def test_full_sync_idempotent(self, manager):
        _create_skill(manager.source_dir, "skill-a", "# A")

        await manager.sync()
        await manager.sync()

        assert (manager.claude_skills_dir / "skill-a").is_symlink()

    @pytest.mark.asyncio
    async def test_sync_handles_permission_error(self, manager):
        """Sync logs and continues on permission errors."""
        with patch.object(manager, "_ensure_directories", side_effect=PermissionError("denied")):
            await manager.sync()
        # No exception raised — graceful degradation
