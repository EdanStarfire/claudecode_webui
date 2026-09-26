"""Tests for issue #2026: replay-free, non-blocking reload/live display parity.

Covers:
- AC6: _convert_stored_message_to_websocket() call count during a paginated
  read is proportional to page size, not offset — the regression test that
  would have caught #2006's quadratic prefix-replay bug before it shipped.
- Part B2: the pre-store display hook never blocks storage on failure, and
  attaches a bounded delta (not a full cumulative snapshot) to the record.
"""

import json
from unittest.mock import patch

import pytest
from claude_agent_sdk import AssistantMessage, UserMessage
from claude_agent_sdk.types import TextBlock, ToolResultBlock, ToolUseBlock

from backend.claude_sdk import ClaudeSDK
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator


@pytest.fixture
async def temp_coordinator(tmp_path):
    coordinator = SessionCoordinator(tmp_path)
    await coordinator.initialize()
    yield coordinator
    await coordinator.cleanup()


@pytest.fixture
async def sample_session_config(temp_coordinator):
    import uuid

    project = await temp_coordinator.project_manager.create_project(
        name="Test Project", working_directory="/test/project"
    )
    return {
        "session_id": str(uuid.uuid4()),
        "project_id": project.project_id,
        "config": SessionConfig(
            permission_mode="acceptEdits",
            system_prompt="Test system prompt",
            allowed_tools=["bash", "edit", "read"],
            model="claude-3-sonnet-20241022",
        ),
    }


def _write_synthetic_records(messages_file, count: int) -> None:
    """Write `count` minimal but valid _type-discriminated SystemMessage records
    directly to the JSONL file, bypassing append_message()'s per-call file
    open/close for setup speed — this test cares about read-path behavior, not
    write-path performance.
    """
    lines = []
    for i in range(count):
        lines.append(json.dumps({
            "_type": "SystemMessage",
            "timestamp": 1700000000.0 + i,
            "session_id": "synthetic-session",
            "data": {"subtype": "status", "data": {}},
            "message_id": f"synthetic-{i}",
        }))
    messages_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestIssue2026AC6ConversionProportionality:
    """AC6: per-page conversion work must be O(page size), never O(offset).

    #2006's bug replayed the entire discarded [0, offset) prefix through
    DisplayProjection on every paginated request — so _convert_stored_message_
    to_websocket()'s call count for a single page grew with the page's offset,
    not just its size. This test builds a 20,000+ record synthetic session and
    asserts the call count for a late page is no larger than for the first
    page, proportional to the page size requested — not the offset.
    """

    RECORD_COUNT = 20_000
    PAGE_SIZE = 50

    @pytest.mark.asyncio
    async def test_call_count_is_page_size_not_offset(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        storage = coordinator._storage_managers[session_id]
        _write_synthetic_records(storage.messages_file, self.RECORD_COUNT)

        call_count = {"n": 0}
        original = coordinator._convert_stored_message_to_websocket

        def counting_wrapper(stored_msg):
            call_count["n"] += 1
            return original(stored_msg)

        coordinator._convert_stored_message_to_websocket = counting_wrapper

        # First page: offset=0.
        call_count["n"] = 0
        result = await coordinator.get_session_messages(
            session_id, limit=self.PAGE_SIZE, offset=0
        )
        assert len(result["messages"]) == self.PAGE_SIZE
        first_page_calls = call_count["n"]

        # Last page: offset near the end of a 20,000+ record session — under
        # #2006's bug this would have replayed ~19,950 discarded prior records
        # before ever producing this page's output.
        late_offset = self.RECORD_COUNT - self.PAGE_SIZE
        call_count["n"] = 0
        result = await coordinator.get_session_messages(
            session_id, limit=self.PAGE_SIZE, offset=late_offset
        )
        assert len(result["messages"]) == self.PAGE_SIZE
        last_page_calls = call_count["n"]

        # Both pages must do the same, bounded amount of conversion work —
        # proportional to page size, independent of offset. A generous upper
        # bound (4x page size) tolerates the tool_call synthesis this method's
        # caller layers on top per real message, without masking a genuine
        # O(offset) regression (which would be ~400x larger, not ~4x).
        assert first_page_calls <= self.PAGE_SIZE * 4
        assert last_page_calls <= self.PAGE_SIZE * 4
        assert last_page_calls <= first_page_calls * 2

    @pytest.mark.asyncio
    async def test_archive_path_call_count_is_page_size_not_offset(
        self, temp_coordinator, sample_session_config
    ):
        """AC5: the archive reload path shares the same conversion method and
        must have the same non-quadratic shape."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        storage = coordinator._storage_managers[session_id]
        _write_synthetic_records(storage.messages_file, self.RECORD_COUNT)

        # Reuse get_session_messages()'s underlying conversion for a call-count
        # baseline via direct instrumentation on the shared method, then drive
        # get_archive_messages() through a minimal fake archive_manager, since
        # spinning up a full legion archive for 20,000 records is unnecessary
        # for this test's purpose (verifying get_archive_messages() delegates
        # to the same non-quadratic conversion, not exercising ArchiveManager
        # itself, which has its own tests).
        raw_messages = await storage.read_messages()

        class _FakeArchiveManager:
            async def get_archive_messages(self, session_id, archive_id, offset=0, limit=None):
                end = offset + limit if limit else None
                page = raw_messages[offset:end]
                return {
                    "messages": page,
                    "total_count": len(raw_messages),
                    "offset": offset,
                    "has_more": end is not None and end < len(raw_messages),
                }

        class _FakeLegionSystem:
            archive_manager = _FakeArchiveManager()

        coordinator.legion_system = _FakeLegionSystem()

        call_count = {"n": 0}
        original = coordinator._convert_stored_message_to_websocket

        def counting_wrapper(stored_msg):
            call_count["n"] += 1
            return original(stored_msg)

        coordinator._convert_stored_message_to_websocket = counting_wrapper

        call_count["n"] = 0
        await coordinator.get_archive_messages(
            session_id, "archive-1", offset=0, limit=self.PAGE_SIZE
        )
        first_page_calls = call_count["n"]

        late_offset = self.RECORD_COUNT - self.PAGE_SIZE
        call_count["n"] = 0
        await coordinator.get_archive_messages(
            session_id, "archive-1", offset=late_offset, limit=self.PAGE_SIZE
        )
        last_page_calls = call_count["n"]

        assert first_page_calls <= self.PAGE_SIZE
        assert last_page_calls <= self.PAGE_SIZE


class TestIssue2026PreStoreHookNonFatal:
    """Part B2: the pre-store display hook must never block persistence. Same
    non-fatal try/except semantics as today's (now-removed) post-store
    computation — if it throws, the message is still stored with no `display`."""

    @pytest.mark.asyncio
    async def test_compute_display_metadata_returns_none_on_failure(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        with patch.object(
            coordinator.message_processor, "process_message", side_effect=RuntimeError("boom")
        ):
            result = coordinator._compute_display_metadata_for_storage(
                session_id, {"type": "assistant", "content": "hi", "session_id": session_id}
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_store_processed_message_still_stores_when_display_hook_throws(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        message_data = {
            "type": "system",
            "subtype": "client_launched",
            "content": "Claude Code Launched",
            "session_id": session_id,
            "timestamp": 1700000000.0,
        }

        with patch.object(
            coordinator, "_compute_display_metadata_for_storage", side_effect=RuntimeError("boom")
        ):
            await coordinator._store_processed_message(session_id, message_data)

        storage = coordinator._storage_managers[session_id]
        stored = await storage.read_messages()
        assert len(stored) == 1
        assert stored[0]["content"] == "Claude Code Launched"
        assert "display" not in stored[0]

    @pytest.mark.asyncio
    async def test_claude_sdk_still_stores_when_display_hook_throws(self, tmp_path):
        """The live SDK streaming path (claude_sdk.py's _process_sdk_message) must
        keep storing/delivering the message even if display_hook raises."""
        from backend.data_storage import DataStorageManager

        storage_manager = DataStorageManager(tmp_path / "sdk_session")
        await storage_manager.initialize()

        received = []

        async def message_callback(msg):
            received.append(msg)

        def failing_display_hook(_msg):
            raise RuntimeError("boom")

        sdk = ClaudeSDK(
            session_id="sdk-session",
            working_directory=str(tmp_path),
            storage_manager=storage_manager,
            message_callback=message_callback,
            display_hook=failing_display_hook,
            error_callback=None,
            permission_callback=lambda *a, **kw: True,
        )

        assistant_msg = AssistantMessage(
            content=[TextBlock(text="hello")],
            model="claude-3-5-sonnet-20241022",
        )
        await sdk._process_sdk_message(assistant_msg)

        stored = await storage_manager.read_messages()
        assert len(stored) == 1
        assert stored[0]["_type"] == "AssistantMessage"
        assert "display" not in stored[0]
        assert len(received) == 1


class TestIssue2026PreStoreHookBoundedDelta:
    """Part B1+B2 integration: the persisted `display` for a message is a
    bounded delta (only what that message touched), not the full cumulative
    tool_states snapshot #2006's bug (and the pre-#2026 live broadcast) sent."""

    @pytest.mark.asyncio
    async def test_persisted_display_only_contains_the_touched_tool(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        first_tool_id = "toolu_2026_first"
        second_tool_id = "toolu_2026_second"

        # First assistant turn creates a tool call — its delta should mention
        # only that one tool.
        first_msg = {
            "type": "assistant",
            "sdk_message": AssistantMessage(
                content=[ToolUseBlock(id=first_tool_id, name="Read", input={"file_path": "/a.py"})],
                model="claude-3-5-sonnet-20241022",
            ),
            "session_id": session_id,
            "timestamp": 1.0,
        }
        first_display = coordinator._compute_display_metadata_for_storage(session_id, first_msg)
        assert first_display is not None
        assert set(first_display["tool_states"].keys()) == {first_tool_id}

        # Second assistant turn creates a second, unrelated tool call — its
        # delta must mention ONLY the new tool, not the first one again (the
        # #2006-style full-snapshot bug would repeat every tool ever seen).
        second_msg = {
            "type": "assistant",
            "sdk_message": AssistantMessage(
                content=[ToolUseBlock(id=second_tool_id, name="Read", input={"file_path": "/b.py"})],
                model="claude-3-5-sonnet-20241022",
            ),
            "session_id": session_id,
            "timestamp": 2.0,
        }
        second_display = coordinator._compute_display_metadata_for_storage(session_id, second_msg)
        assert second_display is not None
        assert set(second_display["tool_states"].keys()) == {second_tool_id}

        # A tool-result message only mentions the tool it just completed.
        result_msg = {
            "type": "user",
            "sdk_message": UserMessage(
                content=[ToolResultBlock(tool_use_id=first_tool_id, content="ok", is_error=False)],
            ),
            "session_id": session_id,
            "timestamp": 3.0,
        }
        result_display = coordinator._compute_display_metadata_for_storage(session_id, result_msg)
        assert result_display is not None
        assert set(result_display["tool_states"].keys()) == {first_tool_id}
        assert result_display["tool_states"][first_tool_id]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_unrelated_message_gets_empty_display_delta(
        self, temp_coordinator, sample_session_config
    ):
        """A message type DisplayProjection has no special handling for (e.g. a
        plain system message) gets an empty delta, not the full accumulated
        snapshot of every tool ever seen in the session."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        tool_msg = {
            "type": "assistant",
            "sdk_message": AssistantMessage(
                content=[ToolUseBlock(id="toolu_unrelated", name="Read", input={"file_path": "/a.py"})],
                model="claude-3-5-sonnet-20241022",
            ),
            "session_id": session_id,
            "timestamp": 1.0,
        }
        coordinator._compute_display_metadata_for_storage(session_id, tool_msg)

        system_msg = {
            "type": "system",
            "subtype": "client_launched",
            "content": "Claude Code Launched",
            "session_id": session_id,
            "timestamp": 2.0,
        }
        display = coordinator._compute_display_metadata_for_storage(session_id, system_msg)
        assert display is None or display["tool_states"] == {}


class TestIssue2026OrphanedToolsDeltaPropagation:
    """_mark_tools_orphaned() mutates DisplayProjection state directly, bypassing
    process_message() — it must still surface on whatever message the session
    emits next, exactly once, matching what interrupting a session used to
    deliver via the old full-snapshot design (builder-review finding, confirmed
    independently by three review passes)."""

    @pytest.mark.asyncio
    async def test_orphaned_tool_surfaces_on_the_next_message_after_interrupt(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        tool_id = "toolu_2026_orphan"
        tool_msg = {
            "type": "assistant",
            "sdk_message": AssistantMessage(
                content=[ToolUseBlock(id=tool_id, name="Bash", input={"command": "sleep 100"})],
                model="claude-3-5-sonnet-20241022",
            ),
            "session_id": session_id,
            "timestamp": 1.0,
        }
        coordinator._compute_display_metadata_for_storage(session_id, tool_msg)

        # Mirrors interrupt_session()'s call ordering: mark orphaned, then send
        # the interrupt system message.
        orphaned = coordinator._mark_tools_orphaned(session_id)
        assert orphaned == [tool_id]

        interrupt_msg = {
            "type": "system",
            "subtype": "interrupt",
            "content": "User Interrupted Processing",
            "session_id": session_id,
            "timestamp": 2.0,
        }
        display = coordinator._compute_display_metadata_for_storage(session_id, interrupt_msg)

        assert display is not None
        assert tool_id in display["tool_states"]
        assert display["tool_states"][tool_id]["state"] == "orphaned"
        assert tool_id in display["orphaned_tools"]

    @pytest.mark.asyncio
    async def test_pending_orphan_delta_is_consumed_exactly_once(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        tool_id = "toolu_2026_orphan_once"
        tool_msg = {
            "type": "assistant",
            "sdk_message": AssistantMessage(
                content=[ToolUseBlock(id=tool_id, name="Bash", input={"command": "sleep 100"})],
                model="claude-3-5-sonnet-20241022",
            ),
            "session_id": session_id,
            "timestamp": 1.0,
        }
        coordinator._compute_display_metadata_for_storage(session_id, tool_msg)
        coordinator._mark_tools_orphaned(session_id)

        interrupt_msg = {
            "type": "system",
            "subtype": "interrupt",
            "content": "User Interrupted Processing",
            "session_id": session_id,
            "timestamp": 2.0,
        }
        first = coordinator._compute_display_metadata_for_storage(session_id, interrupt_msg)
        assert tool_id in first["orphaned_tools"]

        # A second, unrelated message must NOT repeat the orphaned delta — it
        # was already delivered once.
        second_msg = {
            "type": "system",
            "subtype": "client_launched",
            "content": "Claude Code Launched",
            "session_id": session_id,
            "timestamp": 3.0,
        }
        second = coordinator._compute_display_metadata_for_storage(session_id, second_msg)
        assert second is None or second.get("orphaned_tools") == []
