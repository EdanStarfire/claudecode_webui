"""Marked replies the relay itself posts into the owner's self-DM thread (issue #2103 stage 3a).

These are relay-originated, not agent tool calls, so they bypass the agent policy gate. Every
reply: fixed text (nothing from Slack is echoed, so a hostile handle can never be reflected),
the self-DM agent marker, agent metadata, and a final ``contains_trigger_syntax`` assertion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..adapter import RelayError
from ..footer import agent_metadata, build_outbound
from ..safety import contains_trigger_syntax, safe_error_code

if TYPE_CHECKING:
    from backend.relay_manager import RelayManager

logger = logging.getLogger(__name__)

REPLY_TEXTS: dict[str, str] = {
    "unknown_handle": "Not delivered: no session with that handle is attached to this relay.",
    "ambiguous_handle": "Not delivered: the message names more than one session handle. "
    "Mention exactly one.",
    "empty_instruction": "Not delivered: the instruction is empty after the handle.",
    "received_late": "Not executed (received late): the instruction was older than 15 minutes "
    "when it was processed.",
    "session_inactive": "Not delivered: that session is no longer active.",
    "session_gone": "Not delivered: that session no longer exists.",
    "ephemeral": "Not delivered: that session cannot take instructions.",
    "session_error": "Not delivered: that session is in an error state.",
    "not_attached": "Not delivered: that session no longer has this relay attached.",
    "no_token": "Not delivered: the relay has no usable user token.",
    "delivery_failed": "Not delivered: the session could not accept the instruction.",
}


def reply_text(code: str) -> str:
    return REPLY_TEXTS.get(code, REPLY_TEXTS["delivery_failed"])


class InboundResponder:
    def __init__(self, relay_manager: RelayManager) -> None:
        self._manager = relay_manager

    async def reply(self, relay_id: str, channel: str, thread_ts: str, code: str) -> str | None:
        """Post the fixed reply for *code*. Returns the posted ts, or None on failure.
        Raises nothing: replies are best-effort."""
        text = reply_text(code)
        if contains_trigger_syntax(text):  # defence in depth: the texts are constants
            return None
        outbound = build_outbound(text, None, marker=True)
        if contains_trigger_syntax(outbound.text):
            return None
        try:
            adapter = self._manager.adapter_for(relay_id)
            posted = await adapter.post(
                channel, outbound.text, outbound.blocks, thread_ts,
                agent_metadata("", None, relay_id),
            )
        except RelayError as e:
            logger.warning("Inbound reply failed (%s)", safe_error_code(e.code))
            return None
        except Exception:
            logger.exception("Inbound reply failed")
            return None
        return posted.ts
