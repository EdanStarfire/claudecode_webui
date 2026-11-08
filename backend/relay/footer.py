"""Footer template validation and rendering (issue #2103).

``template_variables.py`` is path-oriented, so the relay footer has its own
small renderer. Variables: ``{session_name}``, ``{session_handle}``,
``{session_link}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .events import RELAY_EVENT_TYPE
from .safety import contains_trigger_syntax, strip_trigger_syntax, with_marker

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


DEFAULT_FOOTER_TEMPLATE = "Sent by Claude agent {session_name} ({session_handle}) via Claude WebUI"
SLACK_SECTION_LIMIT = 3000  # chars per section block text
MAX_BODY_LENGTH = 35000  # chat.postMessage truncates text beyond ~40k


class FooterError(ValueError):
    """The rendered footer would contain trigger syntax."""


def _escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_session_footer(
    template: str | None, session_name: str, session_handle: str, session_link: str = ""
) -> str:
    """Render the footer for a session. The handle is plain text (never ``!handle``) and the
    session name has any ``!word`` sanitized. Raises FooterError if trigger syntax remains."""
    values = {
        "session_name": _escape_mrkdwn(strip_trigger_syntax(session_name or "session")),
        "session_handle": _escape_mrkdwn(session_handle or ""),
        "session_link": session_link or "",
    }
    footer = render_footer(template or DEFAULT_FOOTER_TEMPLATE, values).strip()
    if contains_trigger_syntax(footer):
        raise FooterError("footer would contain !handle trigger syntax")
    return footer


def agent_metadata(session_id: str, handle: str | None, relay_id: str) -> dict:
    """Slack message metadata marking a message as sent by an agent session."""
    return {
        "event_type": RELAY_EVENT_TYPE,
        "event_payload": {"session_id": session_id, "handle": handle or "", "relay_id": relay_id},
    }


@dataclass
class OutboundMessage:
    text: str  # notification/fallback text (the body)
    blocks: list[dict] | None  # None when no footer: plain text message


def build_outbound(body: str, footer: str | None, marker: bool = False) -> OutboundMessage:
    """Body as section block(s) followed by a context block holding the footer (if any).

    With *marker* (self-DM) the agent marker prefixes ``text`` and, since Slack renders blocks
    rather than ``text`` when both exist, the first section block too.
    """
    if marker:
        body = with_marker(body)
    if not footer:
        return OutboundMessage(text=body, blocks=None)
    chunks = [body[i : i + SLACK_SECTION_LIMIT] for i in range(0, len(body), SLACK_SECTION_LIMIT)]
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return OutboundMessage(text=body, blocks=blocks)
