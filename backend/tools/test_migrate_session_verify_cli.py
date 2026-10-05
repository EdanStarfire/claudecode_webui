"""Tests for migrate_session_verify_cli.py's core comparison function (issue
#2084 stage 3-C, §8/§9).

Not under backend/tests/ (pytest's testpaths) — opt-in, invoked explicitly:
    uv run pytest backend/tools/test_migrate_session_verify_cli.py
"""

import json
import uuid
from pathlib import Path

import pytest

from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.tools.migrate_session_verify_cli import (
    _diff,
    _group_by_logical_identity,
    _normalize,
    verify_archive_migration,
    verify_session_migration,
)

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


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


async def _make_legacy_session(coordinator, sample_session_config, fixture_name):
    session_id = await coordinator.create_session(**sample_session_config)
    coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
    await coordinator.session_manager._persist_session_state(session_id)
    storage = coordinator._storage_managers[session_id]
    storage.messages_file.write_text(
        (FIXTURES_DIR / fixture_name / "messages.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return session_id, storage


class TestDiffLogic:
    def test_identical_lists_produce_no_divergence(self):
        a = [{"type": "system", "content": "x"}]
        assert _diff(a, list(a)) == []

    def test_field_level_difference_is_detected(self):
        before = [{"type": "tool_call", "tool_use_id": "t1", "status": "completed", "result": "ok"}]
        after = [{"type": "tool_call", "tool_use_id": "t1", "status": "completed", "result": "WRONG"}]
        divergences = _diff(before, after)
        assert len(divergences) == 1
        assert divergences[0]["key"] == "tool_call:t1:completed"

    def test_tool_call_position_shift_alone_is_not_a_divergence(self):
        """A materialized tool_call record's insertion legitimately shifts its
        position relative to the surrounding regular messages — the diff groups
        by logical identity (tool_use_id:status for tool_call, type:index for
        everything else, since only tool_call records ever move), not raw line
        position, so this alone must not be flagged."""
        before = [
            {"type": "system", "content": "a"},
            {"type": "tool_call", "tool_use_id": "t1", "status": "pending"},
            {"type": "system", "content": "b"},
        ]
        after = [
            {"type": "system", "content": "a"},
            {"type": "system", "content": "b"},
            {"type": "tool_call", "tool_use_id": "t1", "status": "pending"},
        ]
        assert _diff(before, after) == []

    def test_corrupted_message_id_on_non_tool_call_record_is_caught(self):
        """Regression lock (found in review): the comparison pipeline — _normalize()
        feeding _diff() — must not be blind to migration corrupting or reassigning
        a real (non-tool_call) record's message_id. A tool_call record's id is
        exempt (it never had a stable one before migration); every other type's
        is a real identifier and must be compared."""
        before = [_normalize({"type": "assistant", "content": "hi", "message_id": "original-id"})]
        after = [_normalize({"type": "assistant", "content": "hi", "message_id": "corrupted-id"})]
        divergences = _diff(before, after)
        assert len(divergences) == 1
        assert divergences[0]["before"][0]["message_id"] == "original-id"
        assert divergences[0]["after"][0]["message_id"] == "corrupted-id"

    def test_missing_record_is_a_divergence(self):
        before = [{"type": "tool_call", "tool_use_id": "t1", "status": "completed"}]
        assert _diff(before, []) != []

    def test_group_keys_for_regular_messages_ignore_tool_call_insertions(self):
        """Regression lock for the bug a naive overall-index key would hit: two
        `system` messages separated by an inserted tool_call record must get the
        SAME keys whether or not that tool_call record is present."""
        with_tool_call = [
            {"type": "system", "content": "a"},
            {"type": "tool_call", "tool_use_id": "t1", "status": "pending"},
            {"type": "system", "content": "b"},
        ]
        without_tool_call = [
            {"type": "system", "content": "a"},
            {"type": "system", "content": "b"},
        ]
        keys_with = set(_group_by_logical_identity(with_tool_call))
        keys_without = set(_group_by_logical_identity(without_tool_call))
        assert {"system:0", "system:1"} == keys_without
        assert {"system:0", "system:1", "tool_call:t1:pending"} == keys_with


class TestNormalize:
    def test_strips_message_id_on_tool_call_only(self):
        """A synthesized tool_call never had a stable id before migration — only
        it is exempt from message_id comparison."""
        assert "message_id" not in _normalize(
            {"type": "tool_call", "tool_use_id": "t1", "status": "pending", "message_id": "x"}
        )

    def test_keeps_message_id_on_every_other_type(self):
        """Every other message type's message_id is a real, already-stable
        identifier propagated verbatim from the stored record — stripping it
        here would make the comparison blind to migration corrupting or
        reassigning a real record's identity."""
        for msg_type in ("system", "user", "assistant", "result", "permission_request"):
            out = _normalize({"type": msg_type, "message_id": "x"})
            assert out.get("message_id") == "x", f"message_id stripped for type={msg_type}"

    def test_strips_tool_call_timestamp_only(self):
        tc = _normalize({"type": "tool_call", "timestamp": 1.0, "created_at": 1.0})
        assert "timestamp" not in tc
        assert tc["created_at"] == 1.0

    def test_strips_processed_at_from_metadata(self):
        out = _normalize({"type": "user", "metadata": {"processed_at": 1.0, "role": None}})
        assert "processed_at" not in out["metadata"]
        assert out["metadata"]["role"] is None


class TestVerifySessionMigrationDryRun:
    @pytest.mark.parametrize(
        "fixture_name", ["tool_use", "permission_flow", "multi_turn", "single_turn", "hook_messages"]
    )
    @pytest.mark.asyncio
    async def test_zero_divergence_against_every_committed_fixture(
        self, temp_coordinator, sample_session_config, fixture_name
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, fixture_name
        )

        report = await verify_session_migration(coordinator, session_id, apply=False)

        assert report.ok, report.divergences
        assert report.applied is False
        assert report.archive_dir is not None
        assert report.archive_dir.exists()
        assert (report.archive_dir / "messages.jsonl").read_text(encoding="utf-8") == (
            FIXTURES_DIR / fixture_name / "messages.jsonl"
        ).read_text(encoding="utf-8")

        # Dry run must never touch the real session file.
        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_schema_version == 0
        assert info.message_migration_status is None

    @pytest.mark.asyncio
    async def test_unknown_session_raises(self, temp_coordinator):
        with pytest.raises(ValueError):
            await verify_session_migration(temp_coordinator, "no-such-session", apply=False)


class TestVerifySessionMigrationApply:
    @pytest.mark.asyncio
    async def test_apply_flips_real_session_and_reports_ok(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "permission_flow"
        )

        report = await verify_session_migration(coordinator, session_id, apply=True)

        assert report.ok, report.divergences
        assert report.applied is True
        assert report.archive_dir is None  # no preview snapshot needed for --apply

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "completed"
        assert info.message_schema_version != 0

    @pytest.mark.asyncio
    async def test_apply_refuses_when_already_completed_elsewhere(
        self, temp_coordinator, sample_session_config
    ):
        """A concurrently-run production migration (background/on-demand) must
        not be silently re-run and corrupted by a second --apply invocation."""
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(
            coordinator, sample_session_config, "tool_use"
        )
        assert await coordinator.session_manager.try_claim_message_migration(session_id)
        await coordinator.session_manager.complete_message_migration(session_id, 0)

        with pytest.raises(RuntimeError):
            await verify_session_migration(coordinator, session_id, apply=True)


def _write_legacy_archive(archive_dir: Path, fixture_name: str, session_id: str) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "messages.jsonl").write_text(
        (FIXTURES_DIR / fixture_name / "messages.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    state = {
        "session_id": session_id,
        "message_schema_version": 0,
        "message_migration_status": None,
    }
    (archive_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


class TestVerifyArchiveMigrationDryRun:
    """Issue #2084 (stage 3-D-prep, §3): --archive-dir mode, same dry-run
    semantics as verify_session_migration() but with no live SessionInfo."""

    @pytest.mark.asyncio
    async def test_zero_divergence_and_archive_untouched(self, temp_coordinator, tmp_path):
        archive_dir = tmp_path / "archive"
        session_id = str(uuid.uuid4())
        _write_legacy_archive(archive_dir, "permission_flow", session_id)

        report = await verify_archive_migration(temp_coordinator, archive_dir, apply=False)

        assert report.ok, report.divergences
        assert report.applied is False
        assert report.session_id == session_id

        # Dry run must never touch the real archive's own files.
        reloaded_state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert reloaded_state["message_schema_version"] == 0
        assert reloaded_state["message_migration_status"] is None

    @pytest.mark.asyncio
    async def test_missing_files_raises(self, temp_coordinator, tmp_path):
        archive_dir = tmp_path / "empty-archive"
        archive_dir.mkdir()
        with pytest.raises(ValueError):
            await verify_archive_migration(temp_coordinator, archive_dir, apply=False)

    @pytest.mark.asyncio
    async def test_already_canonical_archive_raises(self, temp_coordinator, tmp_path):
        archive_dir = tmp_path / "canon-archive"
        archive_dir.mkdir()
        (archive_dir / "messages.jsonl").write_text("", encoding="utf-8")
        state = {
            "session_id": "s1",
            "message_schema_version": CURRENT_MESSAGE_SCHEMA_VERSION,
        }
        (archive_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

        with pytest.raises(RuntimeError):
            await verify_archive_migration(temp_coordinator, archive_dir, apply=False)


class TestVerifyArchiveMigrationApply:
    @pytest.mark.asyncio
    async def test_apply_flips_archive_state_json_in_place(self, temp_coordinator, tmp_path):
        archive_dir = tmp_path / "archive"
        session_id = str(uuid.uuid4())
        _write_legacy_archive(archive_dir, "permission_flow", session_id)

        report = await verify_archive_migration(temp_coordinator, archive_dir, apply=True)

        assert report.ok, report.divergences
        assert report.applied is True

        reloaded_state = json.loads((archive_dir / "state.json").read_text(encoding="utf-8"))
        assert reloaded_state["message_schema_version"] == CURRENT_MESSAGE_SCHEMA_VERSION
        assert reloaded_state["message_migration_status"]["state"] == "completed"
