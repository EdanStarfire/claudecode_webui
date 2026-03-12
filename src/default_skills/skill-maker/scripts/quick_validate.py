"""Quick validator for SKILL.md format.

Checks that a skill directory contains a valid SKILL.md with required
frontmatter fields. No external dependencies (no PyYAML).

Adapted from Anthropic's skill-creator (Apache 2.0).

Usage:
    python -m scripts.quick_validate <path-to-skill-dir>
"""

import sys
from pathlib import Path

from .utils import parse_frontmatter


def validate_skill(skill_dir: str | Path) -> list[str]:
    """Validate a skill directory's SKILL.md format.

    Args:
        skill_dir: Path to the skill directory (must contain SKILL.md).

    Returns:
        List of error/warning strings. Empty list means valid.
    """
    skill_dir = Path(skill_dir)
    issues: list[str] = []

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        issues.append(f"ERROR: SKILL.md not found in {skill_dir}")
        return issues

    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        issues.append(f"ERROR: Could not read SKILL.md: {e}")
        return issues

    if not content.strip():
        issues.append("ERROR: SKILL.md is empty")
        return issues

    # Check frontmatter delimiters
    if not content.startswith("---"):
        issues.append("ERROR: SKILL.md must start with --- frontmatter delimiter")
        return issues

    parts = content.split("---", 2)
    if len(parts) < 3:
        issues.append("ERROR: SKILL.md missing closing --- frontmatter delimiter")
        return issues

    # Parse frontmatter
    frontmatter = parse_frontmatter(content)

    # Required fields
    if not frontmatter.get("name"):
        issues.append("ERROR: Frontmatter missing required 'name' field")

    if not frontmatter.get("description"):
        issues.append("ERROR: Frontmatter missing required 'description' field")

    # Check for empty values
    name = frontmatter.get("name", "")
    if isinstance(name, str) and not name.strip():
        issues.append("ERROR: 'name' field is empty")

    desc = frontmatter.get("description", "")
    if isinstance(desc, str) and not desc.strip():
        issues.append("ERROR: 'description' field is empty")

    # Optional but recommended
    if "allowed-tools" not in frontmatter:
        issues.append("WARNING: 'allowed-tools' not specified (recommended)")

    # Check body content exists after frontmatter
    body = parts[2].strip()
    if not body:
        issues.append("WARNING: No content after frontmatter (skill body is empty)")

    return issues


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.quick_validate <path-to-skill-dir>")
        sys.exit(1)

    skill_dir = Path(sys.argv[1])
    if not skill_dir.is_dir():
        print(f"ERROR: {skill_dir} is not a directory")
        sys.exit(1)

    issues = validate_skill(skill_dir)

    if not issues:
        print(f"VALID: {skill_dir}/SKILL.md passes all checks")
        sys.exit(0)

    errors = [i for i in issues if i.startswith("ERROR")]
    warnings = [i for i in issues if i.startswith("WARNING")]

    for issue in issues:
        print(issue)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        sys.exit(1)
    else:
        print(f"\nPASSED with {len(warnings)} warning(s)")
        sys.exit(0)


if __name__ == "__main__":
    main()
