"""Issue #2030 (stage 2 PR A): history index hydration / live feed / invalidation / LRU /
waiter cap / per-session ordering, the /history_index routes, and the existing snapshot
hydrators now running on the dedicated cold-scan pool."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.history_index import (
    HistoryIndexBuildingError,
    HistoryIndexState,
    reduce_records,
)
from backend.message_history_reader import FileMessageHistoryReader

SID = "idx-session"
CONT = "This session is being continued from a previous conversation that ran out"


def _user(n, content=None, **meta):
    rec = {"type": "user", "message_id": f"u{n}", "content": content or f"turn {n}", "session_id": SID}
    if meta:
        rec["metadata"] = meta
    return rec


def _asst(n):
    return {"type": "assistant", "message_id": f"a{n}", "content": f"reply {n}", "session_id": SID,
            "metadata": {"tool_uses": []}}


def _status(n, status):
    return {"type": "system", "message_id": f"s{n}", "content": "System message", "session_id": SID,
            "metadata": {"subtype": "status", "init_data": {"status": status}}}


def _boundary(n):
    return {"type": "system", "message_id": f"b{n}", "content": "compacted", "session_id": SID,
            "metadata": {"subtype": "compact_boundary"}}


def _compaction(n):
    return [_status(f"{n}a", "compacting"), _status(f"{n}b", None), _boundary(n),
            {"type": "user", "message_id": f"c{n}", "content": CONT, "session_id": SID}]


def _history(turns=6):
    out = []
    for i in range(turns):
        out += [_user(i), _asst(i), _user(f"r{i}", "Tool results: 1 result", has_tool_results=True)]
        if i == 2:
            out += _compaction(i)
    return out


async def _coordinator(tmp_path, records, *, sid=SID, **perf):
    import backend.session_coordinator as sc_module
    from backend.config_manager import PerformanceConfig
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    if perf:
        from backend.session_query import SessionQuery

        coordinator._session_query.shutdown()
        coordinator._session_query = SessionQuery(
            coordinator=coordinator, performance=PerformanceConfig(**perf)
        )
    session_dir = tmp_path / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    coordinator._apply_history_index_feed(storage, sid)
    for rec in records:
        await storage.append_message(dict(rec))
    coordinator._storage_managers[sid] = storage
    return coordinator, storage


def _expected(records):
    state = reduce_records(records)
    return state.turn_ids(), state.compactions


@pytest.mark.asyncio
async def test_hydrate_returns_turns_and_compactions_with_cursors(tmp_path):
    records = _history()
    coordinator, storage = await _coordinator(tmp_path, records)
    payload = await coordinator.get_history_index(SID)
    turns, compactions = _expected(records)
    assert [t[1] for t in payload["turns"]] == turns
    assert payload["compactions"] == compactions == ["s2a"]
    assert set(payload) == {"history_id", "total_bytes", "turns", "compactions"}
    assert payload["total_bytes"] == storage.messages_file.stat().st_size
    raw = storage.messages_file.read_bytes()
    for cursor, message_id, hint in payload["turns"]:
        offset = int(cursor[2:])
        assert hint == offset
        assert json.loads(raw[offset:].split(b"\n", 1)[0])["message_id"] == message_id


@pytest.mark.asyncio
async def test_live_feed_equals_cold_hydration(tmp_path):
    first = _history(3)
    more = _history(5)[len(first):] + [_user("tail")]
    coordinator, storage = await _coordinator(tmp_path, first)
    await coordinator.get_history_index(SID)  # warm
    for rec in more:
        await storage.append_message(dict(rec))  # on_append feeds the warm index
    warm = await coordinator.get_history_index(SID)
    coordinator._session_query.drop_history_index(SID)  # same file, re-hydrated from scratch
    cold = await coordinator.get_history_index(SID)
    assert warm["turns"] == cold["turns"]
    assert warm["compactions"] == cold["compactions"]
    assert warm["total_bytes"] == cold["total_bytes"]


@pytest.mark.asyncio
async def test_compaction_group_split_across_hydrate_and_feed(tmp_path):
    group = _compaction(9)
    coordinator, storage = await _coordinator(tmp_path, [_user(0), *group[:2]])
    await coordinator.get_history_index(SID)  # hydrated with an in-progress group prefix
    for rec in group[2:]:
        await storage.append_message(dict(rec))
    await storage.append_message(_user(1))
    payload = await coordinator.get_history_index(SID)
    assert payload["compactions"] == ["s9a"]
    assert [t[1] for t in payload["turns"]] == ["u0", "u1"]


@pytest.mark.asyncio
async def test_record_appended_mid_scan_is_counted_once(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0), _asst(0)])
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow(self, prefilters, **kwargs):
        yield from real(self, prefilters, **kwargs)
        loop.call_soon_threadsafe(gate.set)  # the scan has consumed the file
        time.sleep(0.2)

    reader_cls.iter_records_with_offsets = slow
    try:
        task = asyncio.ensure_future(coordinator.get_history_index(SID))
        await gate.wait()
        await storage.append_message(_user(1))  # lands mid-scan: buffered, not in the scan
        result = await task
    finally:
        reader_cls.iter_records_with_offsets = real
    assert [t[1] for t in result["turns"]] == ["u0", "u1"]


@pytest.mark.asyncio
async def test_record_read_by_scan_and_also_fed_is_not_double_counted(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow(self, prefilters, **kwargs):
        loop.call_soon_threadsafe(started.set)
        time.sleep(0.1)  # append happens before the file is read
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = slow
    try:
        task = asyncio.ensure_future(coordinator.get_history_index(SID))
        await started.wait()
        await storage.append_message(_user(1))  # scan will read it AND it is buffered
        result = await task
    finally:
        reader_cls.iter_records_with_offsets = real
    assert [t[1] for t in result["turns"]] == ["u0", "u1"]


@pytest.mark.asyncio
async def test_feed_gap_drops_the_warm_index(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    await coordinator.get_history_index(SID)
    query = coordinator._session_query
    assert SID in query._history_indexes
    query.feed_history_index(SID, _user(5), 10_000, 10_100)  # a write the index never saw
    assert SID not in query._history_indexes
    payload = await coordinator.get_history_index(SID)  # lazily rescans
    assert [t[1] for t in payload["turns"]] == ["u0"]


@pytest.mark.asyncio
async def test_clear_on_reset_drops_index_and_abandons_inflight_scan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history())
    query = coordinator._session_query
    await coordinator.get_history_index(SID)
    coordinator._clear_task_list(SID)  # the reset / delete / archive-clear call
    assert SID not in query._history_indexes

    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    started = threading.Event()
    release = threading.Event()

    def blocked(self, prefilters, **kwargs):
        started.set()
        release.wait(5)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = blocked
    try:
        task = asyncio.ensure_future(coordinator.get_history_index(SID))
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        coordinator._clear_task_list(SID)  # reset mid-scan
        release.set()
        with pytest.raises(HistoryIndexBuildingError):
            await task
    finally:
        reader_cls.iter_records_with_offsets = real
    assert SID not in query._history_indexes  # the abandoned scan never cached
    assert query._cold_scans.inflight_count == 0


@pytest.mark.asyncio
async def test_stop_drops_index_and_abandons_inflight_index_scan_only(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, _history())
    query = coordinator._session_query
    await coordinator.get_history_index(SID)
    query.drop_history_index(SID, abandon_scans=True)  # what terminate does
    assert SID not in query._history_indexes


@pytest.mark.asyncio
async def test_history_id_change_forces_rescan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0), _user(1)])
    first = await coordinator.get_history_index(SID)
    # file replaced (new inode, different first record): the cached state is stale
    path = storage.messages_file
    replacement = path.with_suffix(".new")
    replacement.write_text(json.dumps(_user(7)) + "\n", encoding="utf-8")
    replacement.replace(path)
    second = await coordinator.get_history_index(SID)
    assert second["history_id"] != first["history_id"]
    assert [t[1] for t in second["turns"]] == ["u7"]


@pytest.mark.asyncio
async def test_truncated_file_forces_rescan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0), _user(1)])
    await coordinator.get_history_index(SID)
    storage.messages_file.write_text("")
    payload = await coordinator.get_history_index(SID)
    assert payload["turns"] == [] and payload["total_bytes"] == 0


@pytest.mark.asyncio
async def test_lru_cap_evicts_least_recently_requested(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [_user(0)], history_index_cache_max=2)
    for sid in ("s-b", "s-c"):
        await _coordinator_add_session(coordinator, tmp_path, sid)
    query = coordinator._session_query
    await coordinator.get_history_index(SID)
    await coordinator.get_history_index("s-b")
    await coordinator.get_history_index(SID)  # SID is now most recent
    await coordinator.get_history_index("s-c")  # evicts s-b
    assert list(query._history_indexes) == [SID, "s-c"]
    assert (await coordinator.get_history_index("s-b"))["turns"]  # evicted: lazily rescans


async def _coordinator_add_session(coordinator, tmp_path, sid):
    import backend.session_coordinator as sc_module

    session_dir = tmp_path / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    coordinator._apply_history_index_feed(storage, sid)
    await storage.append_message({"type": "user", "message_id": f"{sid}-u", "content": "x"})
    coordinator._storage_managers[sid] = storage


@pytest.mark.asyncio
async def test_waiter_cap_503_then_scan_completes_and_caches(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history(), history_index_wait_s=0.1)
    query = coordinator._session_query
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    release = threading.Event()

    def blocked(self, prefilters, **kwargs):
        release.wait(5)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = blocked
    try:
        t0 = time.monotonic()
        with pytest.raises(HistoryIndexBuildingError):
            await coordinator.get_history_index(SID)
        assert time.monotonic() - t0 < 2.0
        release.set()
        # the scan kept running: a retry joins it / hits the cache
        query._history_index_wait_s = 5.0
        payload = await coordinator.get_history_index(SID)
        assert payload["turns"]
    finally:
        reader_cls.iter_records_with_offsets = real
    assert SID in query._history_indexes


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_scan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history())
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    calls = []

    def counting(self, prefilters, **kwargs):
        calls.append(1)
        time.sleep(0.1)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = counting
    try:
        results = await asyncio.gather(*[coordinator.get_history_index(SID) for _ in range(6)])
    finally:
        reader_cls.iter_records_with_offsets = real
    assert calls == [1]
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_index_scan_chains_behind_same_session_snapshot_scans(tmp_path):
    coordinator, storage = await _coordinator(
        tmp_path,
        _history() + [{"type": "tool_call", "tool_use_id": "t", "name": "TaskCreate",
                       "status": "completed", "input": {"subject": "A"}, "result": "Task #1"}],
    )
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    events = []
    release = threading.Event()

    def traced(self, prefilters, **kwargs):
        kind = "index" if '"user"' in prefilters else "tasks"
        events.append(f"{kind}-start")
        if kind == "tasks":
            release.wait(5)
        yield from real(self, prefilters, **kwargs)
        events.append(f"{kind}-end")

    reader_cls.iter_records_with_offsets = traced
    try:
        tasks = asyncio.ensure_future(coordinator.get_session_tasks(SID))
        for _ in range(200):
            if events:
                break
            await asyncio.sleep(0.01)
        index = asyncio.ensure_future(coordinator.get_history_index(SID))
        await asyncio.sleep(0.2)
        assert events == ["tasks-start"]  # the index scan has not started
        release.set()
        await asyncio.gather(tasks, index)
    finally:
        reader_cls.iter_records_with_offsets = real
    assert events == ["tasks-start", "tasks-end", "index-start", "index-end"]


@pytest.mark.asyncio
async def test_snapshot_hydrators_run_on_the_cold_scan_pool_not_the_default_executor(tmp_path):
    coordinator, storage = await _coordinator(
        tmp_path,
        [{"type": "tool_call", "tool_use_id": "t", "name": "TaskCreate", "status": "completed",
          "input": {"subject": "A"}, "result": "Task #1"}, _user(0)],
    )
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    threads: dict[str, str] = {}

    def spy(self, prefilters, **kwargs):
        threads[prefilters[0]] = threading.current_thread().name
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = spy
    try:
        await coordinator.get_session_tasks(SID)
        await coordinator.get_background_agents(SID)
        coordinator._active_sdks[SID] = object()
        coordinator.session_manager.get_session_info = AsyncMock(
            return_value=SimpleNamespace(state=__import__(
                "backend.session_manager", fromlist=["SessionState"]).SessionState.ACTIVE)
        )
        await coordinator.get_background_tasks(SID)
        await coordinator.get_history_index(SID)
        window = await coordinator.get_session_messages(SID, window="tail")
    finally:
        reader_cls.iter_records_with_offsets = real
    assert len(threads) == 4
    assert all(name.startswith("cold-scan") for name in threads.values()), threads
    assert window["messages"]  # window/tail keeps using the default executor
    # (a window read does not go through the cold pool: nothing left in flight)
    assert coordinator._session_query._cold_scans.inflight_count == 0


@pytest.mark.asyncio
async def test_snapshot_flights_are_single_flight_per_kind(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    reader_cls = type(storage.reader)
    real = reader_cls.iter_records_with_offsets
    calls = []

    def counting(self, prefilters, **kwargs):
        calls.append(1)
        time.sleep(0.1)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_records_with_offsets = counting
    try:
        await asyncio.gather(*[coordinator.get_background_agents(SID) for _ in range(4)])
    finally:
        reader_cls.iter_records_with_offsets = real
    assert calls == [1]  # was one scan per caller before stage 2


@pytest.mark.asyncio
async def test_reset_during_agents_scan_does_not_cache_pre_reset_registry(tmp_path):
    coordinator, storage = await _coordinator(
        tmp_path,
        [{"type": "system", "message_id": "x", "metadata": {"subtype": "task_started",
          "task_id": "t1"}}],
    )
    reader_cls = type(storage.reader)
    real = reader_cls.iter_matching
    started = threading.Event()
    release = threading.Event()

    def blocked(self, prefilters, **kwargs):
        started.set()
        release.wait(5)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_matching = blocked
    try:
        task = asyncio.ensure_future(coordinator.get_background_agents(SID))
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        coordinator._clear_task_list(SID)
        release.set()
        result = await task
    finally:
        reader_cls.iter_matching = real
    assert result["agents"] == []
    assert SID not in coordinator._task_leg_registries


@pytest.mark.asyncio
async def test_delete_session_clears_index_for_session_without_storage_manager(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [_user(0)])
    await coordinator.get_history_index(SID)
    coordinator._storage_managers.pop(SID)  # never-started / terminated session
    coordinator._clear_task_list(SID)
    assert SID not in coordinator._session_query._history_indexes


def test_state_memory_is_compact():
    state = HistoryIndexState("h")
    for i in range(5000):
        state.apply(i * 100, i * 100 + 100, {"type": "user", "message_id": f"{i:08x}-0000-4000-8000-000000000000"})
    assert state.turn_count == 5000
    assert state.approx_bytes() / 5000 < 200


# ------------------------------------------------------------------ archive + routes


def _archive_file(tmp_path, records):
    path = tmp_path / "archive" / "messages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_archive_index_parity_with_live_and_fingerprint_cache(tmp_path):
    records = _history()
    coordinator, _ = await _coordinator(tmp_path, records)
    live = await coordinator.get_history_index(SID)
    path = _archive_file(tmp_path, records)
    query = coordinator._session_query
    archived = await query.get_archive_history_index(SID, "20260101", path)
    assert [t[1] for t in archived["turns"]] == [t[1] for t in live["turns"]]
    assert archived["compactions"] == live["compactions"]
    key = f"{SID}/archive/20260101"
    assert key in query._history_indexes
    cached = query._history_indexes[key]
    again = await query.get_archive_history_index(SID, "20260101", path)
    assert query._history_indexes[key] is cached and again == archived
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(_user("z")) + "\n", encoding="utf-8")
    refreshed = await query.get_archive_history_index(SID, "20260101", path)
    assert refreshed["turns"][-1][1] == "uz"


@pytest.mark.asyncio
async def test_archive_filters_tool_result_only_array_rows(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [])
    path = _archive_file(
        tmp_path,
        [_user(0), {"type": "user", "message_id": "ua", "content": [{"type": "tool_result"}]}],
    )
    payload = await coordinator._session_query.get_archive_history_index(SID, "a", path)
    assert [t[1] for t in payload["turns"]] == ["u0"]


@pytest.mark.asyncio
async def test_missing_archive_yields_empty_index(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [])
    payload = await coordinator._session_query.get_archive_history_index(
        SID, "none", tmp_path / "nope" / "messages.jsonl"
    )
    assert payload["turns"] == [] and payload["compactions"] == [] and payload["total_bytes"] == 0


def _app(coordinator):
    from backend.application_service import ApplicationService
    from backend.routers.archives import build_router as archives_router
    from backend.routers.sessions import build_router as sessions_router

    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.get_session_exists = AsyncMock(return_value=True)
    webui.service.validate_project_exists = AsyncMock(return_value=True)
    app = FastAPI()
    app.include_router(sessions_router(webui))
    app.include_router(archives_router(webui))
    return app


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_history_index_route_ok_503_and_404(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, _history())
    app = _app(coordinator)
    resp = await _get(app, f"/api/sessions/{SID}/history_index")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"history_id", "total_bytes", "turns", "compactions"}
    assert body["turns"] and body["compactions"] == ["s2a"]

    coordinator.get_history_index = AsyncMock(side_effect=HistoryIndexBuildingError(SID))
    resp = await _get(app, f"/api/sessions/{SID}/history_index")
    assert resp.status_code == 503 and resp.json()["detail"] == "index_building"
    assert resp.headers["retry-after"]

    from backend.application_service import ApplicationService  # 404 path
    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.get_session_exists = AsyncMock(return_value=False)
    from backend.routers.sessions import build_router

    missing = FastAPI()
    missing.include_router(build_router(webui))
    assert (await _get(missing, f"/api/sessions/{SID}/history_index")).status_code == 404


@pytest.mark.asyncio
async def test_archive_history_index_route(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [])
    archive_dir = tmp_path / "archives" / SID / "20260101"
    archive_dir.mkdir(parents=True)
    records = _history()
    (archive_dir / "messages.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    coordinator.legion_system = SimpleNamespace(
        archive_manager=SimpleNamespace(
            archive_messages_path=lambda s, a: archive_dir / "messages.jsonl"
        )
    )
    app = _app(coordinator)
    resp = await _get(app, f"/api/projects/p1/archives/{SID}/20260101/history_index")
    assert resp.status_code == 200
    assert [t[1] for t in resp.json()["turns"]] == reduce_records(records).turn_ids()


def test_archive_manager_path_helper_rejects_traversal(tmp_path):
    from backend.legion.archive_manager import ArchiveManager

    manager = ArchiveManager.__new__(ArchiveManager)
    manager._archives_dir = tmp_path
    assert manager.archive_messages_path("s", "a") == tmp_path / "s" / "a" / "messages.jsonl"
    for bad in ("..", "a/b", "", "."):
        with pytest.raises(ValueError):
            manager.archive_messages_path("s", bad)


@pytest.mark.asyncio
async def test_storage_append_records_byte_range(tmp_path):
    import backend.session_coordinator as sc_module

    session_dir = tmp_path / "sessions" / SID
    session_dir.mkdir(parents=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    seen = []

    async def hook(_s, _p, rec):
        seen.append((rec["message_id"], storage.last_append_range))

    storage.on_append.append(hook)
    for i in range(3):
        await storage.append_message({"type": "user", "message_id": f"é{i}", "content": "ü" * i})
    raw = storage.messages_file.read_bytes()
    assert seen[0][1][0] == 0 and seen[-1][1][1] == len(raw)
    for mid, (start, end) in seen:
        assert json.loads(raw[start:end])["message_id"] == mid
    assert [e for _, (_, e) in seen[:-1]] == [s for _, (s, _) in seen[1:]]


def test_reader_probe_and_missing_file(tmp_path):
    reader = FileMessageHistoryReader(tmp_path / "none.jsonl")
    history_id, size = reader.probe()
    assert size == 0 and history_id
    assert list(reader.iter_records_with_offsets(('"user"',))) == []


# ------------------------------------------------------------------ feed property / seam tests


def _random_stream(rng, n):
    """A record stream with compaction groups, interleavings, hidden users, big lines and
    unrelated records, written through the real writer."""
    out = []
    k = 0
    while len(out) < n:
        roll = rng.random()
        k += 1
        if roll < 0.25:
            out.append(_user(f"p{k}"))
        elif roll < 0.35:
            out.append(_user(f"h{k}", "Tool results: 1 result", has_tool_results=True))
        elif roll < 0.45:
            out.append(_asst(k))
        elif roll < 0.55:
            out.append({"type": "tool_call", "tool_use_id": f"t{k}", "name": "Bash",
                        "status": "completed", "result": "x" * rng.choice([10, 2000, 70000])})
        elif roll < 0.8:
            group = _compaction(k)
            if rng.random() < 0.3:  # interleave an unrelated record inside the group
                group.insert(rng.randint(1, 3), rng.choice([
                    _asst(f"i{k}"), _user(f"ic{k}", "Tool results: 2 results", has_tool_results=True),
                    {"type": "system", "message_id": f"hk{k}", "content": "h",
                                     "metadata": {"subtype": "hook_started"}},
                    {"type": "tool_call", "tool_use_id": f"ti{k}", "name": "Read", "status": "completed"}]))
            if rng.random() < 0.2:
                group = group[: rng.randint(1, len(group) - 1)]  # truncated group
            out.extend(group)
        elif roll < 0.85:
            out.append({"type": "system", "message_id": f"in{k}", "content": "System message",
                        "subtype": "init", "metadata": {"subtype": "init", "init_data": {}}})
        else:
            out.append(_user(f"c{k}", CONT))  # lone continuation
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(12))
async def test_live_fed_index_equals_cold_hydrated_index_through_real_writers(tmp_path, seed):
    import random

    rng = random.Random(seed)
    stream = _random_stream(rng, 150)
    cut = rng.randint(0, len(stream))
    coordinator, storage = await _coordinator(tmp_path, stream[:cut])
    query = coordinator._session_query
    await coordinator.get_history_index(SID)  # warm at an arbitrary point (maybe mid-group)
    for rec in stream[cut:]:
        await storage.append_message(dict(rec))
    warm = await coordinator.get_history_index(SID)
    assert SID in query._history_indexes  # still warm: served from the live feed, no rescan
    query.drop_history_index(SID)
    cold = await coordinator.get_history_index(SID)
    assert warm["turns"] == cold["turns"]  # ids, cursors and size hints (byte offsets)
    assert warm["compactions"] == cold["compactions"]
    assert warm["total_bytes"] == cold["total_bytes"] == storage.messages_file.stat().st_size
    expected = reduce_records(stream)
    assert [t[1] for t in cold["turns"]] == expected.turn_ids()
    assert cold["compactions"] == expected.compactions


@pytest.mark.asyncio
async def test_feed_coexists_with_audit_writer_and_never_raises_into_the_writer(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    audited = []

    async def audit(_s, _p, rec):
        audited.append(rec["message_id"])

    storage.on_append.append(audit)  # another consumer after ours
    await coordinator.get_history_index(SID)
    await storage.append_message(_user(1))
    assert audited == ["u1"]
    assert [t[1] for t in (await coordinator.get_history_index(SID))["turns"]] == ["u0", "u1"]

    def boom(*_a, **_k):
        raise RuntimeError("feed bug")

    coordinator._session_query.feed_history_index = boom
    await storage.append_message(_user(2))  # the writer must be unaffected
    assert audited == ["u1", "u2"]
    assert storage.messages_file.read_text(encoding="utf-8").count("\n") == 3


@pytest.mark.asyncio
async def test_feed_hook_is_registered_once_per_storage_manager(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [])
    before = len(storage.on_append)
    coordinator._apply_history_index_feed(storage, SID)
    coordinator._apply_audit_writer(storage, SID)
    assert len(storage.on_append) == before


@pytest.mark.asyncio
async def test_cold_cache_feed_only_buffers_while_scanning(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    query = coordinator._session_query
    await storage.append_message(_user(1))  # cold + nothing in flight: dropped (no state kept)
    assert SID not in query._history_indexes and SID not in query._history_index_pending


@pytest.mark.asyncio
async def test_request_during_delete_cleanup_does_not_recache(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history())
    query = coordinator._session_query
    real_cleanup = storage.cleanup

    async def slow_cleanup():
        await real_cleanup()
        await coordinator.get_history_index(SID)  # lands mid-delete
        await coordinator.get_session_tasks(SID)

    storage.cleanup = slow_cleanup
    await coordinator._cleanup_storage_manager(SID)
    assert SID not in query._history_indexes and SID not in query._task_lists


@pytest.mark.asyncio
async def test_warm_index_dropped_when_a_new_unhooked_storage_manager_appears(tmp_path):
    import backend.session_coordinator as sc_module

    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    await coordinator.get_history_index(SID)
    replacement = sc_module.DataStorageManager(storage.session_dir)  # e.g. restarted session
    await replacement.initialize()
    coordinator._storage_managers[SID] = replacement
    await replacement.append_message(_user(1))  # never fed the warm index
    payload = await coordinator.get_history_index(SID)
    assert [t[1] for t in payload["turns"]] == ["u0", "u1"]


@pytest.mark.asyncio
async def test_unterminated_tail_completed_externally_is_repaired_by_rescan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_user(0)])
    with open(storage.messages_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(_user(9)))  # complete JSON, newline not yet written
    first = await coordinator.get_history_index(SID)
    assert [t[1] for t in first["turns"]] == ["u0"]  # left to the live feed, not consumed
    with open(storage.messages_file, "a", encoding="utf-8") as f:
        f.write("\n")  # the record is completed by something other than the hooked writer
    await storage.append_message(_user(1))  # start > state.end: gap -> drop, lazily rescan
    assert [t[1] for t in (await coordinator.get_history_index(SID))["turns"]] == ["u0", "u9", "u1"]


# ------------------------------------------------------------------ live-path never waits on the pool


def _agent_started(n):
    return {"type": "system", "message_id": f"ts{n}", "content": "", "session_id": SID,
            "metadata": {"subtype": "task_started", "task_id": f"bg{n}", "description": "d"}}


async def _saturate_pool(coordinator, other_sids, release):
    """Occupy every cold-pool worker with other sessions' (blocked) scans."""
    scans = coordinator._session_query._cold_scans
    started = threading.Event()
    count = [0]
    lock = threading.Lock()

    def blocker(cancel):
        with lock:
            count[0] += 1
            if count[0] >= scans.workers:
                started.set()
        release.wait(10)

    async def flight(handle):
        return await handle.run(blocker)

    tasks = [asyncio.ensure_future(scans.single_flight(sid, "index", flight, empty=lambda: None))
             for sid in other_sids]
    for _ in range(200):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()
    return tasks


@pytest.mark.asyncio
async def test_live_callback_registry_hydration_completes_while_pool_is_saturated(tmp_path):
    coordinator, _ = await _coordinator(
        tmp_path, [_user(0), _agent_started(1)], cold_scan_workers=2
    )
    release = threading.Event()
    hogs = await _saturate_pool(coordinator, ["x1", "x2", "x3", "x4"], release)
    try:
        t0 = time.monotonic()
        registry = await asyncio.wait_for(
            coordinator._get_task_leg_registry(SID, live=True), timeout=3
        )
        assert time.monotonic() - t0 < 2.0
        assert registry.snapshot()  # the leg hydrated from the file
        assert coordinator._task_leg_registries[SID] is registry
        # the browser-requested hydrator (pool) DOES queue behind the saturated pool
        browser = asyncio.ensure_future(coordinator.get_background_agents(SID))
        await asyncio.sleep(0.2)
        assert browser.done()  # registry already warm -> served from cache, no pool wait
    finally:
        release.set()
        await asyncio.gather(*hogs, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_browser_hydration_keeps_the_registry_the_live_path_warmed(tmp_path):
    coordinator, _ = await _coordinator(
        tmp_path, [_user(0), _agent_started(1)], cold_scan_workers=1
    )
    release = threading.Event()
    hogs = await _saturate_pool(coordinator, ["x1"], release)
    try:
        browser = asyncio.ensure_future(coordinator.get_background_agents(SID))  # queued in pool
        await asyncio.sleep(0.1)
        assert not browser.done()
        live = await asyncio.wait_for(coordinator._get_task_leg_registry(SID, live=True), 3)
        live.apply_frame("task_started", {"task_id": "bg-live", "description": "fed"}, 1.0)
        release.set()
        result = await asyncio.wait_for(browser, 10)
        assert coordinator._task_leg_registries[SID] is live  # pool job did not overwrite it
        assert any("bg-live" in json.dumps(a) for a in result["agents"])
    finally:
        release.set()
        await asyncio.gather(*hogs, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_live_callers_share_one_scan_and_reset_cancels_it(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, [_agent_started(1)])
    reader_cls = type(storage.reader)
    real = reader_cls.iter_matching
    calls = []
    started = threading.Event()
    release = threading.Event()

    def slow(self, prefilters, **kwargs):
        calls.append(threading.current_thread().name)
        started.set()
        release.wait(5)
        yield from real(self, prefilters, **kwargs)

    reader_cls.iter_matching = slow
    try:
        tasks = [asyncio.ensure_future(coordinator._get_task_leg_registry(SID, live=True))
                 for _ in range(3)]
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        coordinator._clear_task_list(SID)  # reset mid-hydration
        release.set()
        results = await asyncio.gather(*tasks)
    finally:
        reader_cls.iter_matching = real
    assert len(calls) == 1 and not calls[0].startswith("cold-scan")  # default executor
    assert all(not r.snapshot() for r in results)
    assert SID not in coordinator._task_leg_registries  # the pre-reset result was not cached


def test_message_pipeline_task_frame_path_requests_the_live_hydration():
    import inspect

    from backend import message_pipeline

    src = inspect.getsource(message_pipeline)
    assert "_get_task_leg_registry(\n                                session_id, live=True" in src
