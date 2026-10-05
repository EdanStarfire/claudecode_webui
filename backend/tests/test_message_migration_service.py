"""Tests for MessageMigrationService (issue #2084 stage 3-C, §5/§6/§9)."""

from pathlib import Path

import pytest

from backend.message_migration_service import MessageMigrationService
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


async def _make_legacy_session(coordinator, sample_session_config, fixture_name="tool_use"):
    session_id = await coordinator.create_session(**sample_session_config)
    coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
    storage = coordinator._storage_managers[session_id]
    storage.messages_file.write_text(
        (FIXTURES_DIR / fixture_name / "messages.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return session_id, storage


class TestMigrateOne:
    @pytest.mark.asyncio
    async def test_migrates_and_completes(self, temp_coordinator, sample_session_config):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(coordinator, sample_session_config)
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        ran = await service.migrate_one(session_id)
        assert ran is True

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "completed"
        assert info.message_schema_version != 0

    @pytest.mark.asyncio
    async def test_noop_for_already_canonical_session(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        ran = await service.migrate_one(session_id)
        assert ran is False

    @pytest.mark.asyncio
    async def test_second_call_after_completion_is_noop(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(coordinator, sample_session_config)
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        assert await service.migrate_one(session_id) is True
        # Second call must not re-run migration (would double-convert an
        # already-canonical file).
        assert await service.migrate_one(session_id) is False

    @pytest.mark.asyncio
    async def test_quarantines_on_corrupt_data(self, temp_coordinator, sample_session_config):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(coordinator, sample_session_config)
        storage.messages_file.write_text(
            storage.messages_file.read_text(encoding="utf-8") + "NOT VALID JSON\n",
            encoding="utf-8",
        )
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        ran = await service.migrate_one(session_id)
        assert ran is True

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "quarantined"
        assert info.message_schema_version == 0


class TestBackgroundTick:
    @pytest.mark.asyncio
    async def test_tick_picks_and_migrates_a_legacy_session(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id, storage = await _make_legacy_session(coordinator, sample_session_config)
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        await service._tick()

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "completed"

    @pytest.mark.asyncio
    async def test_tick_is_noop_when_no_legacy_sessions(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        await coordinator.create_session(**sample_session_config)  # canonical by default
        service = MessageMigrationService(coordinator, coordinator.session_manager)

        await service._tick()  # must not raise

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, temp_coordinator):
        service = MessageMigrationService(temp_coordinator, temp_coordinator.session_manager)
        await service.start()
        assert service._task is not None
        await service.stop()
        assert service._task is None
