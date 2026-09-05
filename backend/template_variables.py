"""
Template variable substitution for session configuration paths (issue #917).

Supported variables:
  {session_id}   — the session UUID
  {session_data} — absolute path to the session data directory
  {working_dir}  — the session's configured working directory
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWN_VARS = frozenset(["session_id", "session_data", "working_dir"])
_VAR_PATTERN = re.compile(r"\{(\w+)\}")


def resolve_path(template: str | None, variables: dict[str, str | None]) -> str | None:
    """
    Replace {variable} tokens in *template* with values from *variables*.

    - Known variables with a non-None value are substituted.
    - Known variables whose value is None are left unreplaced and a warning is logged.
    - Unknown variable names are left unreplaced and a warning is logged.
    - Returns None if template is None.
    """
    if template is None:
        return None

    def replacer(match: re.Match) -> str:
        name = match.group(1)
        if name not in KNOWN_VARS:
            logger.warning("Template path contains unknown variable {%s}: %r", name, template)
            return match.group(0)  # pass through literally
        value = variables.get(name)
        if value is None:
            logger.warning(
                "Template variable {%s} cannot be resolved (value is None): %r", name, template
            )
            return match.group(0)  # pass through literally
        return value

    return _VAR_PATTERN.sub(replacer, template)


def resolve_path_list(
    templates: list[str] | None, variables: dict[str, str | None]
) -> list[str] | None:
    """Apply resolve_path to each element of a list. Returns None if input is None."""
    if templates is None:
        return None
    return [resolve_path(t, variables) for t in templates]


def build_variables(
    session_id: str,
    session_dir: Path,
    working_directory: str | None,
) -> dict[str, str | None]:
    """Build the substitution variable mapping for a session."""
    return {
        "session_id": session_id,
        "session_data": str(session_dir),
        "working_dir": working_directory,
    }
