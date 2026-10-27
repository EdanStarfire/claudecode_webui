"""Issue #2030 (stage 1a, §3.6): SDK task-list snapshot reducer, hydration and live feed."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.message_history_reader import WindowBudget
from backend.task_list import TaskListState

CASES_FILE = Path(__file__).parent / "fixtures" / "task_reducer_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
SID = "task-session"


def _reduce(records: list[dict]) -> list[dict]:
    state = TaskListState()
    for record in records:
        state.apply_tool_call(record)
    return state.snapshot()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_reducer_cases(case):
    assert _reduce(case["records"]) == case["expected"]


def test_shared_case_file_covers_required_scenarios():
    names = {c["name"] for c in CASES}
    for required in (
        "create_single", "update_fields_and_status", "deleted_removes_task",
        "add_blocks_union_dedup", "metadata_merge", "errors_are_skipped",
        "update_for_never_created_id_makes_subjectless_task",
    ):
        assert required in names


def test_non_task_and_non_tool_call_records_ignored():
    state = TaskListState()
    assert state.apply_tool_call({"type": "assistant", "name": "TaskCreate", "status": "completed"}) is False
    assert state.apply_tool_call({"type": "tool_call", "name": "Bash", "status": "completed"}) is False
    assert state.snapshot() == []


def test_non_iterable_add_blocks_aborts_record_but_keeps_partial_effects():
    # browser: `[...task.blocks, ...true]` throws after subject was already assigned
    records = [
        {"type": "tool_call", "tool_use_id": "a", "name": "TaskCreate", "status": "completed",
         "input": {"subject": "A"}, "result": "Task #1 created"},
        {"type": "tool_call", "tool_use_id": "b", "name": "TaskUpdate", "status": "completed",
         "input": {"taskId": "1", "subject": "A2", "addBlocks": True}, "result": "ok"},
    ]
    assert _reduce(records)[0]["subject"] == "A2"


def test_snapshot_returns_copies():
    state = TaskListState()
    state.apply_tool_call({"type": "tool_call", "tool_use_id": "a", "name": "TaskCreate",
                           "status": "completed", "input": {"subject": "A"}, "result": "Task #1"})
    state.snapshot()[0]["subject"] = "mutated"
    assert state.snapshot()[0]["subject"] == "A"


# --------------------------------------------------------------------------- coordinator


def _call(i: int, name: str, inp: dict, result: str = "ok", status: str = "completed") -> dict:
    return {
        "type": "tool_call", "tool_use_id": f"tu{i}", "name": name, "status": status,
        "input": inp, "result": result, "session_id": SID,
    }


def _noise(i: int) -> dict:
    kind = "user" if i % 50 == 0 else "assistant"
    return {"type": kind, "message_id": f"n{i}", "content": f"noise {i}", "session_id": SID}


async def _coordinator(tmp_path, records):
    import backend.session_coordinator as sc_module
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    session_dir = tmp_path / "sessions" / SID
    session_dir.mkdir(parents=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    for rec in records:
        await storage.append_message(dict(rec))
    coordinator._storage_managers[SID] = storage
    return coordinator, storage


CREATE = _call(1, "TaskCreate", {"subject": "A"}, "Task #1 created")
UPDATE = _call(2, "TaskUpdate", {"taskId": "1", "status": "completed"}, "Updated task #1")


@pytest.mark.asyncio
async def test_endpoint_hydrates_from_storage_and_caches(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [_noise(0), CREATE, _noise(1), UPDATE])
    result = await coordinator.get_session_tasks(SID)
    assert result["session_id"] == SID
    assert [(t["id"], t["subject"], t["status"]) for t in result["tasks"]] == [("1", "A", "completed")]
    assert SID in coordinator._session_query._task_lists


@pytest.mark.asyncio
async def test_hydration_equals_live_feed_equivalence(tmp_path):
    records = [CREATE, _call(3, "TaskCreate", {"subject": "B"}, "Task #2 created"), UPDATE]
    cold, _ = await _coordinator(tmp_path / "cold", records)
    hydrated = await cold.get_session_tasks(SID)

    warm, _ = await _coordinator(tmp_path / "warm", [])
    await warm.get_session_tasks(SID)  # warm the (empty) cache first
    for rec in records:
        warm._session_query.feed_task_tool_call(SID, rec)
    assert (await warm.get_session_tasks(SID)) == hydrated


@pytest.mark.asyncio
async def test_double_count_guard_cold_cache_feed_is_not_applied(tmp_path):
    # storage already holds the record (feed runs after the durable write)
    coordinator, _ = await _coordinator(tmp_path, [CREATE])
    coordinator._session_query.feed_task_tool_call(SID, CREATE)  # cold: ignored
    assert SID not in coordinator._session_query._task_lists
    result = await coordinator.get_session_tasks(SID)
    assert len(result["tasks"]) == 1
    # and a duplicate warm feed of the same tool_use_id is also idempotent
    coordinator._session_query.feed_task_tool_call(SID, CREATE)
    assert len((await coordinator.get_session_tasks(SID))["tasks"]) == 1


@pytest.mark.asyncio
async def test_record_appended_while_hydrating_is_not_lost(tmp_path):
    import asyncio

    coordinator, storage = await _coordinator(tmp_path, [CREATE])
    query = coordinator._session_query
    real_iter = type(storage.reader).iter_matching

    gate = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_iter(self, prefilters):
        yield from real_iter(self, prefilters)
        # hydration has consumed the file; now let a live record land, then finish
        loop.call_soon_threadsafe(gate.set)
        import time
        time.sleep(0.2)

    type(storage.reader).iter_matching = slow_iter
    try:
        task = asyncio.ensure_future(coordinator.get_session_tasks(SID))
        await gate.wait()
        await storage.append_message(dict(UPDATE))
        query.feed_task_tool_call(SID, UPDATE)  # arrives mid-hydration -> buffered
        result = await task
    finally:
        type(storage.reader).iter_matching = real_iter
    assert [(t["id"], t["status"]) for t in result["tasks"]] == [("1", "completed")]


@pytest.mark.asyncio
async def test_tool_call_storage_path_feeds_warm_cache(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [])
    await coordinator.get_session_tasks(SID)  # warm
    await coordinator._tool_call_tracker._write_tool_call_update(storage, dict(CREATE), "tu1")
    assert [t["id"] for t in (await coordinator.get_session_tasks(SID))["tasks"]] == ["1"]


@pytest.mark.asyncio
async def test_cleared_on_history_wipe_but_retained_across_process_death(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [CREATE])
    await coordinator.get_session_tasks(SID)
    # process death / client_launched style resets leave the SDK task list intact
    coordinator._background_tasks.pop(SID, None)
    assert SID in coordinator._session_query._task_lists
    coordinator._clear_task_list(SID)
    assert SID not in coordinator._session_query._task_lists


@pytest.mark.asyncio
async def test_unstarted_session_returns_empty_ephemeral(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    assert (await coordinator.get_session_tasks("nope"))["tasks"] == []
    assert "nope" not in coordinator._session_query._task_lists


@pytest.mark.asyncio
async def test_task_created_far_outside_any_window_is_in_snapshot(tmp_path):
    early = [CREATE] + [_noise(i) for i in range(2000)]
    late_update = _call(5, "TaskUpdate", {"taskId": "1", "status": "in_progress"}, "Updated")
    coordinator, storage = await _coordinator(tmp_path, early + [late_update])
    window = await storage.reader.read_tail(WindowBudget(max_records=60))
    assert all(r.get("name") != "TaskCreate" for r in window.records)  # create is outside it
    tasks = (await coordinator.get_session_tasks(SID))["tasks"]
    assert [(t["id"], t["subject"], t["status"]) for t in tasks] == [("1", "A", "in_progress")]


@pytest.mark.asyncio
async def test_snapshot_readable_after_storage_manager_dropped(tmp_path):
    """terminate drops the storage manager; the task list is conversation state and
    must still be served from messages.jsonl."""
    coordinator, _ = await _coordinator(tmp_path, [CREATE])
    del coordinator._storage_managers[SID]
    coordinator.session_manager.get_session_directory = AsyncMock(
        return_value=tmp_path / "sessions" / SID
    )
    tasks = (await coordinator.get_session_tasks(SID))["tasks"]
    assert [t["id"] for t in tasks] == ["1"]
    coordinator.session_manager.get_session_directory = AsyncMock(return_value=None)
    coordinator._clear_task_list(SID)
    assert (await coordinator.get_session_tasks(SID))["tasks"] == []


@pytest.mark.asyncio
async def test_reset_during_hydration_does_not_serve_or_cache_pre_reset_state(tmp_path):
    import asyncio
    import time

    coordinator, storage = await _coordinator(tmp_path, [CREATE])
    real_iter = type(storage.reader).iter_matching
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_iter(self, prefilters):
        yield from real_iter(self, prefilters)
        loop.call_soon_threadsafe(entered.set)
        time.sleep(0.2)

    type(storage.reader).iter_matching = slow_iter
    try:
        first = asyncio.ensure_future(coordinator.get_session_tasks(SID))
        await entered.wait()
        coordinator._clear_task_list(SID)  # history wipe
        open(storage.messages_file, "w").close()
        type(storage.reader).iter_matching = real_iter
        after = await coordinator.get_session_tasks(SID)
        await first
    finally:
        type(storage.reader).iter_matching = real_iter
    assert after["tasks"] == []
    assert SID not in coordinator._session_query._task_lists or not coordinator._session_query._task_lists[SID].snapshot()
