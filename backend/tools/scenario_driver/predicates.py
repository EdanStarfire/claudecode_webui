"""Predicate builders for `Step.wait` specs (AC3/AC4).

Match against the REAL live poll-event shapes (confirmed by reading
`backend/permission_service.py`, `backend/message_parser.py`,
`backend/web_server.py`, `backend/session_coordinator.py`, and
`backend/mcp/resource_mcp_tools.py` — not the raw_log.jsonl recording shape
and not guessed from fixture_export.py's marker heuristics):

- Session-stream, tool/message-lifecycle events arrive wrapped:
  `{"type": "message", "session_id": ..., "data": {...}, "timestamp": ...}`.
  Permission lifecycle has no distinct event type — it's the SAME `tool_call`
  message type at every stage (`pending` -> `awaiting_permission` -> `running`/
  `denied` -> `completed`/`failed`/`interrupted`), correlated by `tool_use_id`;
  a `request_id` field only appears once status is `awaiting_permission`.
- A few session-stream events are bare, unwrapped: `resource_registered`,
  `link_registered`.
- The UI stream's events are always bare: `state_change`, `notification`,
  `session_reset`.
"""

from __future__ import annotations

from collections.abc import Callable

from .poll_consumer import TaggedEvent

Predicate = Callable[[TaggedEvent], bool]


def _message_data(tagged: TaggedEvent) -> dict | None:
    event = tagged.event
    if event.get("type") != "message":
        return None
    return event.get("data") or {}


def result_message(session_id: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        return data is not None and data.get("type") == "result" and data.get("session_id") == session_id

    return _match


def assistant_delta(session_id: str) -> Predicate:
    """Unlike most session-stream events, `assistant_delta` is bare/top-level
    (`{"type": "assistant_delta", "session_id": ..., "data": {...}}`), not
    wrapped in the generic `{"type": "message", "data": {...}}` envelope —
    confirmed against a real live session, not just code-reading."""

    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        return event.get("type") == "assistant_delta" and event.get("session_id") == session_id

    return _match


def tool_call_awaiting_permission(session_id: str, tool_name: str | None = None) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        if data is None or data.get("type") != "tool_call":
            return False
        if data.get("status") != "awaiting_permission" or not data.get("request_id"):
            return False
        if data.get("session_id") != session_id:
            return False
        return tool_name is None or data.get("name") == tool_name

    return _match


def tool_call_status(session_id: str, status: str, tool_name: str | None = None) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        if data is None or data.get("type") != "tool_call" or data.get("status") != status:
            return False
        if data.get("session_id") != session_id:
            return False
        return tool_name is None or data.get("name") == tool_name

    return _match


def system_subtype(session_id: str, subtype: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        if data is None or data.get("type") != "system" or data.get("session_id") != session_id:
            return False
        return (data.get("metadata") or {}).get("subtype") == subtype

    return _match


def task_event(session_id: str, subtypes: tuple[str, ...]) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        if data is None or data.get("type") != "system" or data.get("session_id") != session_id:
            return False
        return (data.get("metadata") or {}).get("subtype") in subtypes

    return _match


def state_change(session_id: str, state: str | None = None) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.get("type") != "state_change":
            return False
        data = event.get("data") or {}
        if data.get("session_id") != session_id:
            return False
        return state is None or (data.get("session") or {}).get("state") == state

    return _match


def permission_mode_state_change(session_id: str, mode: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.get("type") != "state_change":
            return False
        data = event.get("data") or {}
        if data.get("session_id") != session_id:
            return False
        return (data.get("session") or {}).get("current_permission_mode") == mode

    return _match


def model_state_change(session_id: str, model: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.get("type") != "state_change":
            return False
        data = event.get("data") or {}
        if data.get("session_id") != session_id:
            return False
        return (data.get("session") or {}).get("current_model") == model

    return _match


def resource_registered(session_id: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.get("type") != "resource_registered":
            return False
        return (event.get("resource") or {}).get("session_id") == session_id

    return _match


def minion_comm_notification(from_minion_name: str | None = None) -> Predicate:
    """This UI notification fires for EVERY non-SYSTEM/SPAWN/DISPOSE comm,
    both directions — including the driver's own outbound `send_comm` REST
    call, which has no `from_minion_id` and so shows up with the generic
    `"Minion"` fallback name (`comm.from_minion_name or "Minion"`,
    `backend/web_server.py`'s `_broadcast_comm_notification_to_ui`). Only a
    comm sent BY a minion (via its own `send_comm` MCP tool) sets
    `from_minion_name` to that minion's real name. Pass `from_minion_name`
    to match only a reply from that specific minion, not the driver's own
    outgoing comm's echo."""

    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.get("type") != "notification":
            return False
        data = event.get("data") or {}
        if data.get("event_type") != "minion_comm":
            return False
        return from_minion_name is None or data.get("from_minion_name") == from_minion_name

    return _match
