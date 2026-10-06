"""
Tool lifecycle reconstruction state machine (issue #2084 stage 3-C, extracted from
issue #491's original inline implementation in SessionCoordinator.get_session_messages()).

Legacy (pre-#494) sessions never stored an explicit ToolCallUpdate record for a tool
call — its entire lifecycle (input, permission decision, result, terminal state) is
only ever inferred by walking the session's regular messages in order and synthesizing
tool_call entries at the points get_session_messages() has always inserted them. This
module factors that inference out into a reusable class so both the live reload path
(get_session_messages(), unchanged behavior) and the stage 3-C migration driver
(message_migration.py) can run the exact same state machine.

Input contract for feed(): the *converted* (websocket/canonical-shaped) record — the
same shape get_session_messages() already has in hand as `websocket_data` by the time
it runs its trigger checks (type/metadata/timestamp), not the raw on-disk record. An
already-materialized tool_call record (from a stored ToolCallUpdate conversion, or a
canonical session's native flat tool_call record) is recognized by
`converted.get("type") == "tool_call"` and only updates tracking — it never re-triggers
synthesis, mirroring the original function's explicit-record branches exactly.
"""

import re
from collections.abc import Iterable
from typing import Any

from .models.messages import PermissionInfo, ToolCall, ToolDisplayInfo, ToolState
from .session_manager import SessionState

# Issue #324: terminal states that stop a tool from being tracked as "still open".
_TERMINAL_TOOL_STATES = (
    ToolState.COMPLETED,
    ToolState.FAILED,
    ToolState.DENIED,
    ToolState.INTERRUPTED,
    ToolState.ORPHANED,
)


def parse_send_comm_sender_attachments(result_content: Any) -> list[dict] | None:
    """Issue #1593/#1730: Parse sender resource IDs for mcp__legion__send_comm attachments.

    Extracts the JSON footer embedded by `_handle_send_comm` in the tool result text
    (format: `<!-- sender_attachments: [...] -->`). Moved here (stage 3-C) so both
    SessionCoordinator._parse_send_comm_sender_attachments() (kept as a thin delegating
    wrapper for its existing call sites/tests) and this module's own has_tool_results
    trigger share one implementation.
    """
    import json as _json

    if not isinstance(result_content, str):
        return None

    match = re.search(r"<!-- sender_attachments: (.*?) -->", result_content, re.DOTALL)
    if not match:
        return None

    try:
        parsed = _json.loads(match.group(1))
    except _json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, list) and parsed else None


def prescan_stored_tool_calls(
    raw_records: Iterable[dict[str, Any]], reconstructor: "ToolLifecycleReconstructor"
) -> None:
    """Issue #2052/#2084: pre-scan raw (pre-conversion) records for tool_use_ids
    already covered by an explicit stored record, BEFORE any conversion/synthesis
    runs — storage append order does not guarantee a ToolCallUpdate precedes its
    triggering AssistantMessage, so a single forward pass could miss it.

    Shared by get_session_messages() (reload path) and message_migration.py's pass 1
    so this exact matching logic has one implementation — a future fix to tool_call/
    ToolCallUpdate detection only needs to land here, not independently in both
    places (the risk a prior version of this code had: message_migration.py hand-
    rolled an equivalent loop that could silently drift from this one).

    `raw_records` may be a generator over a streamed file — this function holds no
    more than the current record in memory at a time.
    """
    for raw in raw_records:
        if raw.get("_type") == "ToolCallUpdate":
            try:
                tool_use_id = raw.get("data", {}).get("tool_use_id")
            except AttributeError:
                continue
            reconstructor.observe_stored_tool_call(tool_use_id)
        elif raw.get("type") == "tool_call":
            reconstructor.observe_stored_tool_call(raw.get("tool_use_id"))


class ToolLifecycleReconstructor:
    """Reconstructs synthetic tool_call entries from a session's regular message stream.

    One instance per session/pass. `feed()` is called once per converted record, in
    original stream order; it mutates internal active-tool state and returns zero or
    more newly synthesized tool_call dicts (the same shape get_session_messages() has
    always appended to its `parsed_messages` list at these exact points).
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        # Issue #491: tool_use_id -> ToolCall being reconstructed from history.
        self.active_history_tools: dict[str, ToolCall] = {}
        # Issue #494/#2052: tool_use_ids with an explicit stored record — synthesis
        # is suppressed for these. Pre-scanned by callers via observe_stored_tool_call()
        # before feed() runs, since storage append order does not guarantee a
        # ToolCallUpdate precedes its triggering AssistantMessage.
        self.stored_tool_update_ids: set[str] = set()
        # Issue #2093: tool_use_ids that have already reached a terminal state via
        # an explicit record — once here, a later non-terminal explicit record for
        # the same id is necessarily stale/out-of-order (two genuine stored records
        # for the same tool landing out of chronological order in the file) and
        # must not resurrect tracking for it.
        self.terminal_tool_use_ids: set[str] = set()

    def observe_stored_tool_call(self, tool_use_id: str | None) -> None:
        """Mark a tool_use_id as already covered by an explicit stored record."""
        if tool_use_id:
            self.stored_tool_update_ids.add(tool_use_id)

    def feed(self, converted: dict[str, Any]) -> list[dict[str, Any]]:
        """Feed one converted (websocket-shaped) record; return newly synthesized tool_call dicts."""
        synthesized: list[dict[str, Any]] = []

        # Explicit tool_call record (converted ToolCallUpdate, or a canonical
        # session's native flat tool_call) — update tracking only, no synthesis.
        if converted.get("type") == "tool_call":
            tc_id = converted.get("tool_use_id")
            if tc_id:
                self.observe_stored_tool_call(tc_id)
                reconstructed = ToolCall.from_dict(converted)
                if reconstructed.status in _TERMINAL_TOOL_STATES:
                    self.active_history_tools.pop(tc_id, None)
                    self.terminal_tool_use_ids.add(tc_id)
                elif tc_id not in self.terminal_tool_use_ids:
                    self.active_history_tools[tc_id] = reconstructed
                # else: stale out-of-order non-terminal record for an already-
                # concluded tool (issue #2093) — the record itself is still
                # written through unchanged by convert_fn upstream; only
                # lifecycle tracking is suppressed here.
            return synthesized

        msg_type = converted.get("type", "")
        metadata = converted.get("metadata", {}) or {}
        msg_timestamp = converted.get("timestamp")

        # Trigger 1: AssistantMessage with tool_uses -> create pending ToolCall messages.
        if metadata.get("has_tool_uses") and metadata.get("tool_uses"):
            # Issue #195: Propagate parent_tool_use_id to child tool_calls
            parent_tool_use_id = metadata.get("parent_tool_use_id")
            for tool_use in metadata["tool_uses"]:
                tool_use_id = tool_use.get("id")
                if not tool_use_id:
                    continue
                # Issue #494: Skip if this tool has stored ToolCallUpdate entries
                if tool_use_id in self.stored_tool_update_ids:
                    continue
                tool_call = ToolCall(
                    tool_use_id=tool_use_id,
                    session_id=self.session_id,
                    name=tool_use.get("name", ""),
                    input=tool_use.get("input", {}),
                    status=ToolState.PENDING,
                    created_at=msg_timestamp if isinstance(msg_timestamp, (int, float)) else 0.0,
                    parent_tool_use_id=parent_tool_use_id,
                    display=ToolDisplayInfo(
                        state=ToolState.PENDING,
                        visible=True,
                        collapsed=False,
                        style="default",
                    ),
                )
                self.active_history_tools[tool_use_id] = tool_call
                tc_data = tool_call.to_dict()
                tc_data["type"] = "tool_call"
                synthesized.append(tc_data)

        # Trigger 2: PermissionRequestMessage -> update matching ToolCall to awaiting_permission.
        if msg_type == "permission_request" or metadata.get("has_permission_requests"):
            perm_tool_name = metadata.get("tool_name", "")
            perm_request_id = metadata.get("request_id", "")
            perm_suggestions = metadata.get("suggestions", [])

            # Issue #2084 (stage 3-C): a tool_use_id with an explicit stored record
            # is never a synthesis candidate here — its true state comes solely from
            # that record (3-B's ToolCallUpdate-equivalent writes at every real
            # lifecycle transition), not from re-deriving it off the regular message
            # stream. Without this, a canonical/migrated session's explicit PENDING
            # record (which does populate active_history_tools — see the explicit-
            # record branch above) would make this trigger re-fire redundantly on the
            # very same PermissionRequestMessage that already has its own explicit
            # AWAITING_PERMISSION record elsewhere in the stream.
            matched_tool = None
            for tc in self.active_history_tools.values():
                if tc.tool_use_id in self.stored_tool_update_ids:
                    continue
                if tc.name == perm_tool_name and tc.status == ToolState.PENDING:
                    matched_tool = tc
                    break
            if not matched_tool:
                candidates = [
                    tc for tc in self.active_history_tools.values()
                    if tc.tool_use_id not in self.stored_tool_update_ids
                    and tc.name == perm_tool_name and tc.status in (
                        ToolState.PENDING, ToolState.AWAITING_PERMISSION
                    )
                ]
                if len(candidates) == 1:
                    matched_tool = candidates[0]

            if matched_tool:
                matched_tool.status = ToolState.AWAITING_PERMISSION
                matched_tool.requires_permission = True
                matched_tool.permission = PermissionInfo(
                    message=converted.get("content", ""),
                    suggestions=perm_suggestions,
                )
                if matched_tool.display:
                    matched_tool.display.state = ToolState.AWAITING_PERMISSION
                    matched_tool.display.style = "warning"
                tc_data = matched_tool.to_dict()
                tc_data["type"] = "tool_call"
                tc_data["request_id"] = perm_request_id
                synthesized.append(tc_data)

        # Trigger 3: PermissionResponseMessage -> update matching ToolCall with decision.
        if msg_type == "permission_response":
            perm_decision = metadata.get("decision", "")
            perm_request_id = metadata.get("request_id", "")
            perm_tool_name = metadata.get("tool_name", "")
            updated_input = metadata.get("updated_input")
            applied_updates = metadata.get("applied_updates", [])

            # Issue #2084 (stage 3-C): same exclusion as trigger 2 above.
            matched_tool = None
            for tc in self.active_history_tools.values():
                if tc.tool_use_id in self.stored_tool_update_ids:
                    continue
                if tc.name == perm_tool_name and tc.status == ToolState.AWAITING_PERMISSION:
                    matched_tool = tc
                    break

            if matched_tool:
                granted = perm_decision == "allow"
                matched_tool.permission_granted = granted
                if granted:
                    matched_tool.status = ToolState.RUNNING
                    if matched_tool.display:
                        matched_tool.display.state = ToolState.RUNNING
                        matched_tool.display.style = "default"
                else:
                    matched_tool.status = ToolState.DENIED
                    if matched_tool.display:
                        matched_tool.display.state = ToolState.DENIED
                        matched_tool.display.style = "error"

                tc_data = matched_tool.to_dict()
                tc_data["type"] = "tool_call"
                tc_data["request_id"] = perm_request_id
                if updated_input:
                    tc_data["updated_input"] = updated_input
                if applied_updates:
                    tc_data["applied_updates"] = applied_updates
                synthesized.append(tc_data)

                if not granted:
                    self.active_history_tools.pop(matched_tool.tool_use_id, None)

        # Trigger 4: UserMessage with tool_results -> update matching ToolCall to completed/failed.
        if metadata.get("has_tool_results") and metadata.get("tool_results"):
            for tool_result in metadata["tool_results"]:
                tool_use_id = tool_result.get("tool_use_id")
                if not tool_use_id:
                    continue
                # Issue #2084 (stage 3-C): same exclusion as triggers 2/3 above.
                if tool_use_id in self.stored_tool_update_ids:
                    continue
                matched_tool = self.active_history_tools.pop(tool_use_id, None)
                if matched_tool:
                    is_error = tool_result.get("is_error", False)
                    result_content = tool_result.get("content", "")
                    if is_error:
                        matched_tool.status = ToolState.FAILED
                        matched_tool.error = str(result_content) if result_content else "Tool execution failed"
                        if matched_tool.display:
                            matched_tool.display.state = ToolState.FAILED
                            matched_tool.display.style = "error"
                    else:
                        matched_tool.status = ToolState.COMPLETED
                        matched_tool.result = result_content
                        if matched_tool.display:
                            matched_tool.display.state = ToolState.COMPLETED
                            matched_tool.display.style = "success"
                        # Issue #1593/#1730: resolve sender attachment resource IDs
                        if matched_tool.name == "mcp__legion__send_comm":
                            matched_tool.sender_attachments = (
                                parse_send_comm_sender_attachments(result_content)
                            )
                    tc_data = matched_tool.to_dict()
                    tc_data["type"] = "tool_call"
                    synthesized.append(tc_data)

        # Trigger 5: SystemMessage client_launched or interrupt -> mark unresolved tools interrupted.
        if msg_type == "system":
            subtype = metadata.get("subtype", "")
            if subtype in ("client_launched", "interrupt"):
                for tool_use_id in list(self.active_history_tools.keys()):
                    tc = self.active_history_tools.pop(tool_use_id)
                    tc.status = ToolState.INTERRUPTED
                    if tc.display:
                        tc.display.state = ToolState.INTERRUPTED
                        tc.display.style = "orphaned"
                    tc_data = tc.to_dict()
                    tc_data["type"] = "tool_call"
                    synthesized.append(tc_data)

        return synthesized

    def finalize(self, session_state: SessionState) -> list[dict[str, Any]]:
        """Trigger 6 (issue #491): end-of-stream sweep.

        Force-INTERRUPTED for every tool still open, if the session isn't
        ACTIVE/PAUSED/STARTING (i.e. it was terminated/reset without an explicit
        interrupt/client_launched message ever appearing in the stream).
        """
        synthesized: list[dict[str, Any]] = []
        if session_state in (SessionState.ACTIVE, SessionState.PAUSED, SessionState.STARTING):
            return synthesized
        for tool_use_id in list(self.active_history_tools.keys()):
            tc = self.active_history_tools.pop(tool_use_id)
            tc.status = ToolState.INTERRUPTED
            if tc.display:
                tc.display.state = ToolState.INTERRUPTED
                tc.display.style = "orphaned"
            tc_data = tc.to_dict()
            tc_data["type"] = "tool_call"
            synthesized.append(tc_data)
        return synthesized
