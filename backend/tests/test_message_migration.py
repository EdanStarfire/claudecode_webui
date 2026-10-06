"""Tests for message_migration.py (issue #2084 stage 3-C, §3/§9).

Fidelity strategy: both `backend/tests/fixtures/tool_use` and `.../permission_flow`
are, as committed, pre-#494-shaped (no stored ToolCallUpdate record for their one
tool call) — exactly the case §0 identifies as the one a naive migration would lose
silently. Each is migrated and the resulting canonical read is compared, field-by-
field (modulo the freshly-minted message_id on materialized records), against what
get_session_messages() produces for the same session before migration.
"""

import asyncio
import json
from pathlib import Path

import pytest

from backend.data_storage import DataStorageManager
from backend.message_migration import (
    MigrationResult,
    _materialized_record_id,
    migrate_session_messages,
)
from backend.message_migration_service import MessageMigrationService
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionState

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURES_DIR = FIXTURES_DIR / "raw"


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


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name / "messages.jsonl").read_text(encoding="utf-8")


async def _make_legacy_session(coordinator, sample_session_config, fixture_name: str):
    """Create a session, force it legacy (schema_version 0), load a fixture."""
    session_id = await coordinator.create_session(**sample_session_config)
    coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
    storage = coordinator._storage_managers[session_id]
    storage.messages_file.write_text(_load_fixture(fixture_name), encoding="utf-8")
    return session_id, storage


def _strip_volatile(msg: dict) -> dict:
    """Drop fields that are either migration-minted (persistence needs them;
    the ephemeral live synthesis never did) or inherently conversion-time-variant
    in the existing (unmodified) MessageProcessor fallback path.

    - message_id on a tool_call record only: minted deterministically for
      materialized tool_call records (§2) — the live path never persists
      these, so never had one to compare. Every OTHER message type's
      message_id is a real, already-stable identifier propagated verbatim
      from the stored record by both _convert_stored_message_to_websocket()
      (session_coordinator.py:4217-4218) and the MessageProcessor fallback
      branch (:4512-4513) — stripping it unconditionally would make this
      fidelity test blind to migration corrupting or reassigning a real
      record's identity (found in review).
    - top-level "timestamp" on a tool_call record: migration-added (§2) so a
      materialized record carries the same "every stored record has a
      timestamp" convention append_message() gives every live-written record;
      ToolCall.to_dict() itself only ever sets "created_at".
    - metadata.processed_at: stamped fresh with wall-clock time on *every*
      process_message() call, live reload included — two reads of the same
      unmigrated legacy session already disagree on it today, independent of
      migration.
    """
    out = dict(msg)
    if out.get("type") == "tool_call":
        out.pop("message_id", None)
        out.pop("timestamp", None)
    metadata = out.get("metadata")
    if isinstance(metadata, dict) and "processed_at" in metadata:
        out["metadata"] = {k: v for k, v in metadata.items() if k != "processed_at"}
    return out


class TestStripVolatile:
    def test_keeps_message_id_on_non_tool_call_records(self):
        """Regression lock (found in review): message_id must only be stripped
        for tool_call records — every other type's id is real and stable, and
        the fidelity comparison must not be blind to migration corrupting it."""
        out = _strip_volatile({"type": "assistant", "content": "hi", "message_id": "real-id"})
        assert out["message_id"] == "real-id"

    def test_strips_message_id_on_tool_call_only(self):
        out = _strip_volatile(
            {"type": "tool_call", "tool_use_id": "t1", "status": "pending", "message_id": "x"}
        )
        assert "message_id" not in out


class TestMigrationFidelityAgainstFixtures:
    # Number of distinct lifecycle transitions issue #491 synthesizes for the one
    # tool call in each fixture that has no explicit stored ToolCallUpdate — the
    # pre-#494 case (§0). tool_use: pending -> completed (no permission step,
    # acceptEdits). permission_flow: pending -> awaiting_permission -> running ->
    # completed.
    EXPECTED_MATERIALIZED = {"tool_use": 2, "permission_flow": 4}

    @pytest.mark.parametrize("fixture_name", ["tool_use", "permission_flow"])
    @pytest.mark.asyncio
    async def test_migrated_canonical_read_matches_legacy_read(
        self, temp_coordinator, sample_session_config, fixture_name
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, fixture_name
        )

        before = await coordinator.get_session_messages(session_id)

        session_info = await coordinator.session_manager.get_session_info(session_id)
        result = await migrate_session_messages(
            storage.session_dir,
            session_id,
            session_info.state,
            coordinator._convert_legacy_record_to_websocket,
            storage._write_lock,
        )
        assert result.materialized_tool_calls == self.EXPECTED_MATERIALIZED[fixture_name]

        await coordinator.session_manager.complete_message_migration(
            session_id, result.materialized_tool_calls
        )

        after = await coordinator.get_session_messages(session_id)

        before_stripped = [_strip_volatile(m) for m in before["messages"]]
        after_stripped = [_strip_volatile(m) for m in after["messages"]]
        assert after_stripped == before_stripped

    @pytest.mark.asyncio
    async def test_materialized_record_ids_are_deterministic_and_present(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "tool_use"
        )
        session_info = await coordinator.session_manager.get_session_info(session_id)
        await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )
        migrated = [
            json.loads(line) for line in storage.messages_file.read_text().splitlines() if line.strip()
        ]
        tool_calls = [m for m in migrated if m.get("type") == "tool_call"]
        assert len(tool_calls) == self.EXPECTED_MATERIALIZED["tool_use"]
        for tc in tool_calls:
            assert tc["message_id"] == _materialized_record_id(
                session_id, tc["tool_use_id"], tc["status"]
            )


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_two_dry_run_passes_produce_byte_identical_output(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "permission_flow"
        )
        session_info = await coordinator.session_manager.get_session_info(session_id)

        await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
            apply=False, output_filename="preview-1.jsonl",
        )
        await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
            apply=False, output_filename="preview-2.jsonl",
        )

        def _strip_processed_at(msg: dict) -> dict:
            metadata = msg.get("metadata")
            if isinstance(metadata, dict) and "processed_at" in metadata:
                msg = {**msg, "metadata": {k: v for k, v in metadata.items() if k != "processed_at"}}
            return msg

        def _load_normalized(path: Path) -> list[dict]:
            lines = path.read_text(encoding="utf-8").splitlines()
            return [_strip_processed_at(json.loads(line)) for line in lines if line.strip()]

        preview1 = _load_normalized(storage.session_dir / "preview-1.jsonl")
        preview2 = _load_normalized(storage.session_dir / "preview-2.jsonl")
        # message_id is intentionally NOT stripped here — deterministic identity
        # (§2) is exactly the property this test exists to prove.
        assert preview1 == preview2
        # Source must never have been touched by a dry run.
        assert storage.messages_file.read_text(encoding="utf-8") == _load_fixture("permission_flow")

    @pytest.mark.asyncio
    async def test_interrupted_then_restarted_run_matches_uninterrupted_run(
        self, temp_coordinator, sample_session_config
    ):
        """§3's resumability model: a restart replays the untouched source from
        scratch and (thanks to deterministic ids) produces byte-identical output
        to what an uninterrupted run would have produced."""
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "tool_use"
        )
        session_info = await coordinator.session_manager.get_session_info(session_id)

        # Simulate an interrupted attempt: a partial/garbage .migrating tmp file
        # left behind from a prior crash, which the next run must simply overwrite.
        stale_tmp = storage.session_dir / "messages.jsonl.migrating"
        stale_tmp.write_text("garbage-from-interrupted-run\n", encoding="utf-8")

        result = await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )
        migrated_content = storage.messages_file.read_text(encoding="utf-8")
        assert "garbage-from-interrupted-run" not in migrated_content
        assert result.materialized_tool_calls == TestMigrationFidelityAgainstFixtures.EXPECTED_MATERIALIZED["tool_use"]


class TestOutOfOrderExplicitRecordDuplicate:
    """Issue #2093: regression test using the committed 2026-09-23-primary
    fixture's real interrupted-tool data (AC4). Two genuine stored
    ToolCallUpdate records for the same tool_use_id (one terminal
    "interrupted", one non-terminal "pending") landing out of chronological
    order in the file must not resurrect the tool into tracking — otherwise
    finalize()'s end-of-stream sweep synthesizes a spurious duplicate
    "interrupted" record alongside the genuine one.
    """

    TOOL_USE_ID = "toolu_01BFmVrRcyGEqMFWex7e4Ccj"

    def _load_real_records(self) -> tuple[dict, dict, dict]:
        raw_path = RAW_FIXTURES_DIR / "2026-09-23-primary" / "messages.jsonl"
        records = [
            json.loads(line)
            for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assistant = next(
            r for r in records
            if r.get("_type") == "AssistantMessage"
            and any(
                isinstance(c, dict) and c.get("id") == self.TOOL_USE_ID
                for c in r.get("data", {}).get("content", [])
            )
        )
        pending = next(
            r for r in records
            if r.get("_type") == "ToolCallUpdate"
            and r["data"]["tool_use_id"] == self.TOOL_USE_ID
            and r["data"]["status"] == "pending"
        )
        interrupted = next(
            r for r in records
            if r.get("_type") == "ToolCallUpdate"
            and r["data"]["tool_use_id"] == self.TOOL_USE_ID
            and r["data"]["status"] == "interrupted"
        )
        return assistant, pending, interrupted

    @pytest.mark.asyncio
    async def test_out_of_order_terminal_record_does_not_duplicate_on_finalize(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        assistant, pending, interrupted = self._load_real_records()
        # Out-of-order hazard (§0): the terminal record lands BEFORE the
        # non-terminal record for the same tool_use_id — the real-world
        # race _schedule_tool_call_update_storage's independent
        # asyncio.ensure_future() calls make possible.
        lines = [assistant, interrupted, pending]
        storage.messages_file.write_text(
            "\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8"
        )

        result = await migrate_session_messages(
            storage.session_dir, session_id, SessionState.TERMINATED,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )

        # No synthesis should fire: both records are explicit (written through
        # unchanged by convert_fn), and the stale out-of-order non-terminal
        # record must not resurrect tracking, so finalize()'s end-of-stream
        # sweep stays a no-op — no THIRD, synthesized duplicate "interrupted"
        # record.
        assert result.materialized_tool_calls == 0

        migrated = [
            json.loads(line)
            for line in storage.messages_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tool_calls = [m for m in migrated if m.get("type") == "tool_call"]
        assert len(tool_calls) == 2
        statuses = sorted(tc["status"] for tc in tool_calls)
        assert statuses == ["interrupted", "pending"]


class TestTrigger5AndFinalizeTerminalGuard:
    """Issue #2093 (reopened) — a sibling gap to the one
    TestOutOfOrderExplicitRecordDuplicate above fixed: trigger 5 (client_launched/
    interrupt) and finalize() never checked terminal_tool_use_ids at all, unlike
    triggers 1-4. This tool's own natural (unmodified) record order in the
    2026-09-23-primary fixture IS the exact repro shape: a pending ToolCallUpdate,
    then a genuine "interrupt" system message, then the genuine "interrupted"
    ToolCallUpdate terminal record — in that order. Before this fix, trigger 5
    would fire on the interrupt system message while the tool was still tracked
    as open (the genuine terminal record not having been fed yet), synthesizing
    a bogus duplicate "interrupted" record alongside the real one.
    """

    TOOL_USE_ID = "toolu_01BFmVrRcyGEqMFWex7e4Ccj"

    def _load_real_records(self) -> tuple[dict, dict, dict, dict]:
        raw_path = RAW_FIXTURES_DIR / "2026-09-23-primary" / "messages.jsonl"
        records = [
            json.loads(line)
            for line in raw_path.read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        assistant = next(
            r for r in records
            if r.get("_type") == "AssistantMessage"
            and any(
                isinstance(c, dict) and c.get("id") == self.TOOL_USE_ID
                for c in r.get("data", {}).get("content", [])
            )
        )
        pending = next(
            r for r in records
            if r.get("_type") == "ToolCallUpdate"
            and r["data"]["tool_use_id"] == self.TOOL_USE_ID
            and r["data"]["status"] == "pending"
        )
        interrupt_message = next(
            r for r in records
            if r.get("type") == "system" and r.get("metadata", {}).get("subtype") == "interrupt"
        )
        interrupted = next(
            r for r in records
            if r.get("_type") == "ToolCallUpdate"
            and r["data"]["tool_use_id"] == self.TOOL_USE_ID
            and r["data"]["status"] == "interrupted"
        )
        return assistant, pending, interrupt_message, interrupted

    @pytest.mark.asyncio
    async def test_trigger5_interrupt_message_does_not_duplicate(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        assistant, pending, interrupt_message, interrupted = self._load_real_records()
        # Natural real-world order (§0): the non-terminal record is fed, then
        # trigger 5's interrupt system message fires, and only THEN does the
        # genuine terminal record appear later in the stream.
        lines = [assistant, pending, interrupt_message, interrupted]
        storage.messages_file.write_text(
            "\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8"
        )

        result = await migrate_session_messages(
            storage.session_dir, session_id, SessionState.TERMINATED,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )

        assert result.materialized_tool_calls == 0

        migrated = [
            json.loads(line)
            for line in storage.messages_file.read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        tool_calls = [m for m in migrated if m.get("type") == "tool_call"]
        assert len(tool_calls) == 2
        statuses = sorted(tc["status"] for tc in tool_calls)
        assert statuses == ["interrupted", "pending"]

    @pytest.mark.asyncio
    async def test_finalize_sweep_does_not_duplicate_without_interrupt_message(
        self, temp_coordinator, sample_session_config
    ):
        """Symmetric to the trigger-5 test above: no client_launched/interrupt
        message anywhere in the stream — finalize()'s end-of-stream sweep is the
        only thing that could fire for a still-open tool."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        assistant, pending, _interrupt_message, interrupted = self._load_real_records()
        lines = [assistant, pending, interrupted]
        storage.messages_file.write_text(
            "\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8"
        )

        result = await migrate_session_messages(
            storage.session_dir, session_id, SessionState.TERMINATED,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )

        assert result.materialized_tool_calls == 0

        migrated = [
            json.loads(line)
            for line in storage.messages_file.read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        tool_calls = [m for m in migrated if m.get("type") == "tool_call"]
        assert len(tool_calls) == 2
        statuses = sorted(tc["status"] for tc in tool_calls)
        assert statuses == ["interrupted", "pending"]


class TestMigrationQuarantine:
    @pytest.mark.asyncio
    async def test_corrupt_line_raises_and_leaves_source_untouched(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        original_content = _load_fixture("tool_use").rstrip("\n") + "\nNOT VALID JSON\n"
        storage.messages_file.write_text(original_content, encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            await migrate_session_messages(
                storage.session_dir, session_id, SessionState.CREATED,
                coordinator._convert_legacy_record_to_websocket, storage._write_lock,
            )

        # Original source must be byte-unchanged — the atomic swap never ran.
        assert storage.messages_file.read_text(encoding="utf-8") == original_content
        # Tmp file may exist (partial pass-2 output) but the real file is untouched,
        # so legacy reads still work.
        session_result = await coordinator.get_session_messages(session_id)
        assert session_result["messages"][0]["type"] == "system"

    @pytest.mark.asyncio
    async def test_quarantine_status_set_via_session_manager(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        assert await coordinator.session_manager.try_claim_message_migration(session_id) is True
        await coordinator.session_manager.quarantine_message_migration(session_id, "boom")

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "quarantined"
        assert info.message_migration_status["error"] == "boom"
        assert info.message_schema_version == 0  # quarantine never flips schema_version

    @pytest.mark.asyncio
    async def test_claim_is_exclusive_and_quarantine_is_reclaimable(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0

        assert await coordinator.session_manager.try_claim_message_migration(session_id) is True
        # A second concurrent claim attempt (e.g. on-demand racing the background tick)
        # must lose — the first claimant already marked it in_progress.
        assert await coordinator.session_manager.try_claim_message_migration(session_id) is False

        await coordinator.session_manager.quarantine_message_migration(session_id, "boom")
        # Per §7, a quarantined session is retried on a later open attempt.
        assert await coordinator.session_manager.try_claim_message_migration(session_id) is True

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "in_progress"

    @pytest.mark.asyncio
    async def test_claim_rejected_once_completed(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0

        assert await coordinator.session_manager.try_claim_message_migration(session_id) is True
        await coordinator.session_manager.complete_message_migration(session_id, 3)
        assert await coordinator.session_manager.try_claim_message_migration(session_id) is False


class TestWriteLockConcurrency:
    @pytest.mark.asyncio
    async def test_append_message_blocks_while_lock_held(self, tmp_path):
        storage = DataStorageManager(tmp_path / "sess-a")
        await storage.initialize()

        await storage._write_lock.acquire()
        try:
            append_task = asyncio.create_task(storage.append_message({"type": "user", "content": "hi"}))
            await asyncio.sleep(0.05)
            assert not append_task.done()
        finally:
            storage._write_lock.release()

        await append_task
        assert storage.get_message_count is not None  # sanity: storage still usable
        count = await storage.get_message_count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_other_session_append_unaffected_by_lock(self, tmp_path):
        storage_a = DataStorageManager(tmp_path / "sess-a")
        storage_b = DataStorageManager(tmp_path / "sess-b")
        await storage_a.initialize()
        await storage_b.initialize()

        await storage_a._write_lock.acquire()
        try:
            await asyncio.wait_for(
                storage_b.append_message({"type": "user", "content": "hi"}), timeout=1.0
            )
        finally:
            storage_a._write_lock.release()

        assert await storage_b.get_message_count() == 1


class TestMigrationResultShape:
    @pytest.mark.asyncio
    async def test_result_fields(self, temp_coordinator, sample_session_config):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "tool_use"
        )
        session_info = await coordinator.session_manager.get_session_info(session_id)
        result = await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )
        assert isinstance(result, MigrationResult)
        assert result.session_id == session_id
        assert result.line_count == 7  # fixture record count
        assert result.applied is True
        assert result.output_path == storage.messages_file


class TestMigrationAtScale:
    """T2-equivalent (§9): bounded memory/time at scale, no readiness delay."""

    RECORD_PAIRS = 10_000  # -> 2 raw lines each -> 20,000 raw records

    @staticmethod
    def _write_large_legacy_session(messages_file: Path, pairs: int) -> None:
        lines = []
        for i in range(pairs):
            tool_use_id = f"toolu_scale_{i}"
            ts = 1_000_000.0 + i * 2
            lines.append(json.dumps({
                "_type": "AssistantMessage",
                "timestamp": ts,
                "session_id": "scale-session",
                "data": {
                    "content": [
                        {"type": "text", "text": f"Doing step {i}"},
                        {"type": "tool_use", "id": tool_use_id, "name": "Read",
                         "input": {"file_path": f"/tmp/f{i}.txt"}},
                    ],
                    "model": "claude-sonnet-4-5-20250929",
                },
            }))
            lines.append(json.dumps({
                "_type": "UserMessage",
                "timestamp": ts + 1,
                "session_id": "scale-session",
                "data": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_use_id,
                         "content": f"result {i}", "is_error": False},
                    ],
                },
            }))
        messages_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @pytest.mark.asyncio
    async def test_bounded_memory_and_correct_materialization_at_scale(
        self, temp_coordinator, sample_session_config
    ):
        import resource

        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]
        self._write_large_legacy_session(storage.messages_file, self.RECORD_PAIRS)

        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KB on Linux

        session_info = await coordinator.session_manager.get_session_info(session_id)
        result = await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )

        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_growth_mb = (rss_after - rss_before) / 1024

        assert result.line_count == self.RECORD_PAIRS * 2
        # Every tool call goes pending -> completed with no permission step
        # (acceptEdits), so exactly 2 materialized records per tool call.
        assert result.materialized_tool_calls == self.RECORD_PAIRS * 2

        # Coarse ceiling, not exact: two bounded-memory streaming passes over
        # 20,000 records plus a handful of concurrently-open tool calls should
        # cost nowhere near loading the whole file (and the active_history_tools
        # working set) into memory at once. Generous enough to avoid flaking on
        # allocator/GC noise while still catching a real O(file size) regression.
        assert rss_growth_mb < 200, f"RSS grew by {rss_growth_mb:.1f}MB during migration"

    @pytest.mark.asyncio
    async def test_resumability_at_scale_byte_identical_after_restart(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]
        self._write_large_legacy_session(storage.messages_file, 500)
        session_info = await coordinator.session_manager.get_session_info(session_id)

        await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
            apply=False, output_filename="scale-preview-1.jsonl",
        )
        await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
            apply=False, output_filename="scale-preview-2.jsonl",
        )
        assert (storage.session_dir / "scale-preview-1.jsonl").read_bytes() == (
            storage.session_dir / "scale-preview-2.jsonl"
        ).read_bytes()

    @pytest.mark.asyncio
    async def test_service_start_returns_immediately_regardless_of_backlog(
        self, temp_coordinator, sample_session_config
    ):
        """AC7: scheduling the background task must never block on migration work."""
        import time

        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(coordinator, sample_session_config, "tool_use")
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        start_time = time.monotonic()
        await service.start()
        elapsed = time.monotonic() - start_time
        await service.stop()

        assert elapsed < 1.0


class TestMixedShapeLegacySessionWithLiveCanonicalToolCallRecords:
    """Issue #2084 (stage 3-C): the live write path has unconditionally persisted
    tool_call lifecycle transitions in flat canonical shape since stage 3-B's
    cutover (#2086), regardless of the session's own message_schema_version. A
    still-legacy session that stayed active across that deploy can therefore
    contain a mix of legacy-shaped and flat-canonical tool_call records. Found via
    this stage's own fidelity testing — a real latent bug exposed (not introduced)
    by this stage's work, fixed in _convert_legacy_record_to_websocket() and the
    pre-scan loop's stored_tool_update_ids population.
    """

    @pytest.mark.asyncio
    async def test_flat_tool_call_record_passes_through_without_duplication(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        legacy_assistant = {
            "_type": "AssistantMessage", "timestamp": 1.0, "session_id": session_id,
            "data": {
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
                ],
                "model": "m",
            },
        }
        flat_tool_call = {
            "tool_use_id": "tu1", "session_id": session_id, "name": "Read", "input": {},
            "status": "completed", "created_at": 1.0, "requires_permission": False,
            "result": "ok", "type": "tool_call", "timestamp": 1.0, "message_id": "m-flat-1",
        }
        storage.messages_file.write_text(
            json.dumps(legacy_assistant) + "\n" + json.dumps(flat_tool_call) + "\n",
            encoding="utf-8",
        )

        result = await coordinator.get_session_messages(session_id)
        tool_calls = [m for m in result["messages"] if m.get("tool_use_id") == "tu1"]
        # Exactly one tool_call entry for the explicit record's own transition —
        # no duplicate "pending" from trigger 1 (terminal status also means no
        # end-of-stream interrupt sweep), and it must never have been
        # mis-dispatched into a "tool_use" legacy-shaped message.
        assert len(tool_calls) == 1
        assert tool_calls[0]["type"] == "tool_call"
        assert tool_calls[0]["status"] == "completed"
        assert not any(m.get("type") == "tool_use" for m in result["messages"])

    @pytest.mark.asyncio
    async def test_migration_handles_flat_tool_call_record_correctly(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]

        legacy_assistant = {
            "_type": "AssistantMessage", "timestamp": 1.0, "session_id": session_id,
            "data": {
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {}},
                ],
                "model": "m",
            },
        }
        flat_tool_call = {
            "tool_use_id": "tu1", "session_id": session_id, "name": "Read", "input": {},
            "status": "completed", "created_at": 1.0, "requires_permission": False,
            "result": "ok", "type": "tool_call", "timestamp": 1.0, "message_id": "m-flat-1",
        }
        storage.messages_file.write_text(
            json.dumps(legacy_assistant) + "\n" + json.dumps(flat_tool_call) + "\n",
            encoding="utf-8",
        )

        session_info = await coordinator.session_manager.get_session_info(session_id)
        result = await migrate_session_messages(
            storage.session_dir, session_id, session_info.state,
            coordinator._convert_legacy_record_to_websocket, storage._write_lock,
        )
        # No synthesis needed at all — the explicit flat record already covers
        # the only transition this tool call ever had.
        assert result.materialized_tool_calls == 0

        migrated = [
            json.loads(line) for line in storage.messages_file.read_text().splitlines() if line.strip()
        ]
        tool_calls = [m for m in migrated if m.get("tool_use_id") == "tu1"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["type"] == "tool_call"
        assert tool_calls[0]["status"] == "completed"
