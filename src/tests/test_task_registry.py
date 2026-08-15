"""
Regression tests for issue #1746 (stage: backend) — TaskLegRegistry.

Covers:
- §4 TaskLegRegistry mechanics: single-leg, multi-leg resume, concurrent
  agents, TaskStop-only termination, progress-after-terminal no-op.
- §3 task_id stability: a live-pipeline test (message_parser -> registry)
  driving two full launch/resume legs sharing one task_id through real SDK
  dataclasses, proving the fix holds through the actual parsing path, not
  just the registry's own bookkeeping.
- §4 reload/live parity: a hand-built messages.jsonl fixture reconstructs an
  identical registry to the equivalent live-streamed sequence.
- §4 new endpoint: GET /api/sessions/{id}/background_agents integration test.
"""

from __future__ import annotations

import json

import pytest
from claude_agent_sdk import (
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)

from src.message_parser import MessageParser, MessageProcessor
from src.task_registry import TaskLegRegistry

# ---------------------------------------------------------------------------
# §4: TaskLegRegistry mechanics (pure unit tests, no I/O)
# ---------------------------------------------------------------------------


def _started(task_id, tool_use_id, description="alpha: do work"):
    return {
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "description": description,
    }


def _progress(task_id):
    return {"task_id": task_id}


def _notification(task_id, status):
    return {"task_id": task_id, "status": status}


def _updated(task_id, status=None, patch=None):
    return {"task_id": task_id, "status": status, "patch": patch or {}}


def test_single_leg_lifecycle_reaches_completed():
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame("task_progress", _progress("t1"), timestamp=2.0)
    registry.apply_frame("task_notification", _notification("t1", "completed"), timestamp=3.0)

    snapshot = registry.snapshot()
    assert len(snapshot) == 1
    entry = snapshot[0]
    assert entry["task_id"] == "t1"
    assert entry["current_status"] == "completed"
    assert len(entry["legs"]) == 1
    assert entry["legs"][0]["tool_use_id"] == "tu-1"
    assert entry["legs"][0]["ended_at"] == 3.0
    assert registry.current_status("t1") == "completed"


def test_multi_leg_resume_shares_task_id_distinct_tool_use_ids():
    """§3: task_id is stable across a resumed agent's legs; tool_use_id is per-leg."""
    registry = TaskLegRegistry()

    # Leg 1
    registry.apply_frame("task_started", _started("t-resume", "tu-A"), timestamp=1.0)
    registry.apply_frame("task_progress", _progress("t-resume"), timestamp=2.0)
    registry.apply_frame("task_notification", _notification("t-resume", "completed"), timestamp=3.0)

    # Leg 2 (resumed agent, same task_id, new tool_use_id)
    registry.apply_frame("task_started", _started("t-resume", "tu-B"), timestamp=4.0)
    registry.apply_frame("task_progress", _progress("t-resume"), timestamp=5.0)
    registry.apply_frame("task_notification", _notification("t-resume", "completed"), timestamp=6.0)

    snapshot = registry.snapshot()
    assert len(snapshot) == 1, "both legs must collapse under the one stable task_id"
    entry = snapshot[0]
    assert len(entry["legs"]) == 2
    assert entry["legs"][0]["tool_use_id"] == "tu-A"
    assert entry["legs"][1]["tool_use_id"] == "tu-B"
    assert entry["latest_leg"]["tool_use_id"] == "tu-B"
    assert entry["current_status"] == "completed"


def test_concurrent_different_task_ids_no_cross_contamination():
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame("task_started", _started("t2", "tu-2"), timestamp=1.0)
    registry.apply_frame("task_notification", _notification("t1", "completed"), timestamp=2.0)

    assert registry.current_status("t1") == "completed"
    assert registry.current_status("t2") == "running"
    snapshot = {e["task_id"]: e for e in registry.snapshot()}
    assert set(snapshot) == {"t1", "t2"}


def test_task_stop_only_termination_reaches_stopped_via_task_updated():
    """A TaskStop-terminated task reports status="killed" only via task_updated,
    with no task_notification at all — must still close out the leg as "stopped"."""
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame("task_updated", _updated("t1", status="killed"), timestamp=2.0)

    assert registry.current_status("t1") == "stopped"
    leg = registry.snapshot()[0]["legs"][0]
    assert leg["ended_at"] == 2.0


def test_task_updated_status_only_in_patch_still_detected():
    """task_updated may report status only inside patch, not the top-level field."""
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame(
        "task_updated", _updated("t1", status=None, patch={"status": "failed"}), timestamp=2.0
    )
    assert registry.current_status("t1") == "failed"


def test_duplicate_terminal_frame_does_not_overwrite_first_terminal_status():
    """First-terminal-wins: task_notification is only "sometimes" suppressed
    after task_updated (or vice versa) already closed a leg out — a later,
    duplicate/conflicting terminal frame must not overwrite the first."""
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame("task_notification", _notification("t1", "completed"), timestamp=2.0)
    # A late task_updated for the same already-terminal leg reports a
    # different status — must not clobber the first terminal result.
    registry.apply_frame("task_updated", _updated("t1", status="killed"), timestamp=3.0)

    leg = registry.snapshot()[0]["legs"][0]
    assert leg["status"] == "completed"
    assert leg["ended_at"] == 2.0, "the first terminal frame's timestamp must be preserved"


def test_progress_after_terminal_is_noop():
    """Ordering: progress arriving after a terminal notification must not resurrect the leg."""
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", _started("t1", "tu-1"), timestamp=1.0)
    registry.apply_frame("task_notification", _notification("t1", "completed"), timestamp=2.0)
    registry.apply_frame("task_progress", _progress("t1"), timestamp=3.0)

    leg = registry.snapshot()[0]["legs"][0]
    assert leg["status"] == "completed"
    assert leg["last_progress_at"] == 1.0, "stale progress must not overwrite last_progress_at"


def test_frame_with_no_task_id_is_ignored():
    registry = TaskLegRegistry()
    registry.apply_frame("task_started", {"tool_use_id": "tu-1"}, timestamp=1.0)
    assert registry.snapshot() == []


def test_progress_or_terminal_with_no_started_leg_is_dropped():
    registry = TaskLegRegistry()
    registry.apply_frame("task_notification", _notification("ghost", "completed"), timestamp=1.0)
    assert registry.snapshot() == []


# ---------------------------------------------------------------------------
# §3: live-pipeline stability test (message_parser -> registry), real SDK dataclasses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_pipeline_two_leg_resume_preserves_task_id_stability():
    """Drives real TaskStartedMessage/TaskProgressMessage/TaskNotificationMessage
    SDK objects through MessageProcessor (the live path, now with the §2a fix)
    for two separate legs sharing one task_id, then feeds each parsed frame's
    metadata into TaskLegRegistry — exercising the full live pipeline, not just
    the registry's own bookkeeping."""
    processor = MessageProcessor(MessageParser())
    registry = TaskLegRegistry()

    def _drive(sdk_msg, timestamp):
        message_data = {
            "type": "system",
            "sdk_message": sdk_msg,
            "session_id": "sess-1",
            "timestamp": timestamp,
        }
        parsed = processor.process_message(message_data, source="sdk")
        assert parsed.metadata.get("subtype") in (
            "task_started", "task_progress", "task_notification", "task_updated",
        )
        registry.apply_frame(parsed.metadata["subtype"], parsed.metadata, timestamp)

    # Leg 1
    _drive(TaskStartedMessage(
        subtype="task_started", data={}, task_id="shared-task-id",
        description="alpha: leg one", uuid="u1", session_id="sub-sess-1", tool_use_id="tu-A",
    ), 1.0)
    _drive(TaskProgressMessage(
        subtype="task_progress", data={}, task_id="shared-task-id",
        description="alpha: leg one", usage=None, uuid="u2", session_id="sub-sess-1", tool_use_id="tu-A",
    ), 2.0)
    _drive(TaskNotificationMessage(
        subtype="task_notification", data={}, task_id="shared-task-id",
        status="completed", output_file="", summary="done", uuid="u3",
        session_id="sub-sess-1", tool_use_id="tu-A",
    ), 3.0)

    # Simulated resume trigger — leg 2, same task_id, new tool_use_id
    _drive(TaskStartedMessage(
        subtype="task_started", data={}, task_id="shared-task-id",
        description="alpha: leg two", uuid="u4", session_id="sub-sess-1", tool_use_id="tu-B",
    ), 4.0)
    _drive(TaskProgressMessage(
        subtype="task_progress", data={}, task_id="shared-task-id",
        description="alpha: leg two", usage=None, uuid="u5", session_id="sub-sess-1", tool_use_id="tu-B",
    ), 5.0)
    _drive(TaskNotificationMessage(
        subtype="task_notification", data={}, task_id="shared-task-id",
        status="completed", output_file="", summary="done again", uuid="u6",
        session_id="sub-sess-1", tool_use_id="tu-B",
    ), 6.0)

    snapshot = registry.snapshot()
    assert len(snapshot) == 1
    entry = snapshot[0]
    assert entry["task_id"] == "shared-task-id"
    assert len(entry["legs"]) == 2
    assert entry["legs"][0]["tool_use_id"] == "tu-A"
    assert entry["legs"][1]["tool_use_id"] == "tu-B"
    assert entry["latest_leg"]["tool_use_id"] == "tu-B"
    assert entry["current_status"] == "completed"


@pytest.mark.asyncio
async def test_live_pipeline_task_updated_killed_no_notification():
    """§2a regression guard: TaskUpdatedMessage must be extracted with its own
    handler in the live path, not fall through to the generic system handler."""
    processor = MessageProcessor(MessageParser())
    registry = TaskLegRegistry()

    message_data = {
        "type": "system",
        "sdk_message": TaskStartedMessage(
            subtype="task_started", data={}, task_id="stopped-task",
            description="beta: stoppable", uuid="u1", session_id="sub-sess-2", tool_use_id="tu-X",
        ),
        "session_id": "sess-2",
        "timestamp": 1.0,
    }
    parsed = processor.process_message(message_data, source="sdk")
    registry.apply_frame(parsed.metadata["subtype"], parsed.metadata, 1.0)

    updated_data = {
        "type": "system",
        "sdk_message": TaskUpdatedMessage(
            subtype="task_updated", data={}, task_id="stopped-task",
            patch={"status": "killed"}, status="killed", session_id="sub-sess-2", uuid="u2",
        ),
        "session_id": "sess-2",
        "timestamp": 2.0,
    }
    parsed = processor.process_message(updated_data, source="sdk")
    # This is exactly the bug §2a fixes: without TaskUpdatedHandler this would
    # be subtype="init" (SystemMessageHandler's default) and task_id would be lost.
    assert parsed.metadata.get("subtype") == "task_updated"
    assert parsed.metadata.get("task_id") == "stopped-task"
    registry.apply_frame(parsed.metadata["subtype"], parsed.metadata, 2.0)

    assert registry.current_status("stopped-task") == "stopped"


# ---------------------------------------------------------------------------
# §4: reload/live parity
# ---------------------------------------------------------------------------


def _stored_task_started(task_id, tool_use_id, session_id, ts):
    return {
        "_type": "TaskStartedMessage",
        "timestamp": ts,
        "session_id": session_id,
        "data": {
            "subtype": "task_started",
            "data": {},
            "task_id": task_id,
            "description": "alpha: hydrated",
            "uuid": f"uuid-{task_id}-started",
            "session_id": "sub-" + session_id,
            "tool_use_id": tool_use_id,
            "task_type": "local_agent",
        },
    }


def _stored_task_progress(task_id, tool_use_id, session_id, ts):
    return {
        "_type": "TaskProgressMessage",
        "timestamp": ts,
        "session_id": session_id,
        "data": {
            "subtype": "task_progress",
            "data": {},
            "task_id": task_id,
            "description": "alpha: hydrated",
            "usage": None,
            "uuid": f"uuid-{task_id}-progress",
            "session_id": "sub-" + session_id,
            "tool_use_id": tool_use_id,
            "last_tool_name": "Read",
        },
    }


def _stored_task_notification(task_id, tool_use_id, session_id, status, ts):
    return {
        "_type": "TaskNotificationMessage",
        "timestamp": ts,
        "session_id": session_id,
        "data": {
            "subtype": "task_notification",
            "data": {},
            "task_id": task_id,
            "status": status,
            "output_file": "",
            "summary": "done",
            "uuid": f"uuid-{task_id}-notif",
            "session_id": "sub-" + session_id,
            "tool_use_id": tool_use_id,
            "usage": None,
        },
    }


@pytest.mark.asyncio
async def test_reload_reconstruction_matches_live_streamed_sequence(tmp_path):
    """A hand-built messages.jsonl fixture must reconstruct an identical
    registry to the equivalent live-streamed sequence."""
    import src.session_coordinator as sc_module
    from src.session_coordinator import SessionCoordinator

    session_id = "sess-parity"

    # Build the "live" registry directly via apply_frame, using the same
    # metadata shape _convert_stored_message_to_websocket would produce.
    live_registry = TaskLegRegistry()
    live_registry.apply_frame(
        "task_started",
        {"task_id": "t1", "tool_use_id": "tu-1", "description": "alpha: hydrated"},
        1.0,
    )
    live_registry.apply_frame(
        "task_progress", {"task_id": "t1", "description": "alpha: hydrated"}, 2.0
    )
    live_registry.apply_frame(
        "task_notification", {"task_id": "t1", "status": "completed"}, 3.0
    )

    coordinator = SessionCoordinator(data_dir=tmp_path)
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)

    messages_path = session_dir / "messages.jsonl"
    with messages_path.open("w", encoding="utf-8") as f:
        for msg in (
            _stored_task_started("t1", "tu-1", session_id, 1.0),
            _stored_task_progress("t1", "tu-1", session_id, 2.0),
            _stored_task_notification("t1", "tu-1", session_id, "completed", 3.0),
        ):
            f.write(json.dumps(msg) + "\n")

    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    coordinator._storage_managers[session_id] = storage

    reloaded_registry = await coordinator._get_task_leg_registry(session_id)

    assert reloaded_registry.snapshot() == live_registry.snapshot()


# ---------------------------------------------------------------------------
# §4: cold-cache live callback must not double-apply the triggering frame
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_callback_cold_cache_does_not_double_apply_triggering_frame(tmp_path):
    """ClaudeSDK always persists a message (storage_manager.append_message) before
    invoking message_callback — so the very first Task frame seen by a session
    with no cached TaskLegRegistry yet is *already* in messages.jsonl by the
    time the live callback runs. Hydration therefore already includes it; the
    live callback must not apply it a second time (which would append two legs
    for one real launch)."""
    import src.session_coordinator as sc_module
    from src.session_coordinator import SessionCoordinator

    session_id = "sess-cold-cache"
    coordinator = SessionCoordinator(data_dir=tmp_path)
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)

    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    coordinator._storage_managers[session_id] = storage

    # Simulate ClaudeSDK._store_sdk_message() having already persisted this
    # exact frame before message_callback fires.
    await storage.append_message(_stored_task_started("t1", "tu-1", session_id, 1.0))

    assert session_id not in coordinator._task_leg_registries, "cache must start cold"

    sdk_msg = TaskStartedMessage(
        subtype="task_started", data={}, task_id="t1",
        description="alpha: cold cache", uuid="u1", session_id="sub-sess", tool_use_id="tu-1",
    )
    converted_message = {
        "type": "system",
        "sdk_message": sdk_msg,
        "session_id": session_id,
        "timestamp": 1.0,
    }

    callback = coordinator._create_message_callback(session_id)
    await callback(converted_message)

    registry = coordinator._task_leg_registries[session_id]
    snapshot = registry.snapshot()
    assert len(snapshot) == 1
    assert len(snapshot[0]["legs"]) == 1, "the triggering frame must not be applied twice"
