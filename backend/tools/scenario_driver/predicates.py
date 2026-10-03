"""Predicate builders for `Step.wait` specs (issue #2038).

These match the live poll-event shapes broadcast by
`backend/permission_service.py`, `backend/message_parser.py`,
`backend/web_server.py`, `backend/session_coordinator.py`, and
`backend/mcp/resource_mcp_tools.py`. This is a different, richer shape than
`raw_log.jsonl`'s recording format — see `backend/fixture_export.py` for that
one:

- Session-stream tool/permission-lifecycle events arrive as the bare `tool_call` top-level
  type: `{"type": "tool_call", "session_id": ..., "data": {...}, "timestamp": ...}`.
  Permission lifecycle has no distinct event type — it's the SAME `tool_call` data shape at
  every stage (`pending` -> `awaiting_permission` -> `running`/`denied` ->
  `completed`/`failed`/`interrupted`), correlated by `tool_use_id`; a `request_id` field
  only appears once status is `awaiting_permission`.
- A few other session-stream events are also bare, unwrapped: `resource_registered`,
  `link_registered`, `assistant_delta`. Most other session-stream lifecycle events
  (`system` subtypes, `result`) still arrive wrapped in the generic `{"type": "message",
  "data": {...}}` envelope — `tool_call` is the one type that moved to bare-only in #2065.
- The UI stream's events are always bare: `state_change`, `notification`,
  `session_reset`.
"""

from __future__ import annotations

from collections.abc import Callable

from .poll_consumer import TaggedEvent

Predicate = Callable[[TaggedEvent], bool]


def _message_data(tagged: TaggedEvent) -> dict | None:
    event = tagged.event
    if event.type != "message":
        return None
    return event.data or {}


def result_message(session_id: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        return data is not None and data.get("type") == "result" and data.get("session_id") == session_id

    return _match


def assistant_delta(session_id: str) -> Predicate:
    """Unlike most session-stream events, `assistant_delta` is bare/top-level
    (`{"type": "assistant_delta", "session_id": ..., "data": {...}}`), not
    wrapped in the generic `{"type": "message", "data": {...}}` envelope."""

    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        return event.type == "assistant_delta" and event.data.get("session_id") == session_id

    return _match


def _tool_call_data(tagged: TaggedEvent) -> dict | None:
    """Issue #2065 stage 2b-C: the bare `tool_call` top-level event is the only shape
    emitted now that the 2a-C shim (shared/event_emitter.py::emit_tool_call) is gone.
    `EventEnvelope.from_dict()`'s key-folding makes `.data` identical in shape to what
    `_message_data()` returned for the pre-2b-C wrapped form — only the outer `.type`
    differs — so this is a straight swap of data source, not a reshape."""
    event = tagged.event
    if event.type != "tool_call":
        return None
    return event.data or {}


def tool_call_awaiting_permission(session_id: str, tool_name: str | None = None) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _tool_call_data(tagged)
        if data is None:
            return False
        if data.get("status") != "awaiting_permission" or not data.get("request_id"):
            return False
        if data.get("session_id") != session_id:
            return False
        return tool_name is None or data.get("name") == tool_name

    return _match


def tool_call_status(session_id: str, status: str, tool_name: str | None = None) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        data = _tool_call_data(tagged)
        if data is None or data.get("status") != status:
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
        if event.type != "state_change":
            return False
        data = event.data or {}
        if data.get("session_id") != session_id:
            return False
        return state is None or (data.get("session") or {}).get("state") == state

    return _match


def permission_mode_state_change(session_id: str, mode: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.type != "state_change":
            return False
        data = event.data or {}
        if data.get("session_id") != session_id:
            return False
        return (data.get("session") or {}).get("current_permission_mode") == mode

    return _match


def model_state_change(session_id: str, model: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.type != "state_change":
            return False
        data = event.data or {}
        if data.get("session_id") != session_id:
            return False
        return (data.get("session") or {}).get("current_model") == model

    return _match


def resource_registered(session_id: str) -> Predicate:
    def _match(tagged: TaggedEvent) -> bool:
        event = tagged.event
        if event.type != "resource_registered":
            return False
        return (event.data.get("resource") or {}).get("session_id") == session_id

    return _match


def comm_delivered() -> Predicate:
    """A comm delivered INTO a session's message stream (e.g. a minion's
    `send_comm` reply reaching the main session) — an ordinary message whose
    `metadata.comm` is set, the same field `fixture_export._check_markers()`
    keys "inter-minion comm" on. Scope it to one session's stream via
    `WaitSpec.stream`."""

    def _match(tagged: TaggedEvent) -> bool:
        data = _message_data(tagged)
        return data is not None and "comm" in (data.get("metadata") or {})

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
        if event.type != "notification":
            return False
        data = event.data or {}
        if data.get("event_type") != "minion_comm":
            return False
        return from_minion_name is None or data.get("from_minion_name") == from_minion_name

    return _match
