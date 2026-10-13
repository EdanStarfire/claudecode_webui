"""T3 (epic #1990 stage 6c, issue #2159): the fleet halt/resume snapshot survives a
real Backend restart — not just a browser reload. Stop All persists which sessions
it stopped (and which were mid-task) via ProjectManager; a freshly-constructed
SessionCoordinator pointed at the same data_dir (simulating a process restart)
reloads that snapshot from disk, and Resume All acts on exactly it.

Uses real SessionManager/ProjectManager/SessionCoordinator/LegionCoordinator
wiring — no mock SDK — but patches start_session()/enqueue_message() themselves
on the post-restart coordinator so resume_all()'s actual SDK-launch mechanics
(which need a real or mocked Claude CLI) don't run; this test is about the
snapshot's persistence and resume_all()'s selection logic, not SDK startup.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION
from backend.session_coordinator import SessionCoordinator
from backend.session_manager import SessionInfo, SessionState


async def _make_coordinator(data_dir: Path) -> SessionCoordinator:
    coordinator = SessionCoordinator(data_dir)
    await coordinator.session_manager.initialize()
    await coordinator.project_manager.initialize()
    return coordinator


async def _seed_session(
    coordinator: SessionCoordinator,
    project_id: str,
    session_id: str,
    *,
    is_processing: bool,
) -> None:
    now = coordinator.session_manager._active_sessions.get(session_id)
    assert now is None, "session_id collision in test setup"
    from datetime import UTC, datetime

    info = SessionInfo(
        session_id=session_id,
        state=SessionState.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        project_id=project_id,
        is_processing=is_processing,
        message_schema_version=CURRENT_MESSAGE_SCHEMA_VERSION,
    )
    coordinator.session_manager._active_sessions[session_id] = info
    await coordinator.session_manager._persist_session_state(session_id)
    await coordinator.project_manager.add_session_to_project(project_id, session_id)


@pytest.mark.asyncio
async def test_fleet_snapshot_survives_restart_and_resume_all_uses_it():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)

        # --- Boot 1: Stop All ---
        coord1 = await _make_coordinator(data_dir)
        project = await coord1.project_manager.create_project("fleet-t3", str(data_dir))
        project_id = project.project_id

        processing_id = f"was-processing-{uuid.uuid4()}"
        idle_id = f"was-idle-{uuid.uuid4()}"
        await _seed_session(coord1, project_id, processing_id, is_processing=True)
        await _seed_session(coord1, project_id, idle_id, is_processing=False)

        halt_result = await coord1.legion_system.legion_coordinator.emergency_halt_all(project_id)
        assert set(halt_result["stopped_session_ids"]) == {processing_id, idle_id}
        assert halt_result["failed_sessions"] == []

        # Both sessions are genuinely TERMINATED — no mock SDK needed for this part.
        term1 = await coord1.session_manager.get_session_info(processing_id)
        term2 = await coord1.session_manager.get_session_info(idle_id)
        assert term1.state == SessionState.TERMINATED
        assert term2.state == SessionState.TERMINATED

        # Snapshot persisted correctly before any restart.
        pre_restart_snapshot = await coord1.project_manager.get_fleet_snapshot(project_id)
        assert set(pre_restart_snapshot["stopped_session_ids"]) == {processing_id, idle_id}
        assert pre_restart_snapshot["processing_session_ids"] == [processing_id]

        # --- Simulated Backend restart: fresh SessionCoordinator, same data_dir ---
        coord2 = await _make_coordinator(data_dir)

        # The snapshot must have survived the restart — this is the actual T3 assertion.
        post_restart_snapshot = await coord2.project_manager.get_fleet_snapshot(project_id)
        assert set(post_restart_snapshot["stopped_session_ids"]) == {processing_id, idle_id}
        assert post_restart_snapshot["processing_session_ids"] == [processing_id]

        # --- Resume All, post-restart ---
        with (
            patch.object(coord2, "start_session", AsyncMock(return_value=True)) as start_mock,
            patch.object(coord2, "enqueue_message", AsyncMock(return_value={"queue_id": "q1"})) as enqueue_mock,
        ):
            resume_result = await coord2.legion_system.legion_coordinator.resume_all(project_id)

        assert resume_result["resumed_count"] == 2
        assert resume_result["failed_minions"] == []

        # The processing session gets a catch-up message (auto-starts via queue);
        # the idle one gets a bare cold start. Exactly the right set, nothing extra.
        enqueue_mock.assert_called_once()
        assert enqueue_mock.call_args.args[0] == processing_id
        start_mock.assert_called_once()
        assert start_mock.call_args.args[0] == idle_id

        # Snapshot is cleared after a successful resume — a second Resume All is a no-op.
        final_snapshot = await coord2.project_manager.get_fleet_snapshot(project_id)
        assert final_snapshot["stopped_session_ids"] == []
        assert final_snapshot["processing_session_ids"] == []


@pytest.mark.asyncio
async def test_resume_all_leaves_unresumed_sessions_in_snapshot_on_failure():
    """A session that fails to resume stays in the snapshot for retry — Resume
    Sessions must not silently lose track of it."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        coord = await _make_coordinator(data_dir)
        project = await coord.project_manager.create_project("fleet-t3-fail", str(data_dir))
        project_id = project.project_id

        failing_id = f"fails-to-resume-{uuid.uuid4()}"
        await _seed_session(coord, project_id, failing_id, is_processing=False)
        await coord.legion_system.legion_coordinator.emergency_halt_all(project_id)

        with patch.object(
            coord, "start_session", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            resume_result = await coord.legion_system.legion_coordinator.resume_all(project_id)

        assert resume_result["resumed_count"] == 0
        assert resume_result["failed_minions"] == [(failing_id, "boom")]

        snapshot = await coord.project_manager.get_fleet_snapshot(project_id)
        assert snapshot["stopped_session_ids"] == [failing_id]
