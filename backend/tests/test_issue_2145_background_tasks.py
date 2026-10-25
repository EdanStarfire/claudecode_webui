"""Issue #2145: latest-snapshot background-task state (hydration, live, clearing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import SystemMessage

from backend.background_tasks import latest_background_tasks, parse_background_tasks
from backend.session_manager import SessionState

FIXTURE = Path(__file__).parent / "fixtures" / "background_tasks_samples.json"
SAMPLES = json.loads(FIXTURE.read_text())
APP_SID = "app-session"


def _sample(label: str, nth: int = 0) -> dict:
    return [s for s in SAMPLES if s["_sample"].strip() == label][nth]


def _bg(tasks: list | None, *, top_level: bool = True) -> dict:
    init = {"type": "system", "subtype": "background_tasks_changed", "session_id": "cli-id"}
    if tasks is not None:
        init["tasks"] = tasks
    rec = {
        "type": "system",
        "session_id": APP_SID,
        "metadata": {"subtype": "background_tasks_changed", "init_data": init},
    }
    if top_level:
        rec["subtype"] = "background_tasks_changed"
    return rec


def _marker(subtype: str, *, top_level: bool = True) -> dict:
    rec = {"type": "system", "session_id": APP_SID, "metadata": {"subtype": subtype}}
    if top_level:
        rec["subtype"] = subtype
    return rec


T1 = {"task_id": "t1", "task_type": "local_bash", "description": "one"}
T2 = {"task_id": "t2", "task_type": "local_agent", "description": "two"}


def test_parse_normalizes_and_is_tolerant():
    s7 = _sample("S7")
    parsed = parse_background_tasks(s7["metadata"])
    assert parsed and set(parsed[0]) == {"task_id", "task_type", "description"}
    assert parse_background_tasks({"init_data": {"tasks": "nope"}}) == []
    assert parse_background_tasks({}) == []
    assert parse_background_tasks(None) == []
    assert parse_background_tasks(
        {"init_data": {"tasks": [{"description": "no id"}, "junk", T1]}}
    ) == [T1]


def test_parse_every_production_sample_without_raising():
    for sample in SAMPLES:
        tasks = parse_background_tasks(sample["metadata"])
        raw = sample["metadata"]["init_data"]["tasks"]
        assert [t["task_id"] for t in tasks] == [t["task_id"] for t in raw]


def test_samples_unknown_task_type_and_cli_session_id_not_used():
    assert _sample("S6")["metadata"]["init_data"]["tasks"][0]["task_type"] == "local_workflow"
    for s in SAMPLES:
        assert s["metadata"]["init_data"]["session_id"] != s["session_id"]


def test_hydration_returns_last_snapshot_replace_semantics():
    assert latest_background_tasks([_bg([T1]), _bg([T1, T2]), _bg([T2])]) == [T2]


def test_empty_snapshot_means_empty():
    assert latest_background_tasks([_bg([T1]), _bg([])]) == []
    s8 = _sample("S8")
    assert latest_background_tasks([s8]) == []


def test_missing_tasks_key_treated_as_empty():
    assert latest_background_tasks([_bg([T1]), _bg(None)]) == []


def test_snapshots_before_client_launched_ignored():
    assert latest_background_tasks([_bg([T1]), _marker("client_launched")]) == []
    assert latest_background_tasks(
        [_bg([T1]), _marker("client_launched"), _bg([T2])]
    ) == [T2]


def test_session_failed_is_a_boundary():
    # S2: 16 tasks, then session_failed, no later snapshot -> empty
    s2 = _sample("S2")
    assert len(parse_background_tasks(s2["metadata"])) == 16
    assert latest_background_tasks([s2, _marker("session_failed")]) == []


def test_both_record_layouts():
    assert latest_background_tasks([_bg([T1], top_level=False)]) == [T1]
    assert latest_background_tasks(
        [_bg([T1], top_level=False), _marker("client_launched", top_level=False)]
    ) == []
    # S4 sample keeps subtype at top level only in some layouts
    s4 = _sample("S4")
    assert latest_background_tasks([s4]) == parse_background_tasks(s4["metadata"])


def test_pause_style_relisting_survives():
    # S4: awaiting_permission does not clear; re-listed task stays.
    assert latest_background_tasks(
        [_bg([T1]), _marker("awaiting_permission"), _bg([T1])]
    ) == [T1]
    assert latest_background_tasks([_bg([T1]), _marker("awaiting_permission")]) == [T1]


def test_non_system_records_ignored():
    assert latest_background_tasks([{"type": "assistant", "subtype": "client_launched"}, _bg([T1])]) == [T1]


# ---------------------------------------------------------------------------
# Coordinator integration
# ---------------------------------------------------------------------------


async def _coordinator(tmp_path, records):
    import backend.session_coordinator as sc_module
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    session_dir = tmp_path / "sessions" / APP_SID
    session_dir.mkdir(parents=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    for rec in records:
        await storage.append_message(rec)
    coordinator._storage_managers[APP_SID] = storage
    return coordinator


def _alive(coordinator, state=SessionState.ACTIVE):
    coordinator._active_sdks[APP_SID] = object()

    class _Info:
        pass

    info = _Info()
    info.state = state

    async def _get(_sid):
        return info

    coordinator.session_manager.get_session_info = _get


@pytest.mark.asyncio
async def test_endpoint_returns_hydrated_snapshot_and_caches(tmp_path):
    coordinator = await _coordinator(tmp_path, [_marker("client_launched"), _bg([T1, T2])])
    _alive(coordinator)
    assert await coordinator.get_background_tasks(APP_SID) == {
        "session_id": APP_SID,
        "tasks": [T1, T2],
    }
    assert coordinator._background_tasks[APP_SID] == [T1, T2]


@pytest.mark.asyncio
async def test_endpoint_empty_when_process_not_alive(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    _alive(coordinator)
    coordinator._active_sdks.pop(APP_SID)
    assert (await coordinator.get_background_tasks(APP_SID))["tasks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.ERROR, SessionState.TERMINATED])
async def test_endpoint_empty_for_dead_states(tmp_path, state):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    _alive(coordinator, state)
    assert (await coordinator.get_background_tasks(APP_SID))["tasks"] == []


@pytest.mark.asyncio
async def test_endpoint_returns_snapshot_for_paused_session(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    _alive(coordinator, SessionState.PAUSED)
    assert (await coordinator.get_background_tasks(APP_SID))["tasks"] == [T1]


def _live_msg(subtype: str, data: dict | None = None) -> dict:
    return {
        "type": "system",
        "subtype": subtype,
        "sdk_message": SystemMessage(subtype=subtype, data=data or {}),
        "session_id": APP_SID,
        "timestamp": 1.0,
    }


def _data(tasks):
    return {"type": "system", "subtype": "background_tasks_changed", "tasks": tasks}


@pytest.mark.asyncio
async def test_live_cold_cache_does_not_double_apply(tmp_path):
    # Storage already holds the triggering frame (ClaudeSDK persists before callback).
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    assert APP_SID not in coordinator._background_tasks
    callback = coordinator._create_message_callback(APP_SID)
    await callback(_live_msg("background_tasks_changed", _data([T1])))
    assert coordinator._background_tasks[APP_SID] == [T1]


@pytest.mark.asyncio
async def test_live_warm_cache_replaces_not_appends(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = [T1]
    callback = coordinator._create_message_callback(APP_SID)
    await callback(_live_msg("background_tasks_changed", _data([T2])))
    assert coordinator._background_tasks[APP_SID] == [T2]
    await callback(_live_msg("background_tasks_changed", _data([])))
    assert coordinator._background_tasks[APP_SID] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("subtype", ["client_launched", "session_failed"])
async def test_live_boundary_frames_clear_cache(tmp_path, subtype):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = [T1]
    callback = coordinator._create_message_callback(APP_SID)
    await callback(_live_msg(subtype))
    assert coordinator._background_tasks[APP_SID] == []


@pytest.mark.asyncio
async def test_pause_style_frame_does_not_clear(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = [T1]
    callback = coordinator._create_message_callback(APP_SID)
    await callback(_live_msg("status", {"type": "system", "subtype": "status"}))
    assert coordinator._background_tasks[APP_SID] == [T1]


@pytest.mark.asyncio
async def test_transition_to_error_clears_cache(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = [T1]
    await coordinator._transition_to_error(APP_SID, "boom")
    assert APP_SID not in coordinator._background_tasks


@pytest.mark.asyncio
async def test_teardown_clears_cache(tmp_path):
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = [T1]

    class _Sdk:
        async def terminate(self):
            pass

        async def disconnect(self):
            pass

    coordinator._active_sdks[APP_SID] = _Sdk()
    await coordinator._session_launcher.teardown_active_sdk(APP_SID, graceful=False)
    assert APP_SID not in coordinator._background_tasks


@pytest.mark.asyncio
async def test_live_canonical_record_dict_warm_cache(tmp_path):
    """Production delivers MessageRecord.to_dict() (metadata nested, init_data intact), not an
    sdk_message — the parser's dict path drops init_data, so the nested metadata must be used."""
    coordinator = await _coordinator(tmp_path, [_bg([T1])])
    coordinator._background_tasks[APP_SID] = []
    callback = coordinator._create_message_callback(APP_SID)
    await callback({**_bg([T1, T2]), "timestamp": 1.0})
    assert coordinator._background_tasks[APP_SID] == [T1, T2]
