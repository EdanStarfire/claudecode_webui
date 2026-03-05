"""
Skill Manager for Claude Code WebUI

Manages global skill deployment outside project directories.
Skills are stored in src/default_skills/ and synced to ~/.config/cc_webui/skills/
at startup, with symlinks created in ~/.claude/skills/.

This avoids injecting skills into user project directories and provides
a single source of truth for WebUI infrastructure skills.
"""

import filecmp
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .logging_config import get_logger

skill_logger = get_logger("skill_manager", category="SKILL_MANAGER")
logger = logging.getLogger(__name__)

# Old path (for migration detection)
OLD_GLOBAL_SKILLS_DIR = Path.home() / ".cc_webui" / "skills"
# New path
NEW_GLOBAL_SKILLS_DIR = Path.home() / ".config" / "cc_webui" / "skills"


class SkillManager:
    """Manages global skill and plan directories for WebUI infrastructure.

    Sync flow:
    1. Migrate skills from ~/.cc_webui/skills/ to ~/.config/cc_webui/skills/ if needed
    2. Ensure ~/.config/cc_webui/skills/ and ~/.cc_webui/plans/ exist
    3. Copy skill directories from src/default_skills/ to ~/.config/cc_webui/skills/
    4. Create symlinks in ~/.claude/skills/ pointing to ~/.config/cc_webui/skills/
    5. Detect and log conflicts with user-owned files
    6. Clean up orphaned skills and symlinks
    """

    def __init__(self):
        self.source_dir = Path(__file__).parent / "default_skills"
        self.global_skills_dir = NEW_GLOBAL_SKILLS_DIR
        self.global_plans_dir = Path.home() / ".cc_webui" / "plans"
        self.claude_skills_dir = Path.home() / ".claude" / "skills"
        self.last_sync_time: str | None = None

        skill_logger.debug(
            f"SkillManager initialized: source={self.source_dir}, "
            f"global_skills={self.global_skills_dir}, claude={self.claude_skills_dir}"
        )

    def migrate_skills_directory(self):
        """Migrate skills from ~/.cc_webui/skills/ to ~/.config/cc_webui/skills/.

        Only migrates if old directory exists and new directory does not.
        """
        old_dir = OLD_GLOBAL_SKILLS_DIR
        new_dir = self.global_skills_dir

        if not old_dir.exists():
            return

        if new_dir.exists():
            skill_logger.warning(
                f"Both old ({old_dir}) and new ({new_dir}) skills directories exist. "
                f"Using new location. Old directory can be removed manually."
            )
            return

        try:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old_dir, new_dir)
            shutil.rmtree(old_dir)
            skill_logger.info(f"Migrated skills directory: {old_dir} -> {new_dir}")
        except Exception as e:
            logger.error(f"Failed to migrate skills directory: {e}")
            skill_logger.warning(
                f"Migration failed, old directory remains at {old_dir}. "
                "Will use old location as fallback."
            )
            if not new_dir.exists():
                self.global_skills_dir = old_dir

    async def sync(self):
        """Orchestrate the full skill sync flow.

        Returns:
            dict with added, updated, removed, symlinks_created counts.
        """
        result = {"added": 0, "updated": 0, "removed": 0, "symlinks_created": 0}
        try:
            self.migrate_skills_directory()
            self._ensure_directories()
            added, updated, removed = self._sync_skills()
            created, conflicts, cleaned = self._manage_symlinks()
            self.last_sync_time = datetime.now(UTC).isoformat()

            result.update(
                added=added, updated=updated, removed=removed, symlinks_created=created
            )

            skill_logger.info(
                f"Skill sync complete: "
                f"skills(added={added}, updated={updated}, removed={removed}), "
                f"symlinks(created={created}, conflicts={conflicts}, cleaned={cleaned})"
            )
        except PermissionError as e:
            logger.error(f"Permission error during skill sync: {e}")
            skill_logger.warning(
                "Skill sync failed due to permissions. "
                "WebUI will continue without global skill management."
            )
        except Exception as e:
            logger.error(f"Unexpected error during skill sync: {e}")
        return result

    async def cleanup_symlinks(self):
        """Remove all symlinks in ~/.claude/skills/ that point to our skills directory."""
        cleaned = 0
        if not self.claude_skills_dir.exists():
            return cleaned
        for entry in sorted(self.claude_skills_dir.iterdir()):
            if entry.is_symlink() and self._is_our_symlink(entry):
                entry.unlink()
                cleaned += 1
                skill_logger.info(f"Cleaned symlink (sync disabled): {entry.name}")
        skill_logger.info(f"Symlink cleanup complete: {cleaned} removed")
        return cleaned

    def _ensure_directories(self):
        """Create ~/.config/cc_webui/skills/ and ~/.cc_webui/plans/ if missing."""
        self.global_skills_dir.mkdir(parents=True, exist_ok=True)
        self.global_plans_dir.mkdir(parents=True, exist_ok=True)
        self.claude_skills_dir.mkdir(parents=True, exist_ok=True)
        skill_logger.debug("Global directories verified")

    def _sync_skills(self) -> tuple[int, int, int]:
        """Copy skill directories from source to ~/.cc_webui/skills/.

        Returns:
            Tuple of (added, updated, removed) counts.
        """
        added = 0
        updated = 0
        removed = 0

        if not self.source_dir.exists():
            skill_logger.warning(f"Source skills directory not found: {self.source_dir}")
            return added, updated, removed

        # Get source skill names
        source_skills = {
            d.name for d in self.source_dir.iterdir() if d.is_dir()
        }

        # Sync each source skill to global dir
        for skill_name in sorted(source_skills):
            src = self.source_dir / skill_name
            dst = self.global_skills_dir / skill_name

            if not dst.exists():
                shutil.copytree(src, dst)
                added += 1
                skill_logger.info(f"Added skill: {skill_name}")
            elif not self._dir_contents_match(src, dst):
                shutil.rmtree(dst)
                shutil.copytree(src, dst)
                updated += 1
                skill_logger.info(f"Updated skill: {skill_name}")
            else:
                skill_logger.debug(f"Skill unchanged: {skill_name}")

        # Remove skills in global dir that are no longer in source
        for entry in sorted(self.global_skills_dir.iterdir()):
            if entry.is_dir() and entry.name not in source_skills:
                shutil.rmtree(entry)
                removed += 1
                skill_logger.info(f"Removed skill: {entry.name}")

        return added, updated, removed

    def _manage_symlinks(self) -> tuple[int, int, int]:
        """Create/update/clean symlinks in ~/.claude/skills/.

        Returns:
            Tuple of (created, conflicts, cleaned) counts.
        """
        created = 0
        conflicts = 0
        cleaned = 0

        # Get current global skill names
        global_skills = {
            d.name for d in self.global_skills_dir.iterdir() if d.is_dir()
        } if self.global_skills_dir.exists() else set()

        # Create/update symlinks for each global skill
        for skill_name in sorted(global_skills):
            target = self.global_skills_dir / skill_name
            link_path = self.claude_skills_dir / skill_name

            if link_path.is_symlink():
                if self._is_our_symlink(link_path):
                    # Our symlink — update target if needed
                    current_target = link_path.resolve()
                    if current_target != target.resolve():
                        link_path.unlink()
                        link_path.symlink_to(target)
                        created += 1
                        skill_logger.info(f"Updated symlink: {skill_name}")
                else:
                    # User's symlink pointing elsewhere
                    conflicts += 1
                    skill_logger.warning(
                        f"Conflict: {link_path} is a symlink owned by user "
                        f"(points to {os.readlink(link_path)}), skipping"
                    )
            elif link_path.exists():
                # Real file/directory owned by user
                conflicts += 1
                skill_logger.warning(
                    f"Conflict: {link_path} exists as user file/directory, skipping"
                )
            else:
                # Remove broken symlinks before creating
                if link_path.is_symlink():
                    link_path.unlink()
                link_path.symlink_to(target)
                created += 1
                skill_logger.info(f"Created symlink: {skill_name}")

        # Clean orphaned symlinks that point into ~/.cc_webui/skills/
        # for skills no longer in source
        for entry in sorted(self.claude_skills_dir.iterdir()):
            if entry.is_symlink() and self._is_our_symlink(entry):
                if entry.name not in global_skills:
                    entry.unlink()
                    cleaned += 1
                    skill_logger.info(f"Cleaned orphaned symlink: {entry.name}")

        return created, conflicts, cleaned

    def _is_our_symlink(self, path: Path) -> bool:
        """Check if a symlink points into our skills directory (old or new path)."""
        if not path.is_symlink():
            return False
        try:
            target = Path(os.readlink(path))
            # Resolve relative symlinks against the link's parent
            if not target.is_absolute():
                target = (path.parent / target).resolve()
            else:
                target = target.resolve()
            # Check both new and old paths during transition
            for skills_dir in (self.global_skills_dir, OLD_GLOBAL_SKILLS_DIR):
                resolved = skills_dir.resolve()
                if str(target).startswith(str(resolved) + os.sep) or target == resolved:
                    return True
            return False
        except (OSError, ValueError):
            return False

    def _dir_contents_match(self, src: Path, dst: Path) -> bool:
        """Compare two directory trees for content equality."""
        cmp = filecmp.dircmp(src, dst)
        return self._dircmp_equal(cmp)

    def _dircmp_equal(self, cmp: filecmp.dircmp) -> bool:
        """Recursively check if dircmp shows no differences."""
        if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
            return False
        for sub_cmp in cmp.subdirs.values():
            if not self._dircmp_equal(sub_cmp):
                return False
        return True
