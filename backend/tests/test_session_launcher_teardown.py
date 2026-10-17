"""Regression tests for SessionLauncher.teardown_active_sdk() (issue #2197, #2214).

Covers the one shared helper that now consolidates the "get sdk ->
terminate/disconnect -> settle sleep -> remove from _active_sdks" shape used by
terminate_session/restart_session/reset_session (backend/session_launcher.py)
and delete_session (backend/session_coordinator.py). Of the 3 axes #2197 found
diverging across call sites, issue #2214 resolved 2: settle ordering is now
unified on sleep-before-delete everywhere (delete_session's ordering flips;
terminate_session gains a Docker-only settle it previously lacked), and the
disconnect-result warning is removed entirely as redundant with disconnect()'s
own exception logging. Only op choice (graceful=True -> disconnect(), restart/
reset; graceful=False -> terminate(), terminate/delete) remains a real,
intentional behavioral divergence across call sites, pinned here so a future
change to the helper can't silently alter it.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionState

SLEEP_TARGET = "backend.session_launcher.asyncio.sleep"


@pytest.fixture
async def temp_coordinator():
    with tempfile.TemporaryDirectory() as temp_dir:
        coordinator = SessionCoordinator(Path(temp_dir))
        await coordinator.initialize()
        yield coordinator
        await coordinator.cleanup()


@pytest.fixture
async def sample_session_config(temp_coordinator):
    import uuid

    coordinator = temp_coordinator
    project = await coordinator.project_manager.create_project(
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


def _recording_sleep(call_order, coordinator=None, session_id=None, expect_deleted=None):
    """Build an AsyncMock sleep side_effect that records (sleep, duration) and
    optionally asserts whether the _active_sdks entry has/hasn't been removed yet."""

    async def _side_effect(duration):
        if expect_deleted is not None:
            is_deleted = session_id not in coordinator._active_sdks
            assert is_deleted == expect_deleted, (
                f"expected session_id deleted={expect_deleted} at sleep time, got {is_deleted}"
            )
        call_order.append(("sleep", duration))

    return AsyncMock(side_effect=_side_effect)


class TestTeardownActiveSdkDirect:
    """Direct unit tests of teardown_active_sdk() itself, independent of callers."""

    @pytest.mark.asyncio
    async def test_no_active_sdk_returns_false_without_sleep_or_delete(
        self, temp_coordinator
    ):
        coordinator = temp_coordinator
        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(SLEEP_TARGET, sleep_mock)
            result = await coordinator._session_launcher.teardown_active_sdk(
                "nonexistent-session", graceful=True, settle_seconds=1.0
            )
        assert result is False
        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_op_runs_before_settle_sleep(self, temp_coordinator):
        coordinator = temp_coordinator
        session_id = "sess-after-op"
        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []

        async def _disconnect():
            call_order.append("disconnect")
            return True

        mock_sdk.disconnect = AsyncMock(side_effect=_disconnect)

        async def _after_op():
            call_order.append("after_op")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SLEEP_TARGET, _recording_sleep(call_order))
            result = await coordinator._session_launcher.teardown_active_sdk(
                session_id,
                graceful=True,
                settle_seconds=1.0,
                after_op=_after_op,
            )

        assert result is True
        assert call_order == ["disconnect", "after_op", ("sleep", 1.0)]

    @pytest.mark.asyncio
    async def test_settle_sleeps_before_removal(self, temp_coordinator):
        coordinator = temp_coordinator
        session_id = "sess-settle-before"
        mock_sdk = AsyncMock()
        mock_sdk.disconnect.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                SLEEP_TARGET,
                _recording_sleep(
                    call_order, coordinator=coordinator, session_id=session_id, expect_deleted=False
                ),
            )
            await coordinator._session_launcher.teardown_active_sdk(
                session_id, graceful=True, settle_seconds=0.5
            )

        assert session_id not in coordinator._active_sdks
        assert call_order == [("sleep", 0.5)]

    @pytest.mark.asyncio
    async def test_graceful_true_calls_disconnect_not_terminate(self, temp_coordinator):
        coordinator = temp_coordinator
        session_id = "sess-graceful"
        mock_sdk = AsyncMock()
        mock_sdk.disconnect.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        await coordinator._session_launcher.teardown_active_sdk(session_id, graceful=True)

        mock_sdk.disconnect.assert_called_once()
        mock_sdk.terminate.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_false_calls_terminate_not_disconnect(self, temp_coordinator):
        coordinator = temp_coordinator
        session_id = "sess-not-graceful"
        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        await coordinator._session_launcher.teardown_active_sdk(session_id, graceful=False)

        mock_sdk.terminate.assert_called_once()
        mock_sdk.disconnect.assert_not_called()

class TestTerminateSessionTeardown:
    @pytest.mark.asyncio
    async def test_terminate_session_non_docker_uses_terminate_with_no_sleep(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)

        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        # SessionManager.terminate_session() has its own unrelated 0.1s placeholder
        # sleep (session_manager.py:810) — mocked out here so it can't be confused
        # with teardown_active_sdk's own (absent for non-Docker sessions) settle
        # sleep. asyncio.sleep is one shared module object, so any patch of it is
        # process-global regardless of which file's `asyncio.sleep` spelling is
        # used to install it.
        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(SLEEP_TARGET, sleep_mock)
            mp.setattr(coordinator.session_manager, "terminate_session", AsyncMock(return_value=True))
            success = await coordinator.terminate_session(session_id)

        assert success is True
        mock_sdk.terminate.assert_called_once()
        mock_sdk.disconnect.assert_not_called()
        sleep_mock.assert_not_called()
        assert session_id not in coordinator._active_sdks

    @pytest.mark.asyncio
    async def test_terminate_session_docker_enabled_sleeps_1_0_before_removal(
        self, temp_coordinator, sample_session_config
    ):
        """Issue #2214: Docker sessions get the #781 settle sleep on terminate too,
        same as restart/reset already have — guards against an immediate
        user-start racing a container still being removed by `docker --rm`."""
        coordinator = temp_coordinator
        sample_session_config["config"].docker_enabled = True
        session_id = await coordinator.create_session(**sample_session_config)

        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                SLEEP_TARGET,
                _recording_sleep(
                    call_order, coordinator=coordinator, session_id=session_id, expect_deleted=False
                ),
            )
            mp.setattr(coordinator.session_manager, "terminate_session", AsyncMock(return_value=True))
            success = await coordinator.terminate_session(session_id)

        assert success is True
        mock_sdk.terminate.assert_called_once()
        mock_sdk.disconnect.assert_not_called()
        assert call_order == [("sleep", 1.0)]
        assert session_id not in coordinator._active_sdks


class TestRestartSessionTeardown:
    @pytest.mark.asyncio
    async def test_restart_session_disconnects_and_sleeps_1_0(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager._update_session_state(session_id, SessionState.ACTIVE)

        mock_sdk = AsyncMock()
        mock_sdk.disconnect.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                SLEEP_TARGET,
                _recording_sleep(
                    call_order, coordinator=coordinator, session_id=session_id, expect_deleted=False
                ),
            )
            with pytest.MonkeyPatch.context() as mp2:
                mp2.setattr(
                    coordinator._session_launcher, "start_session", AsyncMock(return_value=True)
                )
                success = await coordinator.restart_session(session_id)

        assert success is True
        mock_sdk.disconnect.assert_called_once()
        mock_sdk.terminate.assert_not_called()
        assert call_order == [("sleep", 1.0)]
        assert session_id not in coordinator._active_sdks

    @pytest.mark.asyncio
    async def test_restart_session_recover_session_called_before_sleep(
        self, temp_coordinator, sample_session_config
    ):
        """Issue #2197's trickiest correctness detail: recover_session() must run
        between disconnect and the 1.0s settle sleep, not after it."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager._update_session_state(session_id, SessionState.ACTIVE)

        mock_sdk = AsyncMock()
        call_order = []

        async def _disconnect():
            call_order.append("disconnect")
            return True

        mock_sdk.disconnect = AsyncMock(side_effect=_disconnect)
        coordinator._active_sdks[session_id] = mock_sdk

        async def _recover_side_effect(*_args, **_kwargs):
            call_order.append("recover_session")
            return True

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SLEEP_TARGET, _recording_sleep(call_order))
            with pytest.MonkeyPatch.context() as mp2:
                mp2.setattr(
                    coordinator.session_manager,
                    "recover_session",
                    AsyncMock(side_effect=_recover_side_effect),
                )
                mp2.setattr(
                    coordinator._session_launcher, "start_session", AsyncMock(return_value=True)
                )
                success = await coordinator.restart_session(session_id)

        assert success is True
        assert call_order == ["disconnect", "recover_session", ("sleep", 1.0)]

    @pytest.mark.asyncio
    async def test_restart_session_no_active_sdk_still_calls_recover_session(
        self, temp_coordinator, sample_session_config
    ):
        """had_sdk=False branch: recover_session() must still run directly, preserving
        the original 'recover_session always runs' semantics regardless of whether
        there was an SDK to tear down."""
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        await coordinator.session_manager._update_session_state(session_id, SessionState.ACTIVE)
        assert session_id not in coordinator._active_sdks

        recover_mock = AsyncMock(return_value=True)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(coordinator.session_manager, "recover_session", recover_mock)
            mp.setattr(
                coordinator._session_launcher, "start_session", AsyncMock(return_value=True)
            )
            success = await coordinator.restart_session(session_id)

        assert success is True
        recover_mock.assert_called_once_with(session_id)


class TestResetSessionTeardown:
    @pytest.mark.asyncio
    async def test_reset_session_disconnects_and_sleeps_0_5(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.legion_system = None

        mock_sdk = AsyncMock()
        mock_sdk.disconnect.return_value = True
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                SLEEP_TARGET,
                _recording_sleep(
                    call_order, coordinator=coordinator, session_id=session_id, expect_deleted=False
                ),
            )
            mp.setattr(
                coordinator._session_launcher, "start_session", AsyncMock(return_value=True)
            )
            success = await coordinator.reset_session(session_id)

        assert success is True
        mock_sdk.disconnect.assert_called_once()
        mock_sdk.terminate.assert_not_called()
        assert call_order == [("sleep", 0.5)]
        assert session_id not in coordinator._active_sdks

    @pytest.mark.asyncio
    async def test_reset_session_recover_session_called_before_sleep(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.legion_system = None

        mock_sdk = AsyncMock()
        call_order = []

        async def _disconnect():
            call_order.append("disconnect")
            return True

        mock_sdk.disconnect = AsyncMock(side_effect=_disconnect)
        coordinator._active_sdks[session_id] = mock_sdk

        async def _recover_side_effect(*_args, **_kwargs):
            call_order.append("recover_session")
            return True

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SLEEP_TARGET, _recording_sleep(call_order))
            mp.setattr(
                coordinator.session_manager,
                "recover_session",
                AsyncMock(side_effect=_recover_side_effect),
            )
            mp.setattr(
                coordinator._session_launcher, "start_session", AsyncMock(return_value=True)
            )
            success = await coordinator.reset_session(session_id)

        assert success is True
        assert call_order == ["disconnect", "recover_session", ("sleep", 0.5)]

class TestDeleteSessionTeardown:
    @pytest.mark.asyncio
    async def test_delete_session_terminates_and_sleeps_before_delete(
        self, temp_coordinator, sample_session_config
    ):
        coordinator = temp_coordinator
        session_id = await coordinator.create_session(**sample_session_config)
        coordinator.legion_system = None
        # delete_session() has two further unrelated placeholder sleeps of its own
        # (session_manager.terminate_session()'s 0.1s, and the storage-manager
        # cleanup step's own 0.2s at session_coordinator.py:1424) — neutralized here
        # so only teardown_active_sdk's 0.2s settle sleep is observed. asyncio.sleep
        # is one shared module object, so patching it is process-global regardless
        # of which file's `asyncio.sleep` spelling installs the patch.
        coordinator._storage_managers.pop(session_id, None)

        mock_sdk = AsyncMock()
        coordinator._active_sdks[session_id] = mock_sdk

        call_order = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                SLEEP_TARGET,
                _recording_sleep(
                    call_order, coordinator=coordinator, session_id=session_id, expect_deleted=False
                ),
            )
            mp.setattr(coordinator.session_manager, "terminate_session", AsyncMock(return_value=True))
            result = await coordinator.delete_session(session_id)

        assert result["success"] is True
        mock_sdk.terminate.assert_called_once()
        mock_sdk.disconnect.assert_not_called()
        assert call_order == [("sleep", 0.2)]
        assert session_id not in coordinator._active_sdks
