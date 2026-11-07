"""Outbound text safety for the relay (issue #2103).

``!handle`` is the inbound instruction trigger form, so no text the relay sends
(agent-supplied text, footers) may contain it. One shared handle charset
(``HANDLE_PATTERN``) is the source for the trigger regex, the handle format
regex and the reserved-word list, so they cannot drift apart.
"""

from __future__ import annotations

import html
import re
import unicodedata

# Handle charset: lowercase alphanumerics and hyphens, 2-32 chars, no underscore.
HANDLE_PATTERN = r"[a-z0-9][a-z0-9-]{1,31}"

HANDLE_RE = re.compile(rf"^{HANDLE_PATTERN}\Z")  # \Z: "$" would accept a trailing newline

# ``!handle`` anywhere in text: not preceded by a word char or ``!``, not followed
# by a word char or ``-``. Whitespace variants (``! slug``) are not triggers.
TRIGGER_RE = re.compile(rf"(?<![\w!])!({HANDLE_PATTERN})(?![\w-])")

# Slack special mentions are also refused (they match TRIGGER_RE via ``<!here>``).
RESERVED_HANDLES = frozenset({"all", "here", "channel", "everyone", "subteam"})


def _text_forms(text: str) -> list[str]:
    """Raw, HTML-unescaped and NFKC-normalized forms (and their combination)."""
    unescaped = html.unescape(text)
    forms = [text, unescaped]
    forms.append(unicodedata.normalize("NFKC", text))
    forms.append(unicodedata.normalize("NFKC", unescaped))
    return forms


def contains_trigger_syntax(text: str | None) -> bool:
    """True if any form of *text* contains ``!handle`` trigger syntax.

    Code spans/fences are deliberately not exempt: an agent must not be able to
    hide a trigger inside one.
    """
    if not text:
        return False
    return any(TRIGGER_RE.search(form) for form in _text_forms(text))


def validate_handle(handle: str) -> str:
    """Return *handle* if it is a valid, non-reserved handle; raise ValueError otherwise."""
    if not isinstance(handle, str) or not HANDLE_RE.match(handle):
        raise ValueError(
            "relay_handle must match ^[a-z0-9][a-z0-9-]{1,31}$ (lowercase letters, digits, hyphens)"
        )
    if handle in RESERVED_HANDLES:
        raise ValueError(f"relay_handle '{handle}' is reserved")
    return handle
