"""Text the session receives for an owner instruction (issue #2103 stage 3a)."""

from __future__ import annotations

from ..safety import strip_trigger_syntax, wrap_instruction
from .constants import MAX_INSTRUCTION_CHARS


def format_instruction(
    relay_name: str, channel: str, thread_ts: str, text: str, file_count: int = 0,
    nonce: str | None = None,
) -> str:
    """The OWNER INSTRUCTION block plus the thread/reply line. *text* is passed as received
    (Slack mrkdwn, not unescaped) so the nonce wrapper and the handle scanner see one form."""
    body = text
    if len(body) > MAX_INSTRUCTION_CHARS:
        body = body[:MAX_INSTRUCTION_CHARS] + "\n[truncated]"
    if file_count:
        body += f"\n[{file_count} file(s) attached in Slack were not delivered]"
    name = strip_trigger_syntax(relay_name or "Slack").replace('"', "'")[:80]
    return (
        f'📥 Slack instruction from the owner (relay "{name}", your self-DM thread)\n\n'
        f"{wrap_instruction(body, nonce)}\n\n"
        "---\n"
        f"Slack thread: channel={channel} thread_ts={thread_ts}. Answer with the relay `reply` "
        "tool using exactly this channel and thread_ts; replies in this thread need no approval."
    )


def instruction_summary(text: str) -> str:
    return "Slack instruction: " + " ".join(text.split())[:50]
