"""
Permission Resolver for Claude Code WebUI

Resolves effective permissions by parsing Claude Code settings files
and merging permissions from multiple sources.

Issue #36: Enhanced session permission configuration with permission preview
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ResolvedPermission:
    """A permission with its source(s)."""
    permission: str
    sources: list[str]


def resolve_effective_permissions(
    working_directory: str,
    setting_sources: list[str] | None = None,
    session_allowed_tools: list[str] | None = None
) -> list[dict]:
    """
    Resolve effective permissions from settings files and session config.

    Args:
        working_directory: Project working directory (used to find .claude/ settings)
        setting_sources: Which sources to include ("user", "project", "local")
        session_allowed_tools: Tools explicitly allowed at session level

    Returns:
        List of dicts: [{"permission": str, "sources": [str, ...]}]
    """
    if setting_sources is None:
        setting_sources = ["user", "project", "local"]

    # Track permissions with their sources
    # Key: permission string, Value: list of sources that grant it
    permission_map: dict[str, list[str]] = {}

    # Parse each enabled settings source
    for source in setting_sources:
        permissions = _parse_settings_source(source, working_directory)
        for perm in permissions:
            if perm not in permission_map:
                permission_map[perm] = []
            permission_map[perm].append(source)

    # Add session-level allowed tools
    if session_allowed_tools:
        for tool in session_allowed_tools:
            if tool not in permission_map:
                permission_map[tool] = []
            permission_map[tool].append("session")

    # Convert to list of dicts for JSON serialization
    result = []
    for permission, sources in sorted(permission_map.items()):
        result.append({
            "permission": permission,
            "sources": sources
        })

    return result


def _parse_settings_source(source: str, working_directory: str) -> list[str]:
    """
    Parse permissions from a specific settings source.

    Args:
        source: One of "user", "project", "local"
        working_directory: Project working directory

    Returns:
        List of permission strings from this source
    """
    settings_path = _get_settings_path(source, working_directory)
    if not settings_path or not settings_path.exists():
        logger.debug(f"Settings file not found for source '{source}': {settings_path}")
        return []

    try:
        with open(settings_path) as f:
            settings = json.load(f)

        # Extract permissions.allow array
        permissions = settings.get("permissions", {})
        allow_list = permissions.get("allow", [])

        if not isinstance(allow_list, list):
            logger.warning(f"permissions.allow is not a list in {settings_path}")
            return []

        # Normalize permissions to strings
        result = []
        for item in allow_list:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                # Handle structured permission objects (e.g., {"tool": "Bash", "pattern": "git *"})
                tool = item.get("tool", "")
                pattern = item.get("pattern", "")
                if tool and pattern:
                    result.append(f"{tool}({pattern})")
                elif tool:
                    result.append(tool)

        logger.debug(f"Parsed {len(result)} permissions from {source}: {result}")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse settings file {settings_path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error reading settings file {settings_path}: {e}")
        return []


def _get_settings_path(source: str, working_directory: str) -> Path | None:
    """
    Get the path to a settings file for a specific source.

    Args:
        source: One of "user", "project", "local"
        working_directory: Project working directory

    Returns:
        Path to the settings file, or None if source is unknown
    """
    if source == "user":
        # User settings: ~/.claude/settings.json
        return Path.home() / ".claude" / "settings.json"
    elif source == "project":
        # Project settings: <working_dir>/.claude/settings.json
        return Path(working_directory) / ".claude" / "settings.json"
    elif source == "local":
        # Local settings: <working_dir>/.claude/settings.local.json
        return Path(working_directory) / ".claude" / "settings.local.json"
    else:
        logger.warning(f"Unknown settings source: {source}")
        return None


def get_settings_file_info(working_directory: str) -> dict:
    """
    Get information about available settings files.

    Args:
        working_directory: Project working directory

    Returns:
        Dict with info about each settings source
    """
    sources = ["user", "project", "local"]
    result = {}

    for source in sources:
        path = _get_settings_path(source, working_directory)
        if path:
            result[source] = {
                "path": str(path),
                "exists": path.exists(),
                "readable": path.exists() and path.is_file()
            }

    return result
