"""Utility functions for skill-maker scripts.

Provides SKILL.md frontmatter parsing without external YAML dependencies.
Adapted from Anthropic's skill-creator utilities (Apache 2.0).
"""

from pathlib import Path


def parse_skill_md(path: str | Path) -> tuple[str | None, str | None, str]:
    """Parse a SKILL.md file and extract frontmatter fields.

    Args:
        path: Path to a SKILL.md file.

    Returns:
        Tuple of (name, description, full_content).
        name and description are None if not found in frontmatter.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    name = None
    description = None

    # Parse YAML-style frontmatter between --- delimiters
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            for line in frontmatter.strip().splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line[len("name:"):].strip().strip("'\"")
                elif line.startswith("description:"):
                    description = line[len("description:"):].strip().strip("'\"")

    return name, description, content


def parse_frontmatter(content: str) -> dict[str, str | list[str]]:
    """Parse YAML-style frontmatter from SKILL.md content.

    Returns a dict of key-value pairs. List values (indented lines starting
    with ``-``) are returned as lists of strings.

    Args:
        content: Full SKILL.md text content.

    Returns:
        Dict of frontmatter fields. Empty dict if no frontmatter found.
    """
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    result: dict[str, str | list[str]] = {}
    current_key: str | None = None

    for line in parts[1].strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # List item under current key
        if stripped.startswith("- ") and current_key is not None:
            val = stripped[2:].strip().strip("'\"")
            existing = result.get(current_key)
            if isinstance(existing, list):
                existing.append(val)
            else:
                result[current_key] = [val]
            continue

        # Key: value pair
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            current_key = key
            if value:
                result[key] = value
            else:
                # Could be a list or empty value — start as empty string
                result[key] = []
        else:
            current_key = None

    return result
