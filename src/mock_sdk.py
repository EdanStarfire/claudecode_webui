"""
Mock SDK Engine for Deterministic Testing and QA Validation.

Provides MockClaudeSDK — a drop-in replacement for ClaudeSDK that replays
recorded session data deterministically. Enables testing of the full message
pipeline (SDK -> storage -> WebSocket -> frontend) without API keys.

Classes:
    ActionType: Enum of user action types at replay boundaries.
    ActionMismatchError: Raised when test fixture sends wrong action type.
    SessionRecording: Parses messages.jsonl + state.json into replay segments.
    ReplayEngine: Replays segments with timing and action validation.
    MockClaudeSDK: Drop-in replacement for ClaudeSDK with same public interface.
"""

import asyncio
import json
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any

from .claude_sdk import SessionInfo, SessionState

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Types of user actions at replay boundaries."""
    USER_MESSAGE = "USER_MESSAGE"
    PERMISSION_ALLOW = "PERMISSION_ALLOW"
    PERMISSION_ALLOW_WITH_SUGGESTIONS = "PERMISSION_ALLOW_WITH_SUGGESTIONS"
    PERMISSION_DENY = "PERMISSION_DENY"
    PERMISSION_GUIDANCE = "PERMISSION_GUIDANCE"


class ActionMismatchError(Exception):
    """Raised when a test fixture sends the wrong action type."""

    def __init__(self, segment_index: int, expected: ActionType, got: str):
        self.segment_index = segment_index
        self.expected = expected
        self.got = got
        super().__init__(
            f"Action mismatch at segment {segment_index}: "
            f"expected {expected.value}, got {got}"
        )


class SessionRecording:
    """
    Parses a recorded session directory (messages.jsonl + state.json)
    into indexed segments for deterministic replay.

    A segment is a sequence of SDK-generated messages between user action points.
    """

    def __init__(self, session_dir: str | Path):
        self.session_dir = Path(session_dir)
        self.messages: list[dict] = []
        self.state: dict = {}
        self.segments: list[list[dict]] = []
        self.actions: list[ActionType] = []
        self._parse()

    def _parse(self):
        """Parse messages.jsonl and state.json, build segments."""
        self._load_messages()
        self._load_state()
        self._build_segments()

    def _load_messages(self):
        """Load and parse messages.jsonl (handles both legacy and SDK formats)."""
        messages_path = self.session_dir / "messages.jsonl"
        if not messages_path.exists():
            raise FileNotFoundError(f"messages.jsonl not found in {self.session_dir}")

        self.messages = []
        for line in messages_path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                self.messages.append(json.loads(line))

    def _load_state(self):
        """Load state.json for session metadata."""
        state_path = self.session_dir / "state.json"
        if state_path.exists():
            self.state = json.loads(state_path.read_text(encoding="utf-8"))

    def _get_message_type(self, msg: dict) -> str:
        """Get normalized message type from either format."""
        # New SDK format: {"_type": "AssistantMessage", ...}
        if "_type" in msg:
            return msg["_type"]
        # Legacy format: {"type": "assistant", ...}
        return msg.get("type", "unknown")

    def _is_tool_result(self, msg: dict) -> bool:
        """Check if a message is a tool result (SDK-generated, despite user type)."""
        msg_type = self._get_message_type(msg)

        # New format: UserMessage with tool results in data
        if msg_type == "UserMessage":
            data = msg.get("data", {})
            content = data.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        return True
            return False

        # Legacy format: user type with tool_results in metadata
        if msg_type == "user":
            metadata = msg.get("metadata", {})
            if metadata.get("has_tool_results", False):
                return True
            tool_results = metadata.get("tool_results", [])
            if tool_results:
                return True

        return False

    def _is_permission_request(self, msg: dict) -> bool:
        """Check if a message is a permission request."""
        msg_type = self._get_message_type(msg)
        if msg_type == "permission_request":
            return True
        metadata = msg.get("metadata", {})
        return metadata.get("has_permission_requests", False)

    def _is_permission_response(self, msg: dict) -> bool:
        """Check if a message is a permission response."""
        msg_type = self._get_message_type(msg)
        if msg_type == "permission_response":
            return True
        metadata = msg.get("metadata", {})
        return metadata.get("has_permission_responses", False)

    def _is_user_text_message(self, msg: dict) -> bool:
        """Check if a message is a genuine user text message (not tool result)."""
        msg_type = self._get_message_type(msg)

        # Must be user type
        if msg_type not in ("user", "UserMessage"):
            return False

        # Not a tool result
        if self._is_tool_result(msg):
            return False

        # Not a permission response
        if self._is_permission_response(msg):
            return False

        # Has actual text content
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            return True

        # Check data.content for new format
        if msg_type == "UserMessage":
            data = msg.get("data", {})
            data_content = data.get("content", "")
            if isinstance(data_content, str) and data_content.strip():
                return True

        return False

    def _classify_permission_response(self, msg: dict) -> ActionType:
        """Classify a permission response into its specific action type."""
        metadata = msg.get("metadata", {})

        # Check for deny
        behavior = metadata.get("behavior", "")
        if behavior == "deny":
            return ActionType.PERMISSION_DENY

        # Check for guidance (user message following permission_request)
        if metadata.get("is_guidance", False):
            return ActionType.PERMISSION_GUIDANCE

        # Check for allow with suggestions
        applied_updates = metadata.get("applied_updates", [])
        updated_permissions = metadata.get("updated_permissions", [])
        if applied_updates or updated_permissions:
            return ActionType.PERMISSION_ALLOW_WITH_SUGGESTIONS

        # Default: plain allow
        return ActionType.PERMISSION_ALLOW

    def _is_sdk_generated(self, msg: dict) -> bool:
        """Determine if a message is SDK-generated (auto-replayed)."""
        msg_type = self._get_message_type(msg)

        # System messages are always SDK-generated
        if msg_type in ("system", "SystemMessage"):
            return True

        # Assistant messages are always SDK-generated
        if msg_type in ("assistant", "AssistantMessage"):
            return True

        # Result messages are always SDK-generated
        if msg_type in ("result", "ResultMessage"):
            return True

        # Tool results appear as user type but are SDK-generated
        if self._is_tool_result(msg):
            return True

        # Permission requests are SDK-generated (but trigger a pause)
        if self._is_permission_request(msg):
            return True

        return False

    def _build_segments(self):
        """
        Split messages into segments at user action boundaries.

        Each segment is a list of SDK-generated messages to replay.
        Between segments is a user action (stored in self.actions).
        """
        self.segments = []
        self.actions = []
        current_segment: list[dict] = []

        i = 0
        while i < len(self.messages):
            msg = self.messages[i]

            if self._is_user_text_message(msg):
                # User text message — action boundary
                if current_segment or not self.segments:
                    self.segments.append(current_segment)
                    current_segment = []
                self.actions.append(ActionType.USER_MESSAGE)

            elif self._is_permission_response(msg):
                # Permission response — action boundary
                if current_segment or not self.segments:
                    self.segments.append(current_segment)
                    current_segment = []
                action = self._classify_permission_response(msg)
                self.actions.append(action)

            elif self._is_sdk_generated(msg):
                # SDK-generated message — part of current segment
                current_segment.append(msg)

            i += 1

        # Add any trailing SDK messages as a final segment
        if current_segment:
            self.segments.append(current_segment)

    def get_segment_count(self) -> int:
        """Return total number of segments."""
        return len(self.segments)

    def get_action_count(self) -> int:
        """Return total number of user actions."""
        return len(self.actions)

    def get_segment(self, index: int) -> list[dict]:
        """Get messages for a specific segment."""
        if 0 <= index < len(self.segments):
            return self.segments[index]
        return []

    def get_expected_action(self, action_index: int) -> ActionType | None:
        """Get the expected action type at a given action index."""
        if 0 <= action_index < len(self.actions):
            return self.actions[action_index]
        return None

    def get_timestamp(self, msg: dict) -> float:
        """Extract timestamp from a message (either format)."""
        return msg.get("timestamp", 0.0)


class ReplayEngine:
    """
    Replays segments of SDK messages with timing and action validation.
    """

    def __init__(
        self,
        recording: SessionRecording,
        message_callback: Any = None,
        permission_callback: Any = None,
        speed_factor: float = 1.0,
    ):
        self.recording = recording
        self.message_callback = message_callback
        self.permission_callback = permission_callback
        self.speed_factor = speed_factor
        self._segment_cursor = 0
        self._action_cursor = 0
        self._current_task: asyncio.Task | None = None
        self._interrupted = False

    async def replay_segment(self, segment_index: int) -> None:
        """
        Replay all messages in a segment with timing delays.

        Fires message_callback for each message. If a permission_request
        is encountered, fires permission_callback and pauses.
        """
        segment = self.recording.get_segment(segment_index)
        if not segment:
            return

        self._interrupted = False
        prev_timestamp = None

        for msg in segment:
            if self._interrupted:
                break

            # Compute delay from timestamps
            current_timestamp = self.recording.get_timestamp(msg)
            if prev_timestamp is not None and self.speed_factor > 0:
                delta = current_timestamp - prev_timestamp
                if delta > 0:
                    await asyncio.sleep(delta * self.speed_factor)
            prev_timestamp = current_timestamp

            # Fire permission callback for permission requests
            if self.recording._is_permission_request(msg):
                if self.permission_callback:
                    # Extract tool info from message
                    metadata = msg.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    input_params = metadata.get("input_params", {})

                    # Also check data field for new format
                    data = msg.get("data", {})
                    if not tool_name or tool_name == "unknown":
                        tool_name = data.get("tool_name", "unknown")
                    if not input_params:
                        input_params = data.get("input_params", {})

                    # Fire the callback but don't await result here —
                    # the mock SDK handles the permission response flow
                    if self.message_callback:
                        await self._fire_callback(self.message_callback, msg)
                continue

            # Fire message callback
            if self.message_callback:
                await self._fire_callback(self.message_callback, msg)

        self._segment_cursor = segment_index + 1

    def validate_action(
        self, expected: ActionType, actual_type: str, segment_index: int
    ) -> None:
        """
        Validate that the actual action matches the expected action.

        Raises ActionMismatchError if mismatched.
        """
        if expected.value != actual_type:
            raise ActionMismatchError(segment_index, expected, actual_type)

    def interrupt(self) -> None:
        """Interrupt current replay."""
        self._interrupted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    async def _fire_callback(self, callback: Any, *args) -> None:
        """Safely fire a callback (sync or async)."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Error in replay callback: {e}")


class MockClaudeSDK:
    """
    Drop-in replacement for ClaudeSDK that replays recorded session data.

    Provides the same public interface (9 methods, 4 callbacks) as ClaudeSDK
    but replays messages from a recorded session directory deterministically.
    """

    def __init__(
        self,
        session_id: str,
        working_directory: str,
        message_callback: Any = None,
        permission_callback: Any = None,
        error_callback: Any = None,
        speed_factor: float = 0.0,
        session_dir: str | None = None,
        **kwargs,
    ):
        """
        Initialize MockClaudeSDK.

        Args:
            session_id: Unique session identifier
            working_directory: Directory for the session (also used to locate recordings)
            message_callback: Called when new messages are received
            permission_callback: Called for permission requests
            error_callback: Called when errors occur
            speed_factor: Replay speed (0.0 = instant, 1.0 = real-time)
            session_dir: Path to recorded session directory (overrides working_directory)
            **kwargs: Accept and ignore remaining ClaudeSDK parameters
        """
        self.session_id = session_id
        self.working_directory = Path(working_directory)
        self.message_callback = message_callback
        self.permission_callback = permission_callback
        self.error_callback = error_callback
        self.speed_factor = speed_factor
        self.session_dir = Path(session_dir) if session_dir else self.working_directory

        self.info = SessionInfo(
            session_id=session_id,
            working_directory=str(self.working_directory),
        )

        self._recording: SessionRecording | None = None
        self._engine: ReplayEngine | None = None
        self._action_cursor = 0
        self._replay_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        # Wrap message_callback to convert fixture format → legacy dict format
        # so SessionCoordinator's MessageProcessor can parse them correctly
        self._raw_message_callback = message_callback
        if message_callback:
            self.message_callback = self._converting_callback
        else:
            self.message_callback = None

        # Compatibility attributes that ClaudeSDK has
        self.current_permission_mode = kwargs.get("permissions", "default")
        self.model = kwargs.get("model")
        self.storage_manager = kwargs.get("storage_manager")
        self.session_manager = kwargs.get("session_manager")

    # ---- Fixture → legacy dict conversion (issue #561) ----

    _SDK_TYPE_MAP = {
        "SystemMessage": "system",
        "AssistantMessage": "assistant",
        "ResultMessage": "result",
        "UserMessage": "user",
    }

    def _convert_fixture_message(self, msg: dict) -> dict:
        """Convert _type-based fixture message to legacy dict format.

        The SessionCoordinator message callback expects dicts with
        ``"type": "assistant"`` etc.  Fixture JSONL may store SDK-style
        ``"_type": "AssistantMessage"`` dicts.  This method normalises
        them so the existing MessageProcessor handlers can parse them.
        """
        sdk_type = msg.get("_type")
        if not sdk_type:
            # Already in legacy format
            return msg

        legacy_type = self._SDK_TYPE_MAP.get(sdk_type, "unknown")
        converted = {
            "type": legacy_type,
            "timestamp": msg.get("timestamp", time.time()),
            "session_id": msg.get("session_id", self.session_id),
        }
        data = msg.get("data", {})

        if legacy_type == "system":
            subtype = data.get("subtype") or data.get("type", "init")
            converted["subtype"] = subtype
            converted["content"] = data.get("content", f"System {subtype}")
            # Preserve init data for session info feature
            if data:
                converted["metadata"] = {"subtype": subtype, "init_data": data}
        elif legacy_type == "assistant":
            # Extract text content from content blocks
            content_blocks = data.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and "text" in block:
                    text_parts.append(block["text"])
            converted["content"] = "\n".join(text_parts) if text_parts else ""
        elif legacy_type == "result":
            subtype = data.get("subtype", "success")
            converted["subtype"] = subtype
            converted["content"] = f"Result: {subtype}"
            converted["metadata"] = {
                "subtype": subtype,
                "duration_ms": data.get("duration_ms"),
                "is_error": data.get("is_error", False),
                "num_turns": data.get("num_turns"),
                "total_cost_usd": data.get("total_cost_usd"),
            }
        else:
            converted["content"] = msg.get("content", "")

        return converted

    async def _converting_callback(self, msg: dict) -> None:
        """Wrapper that converts fixture messages before calling the real callback."""
        converted = self._convert_fixture_message(msg)
        if self._raw_message_callback:
            if asyncio.iscoroutinefunction(self._raw_message_callback):
                await self._raw_message_callback(converted)
            else:
                self._raw_message_callback(converted)

    async def start(self) -> bool:
        """
        Start the mock session.

        Loads the SessionRecording and replays Segment 0 (client_launched).
        """
        try:
            self.info.state = SessionState.STARTING
            self.info.start_time = time.time()

            # Load recording
            self._recording = SessionRecording(self.session_dir)
            self._engine = ReplayEngine(
                recording=self._recording,
                message_callback=self.message_callback,
                permission_callback=self.permission_callback,
                speed_factor=self.speed_factor,
            )

            self.info.state = SessionState.RUNNING

            # Replay Segment 0 (typically just client_launched system message)
            if self._recording.get_segment_count() > 0:
                await self._engine.replay_segment(0)

            # Notify session manager if available
            if self.session_manager:
                try:
                    await self.session_manager.mark_session_active(self.session_id)
                except Exception:
                    pass  # Session manager is optional for mock

            return True

        except Exception as e:
            logger.error(f"Failed to start mock session: {e}")
            self.info.state = SessionState.FAILED
            self.info.error_message = str(e)
            if self.error_callback:
                await self._safe_callback(self.error_callback, "startup_failed", e)
            return False

    async def send_message(self, message: str) -> bool:
        """
        Process a user message.

        Validates that the expected action is USER_MESSAGE, then replays
        the next segment of SDK messages.
        """
        if self.info.state != SessionState.RUNNING:
            return False

        try:
            # Validate action type
            expected_action = self._recording.get_expected_action(self._action_cursor)
            if expected_action is not None:
                self._engine.validate_action(
                    expected_action,
                    ActionType.USER_MESSAGE.value,
                    self._action_cursor,
                )
            self._action_cursor += 1

            # Store user message via callback (matches real SDK flow)
            if self.message_callback:
                from datetime import UTC, datetime

                user_msg = {
                    "type": "user",
                    "content": message,
                    "session_id": self.session_id,
                    "timestamp": datetime.now(UTC).timestamp(),
                }
                await self._safe_callback(self.message_callback, user_msg)

            # Store via storage manager if available
            if self.storage_manager:
                from datetime import UTC, datetime

                user_msg = {
                    "type": "user",
                    "content": message,
                    "session_id": self.session_id,
                    "timestamp": datetime.now(UTC).timestamp(),
                }
                await self.storage_manager.append_message(user_msg)

            self.info.message_count += 1
            self.info.last_activity = time.time()

            # Replay next segment
            segment_index = self._engine._segment_cursor
            if segment_index < self._recording.get_segment_count():
                self._replay_task = asyncio.create_task(
                    self._engine.replay_segment(segment_index)
                )
                await self._replay_task

                # Check if segment ends with a permission request and handle it
                await self._handle_pending_permissions()

            return True

        except ActionMismatchError:
            raise
        except Exception as e:
            logger.error(f"Error in mock send_message: {e}")
            if self.error_callback:
                await self._safe_callback(self.error_callback, "send_message_failed", e)
            return False

    async def _handle_pending_permissions(self):
        """
        After replaying a segment, check if we need to continue
        replaying more segments (e.g., after permission allow).

        The permission flow works as follows:
        1. Segment ends with permission_request → callback fires
        2. User sends permission response → validate action
        3. Continue to next segment
        """
        # Continue replaying segments as long as the next action is a permission action
        while self._action_cursor < self._recording.get_action_count():
            expected = self._recording.get_expected_action(self._action_cursor)
            if expected in (
                ActionType.PERMISSION_ALLOW,
                ActionType.PERMISSION_ALLOW_WITH_SUGGESTIONS,
                ActionType.PERMISSION_DENY,
                ActionType.PERMISSION_GUIDANCE,
            ):
                # Auto-advance past permission action
                self._action_cursor += 1

                # Replay next segment (tool results after permission)
                segment_index = self._engine._segment_cursor
                if segment_index < self._recording.get_segment_count():
                    self._replay_task = asyncio.create_task(
                        self._engine.replay_segment(segment_index)
                    )
                    await self._replay_task
            else:
                break

    async def interrupt_session(self) -> bool:
        """Interrupt the current replay."""
        try:
            if self._engine:
                self._engine.interrupt()
            if self._replay_task and not self._replay_task.done():
                self._replay_task.cancel()
                try:
                    await self._replay_task
                except asyncio.CancelledError:
                    pass
            return True
        except Exception as e:
            logger.error(f"Failed to interrupt mock session: {e}")
            return False

    async def set_permission_mode(self, mode: str) -> bool:
        """No-op for mock — permission mode changes are ignored."""
        self.current_permission_mode = mode
        return True

    async def disconnect(self) -> bool:
        """Disconnect the mock session."""
        self._shutdown_event.set()
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
        return True

    async def terminate(self, timeout: float = 5.0) -> bool:
        """Terminate the mock session."""
        self.info.state = SessionState.TERMINATED
        self._shutdown_event.set()
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
            try:
                await asyncio.wait_for(self._replay_task, timeout=timeout)
            except (TimeoutError, asyncio.CancelledError):
                pass
        return True

    def get_queue_size(self) -> int:
        """Return 0 — mock has no queue."""
        return 0

    def get_info(self) -> dict[str, Any]:
        """Get session info dict."""
        from dataclasses import asdict

        info_dict = asdict(self.info)
        info_dict["state"] = self.info.state.value
        info_dict["mock"] = True
        return info_dict

    def is_running(self) -> bool:
        """Check if mock session is running."""
        return self.info.state in (SessionState.RUNNING, SessionState.PROCESSING)

    async def _safe_callback(self, callback, *args):
        """Safely execute a callback."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Error in mock callback: {e}")
