"""Tests for the canonical MessageRecord schema (Issue #2084 stage 3-A).

Pure unit-level — nothing in this stage is wired into the live pipeline (claude_sdk.py,
session_coordinator.py, message_parser.py, mock_sdk.py, data_storage.py,
permission_service.py are all untouched), so there is no integration surface yet. These
tests exercise the 5 factory methods, the single identity-assignment points
(_mint_message_id/_derive_turn_id), the sparse to_dict()/from_dict() round-trip, and the
SYSTEM_SUBTYPES registry reconciliation (AC5).
"""

import time

from claude_agent_sdk import (
    AssistantMessage,
    HookEventMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    UserMessage,
)

from backend.models.messages import (
    CURRENT_MESSAGE_SCHEMA_VERSION,
    MessageRecord,
    PermissionInfo,
    PermissionRequestMessage,
    PermissionResponseMessage,
    ToolCall,
    ToolDisplayInfo,
    ToolState,
    _derive_turn_id,
    _mint_message_id,
)
from shared.event_registry import SYSTEM_SUBTYPES


class TestSchemaVersion:
    def test_current_schema_version_is_defined(self):
        assert CURRENT_MESSAGE_SCHEMA_VERSION == 1


class TestMintMessageId:
    def test_mints_unique_uuid_strings(self):
        first = _mint_message_id()
        second = _mint_message_id()
        assert first != second
        assert isinstance(first, str)
        assert len(first) == 36  # uuid4 string form


class TestDeriveTurnId:
    def test_assistant_uses_message_id(self):
        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hi")], model="claude-3-5-sonnet-20241022",
            message_id="anthropic-turn-1",
        )
        assert _derive_turn_id(sdk_msg, "assistant") == "anthropic-turn-1"

    def test_assistant_with_no_message_id_is_none(self):
        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hi")], model="claude-3-5-sonnet-20241022",
        )
        assert _derive_turn_id(sdk_msg, "assistant") is None

    def test_task_started_uses_its_own_uuid(self):
        sdk_msg = TaskStartedMessage(
            subtype="task_started", data={}, task_id="t1", description="alpha: go",
            uuid="task-uuid-1", session_id="sub-sess",
        )
        assert _derive_turn_id(sdk_msg, "system") == "task-uuid-1"

    def test_hook_event_uses_its_own_uuid(self):
        sdk_msg = HookEventMessage(
            subtype="hook_response", data={}, hook_event_name="PostToolUse",
            session_id="sess-1", uuid="hook-uuid-1",
        )
        assert _derive_turn_id(sdk_msg, "system") == "hook-uuid-1"

    def test_plain_system_message_is_none(self):
        sdk_msg = SystemMessage(subtype="init", data={})
        assert _derive_turn_id(sdk_msg, "system") is None

    def test_result_message_is_none(self):
        """ResultMessage carries its own .uuid, promoted to sdk_uuid — never turn_id."""
        sdk_msg = ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1", uuid="result-uuid-1",
        )
        assert _derive_turn_id(sdk_msg, "result") is None


class TestFromSdkMessageAssistant:
    def test_text_content_and_turn_id_and_sdk_uuid(self):
        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hello there")],
            model="claude-3-5-sonnet-20241022",
            message_id="anthropic-turn-7",
            uuid="frame-uuid-7",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "assistant"
        assert record.session_id == "sess-1"
        assert record.content == "hello there"
        assert record.turn_id == "anthropic-turn-7"
        assert record.sdk_uuid == "frame-uuid-7"
        assert record.subtype is None
        assert record.message_id  # minted, non-empty
        assert record.metadata["has_tool_uses"] is False

    def test_mints_a_fresh_message_id_each_call(self):
        sdk_msg = AssistantMessage(content=[TextBlock(text="hi")], model="m")
        r1 = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        r2 = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        assert r1.message_id != r2.message_id

    def test_empty_string_message_id_treated_as_absent(self):
        """message_id="" is falsy, matching AssistantMessageHandler's own truthy guard
        (message_parser.py) — _derive_turn_id must agree, not surface an empty string."""
        sdk_msg = AssistantMessage(content=[TextBlock(text="hi")], model="m", message_id="")
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        assert record.turn_id is None

    def test_display_param_is_passed_through(self):
        sdk_msg = AssistantMessage(content=[TextBlock(text="hi")], model="m")
        record = MessageRecord.from_sdk_message(
            sdk_msg, session_id="sess-1", display={"tool_states": {}},
        )
        assert record.display == {"tool_states": {}}


class TestFromSdkMessageUser:
    def test_string_content(self):
        sdk_msg = UserMessage(content="the user said this", uuid="user-frame-1")
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "user"
        assert record.sdk_uuid == "user-frame-1"
        assert record.turn_id is None
        assert record.subtype is None

    def test_list_content_with_tool_blocks(self):
        from claude_agent_sdk import ToolResultBlock, ToolUseBlock

        sdk_msg = UserMessage(
            content=[
                ToolUseBlock(id="tool-1", name="Bash", input={"command": "ls"}),
                ToolResultBlock(tool_use_id="tool-1", content="file1.txt", is_error=False),
            ],
            uuid="user-frame-2",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "user"
        assert record.sdk_uuid == "user-frame-2"
        assert record.metadata["has_tool_results"] is True
        assert record.metadata["tool_results"][0]["tool_use_id"] == "tool-1"


class TestFromSdkMessageSystem:
    def test_plain_system_message_subtype_promoted(self):
        sdk_msg = SystemMessage(subtype="init", data={"cwd": "/tmp"})
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "system"
        assert record.subtype == "init"
        assert record.sdk_uuid is None  # base SystemMessage carries no .uuid
        assert record.turn_id is None

    def test_status_subtype_without_permission_mode_survives(self):
        sdk_msg = SystemMessage(subtype="status", data={})
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        assert record.subtype == "status"


class TestFromSdkMessageHookEvent:
    def test_hook_event_promotes_uuid_and_turn_id(self):
        sdk_msg = HookEventMessage(
            subtype="hook_response",
            data={"hook_name": "PostToolUse", "exit_code": 0, "outcome": "ok"},
            hook_event_name="PostToolUse",
            session_id="sess-1",
            uuid="hook-frame-1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "system"
        assert record.subtype == "hook_response"
        assert record.sdk_uuid == "hook-frame-1"
        assert record.turn_id == "hook-frame-1"


class TestFromSdkMessageResult:
    def test_flat_fields_and_sdk_uuid(self):
        sdk_msg = ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=80, is_error=False,
            num_turns=3, session_id="sess-1", total_cost_usd=0.01, uuid="result-frame-1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "result"
        assert record.sdk_uuid == "result-frame-1"
        assert record.turn_id is None
        assert record.metadata["duration_ms"] == 100
        assert record.metadata["num_turns"] == 3

    def test_deferred_tool_use_is_captured(self):
        from claude_agent_sdk import DeferredToolUse

        sdk_msg = ResultMessage(
            subtype="deferred", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1",
            deferred_tool_use=DeferredToolUse(id="tool-1", name="Bash", input={"command": "ls"}),
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        assert record.metadata["deferred_tool_use"] == {
            "id": "tool-1", "name": "Bash", "input": {"command": "ls"},
        }


class TestFromSdkMessageTaskFrames:
    def test_task_started(self):
        sdk_msg = TaskStartedMessage(
            subtype="task_started", data={}, task_id="task-1", description="alpha: go",
            uuid="task-started-uuid", session_id="sub-sess-1", tool_use_id="tu-1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.type == "system"
        assert record.subtype == "task_started"
        assert record.sdk_uuid == "task-started-uuid"
        assert record.turn_id == "task-started-uuid"
        assert record.metadata["task_id"] == "task-1"

    def test_task_progress(self):
        sdk_msg = TaskProgressMessage(
            subtype="task_progress", data={}, task_id="task-1", description="alpha: go",
            usage=None, uuid="task-progress-uuid", session_id="sub-sess-1", tool_use_id="tu-1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.subtype == "task_progress"
        assert record.sdk_uuid == "task-progress-uuid"
        assert record.turn_id == "task-progress-uuid"

    def test_task_notification(self):
        sdk_msg = TaskNotificationMessage(
            subtype="task_notification", data={}, task_id="task-1", status="completed",
            output_file="", summary="done", uuid="task-notif-uuid", session_id="sub-sess-1",
            tool_use_id="tu-1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.subtype == "task_notification"
        assert record.sdk_uuid == "task-notif-uuid"
        assert record.turn_id == "task-notif-uuid"
        assert record.metadata["task_id"] == "task-1"

    def test_task_updated(self):
        sdk_msg = TaskUpdatedMessage(
            subtype="task_updated", data={}, task_id="task-1",
            patch={"status": "killed"}, status="killed",
            session_id="sub-sess-1", uuid="task-updated-uuid",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")

        assert record.subtype == "task_updated"
        assert record.sdk_uuid == "task-updated-uuid"
        assert record.turn_id == "task-updated-uuid"
        assert record.metadata["patch"] == {"status": "killed"}


class TestFromToolCall:
    def _make_tool_call(self, **overrides) -> ToolCall:
        defaults = dict(
            tool_use_id="tool-1",
            session_id="sess-1",
            name="Edit",
            input={"file_path": "foo.py"},
            status=ToolState.AWAITING_PERMISSION,
            created_at=1.0,
            turn_id="turn-1",
        )
        defaults.update(overrides)
        return ToolCall(**defaults)

    def test_basic_mapping(self):
        tool_call = self._make_tool_call(
            display=ToolDisplayInfo(state=ToolState.AWAITING_PERMISSION, style="warning"),
        )
        record = MessageRecord.from_tool_call(tool_call)

        assert record.type == "tool_call"
        assert record.session_id == "sess-1"
        assert record.turn_id == "turn-1"
        assert record.sdk_uuid is None
        assert record.content is None
        assert record.display == {
            "state": "awaiting_permission", "visible": True, "collapsed": False,
            "style": "warning", "linked_permission_id": None,
        }
        # #2045-relied-upon fields present under metadata
        assert record.metadata["tool_use_id"] == "tool-1"
        assert record.metadata["name"] == "Edit"
        assert record.metadata["input"] == {"file_path": "foo.py"}
        assert record.metadata["status"] == "awaiting_permission"
        assert record.metadata["created_at"] == 1.0
        assert "request_id" not in record.metadata  # not a direct ToolCall field

    def test_no_display_is_none(self):
        tool_call = self._make_tool_call()  # no display set
        record = MessageRecord.from_tool_call(tool_call)
        assert record.display is None

    def test_turn_id_none_preserved(self):
        tool_call = self._make_tool_call(turn_id=None)
        record = MessageRecord.from_tool_call(tool_call)
        assert record.turn_id is None

    def test_timestamp_precedence(self):
        tool_call = self._make_tool_call(created_at=1.0, started_at=2.0, completed_at=3.0)
        assert MessageRecord.from_tool_call(tool_call).timestamp == 3.0

        tool_call = self._make_tool_call(created_at=1.0, started_at=2.0)
        assert MessageRecord.from_tool_call(tool_call).timestamp == 2.0

        tool_call = self._make_tool_call(created_at=1.0)
        assert MessageRecord.from_tool_call(tool_call).timestamp == 1.0

    def test_mints_fresh_message_id_per_transition(self):
        tool_call = self._make_tool_call()
        r1 = MessageRecord.from_tool_call(tool_call)
        r2 = MessageRecord.from_tool_call(tool_call)
        assert r1.message_id != r2.message_id

    def test_triggering_message_permission_request_embedding(self):
        tool_call = self._make_tool_call()
        request = PermissionRequestMessage(
            request_id="req-1", tool_name="Edit", session_id="sess-1", tool_use_id="tool-1",
        )
        record = MessageRecord.from_tool_call(tool_call, triggering_message=request.to_dict())

        assert record.metadata["_triggering_message"]["request_id"] == "req-1"
        assert record.metadata["_triggering_message"]["tool_name"] == "Edit"

    def test_triggering_message_permission_response_embedding(self):
        tool_call = self._make_tool_call()
        response = PermissionResponseMessage(
            request_id="req-1", decision="allow", tool_name="Edit", session_id="sess-1",
        )
        record = MessageRecord.from_tool_call(tool_call, triggering_message=response.to_dict())

        assert record.metadata["_triggering_message"]["decision"] == "allow"

    def test_permission_suggestions_survive_mapping(self):
        tool_call = self._make_tool_call(
            requires_permission=True,
            permission=PermissionInfo(message="Allow Edit?", suggestions=[{"type": "addRules"}]),
        )
        record = MessageRecord.from_tool_call(tool_call)
        assert record.metadata["permission"]["suggestions"] == [{"type": "addRules"}]


class TestFromUserInput:
    def test_basic_fields(self):
        record = MessageRecord.from_user_input("hello", session_id="sess-1")
        assert record.type == "user"
        assert record.content == "hello"
        assert record.turn_id is None
        assert record.sdk_uuid is None
        assert record.metadata == {}

    def test_preserves_comm_and_attachments_metadata(self):
        metadata = {"comm": {"comm_id": "c1"}, "attachments": [{"name": "a.png"}]}
        record = MessageRecord.from_user_input("hi", session_id="sess-1", metadata=metadata)
        assert record.metadata["comm"] == {"comm_id": "c1"}
        assert record.metadata["attachments"] == [{"name": "a.png"}]


class TestFromError:
    def test_mints_message_id_when_none_given(self):
        record = MessageRecord.from_error("boom", session_id="sess-1", error="Storage failed: boom")
        assert record.type == "error"
        assert record.content == "boom"
        assert record.metadata["error"] == "Storage failed: boom"
        assert record.message_id

    def test_preserves_caller_supplied_message_id(self):
        record = MessageRecord.from_error(
            "boom", session_id="sess-1", error="failed", message_id="pre-minted-id",
        )
        assert record.message_id == "pre-minted-id"


class TestToDictFromDictRoundTrip:
    def test_sparse_keys_omit_none_and_empty(self):
        record = MessageRecord(
            type="user", timestamp=1.0, session_id="sess-1", message_id="m1",
        )
        data = record.to_dict()
        assert data == {
            "type": "user", "timestamp": 1.0, "session_id": "sess-1",
            "message_id": "m1", "content": None,
        }

    def test_full_record_round_trip(self):
        record = MessageRecord(
            type="system", timestamp=1.0, session_id="sess-1", message_id="m1",
            turn_id="t1", sdk_uuid="u1", subtype="init", content="hi",
            display={"state": "running"}, metadata={"name": "Edit"},
        )
        data = record.to_dict()
        assert data["turn_id"] == "t1"
        assert data["sdk_uuid"] == "u1"
        assert data["subtype"] == "init"
        assert data["display"] == {"state": "running"}
        assert data["metadata"] == {"name": "Edit"}

        restored = MessageRecord.from_dict(data)
        assert restored == record

    def test_round_trip_through_from_sdk_message(self):
        sdk_msg = AssistantMessage(
            content=[TextBlock(text="hi")], model="m", message_id="t1", uuid="u1",
        )
        record = MessageRecord.from_sdk_message(sdk_msg, session_id="sess-1")
        restored = MessageRecord.from_dict(record.to_dict())
        assert restored == record


class TestToolCallFlatShape:
    """Issue #2084 stage 3-B, §1: tool_call records must be flat (matching the live
    envelope every real emitter has always produced), not nested under `metadata`
    the way 3-A's merged `from_tool_call()` originally shipped."""

    def _make_tool_call(self, **overrides) -> ToolCall:
        defaults = dict(
            tool_use_id="tool-1", session_id="sess-1", name="Edit",
            input={"file_path": "foo.py"}, status=ToolState.AWAITING_PERMISSION,
            created_at=1.0, turn_id="turn-1",
        )
        defaults.update(overrides)
        return ToolCall(**defaults)

    def test_to_dict_is_flat_not_nested_under_metadata(self):
        tool_call = self._make_tool_call()
        data = MessageRecord.from_tool_call(tool_call).to_dict()

        assert "metadata" not in data
        assert data["tool_use_id"] == "tool-1"
        assert data["name"] == "Edit"
        assert data["input"] == {"file_path": "foo.py"}
        assert data["status"] == "awaiting_permission"
        # Core MessageRecord fields and the flattened ToolCall fields coexist at the
        # same top level — a harmless same-value overwrite (session_id/turn_id match
        # both the record's own fields and the embedded tool_call.to_dict() copy).
        assert data["type"] == "tool_call"
        assert data["session_id"] == "sess-1"
        assert data["turn_id"] == "turn-1"

    def test_from_dict_reconstructs_metadata_for_tool_call(self):
        tool_call = self._make_tool_call()
        data = MessageRecord.from_tool_call(tool_call).to_dict()
        restored = MessageRecord.from_dict(data)

        assert restored.type == "tool_call"
        assert restored.metadata["tool_use_id"] == "tool-1"
        assert restored.metadata["name"] == "Edit"
        assert restored.metadata["status"] == "awaiting_permission"
        # Core keys are not duplicated into the reconstructed metadata dict.
        for core_key in ("type", "timestamp", "session_id", "message_id", "content"):
            assert core_key not in restored.metadata

    def test_dict_level_round_trip_is_stable(self):
        """to_dict() output is stable under re-serialization (same dict both times),
        even though the reconstructed MessageRecord.metadata intentionally excludes
        the core fields already captured at the top level — no double-nesting, not a
        round-trip break (session_id/turn_id/display are still present in the dict
        itself, just no longer duplicated into the object's own .metadata attribute)."""
        tool_call = self._make_tool_call(
            display=ToolDisplayInfo(state=ToolState.AWAITING_PERMISSION, style="warning"),
        )
        record = MessageRecord.from_tool_call(tool_call)
        data = record.to_dict()
        restored = MessageRecord.from_dict(data)
        assert restored.to_dict() == data

    def test_reconstructed_metadata_is_toolcall_from_dict_compatible(self):
        """Feeding the full to_dict() output (not just the stripped-down metadata)
        into ToolCall.from_dict() reconstructs a valid ToolCall — matching what
        get_session_messages()'s reconstructor already hands downstream code today."""
        tool_call = self._make_tool_call(
            display=ToolDisplayInfo(state=ToolState.AWAITING_PERMISSION, style="warning"),
        )
        data = MessageRecord.from_tool_call(tool_call).to_dict()
        reconstructed = ToolCall.from_dict(data)
        assert reconstructed.tool_use_id == "tool-1"
        assert reconstructed.session_id == "sess-1"
        assert reconstructed.turn_id == "turn-1"
        assert reconstructed.status == ToolState.AWAITING_PERMISSION

    def test_no_accidental_double_nesting(self):
        """metadata must never itself contain a `metadata` key after a round trip."""
        tool_call = self._make_tool_call()
        record = MessageRecord.from_tool_call(tool_call)
        restored = MessageRecord.from_dict(record.to_dict())
        assert "metadata" not in restored.metadata

    def test_request_id_promoted_from_permission_request_triggering_message(self):
        tool_call = self._make_tool_call()
        request = PermissionRequestMessage(
            request_id="req-1", tool_name="Edit", session_id="sess-1", tool_use_id="tool-1",
        )
        data = MessageRecord.from_tool_call(tool_call, triggering_message=request.to_dict()).to_dict()

        # Flat, not just nested in _triggering_message — this is what lets
        # permission_service.py's live emission and the stored ToolCallUpdate agree.
        assert data["request_id"] == "req-1"
        assert data["_triggering_message"]["request_id"] == "req-1"

    def test_request_id_promoted_from_permission_response_triggering_message(self):
        tool_call = self._make_tool_call()
        response = PermissionResponseMessage(
            request_id="req-1", decision="allow", tool_name="Edit", session_id="sess-1",
        )
        data = MessageRecord.from_tool_call(tool_call, triggering_message=response.to_dict()).to_dict()
        assert data["request_id"] == "req-1"

    def test_no_request_id_when_triggering_message_lacks_one(self):
        tool_call = self._make_tool_call()
        data = MessageRecord.from_tool_call(tool_call, triggering_message={"tool_name": "Edit"}).to_dict()
        assert "request_id" not in data

    def test_no_request_id_without_triggering_message(self):
        tool_call = self._make_tool_call()
        data = MessageRecord.from_tool_call(tool_call).to_dict()
        assert "request_id" not in data


class TestSubtypeRegistryRoundTrip:
    def test_every_registered_subtype_round_trips_on_message_record(self):
        assert len(SYSTEM_SUBTYPES) == 22
        for subtype in SYSTEM_SUBTYPES:
            record = MessageRecord(
                type="system", timestamp=time.time(), session_id="sess-1",
                message_id=_mint_message_id(), subtype=subtype,
            )
            restored = MessageRecord.from_dict(record.to_dict())
            assert restored.subtype == subtype
