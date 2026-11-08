"""Policy-scoped Slack search (issue #2103).

The query is limited with ``in:`` filters to readable channels (read_only/ask/auto),
chunked to a bounded filter count per call (Slack treats several ``in:`` filters as OR),
and every result is post-filtered again against the readable set from result metadata, so
blocked and unlisted channels can never appear even if the provider returns them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .adapter import RelayMessage
from .events import is_agent_message
from .gate import GateError
from .policy import ALLOW

if TYPE_CHECKING:
    from .gate import RelayContext, RelayPolicyGate

MAX_FILTERS_PER_QUERY = 10
MAX_RESULTS = 50
SEARCH_TEXT_LIMIT = 500


def message_dict(
    msg: RelayMessage,
    text_limit: int,
    channel: str | None = None,
    channel_name: str | None = None,
    user_names: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """One result entry. Everything Slack-user-controlled (text, names) lives here, and this
    dict is only ever returned inside the untrusted wrapper."""
    text = msg.text or ""
    return {
        "channel": channel or msg.channel,
        "channel_name": channel_name,
        "ts": msg.ts,
        "thread_ts": msg.thread_ts,
        "user": msg.user,
        "user_name": (user_names or {}).get(msg.user) if msg.user else None,
        "text": text[:text_limit] + ("…" if len(text) > text_limit else ""),
        "permalink": msg.permalink,
        "is_agent_message": is_agent_message(msg),
    }


@dataclass
class SearchOutcome:
    messages: list[RelayMessage]
    note: str | None = None


class RelaySearchService:
    def __init__(self, gate: RelayPolicyGate) -> None:
        self._gate = gate

    async def readable_and_listed(self, ctx: RelayContext) -> tuple[list[str], set[str]]:
        policies = await self._gate.service.effective_relay_policies(ctx.session_info, ctx.relay_id)
        model = ctx.spec.policy_model
        readable = [c for c, p in policies.items() if model.decide(p.policy, "search").kind == ALLOW]
        return readable, set(policies)

    async def _unlisted_count(self, ctx: RelayContext, listed: set[str]) -> int | None:
        try:
            convs = await ctx.adapter.list_conversations(limit=1000)
        except Exception:  # best-effort note only
            return None
        return sum(1 for c in convs if c.id not in listed)

    async def search(self, ctx: RelayContext, query: str, limit: int) -> SearchOutcome:
        limit = max(1, min(int(limit), MAX_RESULTS))
        readable, listed = await self.readable_and_listed(ctx)
        if not readable:
            raise GateError(
                "no_readable_channels",
                "No channel is readable for this session (all are blocked or have no policy). "
                "Ask the user to set a channel policy (read_only/ask/auto).",
            )
        allowed = set(readable)
        merged: list[RelayMessage] = []
        seen: set[tuple[str | None, str]] = set()
        for i in range(0, len(readable), MAX_FILTERS_PER_QUERY):
            chunk = readable[i : i + MAX_FILTERS_PER_QUERY]
            for msg in await ctx.adapter.search(query, chunk, limit):
                key = (msg.channel, msg.ts)
                if msg.channel not in allowed or key in seen:
                    continue  # post-filter: blocked / unlisted / unknown channel
                seen.add(key)
                merged.append(msg)
        note = None
        unlisted = await self._unlisted_count(ctx, listed)
        if unlisted:
            note = (
                f"{unlisted} channel(s) without a policy were excluded from this search. "
                "Reading one directly (read_thread) asks the user to choose its policy."
            )
        return SearchOutcome(merged[:limit], note)
