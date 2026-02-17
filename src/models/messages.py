"""
Unified message models for Claude WebUI.

This module provides dataclass-based message types that align with the Claude Agent SDK's
dataclass patterns while adding WebUI-specific types for permissions and display projection.

Key design principles:
1. SDK messages (AssistantMessage, UserMessage, etc.) are serialized via dataclasses.asdict()
2. WebUI-specific types (permissions, display) use the same dataclass pattern
3. StoredMessage provides a unified wrapper with _type discriminator for storage/WebSocket
4. DisplayMetadata (Issue #310) attaches display projection to any message type

Usage:
    from claude_agent_sdk import AssistantMessage
    from src.models.messages import StoredMessage, DisplayMetadata

    # Wrap SDK message for storage
    stored = StoredMessage.from_sdk_message(sdk_msg, session_id, timestamp)

    # Add display projection
    stored.display = DisplayMetadata(tool_states={...})

    # Serialize for storage/WebSocket
    json_data = stored.to_dict()
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

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

    Note: PERMISSION_REQUIRED, EXECUTING, ORPHANED kept for backward compatibility
    with DisplayProjection. New code should use the #324 states.
    """
    PENDING = "pending"
    PERMISSION_REQUIRED = "permission_required"  # Legacy (use AWAITING_PERMISSION)
    AWAITING_PERMISSION = "awaiting_permission"  # Issue #324
    EXECUTING = "executing"  # Legacy (use RUNNING)
    RUNNING = "running"  # Issue #324
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"  # Issue #324: Permission denied
    INTERRUPTED = "interrupted"  # Issue #324: Session terminated before completion
    ORPHANED = "orphaned"  # Legacy (use INTERRUPTED)


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage/WebSocket."""
        return {
            "message": self.message,
            "suggestions": self.suggestions,
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionInfo":
        """Create from stored dict."""
        return cls(
            message=data.get("message", ""),
            suggestions=data.get("suggestions", []),
            risk_level=data.get("risk_level", "medium"),
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

    # Result (when completed)
    result: Any = None
    error: str | None = None

    # Display hints (backend-computed)
    display: "ToolDisplayInfo | None" = None

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

        # Result fields
        if self.result is not None:
            result["result"] = self.result
        if self.error is not None:
            result["error"] = self.error

        # Display hints
        if self.display is not None:
            result["display"] = self.display.to_dict()

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
            result=data.get("result"),
            error=data.get("error"),
            display=display,
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

    @property
    def content(self) -> str:
        """Human-readable content for display."""
        return f"Permission requested for tool: {self.tool_name}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        return {
            'request_id': self.request_id,
            'tool_name': self.tool_name,
            'input_params': self.input_params,
            'suggestions': [s.to_dict() for s in self.suggestions],
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'content': self.content,
        }

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

    Tracks the visual state of a tool in the UI, computed by the backend
    DisplayProjection layer.
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


@dataclass
class DisplayMetadata:
    """
    Display projection metadata attached to messages.

    This is the core of Issue #310 - backend-computed display state that
    eliminates frontend business logic for tool lifecycle management.
    """
    tool_states: dict[str, ToolDisplayInfo] = field(default_factory=dict)
    orphaned_tools: list[str] = field(default_factory=list)
    linked_permissions: dict[str, str] = field(default_factory=dict)  # request_id → tool_use_id

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for WebSocket transmission."""
        return {
            'tool_states': {
                tid: info.to_dict()
                for tid, info in self.tool_states.items()
            },
            'orphaned_tools': self.orphaned_tools,
            'linked_permissions': self.linked_permissions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'DisplayMetadata':
        """Create from dict."""
        tool_states = {
            tid: ToolDisplayInfo.from_dict(info)
            for tid, info in data.get('tool_states', {}).items()
        }
        return cls(
            tool_states=tool_states,
            orphaned_tools=data.get('orphaned_tools', []),
            linked_permissions=data.get('linked_permissions', {}),
        )


# ============================================================
# Unified Message Wrapper
# ============================================================

@dataclass
class StoredMessage:
    """
    Unified wrapper for all message types in storage and WebSocket.

    This provides a consistent format for:
    - SDK messages (AssistantMessage, UserMessage, SystemMessage, ResultMessage)
    - WebUI messages (PermissionRequestMessage, PermissionResponseMessage)

    The _type field acts as a discriminator for deserialization.
    """
    _type: str  # Discriminator: 'AssistantMessage', 'PermissionRequestMessage', etc.
    timestamp: float
    session_id: str
    data: dict[str, Any]  # The actual message content (SDK asdict() or WebUI dataclass)
    display: DisplayMetadata | None = None  # Issue #310 projection

    @classmethod
    def from_sdk_message(
        cls,
        sdk_msg: Any,
        session_id: str,
        timestamp: float,
        display: DisplayMetadata | None = None
    ) -> 'StoredMessage':
        """
        Create from SDK message using dataclasses.asdict().

        Args:
            sdk_msg: SDK message object (AssistantMessage, UserMessage, etc.)
            session_id: Session identifier
            timestamp: Message timestamp
            display: Optional display projection metadata
        """
        return cls(
            _type=type(sdk_msg).__name__,
            timestamp=timestamp,
            session_id=session_id,
            data=asdict(sdk_msg),
            display=display,
        )

    @classmethod
    def from_permission_request(
        cls,
        request: PermissionRequestMessage,
        display: DisplayMetadata | None = None
    ) -> 'StoredMessage':
        """Create from permission request message."""
        return cls(
            _type='PermissionRequestMessage',
            timestamp=request.timestamp,
            session_id=request.session_id or '',
            data=request.to_dict(),
            display=display,
        )

    @classmethod
    def from_permission_response(
        cls,
        response: PermissionResponseMessage,
        display: DisplayMetadata | None = None
    ) -> 'StoredMessage':
        """Create from permission response message."""
        return cls(
            _type='PermissionResponseMessage',
            timestamp=response.timestamp,
            session_id=response.session_id or '',
            data=response.to_dict(),
            display=display,
        )

    @classmethod
    def from_tool_call_update(
        cls,
        tool_call: ToolCall,
        triggering_message: dict[str, Any] | None = None,
    ) -> 'StoredMessage':
        """
        Create from a ToolCall lifecycle update (Issue #494).

        Stores the ToolCall state at each transition (PENDING, AWAITING_PERMISSION,
        RUNNING, COMPLETED, etc.) as a StoredMessage with _type="ToolCallUpdate".

        The optional triggering_message embeds the raw SDK data that caused
        this transition (e.g., the ToolUseBlock, PermissionRequestMessage data,
        ToolResultBlock). It is stripped before frontend propagation.
        """
        import time as _time
        data = tool_call.to_dict()
        if triggering_message is not None:
            data['_triggering_message'] = triggering_message
        return cls(
            _type='ToolCallUpdate',
            timestamp=tool_call.completed_at or tool_call.started_at or tool_call.created_at or _time.time(),
            session_id=tool_call.session_id,
            data=data,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage/WebSocket."""
        result = {
            '_type': self._type,
            'timestamp': self.timestamp,
            'session_id': self.session_id,
            'data': self.data,
        }
        if self.display:
            result['display'] = self.display.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'StoredMessage':
        """Deserialize from storage/WebSocket."""
        display = None
        if 'display' in data and data['display']:
            display = DisplayMetadata.from_dict(data['display'])
        return cls(
            _type=data.get('_type', 'Unknown'),
            timestamp=data.get('timestamp', 0.0),
            session_id=data.get('session_id', ''),
            data=data.get('data', {}),
            display=display,
        )

    def get_content(self) -> str:
        """Extract human-readable content from the message."""
        # For SDK messages with content field
        if 'content' in self.data:
            content = self.data['content']
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Extract text from content blocks
                texts = []
                for block in content:
                    if isinstance(block, dict) and 'text' in block:
                        texts.append(block['text'])
                return ' '.join(texts)
        return ''

    def get_tool_uses(self) -> list[dict[str, Any]]:
        """Extract tool use blocks from AssistantMessage."""
        if self._type != 'AssistantMessage':
            return []
        tool_uses = []
        content = self.data.get('content', [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and 'id' in block and 'name' in block:
                    tool_uses.append(block)
        return tool_uses

    def get_tool_results(self) -> list[dict[str, Any]]:
        """Extract tool result blocks from UserMessage."""
        if self._type != 'UserMessage':
            return []
        tool_results = []
        content = self.data.get('content', [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and 'tool_use_id' in block:
                    tool_results.append(block)
        return tool_results


# ============================================================
# Type aliases for clarity
# ============================================================

# All message types that can be stored
WebUIMessage = PermissionRequestMessage | PermissionResponseMessage
SDKMessageType = Literal['AssistantMessage', 'UserMessage', 'SystemMessage', 'ResultMessage']
WebUIMessageType = Literal['PermissionRequestMessage', 'PermissionResponseMessage']
AllMessageTypes = SDKMessageType | WebUIMessageType


# ============================================================
# Conversion Utilities
# ============================================================

def sdk_message_to_stored(
    sdk_msg: Any,
    session_id: str,
    timestamp: float | None = None,
) -> StoredMessage:
    """
    Convert an SDK message object to StoredMessage format.

    This is the primary entry point for converting SDK messages to the
    unified storage format. It uses dataclasses.asdict() for clean serialization.

    Args:
        sdk_msg: SDK message object (AssistantMessage, UserMessage, etc.)
        session_id: Session identifier
        timestamp: Optional timestamp (defaults to current time)

    Returns:
        StoredMessage ready for storage/WebSocket transmission
    """
    import time
    if timestamp is None:
        timestamp = time.time()

    return StoredMessage.from_sdk_message(sdk_msg, session_id, timestamp)


def stored_to_legacy_format(stored: StoredMessage) -> dict[str, Any]:
    """
    Convert StoredMessage to legacy format for backward compatibility.

    This allows gradual migration - new code produces StoredMessage,
    but existing consumers can still receive the legacy dict format.

    The legacy format has:
    - type: 'assistant', 'user', 'system', 'result', 'permission_request', etc.
    - content: Human-readable text
    - metadata: Additional fields
    - timestamp, session_id
    """
    # Map _type to legacy type string
    type_mapping = {
        'AssistantMessage': 'assistant',
        'UserMessage': 'user',
        'SystemMessage': 'system',
        'ResultMessage': 'result',
        'PermissionRequestMessage': 'permission_request',
        'PermissionResponseMessage': 'permission_response',
    }
    legacy_type = type_mapping.get(stored._type, 'unknown')

    # Build legacy format
    result = {
        'type': legacy_type,
        'timestamp': stored.timestamp,
        'session_id': stored.session_id,
    }

    # Add content
    result['content'] = stored.get_content()

    # Copy data fields to top level for backward compatibility
    result.update(stored.data)

    # Build metadata
    metadata = {
        'has_tool_uses': len(stored.get_tool_uses()) > 0,
        'has_tool_results': len(stored.get_tool_results()) > 0,
        'tool_uses': stored.get_tool_uses(),
        'tool_results': stored.get_tool_results(),
    }

    # Extract thinking blocks if present
    thinking_blocks = []
    if stored._type == 'AssistantMessage':
        content = stored.data.get('content', [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and 'thinking' in block:
                    thinking_blocks.append({
                        'content': block['thinking'],
                        'timestamp': stored.timestamp,
                    })
    metadata['has_thinking'] = len(thinking_blocks) > 0
    metadata['thinking_blocks'] = thinking_blocks

    # Add display metadata if present
    if stored.display:
        result['display'] = stored.display.to_dict()

    result['metadata'] = metadata

    return result


def legacy_to_stored(legacy: dict[str, Any]) -> StoredMessage:
    """
    Convert legacy format dict to StoredMessage.

    This enables reading old stored messages into the new format.
    """
    # Determine _type from legacy type field
    type_mapping = {
        'assistant': 'AssistantMessage',
        'user': 'UserMessage',
        'system': 'SystemMessage',
        'result': 'ResultMessage',
        'permission_request': 'PermissionRequestMessage',
        'permission_response': 'PermissionResponseMessage',
    }
    legacy_type = legacy.get('type', 'unknown')
    _type = type_mapping.get(legacy_type, 'Unknown')

    # Extract display if present
    display = None
    if 'display' in legacy and legacy['display']:
        display = DisplayMetadata.from_dict(legacy['display'])

    # Build data dict (exclude metadata and display from top level)
    data = {k: v for k, v in legacy.items()
            if k not in ('type', 'timestamp', 'session_id', 'metadata', 'display', '_type')}

    return StoredMessage(
        _type=_type,
        timestamp=legacy.get('timestamp', 0.0),
        session_id=legacy.get('session_id', ''),
        data=data,
        display=display,
    )


# ============================================================
# Display Projection (Issue #310 Core)
# ============================================================

class DisplayProjection:
    """
    Computes display metadata for messages based on tool lifecycle.

    This is the core of Issue #310 - moving tool correlation and lifecycle
    tracking from the frontend to the backend. The frontend simply reads
    the computed `display` field instead of maintaining complex state.

    Usage:
        projection = DisplayProjection()

        # Process messages in order
        for msg in messages:
            display = projection.process_message(msg)
            msg.display = display

        # Or process all at once
        messages = projection.process_all(messages)
    """

    def __init__(self) -> None:
        """Initialize projection state."""
        # Tool state tracking: tool_use_id → ToolDisplayInfo
        self._tool_states: dict[str, ToolDisplayInfo] = {}

        # Permission correlation: request_id → tool_use_id
        self._permission_to_tool: dict[str, str] = {}

        # Tool signature → tool_use_id for permission matching
        # Signature = f"{tool_name}:{first_param_hash}"
        self._tool_signatures: dict[str, str] = {}

        # Active tools waiting for results
        self._active_tools: set[str] = set()

        # Orphaned tools (session reset/interrupt)
        self._orphaned_tools: list[str] = []

    def reset(self) -> None:
        """Reset projection state (e.g., on session reset)."""
        self._tool_states.clear()
        self._permission_to_tool.clear()
        self._tool_signatures.clear()
        self._active_tools.clear()
        self._orphaned_tools.clear()

    def mark_tools_orphaned(self) -> list[str]:
        """
        Mark all active tools as orphaned.

        Call this when session is interrupted/reset to mark pending tools
        as abandoned. Returns list of orphaned tool IDs.
        """
        orphaned = list(self._active_tools)
        for tool_id in orphaned:
            if tool_id in self._tool_states:
                self._tool_states[tool_id].state = ToolState.ORPHANED
                self._tool_states[tool_id].style = 'orphaned'
            self._orphaned_tools.append(tool_id)
        self._active_tools.clear()
        return orphaned

    def process_message(self, message: StoredMessage) -> DisplayMetadata:
        """
        Process a single message and compute its display metadata.

        Updates internal state and returns the computed DisplayMetadata
        to attach to the message.
        """
        # Handle different message types
        if message._type == 'AssistantMessage':
            return self._process_assistant_message(message)
        elif message._type == 'UserMessage':
            return self._process_user_message(message)
        elif message._type == 'PermissionRequestMessage':
            return self._process_permission_request(message)
        elif message._type == 'PermissionResponseMessage':
            return self._process_permission_response(message)
        else:
            # No special handling needed
            return self._build_display_metadata()

    def process_all(self, messages: list[StoredMessage]) -> list[StoredMessage]:
        """
        Process all messages and attach display metadata.

        Returns the same list with display fields populated.
        """
        for msg in messages:
            msg.display = self.process_message(msg)
        return messages

    def _process_assistant_message(self, message: StoredMessage) -> DisplayMetadata:
        """Process AssistantMessage - extract and track tool uses."""
        tool_uses = message.get_tool_uses()

        for tool_use in tool_uses:
            tool_id = tool_use.get('id')
            tool_name = tool_use.get('name', '')
            if not tool_id:
                continue

            # Create initial tool state
            self._tool_states[tool_id] = ToolDisplayInfo(
                state=ToolState.PENDING,
                visible=True,
                collapsed=False,
                style='default',
            )

            # Track as active (waiting for result)
            self._active_tools.add(tool_id)

            # Create signature for permission matching
            signature = self._create_tool_signature(tool_name, tool_use.get('input', {}))
            self._tool_signatures[signature] = tool_id

        return self._build_display_metadata()

    def _process_user_message(self, message: StoredMessage) -> DisplayMetadata:
        """Process UserMessage - extract tool results and update states."""
        tool_results = message.get_tool_results()

        for result in tool_results:
            tool_id = result.get('tool_use_id')
            if not tool_id:
                continue

            if tool_id in self._tool_states:
                # Determine success/failure
                is_error = result.get('is_error', False)
                if is_error:
                    self._tool_states[tool_id].state = ToolState.FAILED
                    self._tool_states[tool_id].style = 'error'
                else:
                    self._tool_states[tool_id].state = ToolState.COMPLETED
                    self._tool_states[tool_id].style = 'success'

                # No longer active
                self._active_tools.discard(tool_id)

        return self._build_display_metadata()

    def _process_permission_request(self, message: StoredMessage) -> DisplayMetadata:
        """Process PermissionRequestMessage - link to tool and update state."""
        request_id = message.data.get('request_id', '')
        tool_name = message.data.get('tool_name', '')
        input_params = message.data.get('input_params', {})

        # Find matching tool by signature
        signature = self._create_tool_signature(tool_name, input_params)
        tool_id = self._tool_signatures.get(signature)

        if tool_id:
            # Link permission to tool
            self._permission_to_tool[request_id] = tool_id

            # Update tool state
            if tool_id in self._tool_states:
                self._tool_states[tool_id].state = ToolState.PERMISSION_REQUIRED
                self._tool_states[tool_id].style = 'warning'
                self._tool_states[tool_id].linked_permission_id = request_id

        return self._build_display_metadata()

    def _process_permission_response(self, message: StoredMessage) -> DisplayMetadata:
        """Process PermissionResponseMessage - update tool state based on decision."""
        request_id = message.data.get('request_id', '')
        decision = message.data.get('decision', '')

        # Find linked tool
        tool_id = self._permission_to_tool.get(request_id)

        if tool_id and tool_id in self._tool_states:
            if decision == 'allow':
                # Permission granted - tool now executing
                self._tool_states[tool_id].state = ToolState.EXECUTING
                self._tool_states[tool_id].style = 'default'
            else:
                # Permission denied - tool failed
                self._tool_states[tool_id].state = ToolState.FAILED
                self._tool_states[tool_id].style = 'error'
                self._active_tools.discard(tool_id)

        return self._build_display_metadata()

    def _create_tool_signature(self, tool_name: str, input_params: dict) -> str:
        """
        Create a signature for matching tools to permission requests.

        Uses tool name + hash of first significant parameter to create
        a reasonably unique signature without requiring exact param matching.
        """
        import hashlib
        import json

        # Get first significant param value for hashing
        first_value = ''
        for key, value in input_params.items():
            if value and key not in ('_simulatedSedEdit',):  # Skip internal params
                if isinstance(value, str):
                    first_value = value[:100]  # Limit length
                else:
                    first_value = json.dumps(value)[:100]
                break

        # Create hash
        param_hash = hashlib.md5(first_value.encode()).hexdigest()[:8]
        return f"{tool_name}:{param_hash}"

    def _build_display_metadata(self) -> DisplayMetadata:
        """Build current DisplayMetadata from internal state."""
        return DisplayMetadata(
            tool_states=dict(self._tool_states),  # Copy to avoid mutation
            orphaned_tools=list(self._orphaned_tools),
            linked_permissions=dict(self._permission_to_tool),
        )
