"""Tests for the on-demand migration trigger wired into start_session() (issue
#2084 stage 3-C, §6)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from backend.message_migration_service import MessageMigrationService
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator


def _install_mock_sdk(coordinator):
    """Issue #559: inject a mock SDK factory so start_session() succeeds without
    a real working directory / Claude Code CLI."""
    mock_sdk_instance = AsyncMock()
    mock_sdk_instance.start.return_value = True
    mock_sdk_instance.is_running.return_value = False
    coordinator.set_sdk_factory(Mock(return_value=mock_sdk_instance))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
async def temp_coordinator(tmp_path):
    coordinator = SessionCoordinator(tmp_path)
    await coordinator.initialize()
    coordinator._message_migration_service = MessageMigrationService(
        coordinator, coordinator.session_manager
    )
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


class TestOnDemandTrigger:
    @pytest.mark.asyncio
    async def test_start_session_migrates_legacy_session(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]
        storage.messages_file.write_text(
            (FIXTURES_DIR / "tool_use" / "messages.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        _install_mock_sdk(coordinator)
        notices = []
        coordinator.set_migration_notice_callback(lambda sid: notices.append(sid))

        ok = await coordinator.start_session(session_id)
        assert ok is True

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status["state"] == "completed"
        assert info.message_schema_version != 0
        assert notices == [session_id]

    @pytest.mark.asyncio
    async def test_start_session_noop_for_canonical_session(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        _install_mock_sdk(coordinator)
        notices = []
        coordinator.set_migration_notice_callback(lambda sid: notices.append(sid))

        ok = await coordinator.start_session(session_id)
        assert ok is True
        assert notices == []

        info = await coordinator.session_manager.get_session_info(session_id)
        assert info.message_migration_status is None

    @pytest.mark.asyncio
    async def test_restart_after_migration_does_not_renotify(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.session_manager._active_sessions[session_id].message_schema_version = 0
        storage = coordinator._storage_managers[session_id]
        storage.messages_file.write_text(
            (FIXTURES_DIR / "tool_use" / "messages.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        _install_mock_sdk(coordinator)
        notices = []
        coordinator.set_migration_notice_callback(lambda sid: notices.append(sid))

        await coordinator.start_session(session_id)
        await coordinator.terminate_session(session_id)
        await coordinator.start_session(session_id)

        assert notices == [session_id]  # only once, not on the second start

    @pytest.mark.asyncio
    async def test_start_session_reuses_cached_storage_manager_lock(
        self, temp_coordinator, sample_session_config
    ):
        """Issue #2084 (stage 3-C) regression lock: start_session() must reuse an
        already-cached DataStorageManager rather than unconditionally constructing
        a fresh one — a fresh instance would carry a brand-new, unlocked
        `_write_lock`, orphaning the lock a concurrent background/on-demand
        migration might already be holding for this exact session, silently
        defeating the lock's entire coordination purpose. Simulates the race by
        caching a storage manager first (as a background migration tick would via
        get_or_create_storage_manager()) before calling start_session()."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        _install_mock_sdk(coordinator)

        pre_existing_storage = await coordinator.get_or_create_storage_manager(session_id)

        await coordinator.start_session(session_id)

        assert coordinator._storage_managers[session_id] is pre_existing_storage
        assert coordinator._storage_managers[session_id]._write_lock is (
            pre_existing_storage._write_lock
        )

    @pytest.mark.asyncio
    async def test_concurrent_get_or_create_storage_manager_first_access_is_race_free(
        self, temp_coordinator, sample_session_config
    ):
        """Issue #2084 (stage 3-C) regression lock, found in review one layer up
        from the start_session() fix above: get_or_create_storage_manager() itself
        is a check-then-create sequence with two await points (get_session_directory,
        storage.initialize()) between the cache check and the cache write. Without
        SessionManager's per-session lock guarding the whole body, two concurrent
        *first-time* callers (e.g. the background migration tick and a user's
        start_session() racing on a session that's never had a storage manager
        cached) could each construct their own independent DataStorageManager with
        its own independent, unlocked _write_lock, silently orphaning whichever one
        loses the final dict-assignment race."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        # create_session() populates _storage_managers itself — drop it to simulate
        # the real scenario this test targets: a dormant session (created before a
        # backend restart, never reopened since) with no cached storage manager at
        # all, same as what SessionWatchdogService/MessageMigrationService's startup
        # scan would find for any pre-existing session on a fresh Backend process.
        del coordinator._storage_managers[session_id]
        assert session_id not in coordinator._storage_managers

        results = await asyncio.gather(
            coordinator.get_or_create_storage_manager(session_id),
            coordinator.get_or_create_storage_manager(session_id),
        )

        first, second = results
        assert first is not None and second is not None
        assert first is second
        assert first._write_lock is second._write_lock
        assert coordinator._storage_managers[session_id] is first
