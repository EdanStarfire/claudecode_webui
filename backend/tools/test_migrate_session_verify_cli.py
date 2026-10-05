"""Tests for migrate_session_verify_cli.py's core comparison function (issue
#2084 stage 3-C, §8/§9).

Not under backend/tests/ (pytest's testpaths) — opt-in, invoked explicitly:
    uv run pytest backend/tools/test_migrate_session_verify_cli.py
"""

from pathlib import Path

import pytest

from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.tools.migrate_session_verify_cli import (
    _diff,
    _group_by_logical_identity,
    _normalize,
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
    def test_strips_message_id(self):
        assert "message_id" not in _normalize({"type": "system", "message_id": "x"})

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
