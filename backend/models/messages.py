"""
Unified message models for Claude WebUI.

This module provides dataclass-based message types that align with the Claude Agent SDK's
dataclass patterns while adding WebUI-specific types for permissions and the unified
ToolCall lifecycle (Issue #324).

Key design principles:
1. SDK messages (AssistantMessage, UserMessage, etc.) are serialized via dataclasses.asdict()
2. WebUI-specific types (permissions, tool display) use the same dataclass pattern
3. MessageRecord (Issue #2084) is the one canonical wrapper for storage/WebSocket
4. ToolDisplayInfo (Issue #324) attaches display state to a ToolCall

Usage:
    from backend.models.messages import MessageRecord

    record = MessageRecord.from_sdk_message(sdk_message, session_id=session_id)

    # Serialize for storage/WebSocket
    json_data = record.to_dict()
"""

import json
import time
import uuid as uuid_lib
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any

from claude_agent_sdk import (
    HookEventMessage,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)

from backend.message_parser import MessageParser

# ============================================================
# Tool State Enum (Issue #310 - Display Projection)
# ============================================================

class ToolState(Enum):
    """Tool lifecycle states for display projection.

    Issue #324: Added DENIED and INTERRUPTED for unified ToolCall lifecycle.

    Status lifecycle:
        PENDING → AWAITING_PERMISSION → RUNNING → COMPLETED/FAILED
                                      ↘ DENIED
                        RUNNING → INTERRUPTED (session terminated)
    """
    PENDING = "pending"
    AWAITING_PERMISSION = "awaiting_permission"  # Issue #324
    RUNNING = "running"  # Issue #324
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"  # Issue #324: Permission denied
    INTERRUPTED = "interrupted"  # Issue #324: Session terminated before completion


# ============================================================
# Unified ToolCall Types (Issue #324)
# ============================================================

@dataclass
class PermissionInfo:
    """
    Permission request data embedded in ToolCall (Issue #324).

    This replaces the separate PermissionRequestMessage for unified tool lifecycle.
    All permission-related data is embedded directly in the ToolCall.
    """
    message: str  # "Allow Edit to modify file.py?"
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    risk_level: str = "medium"  # "low", "medium", "high"
    # Issue #1302: SDK 0.1.74 enriched context fields
    decision_reason: Any | None = None
    blocked_path: str | None = None
    title: str | None = None
    display_name: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage/WebSocket."""
        result: dict[str, Any] = {
            "message": self.message,
            "suggestions": self.suggestions,
            "risk_level": self.risk_level,
        }
        if self.decision_reason is not None:
            result["decision_reason"] = self.decision_reason
        if self.blocked_path is not None:
            result["blocked_path"] = self.blocked_path
        if self.title is not None:
            result["title"] = self.title
        if self.display_name is not None:
            result["display_name"] = self.display_name
        if self.description is not None:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionInfo":
        """Create from stored dict."""
        return cls(
            message=data.get("message", ""),
            suggestions=data.get("suggestions", []),
            risk_level=data.get("risk_level", "medium"),
            decision_reason=data.get("decision_reason"),
            blocked_path=data.get("blocked_path"),
            title=data.get("title"),
            display_name=data.get("display_name"),
            description=data.get("description"),
        )


@dataclass
class ToolCall:
    """
    Unified tool call with complete lifecycle state (Issue #324).

    This dataclass consolidates the tool_use, permission_request, permission_response,
    and tool_result message types into a single message type with stateful updates.

    Frontend receives these messages keyed by tool_use_id and updates existing entries
    when new status updates arrive - no correlation logic needed.

    Status lifecycle:
        PENDING → AWAITING_PERMISSION → RUNNING → COMPLETED
                                      ↘ DENIED
                        RUNNING → FAILED
                        RUNNING → INTERRUPTED

    WebSocket message format:
        {
            "type": "tool_call",
            "tool_use_id": "abc123",
            "status": "awaiting_permission",
            "name": "Edit",
            "input": {...},
            "permission": {...},
            ...
        }
    """
    # Identity
    tool_use_id: str
    session_id: str

    # Tool info
    name: str  # "Edit", "Bash", "Read"
    input: dict[str, Any] = field(default_factory=dict)

    # Lifecycle status
    status: ToolState = ToolState.PENDING

    # Timing
    created_at: float = 0.0
    started_at: float | None = None  # When execution began (after permission)
    completed_at: float | None = None  # When execution finished

    # Permission (embedded)
    requires_permission: bool = False
    permission: PermissionInfo | None = None
    permission_granted: bool | None = None
    permission_response_at: float | None = None
    applied_updates: list[dict[str, Any]] = field(default_factory=list)

    # Result (when completed)
    result: Any = None
    error: str | None = None

    # Issue #195: Parent Task tool that spawned this subagent tool
    parent_tool_use_id: str | None = None

    # Issue #1694/#1958: id of the assistant TURN that produced this tool_use, for
    # frontend anchoring of permission prompts to their owning bubble.
    turn_id: str | None = None

    # Issue #2109 (AC6): set when this ToolCall transitions to awaiting_permission,
    # so a later permission_response can find it back by request_id alone —
    # no name+status fallback needed.
    request_id: str | None = None

    # Issue #707: Auto-approval reason (set when suggestion-based auto-approval fires)
    auto_approved_reason: str | None = None

    # Display hints (backend-computed)
    display: "ToolDisplayInfo | None" = None

    # Issue #1593: sender resource IDs for outbound comm attachment chips
    # Set when the tool call is mcp__legion__send_comm with file attachments.
    # Each entry: {name, resource_id, size, mime_type}
    sender_attachments: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage/WebSocket."""
        result = {
            "tool_use_id": self.tool_use_id,
            "session_id": self.session_id,
            "name": self.name,
            "input": self.input,
            "status": self.status.value,
            "created_at": self.created_at,
            "requires_permission": self.requires_permission,
        }

        # Optional timing fields
        if self.started_at is not None:
            result["started_at"] = self.started_at
        if self.completed_at is not None:
            result["completed_at"] = self.completed_at

        # Permission fields
        if self.permission is not None:
            result["permission"] = self.permission.to_dict()
        if self.permission_granted is not None:
            result["permission_granted"] = self.permission_granted
        if self.permission_response_at is not None:
            result["permission_response_at"] = self.permission_response_at
        if self.applied_updates:
            result["applied_updates"] = self.applied_updates

        # Result fields
        if self.result is not None:
            result["result"] = self.result
        if self.error is not None:
            result["error"] = self.error

        # Issue #195: Parent tool reference
        if self.parent_tool_use_id is not None:
            result["parent_tool_use_id"] = self.parent_tool_use_id

        # Issue #1694/#1958: Owning assistant turn id
        if self.turn_id is not None:
            result["turn_id"] = self.turn_id

        # Issue #2109 (AC6): permission request_id for id-only correlation
        if self.request_id is not None:
            result["request_id"] = self.request_id

        # Issue #707: Auto-approval reason
        if self.auto_approved_reason is not None:
            result["auto_approved_reason"] = self.auto_approved_reason

        # Display hints
        if self.display is not None:
            result["display"] = self.display.to_dict()

        # Issue #1593: sender attachment resource IDs for outbound comm chips
        if self.sender_attachments is not None:
            result["sender_attachments"] = self.sender_attachments

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        """Create from stored dict."""
        # Parse status
        status_str = data.get("status", "pending")
        try:
            status = ToolState(status_str)
        except ValueError:
            status = ToolState.PENDING

        # Parse permission if present
        permission = None
        if "permission" in data and data["permission"]:
            permission = PermissionInfo.from_dict(data["permission"])

        # Parse display if present
        display = None
        if "display" in data and data["display"]:
            display = ToolDisplayInfo.from_dict(data["display"])

        return cls(
            tool_use_id=data.get("tool_use_id", ""),
            session_id=data.get("session_id", ""),
            name=data.get("name", ""),
            input=data.get("input", {}),
            status=status,
            created_at=data.get("created_at", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            requires_permission=data.get("requires_permission", False),
            permission=permission,
            permission_granted=data.get("permission_granted"),
            permission_response_at=data.get("permission_response_at"),
            applied_updates=data.get("applied_updates", []),
            result=data.get("result"),
            error=data.get("error"),
            parent_tool_use_id=data.get("parent_tool_use_id"),
            auto_approved_reason=data.get("auto_approved_reason"),
            display=display,
            sender_attachments=data.get("sender_attachments"),
            # Issue #1958 backward compat: pre-rename ToolCallUpdate records on disk carry
            # the turn id under the old ambiguous "message_id" key. Mirrors the equivalent
            # fallback for assistant messages in message_parser.py's turn_id restoration.
            turn_id=data.get("turn_id") or data.get("message_id"),
            request_id=data.get("request_id"),
        )

    def with_status_update(self, **updates: Any) -> "ToolCall":
        """Create a new ToolCall with updated fields (immutable pattern)."""
        data = self.to_dict()
        data.update(updates)
        # Handle status specially if provided as ToolState
        if "status" in updates and isinstance(updates["status"], ToolState):
            data["status"] = updates["status"].value
        return ToolCall.from_dict(data)


# ============================================================
# Permission-Related Types (WebUI-specific, not in SDK)
# ============================================================

@dataclass
class PermissionSuggestion:
    """
    A suggested permission update from the SDK.

    Maps to SDK's PermissionUpdate but as a storage-friendly dataclass.
    """
    type: str  # 'addRules', 'replaceRules', 'removeRules', 'setMode', 'addDirectories', 'removeDirectories'
    rules: list[dict[str, Any]] | None = None
    behavior: str | None = None  # 'allow', 'deny', 'ask'
    mode: str | None = None  # 'default', 'acceptEdits', 'plan', 'bypassPermissions'
    directories: list[str] | None = None
    destination: str = 'session'  # 'userSettings', 'projectSettings', 'localSettings', 'session'

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, excluding None values."""
        result = {'type': self.type, 'destination': self.destination}
        if self.rules is not None:
            result['rules'] = self.rules
        if self.behavior is not None:
            result['behavior'] = self.behavior
        if self.mode is not None:
            result['mode'] = self.mode
        if self.directories is not None:
            result['directories'] = self.directories
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionSuggestion':
        """Create from dict (e.g., from SDK context.suggestions)."""
        return cls(
            type=data.get('type', ''),
            rules=data.get('rules'),
            behavior=data.get('behavior'),
            mode=data.get('mode'),
            directories=data.get('directories'),
            destination=data.get('destination', 'session')
        )


@dataclass
class PermissionRequestMessage:
    """
    Permission request from SDK callback - stored for replay.

    This captures the permission prompt shown to the user when the SDK
    requests permission to execute a tool.
    """
    request_id: str
    tool_name: str
    input_params: dict[str, Any] = field(default_factory=dict)
    suggestions: list[PermissionSuggestion] = field(default_factory=list)
    timestamp: float = 0.0
    session_id: str | None = None
    tool_use_id: str | None = None  # Issue #953: direct tool invocation ID from SDK v0.1.52+
    agent_id: str | None = None     # Issue #953: sub-agent ID from ToolPermissionContext
    # Issue #1302: SDK 0.1.74 enriched context fields
    decision_reason: Any | None = None
    blocked_path: str | None = None
    title: str | None = None
    display_name: str | None = None
    description: str | None = None

    @property
    def content(self) -> str:
        """Human-readable content for display."""
        return f"Permission requested for tool: {self.tool_name}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        result: dict[str, Any] = {
            'request_id': self.request_id,
            'tool_name': self.tool_name,
            'input_params': self.input_params,
            'suggestions': [s.to_dict() for s in self.suggestions],
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'tool_use_id': self.tool_use_id,
            'agent_id': self.agent_id,
            'content': self.content,
        }
        if self.decision_reason is not None:
            result['decision_reason'] = self.decision_reason
        if self.blocked_path is not None:
            result['blocked_path'] = self.blocked_path
        if self.title is not None:
            result['title'] = self.title
        if self.display_name is not None:
            result['display_name'] = self.display_name
        if self.description is not None:
            result['description'] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionRequestMessage':
        """Create from stored dict."""
        suggestions = [
            PermissionSuggestion.from_dict(s) if isinstance(s, dict) else s
            for s in data.get('suggestions', [])
        ]
        return cls(
            request_id=data.get('request_id', ''),
            tool_name=data.get('tool_name', ''),
            input_params=data.get('input_params', {}),
            suggestions=suggestions,
            timestamp=data.get('timestamp', 0.0),
            session_id=data.get('session_id'),
            tool_use_id=data.get('tool_use_id'),
            agent_id=data.get('agent_id'),
            decision_reason=data.get('decision_reason'),
            blocked_path=data.get('blocked_path'),
            title=data.get('title'),
            display_name=data.get('display_name'),
            description=data.get('description'),
        )


@dataclass
class PermissionResponseMessage:
    """
    Permission response from user - stored for replay.

    This captures the user's decision (allow/deny) and any applied
    permission updates.
    """
    request_id: str
    decision: str  # 'allow' or 'deny'
    tool_name: str
    reasoning: str | None = None
    response_time_ms: int | None = None
    applied_updates: list[PermissionSuggestion] = field(default_factory=list)
    clarification_message: str | None = None  # For deny with clarification
    interrupt: bool = True  # Whether to interrupt on deny
    timestamp: float = 0.0
    session_id: str | None = None
    updated_input: dict | None = None  # For AskUserQuestion answers

    @property
    def content(self) -> str:
        """Human-readable content for display."""
        return f"Permission {self.decision} for tool: {self.tool_name}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        result = {
            'request_id': self.request_id,
            'decision': self.decision,
            'tool_name': self.tool_name,
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'content': self.content,
            'interrupt': self.interrupt,
        }
        if self.reasoning is not None:
            result['reasoning'] = self.reasoning
        if self.response_time_ms is not None:
            result['response_time_ms'] = self.response_time_ms
        if self.applied_updates:
            result['applied_updates'] = [u.to_dict() for u in self.applied_updates]
        if self.clarification_message is not None:
            result['clarification_message'] = self.clarification_message
        if self.updated_input is not None:
            result['updated_input'] = self.updated_input
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PermissionResponseMessage':
        """Create from stored dict."""
        applied_updates = [
            PermissionSuggestion.from_dict(u) if isinstance(u, dict) else u
            for u in data.get('applied_updates', [])
        ]
        return cls(
            request_id=data.get('request_id', ''),
            decision=data.get('decision', ''),
            tool_name=data.get('tool_name', ''),
            reasoning=data.get('reasoning'),
            response_time_ms=data.get('response_time_ms'),
            applied_updates=applied_updates,
            clarification_message=data.get('clarification_message'),
            interrupt=data.get('interrupt', True),
            timestamp=data.get('timestamp', 0.0),
            session_id=data.get('session_id'),
            updated_input=data.get('updated_input'),
        )


# ============================================================
# Display Projection Types (Issue #310)
# ============================================================

@dataclass
class ToolDisplayInfo:
    """
    Display metadata for a single tool call.

    Tracks the visual state of a tool in the UI, attached to a ToolCall's
    `display` field as part of the unified ToolCall lifecycle (Issue #324).
    """
    state: ToolState = ToolState.PENDING
    visible: bool = True
    collapsed: bool = False
    style: str = "default"  # 'default', 'warning', 'error', 'success', 'orphaned'
    linked_permission_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            'state': self.state.value,
            'visible': self.visible,
            'collapsed': self.collapsed,
            'style': self.style,
            'linked_permission_id': self.linked_permission_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolDisplayInfo':
        """Create from dict."""
        state_str = data.get('state', 'pending')
        try:
            state = ToolState(state_str)
        except ValueError:
            state = ToolState.PENDING
        return cls(
            state=state,
            visible=data.get('visible', True),
            collapsed=data.get('collapsed', False),
            style=data.get('style', 'default'),
            linked_permission_id=data.get('linked_permission_id'),
        )


# ============================================================
# Canonical MessageRecord (Issue #2084 stage 3-A; live since stage 3-B)
# ============================================================

# Consumed by 3-B (new-session stamping) and 3-C (migration target) as the single
# source for "what version am I writing/migrating to."
CURRENT_MESSAGE_SCHEMA_VERSION = 1


class LegacyMessageFormatError(Exception):
    """Raised when a stored record still carries the pre-canonical `_type`/`data`
    wrapper (issue #2109, AC10). Production data was confirmed 100% canonical by
    stage 3's migration gate (re-confirmed clean at stage 4a-C's merge) — this
    should never fire against real data; it exists to fail loudly rather than
    silently misread a record if that invariant is ever violated."""

    def __init__(self, session_id: str, stored_type: str):
        super().__init__(
            f"Session {session_id} has a legacy _type={stored_type!r} record — "
            f"expected canonical MessageRecord shape (message_schema_version check)."
        )


# SDK dataclasses whose `.uuid` is their own stable per-frame identity (as opposed to
# AssistantMessage/UserMessage/ResultMessage, which also carry `.uuid` but not as a
# turn-level identity — those are promoted to `sdk_uuid` only, never `turn_id`).
_TASK_OR_HOOK_TYPES = (
    TaskStartedMessage,
    TaskProgressMessage,
    TaskNotificationMessage,
    TaskUpdatedMessage,
    HookEventMessage,
)

# Mirrors ClaudeSDK._convert_sdk_message()'s flat-attribute whitelist (backend/claude_sdk.py)
# exactly, including its gaps (e.g. ResultMessage.result/.structured_output are not copied
# live) — this is the one place that whitelist is duplicated outside claude_sdk.py, since 3-A
# is explicitly forbidden from touching that file (see plan §8) while still needing to feed
# message_parser.py's handlers byte-identical input to the live path.
_SDK_FLATTEN_ATTRS = (
    "message", "data", "subtype", "error", "usage", "model_usage", "model",
    "duration_ms", "total_cost_usd", "parent_tool_use_id", "stop_reason",
    "errors", "permission_denials", "is_error", "num_turns", "duration_api_ms",
    "api_error_status",
)

# Shared MessageParser instance: routes to the right handler (AssistantMessageHandler,
# SystemMessageHandler, the four Task* handlers, etc.) based on the `sdk_message` object's
# type, exactly like the live claude_sdk.py path does.
_MESSAGE_PARSER = MessageParser()


def _mint_message_id() -> str:
    """The one function that mints `message_id` (Issue #2084 AC6)."""
    return str(uuid_lib.uuid4())


def _derive_turn_id(sdk_msg: Any, canonical_type: str) -> str | None:
    """The one function that derives `turn_id` (Issue #2084 AC6).

    `AssistantMessage.message_id` (the Anthropic API turn id) for assistant turns;
    `sdk_msg.uuid` for Task*/HookEventMessage frames that already carry their own
    stable id; `None` otherwise.
    """
    if canonical_type == "assistant":
        # `or None` matches AssistantMessageHandler's own truthy guard (message_parser.py)
        # so an empty-string message_id is treated as absent on both paths consistently.
        return getattr(sdk_msg, "message_id", None) or None
    if isinstance(sdk_msg, _TASK_OR_HOOK_TYPES):
        return getattr(sdk_msg, "uuid", None)
    return None


def _sdk_message_to_parser_input(sdk_msg: Any, session_id: str, timestamp: float) -> dict[str, Any]:
    """Build the `message_data` shape message_parser.py's handlers expect from a raw SDK
    message object, replicating ClaudeSDK._convert_sdk_message()'s flattening so the reused
    handlers produce output identical to the live path (see `_SDK_FLATTEN_ATTRS`)."""
    message_data: dict[str, Any] = {
        "sdk_message": sdk_msg,
        "timestamp": timestamp,
        "session_id": session_id,
    }

    if hasattr(sdk_msg, "content"):
        content = sdk_msg.content
        if isinstance(content, str):
            message_data["content"] = content
        elif isinstance(content, list):
            text_parts = [block.text for block in content if hasattr(block, "text")]
            message_data["content"] = " ".join(text_parts) if text_parts else ""

    for attr in _SDK_FLATTEN_ATTRS:
        if not hasattr(sdk_msg, attr):
            continue
        value = getattr(sdk_msg, attr)
        if isinstance(value, (str, int, float, bool, type(None))):
            message_data[attr] = value
        elif isinstance(value, (dict, list)):
            try:
                json.dumps(value)
                message_data[attr] = value
            except (TypeError, ValueError):
                pass

    if isinstance(sdk_msg, ResultMessage) and sdk_msg.deferred_tool_use:
        dt = sdk_msg.deferred_tool_use
        message_data["deferred_tool_use"] = {"id": dt.id, "name": dt.name, "input": dt.input}

    return message_data


def _message_dict_to_record(
    message_data: dict[str, Any],
    session_id: str,
    display: dict[str, Any] | None = None,
) -> "MessageRecord":
    """Bridge for synthetic system/lifecycle dicts (client_launched, interrupt,
    session_failed, mcp_server_degraded, stderr, ...) that never originate from a real
    SDK dataclass instance, so `from_sdk_message`'s `_sdk_message_to_parser_input`
    flattening doesn't apply — these dicts already carry `type`/`subtype`/`content`
    directly. Feeds the dict straight to the same `_MESSAGE_PARSER.parse_message()`
    dispatch `from_sdk_message` uses internally, keeping one single parser-dispatch call
    site for every system-typed record, live SDK-origin or synthetic.
    """
    message_data = dict(message_data)
    message_data.setdefault("session_id", session_id)
    message_data.setdefault("timestamp", time.time())
    parsed = _MESSAGE_PARSER.parse_message(message_data)
    canonical_type = parsed.type.value

    return MessageRecord(
        type=canonical_type,
        timestamp=parsed.timestamp,
        session_id=session_id,
        message_id=_mint_message_id(),
        turn_id=None,
        sdk_uuid=None,
        subtype=parsed.metadata.get("subtype") if canonical_type == "system" else None,
        content=parsed.content,
        display=display,
        metadata=parsed.metadata,
    )


@dataclass
class MessageRecord:
    """Canonical message shape superseding `StoredMessage`'s 5 legacy writer shapes
    (Issue #2084). Introduced as a pure addition in stage 3-A; the live, fully-wired
    writer/reader shape for every session since stage 3-B (the legacy conversion path
    itself was retired in stage 3-D-cutover, #2099).

    Reuses every existing field name (no JSON key renames); `sdk_uuid` is the one new field.
    """
    type: str                                      # canonical lowercase MessageType value, or "tool_call"
    timestamp: float
    session_id: str
    message_id: str                                # record identity (existing field name, minted once)
    turn_id: str | None = None                      # owning assistant-turn identity, where applicable
    sdk_uuid: str | None = None                     # SDK's own per-frame uuid, where present
    subtype: str | None = None                      # only meaningful when type == "system"
    content: str | None = None                      # existing universal text field
    display: dict[str, Any] | None = None           # ToolDisplayInfo.to_dict(), for tool_call records only
    metadata: dict[str, Any] = field(default_factory=dict)  # existing catch-all

    def to_dict(self) -> dict[str, Any]:
        """Sparse-key serialization mirroring `prepare_for_storage`'s existing convention."""
        result: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "content": self.content,
        }
        if self.turn_id is not None:
            result["turn_id"] = self.turn_id
        if self.sdk_uuid is not None:
            result["sdk_uuid"] = self.sdk_uuid
        if self.subtype is not None:
            result["subtype"] = self.subtype
        if self.display is not None:
            result["display"] = self.display

        if self.type == "tool_call":
            # AC2: the live tool_call envelope has always been flat (every ToolCall field
            # at the top level) — match that shape exactly rather than introduce a second,
            # incompatible nesting convention for one record type. `self.metadata` already
            # contains session_id/turn_id/display (from `tool_call.to_dict()`) identical to
            # the top-level values just set above — .update() is a harmless same-value
            # overwrite, not a real duplication.
            result.update(self.metadata)
        elif self.metadata:
            result["metadata"] = self.metadata
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageRecord":
        if data.get("type") == "tool_call":
            metadata = {k: v for k, v in data.items() if k not in _MESSAGE_RECORD_CORE_KEYS}
        else:
            metadata = data.get("metadata", {})
        return cls(
            type=data.get("type", "unknown"),
            timestamp=data.get("timestamp", 0.0),
            session_id=data.get("session_id", ""),
            message_id=data.get("message_id", ""),
            turn_id=data.get("turn_id"),
            sdk_uuid=data.get("sdk_uuid"),
            subtype=data.get("subtype"),
            content=data.get("content"),
            display=data.get("display"),
            metadata=metadata,
        )

    @classmethod
    def from_sdk_message(
        cls,
        sdk_msg: Any,
        session_id: str,
        display: dict[str, Any] | None = None,
    ) -> "MessageRecord":
        """Shape 1+2 replacement: every AssistantMessage/UserMessage/SystemMessage/
        HookEventMessage/ResultMessage/Task* frame, via the existing per-type handlers."""
        timestamp = time.time()
        message_data = _sdk_message_to_parser_input(sdk_msg, session_id, timestamp)
        parsed = _MESSAGE_PARSER.parse_message(message_data)
        canonical_type = parsed.type.value

        return cls(
            type=canonical_type,
            timestamp=parsed.timestamp,
            session_id=session_id,
            message_id=_mint_message_id(),
            turn_id=_derive_turn_id(sdk_msg, canonical_type),
            sdk_uuid=getattr(sdk_msg, "uuid", None),
            subtype=parsed.metadata.get("subtype") if canonical_type == "system" else None,
            content=parsed.content,
            display=display,
            metadata=parsed.metadata,
        )

    @classmethod
    def from_tool_call(
        cls,
        tool_call: "ToolCall",
        triggering_message: dict[str, Any] | None = None,
    ) -> "MessageRecord":
        """AC4: maps a ToolCallUpdate (shape 4) onto a canonical `type="tool_call"` record."""
        metadata = tool_call.to_dict()
        if triggering_message is not None:
            # Plain flat dict, not a nested MessageRecord — storage-only embedding,
            # stripped before frontend propagation by the live broadcast sites
            # (permission_service.py, web_server.py).
            metadata["_triggering_message"] = triggering_message
            # Matches both live permission_service.py sites, which set this flat on the
            # emitted tool_call payload (not just nested in _triggering_message).
            request_id = triggering_message.get("request_id")
            if request_id is not None:
                metadata["request_id"] = request_id

        return cls(
            type="tool_call",
            timestamp=(
                tool_call.completed_at or tool_call.started_at
                or tool_call.created_at or time.time()
            ),
            session_id=tool_call.session_id,
            message_id=_mint_message_id(),
            turn_id=tool_call.turn_id,
            sdk_uuid=None,
            subtype=None,
            content=None,
            display=tool_call.display.to_dict() if tool_call.display else None,
            metadata=metadata,
        )

    @classmethod
    def from_user_input(
        cls,
        content: str,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> "MessageRecord":
        """Shape 3: bare outbound user/comm/attachment dict. `metadata` preserves the
        `comm`/`attachments` keys exactly as CommRouter populates them today."""
        return cls(
            type="user",
            timestamp=time.time(),
            session_id=session_id,
            message_id=_mint_message_id(),
            turn_id=None,
            sdk_uuid=None,
            subtype=None,
            content=content,
            display=None,
            metadata=dict(metadata) if metadata else {},
        )

    @classmethod
    def from_error(
        cls,
        content: str,
        session_id: str,
        error: str,
        message_id: str | None = None,
    ) -> "MessageRecord":
        """Shape 5: both error-fallback variants (claude_sdk.py's minimal dict and
        session_coordinator.py's raw passthrough). Mints `message_id` only if the caller
        didn't already have one to preserve."""
        return cls(
            type="error",
            timestamp=time.time(),
            session_id=session_id,
            message_id=message_id or _mint_message_id(),
            turn_id=None,
            sdk_uuid=None,
            subtype=None,
            content=content,
            display=None,
            metadata={"error": error},
        )


# Derived from the dataclass fields themselves (not hand-maintained) so a future
# field added to MessageRecord is automatically treated as core by to_dict()/
# from_dict()'s type=="tool_call" flatten/unflatten special case (§1) — adding a
# field here can never silently fall out of sync with the dataclass it mirrors.
_MESSAGE_RECORD_CORE_KEYS = {
    f.name for f in fields(MessageRecord) if f.name != "metadata"
}
