"""Footer template validation and rendering (issue #2103).

``template_variables.py`` is path-oriented, so the relay footer has its own
small renderer. Variables: ``{session_name}``, ``{session_handle}``,
``{session_link}``.
"""

from __future__ import annotations

import re

from .safety import contains_trigger_syntax

FOOTER_MAX_LENGTH = 500
FOOTER_VARIABLES = frozenset({"session_name", "session_handle", "session_link"})

_VAR_RE = re.compile(r"\{([^{}]*)\}")


def validate_footer_template(template: object) -> str | None:
    """Validate a footer template; returns it unchanged (None allowed). Raises ValueError."""
    if template is None:
        return None
    if not isinstance(template, str):
        raise ValueError("relay_footer_template must be a string")
    if len(template) > FOOTER_MAX_LENGTH:
        raise ValueError(f"relay_footer_template must be at most {FOOTER_MAX_LENGTH} characters")
    if contains_trigger_syntax(template):
        raise ValueError("relay_footer_template must not contain !handle trigger syntax")
    unknown = {m for m in _VAR_RE.findall(template) if m not in FOOTER_VARIABLES}
    if unknown:
        raise ValueError(
            f"relay_footer_template has unknown variables {sorted(unknown)}; "
            f"allowed: {sorted(FOOTER_VARIABLES)}"
        )
    # A variable can complete a trigger (e.g. "!{session_handle}" renders "!my-handle").
    sample = {"session_name": "name", "session_handle": "handle", "session_link": "link"}
    if contains_trigger_syntax(render_footer(template, sample)):
        raise ValueError("relay_footer_template must not form !handle trigger syntax once rendered")
    return template


def render_footer(template: str, values: dict[str, str]) -> str:
    """Substitute known ``{var}`` placeholders; unknown braces are left as-is."""
    return _VAR_RE.sub(
        lambda m: values.get(m.group(1), m.group(0)) if m.group(1) in FOOTER_VARIABLES else m.group(0),
        template,
    )
