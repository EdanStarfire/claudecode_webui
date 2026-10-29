"""Issue #2030 (stage 3 PR 3B): search pool / single-flight / supersede / disconnect / waiter cap /
abandonment, pool separation from the cold-scan pool and the default executor, and the
``/search`` routes."""

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

from backend.cold_scan import ColdScanManager
from backend.config_manager import PerformanceConfig
from backend.history_search import SearchCaps, SearchPage
from backend.message_history_reader import FileMessageHistoryReader
from backend.search_scan import SearchBusyError, SearchScanManager

SID = "search-session"
CAPS = SearchCaps(max_results=50, max_scan_bytes=10**9, max_wall_s=0.5)


class FakeReader:
    """Blocks in ``search`` until released, honouring the cancel flag like the real scan."""

    def __init__(self, name="r", release_after=None):
        self.name = name
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[str, str | None]] = []
        self.cancelled_seen = threading.Event()
        self.release_after = release_after

    def search(self, query, cursor, caps, cancel=None):
        self.calls.append((query, cursor))
        self.started.set()
        deadline = time.monotonic() + (self.release_after if self.release_after is not None else 10)
        while not self.release.is_set() and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.cancelled_seen.set()
                return SearchPage([], None, False, "h", 0, 0.0, 0, cancelled=True)
            time.sleep(0.005)
        return SearchPage([{"q": query}], None, True, "h", 1, 1.0, 1)


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.005)


@pytest.fixture
def manager():
    m = SearchScanManager(workers=2)
    yield m
    m.shutdown()


def _settled(manager):
    return manager.inflight_count == 0 and manager.waiter_count == 0


@pytest.mark.asyncio
async def test_identical_requests_share_one_scan(manager):
    reader = FakeReader()
    tasks = [asyncio.ensure_future(manager.search("k", reader, "needle", None, CAPS)) for _ in range(5)]
    await _until(reader.started.is_set)
    reader.release.set()
    pages = await asyncio.gather(*tasks)
    assert len(reader.calls) == 1 and all(p.results == [{"q": "needle"}] for p in pages)
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_new_query_from_the_same_client_supersedes_its_older_query(manager):
    reader = FakeReader()
    old = asyncio.ensure_future(manager.search("k", reader, "first", None, CAPS, client_id="tab1"))
    await _until(reader.started.is_set)
    other_session = FakeReader("other")
    other = asyncio.ensure_future(
        manager.search("k2", other_session, "other", None, CAPS, client_id="tab1")
    )
    new_reader = FakeReader("new")
    new_reader.release.set()
    page = await manager.search("k", new_reader, "second", None, CAPS, client_id="tab1")
    assert page.results == [{"q": "second"}]
    assert await old is None  # superseded: no result, no error
    assert reader.cancelled_seen.wait(2)
    assert manager.superseded == 1
    # a different session was untouched
    other_session.release.set()
    assert (await other).results == [{"q": "other"}]
    assert not other_session.cancelled_seen.is_set()
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_two_clients_different_queries_same_session_do_not_cancel_each_other(manager):
    a, b = FakeReader("a"), FakeReader("b")
    ta = asyncio.ensure_future(manager.search("k", a, "alpha query", None, CAPS, client_id="tab1"))
    tb = asyncio.ensure_future(manager.search("k", b, "beta query", None, CAPS, client_id="tab2"))
    await _until(lambda: a.started.is_set() and b.started.is_set())
    a.release.set()
    b.release.set()
    pa, pb = await asyncio.gather(ta, tb)
    assert pa.results == [{"q": "alpha query"}] and pb.results == [{"q": "beta query"}]
    assert manager.superseded == 0 and not a.cancelled_seen.is_set() and not b.cancelled_seen.is_set()
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_requests_without_client_id_never_cancel_or_get_cancelled(manager):
    a, b = FakeReader("a"), FakeReader("b")
    ta = asyncio.ensure_future(manager.search("k", a, "alpha query", None, CAPS))
    await _until(a.started.is_set)
    tb = asyncio.ensure_future(manager.search("k", b, "beta query", None, CAPS))
    tc = asyncio.ensure_future(manager.search("k", FakeReader("c"), "gamma", None, CAPS, client_id="tab1"))
    await asyncio.sleep(0.05)
    a.release.set()
    b.release.set()
    assert (await ta) is not None and (await tb) is not None
    assert manager.superseded == 0
    tc.cancel()
    await asyncio.gather(tc, return_exceptions=True)


@pytest.mark.asyncio
async def test_identical_queries_from_two_clients_share_one_scan_and_survive_one_superseding(manager):
    reader = FakeReader()
    t1 = asyncio.ensure_future(manager.search("k", reader, "shared", None, CAPS, client_id="tab1"))
    t2 = asyncio.ensure_future(manager.search("k", reader, "shared", None, CAPS, client_id="tab2"))
    await _until(reader.started.is_set)
    other = FakeReader("other")
    other.release.set()
    # tab1 moves on to a different query: its waiter is superseded, tab2's shared scan continues
    p = await manager.search("k", other, "different", None, CAPS, client_id="tab1")
    assert p.results == [{"q": "different"}]
    assert await t1 is None
    assert not reader.cancelled_seen.is_set()
    reader.release.set()
    assert (await t2).results == [{"q": "shared"}]
    assert len(reader.calls) == 1 and manager.superseded == 1
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_superseding_the_last_waiter_cancels_the_scan(manager):
    reader = FakeReader()
    t1 = asyncio.ensure_future(manager.search("k", reader, "first", None, CAPS, client_id="tab1"))
    await _until(reader.started.is_set)
    nxt = FakeReader("n")
    nxt.release.set()
    await manager.search("k", nxt, "second", None, CAPS, client_id="tab1")
    assert await t1 is None and reader.cancelled_seen.wait(2)
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_flight_cap_per_session_refuses_extra_distinct_flights():
    manager = SearchScanManager(workers=4)  # (the cap, not the pool, is under test)
    try:
        await _cap_scenario(manager)
    finally:
        manager.shutdown()


async def _cap_scenario(manager):
    await _refused_request_keeps_own_older_query(manager)
    assert manager.max_flights_per_key == 2
    r1, r2, r3 = FakeReader("1"), FakeReader("2"), FakeReader("3")
    t1 = asyncio.ensure_future(manager.search("k", r1, "one one", None, CAPS, client_id="a"))
    t2 = asyncio.ensure_future(manager.search("k", r2, "two two", None, CAPS, client_id="b"))
    await _until(lambda: r1.started.is_set() and r2.started.is_set())
    with pytest.raises(SearchBusyError):
        await manager.search("k", r3, "three", None, CAPS, client_id="c")
    assert manager.refused == 2 and r3.calls == []  # (one refusal came from the helper above)
    # an identical query joins the existing flight (no new flight), another session is unaffected
    joiner = asyncio.ensure_future(manager.search("k", r1, "one one", None, CAPS, client_id="d"))
    other_session = FakeReader("o")
    other_session.release.set()
    assert (await manager.search("k2", other_session, "three", None, CAPS, client_id="c")) is not None
    r1.release.set()
    r2.release.set()
    assert (await joiner) is not None and (await t1) is not None and (await t2) is not None
    # a client's own query change frees its flight slot immediately
    r4, r5 = FakeReader("4"), FakeReader("5")
    first = asyncio.ensure_future(manager.search("k", r4, "four", None, CAPS, client_id="x"))
    await _until(r4.started.is_set)
    r5.release.set()
    assert (await manager.search("k", r5, "five", None, CAPS, client_id="x")) is not None
    assert await first is None
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_continuation_pages_of_the_same_query_do_not_supersede(manager):
    a, b = FakeReader("a"), FakeReader("b")
    ta = asyncio.ensure_future(manager.search("k", a, "needle", None, CAPS))
    await _until(a.started.is_set)
    tb = asyncio.ensure_future(manager.search("k", b, "needle", "s1:h:100", CAPS))
    await _until(b.started.is_set)
    a.release.set()
    b.release.set()
    assert (await ta) is not None and (await tb) is not None
    assert manager.superseded == 0 and not a.cancelled_seen.is_set()


@pytest.mark.asyncio
async def test_client_disconnect_cancels_scan_and_leaks_nothing(manager):
    reader = FakeReader()
    state = {"gone": False}

    async def is_disconnected():
        return state["gone"]

    task = asyncio.ensure_future(
        manager.search("k", reader, "needle", None, CAPS, is_disconnected=is_disconnected)
    )
    await _until(reader.started.is_set)
    state["gone"] = True
    assert await task is None
    assert reader.cancelled_seen.wait(2)
    assert manager.disconnected == 1
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_cancelled_handler_cancels_scan_and_leaks_nothing(manager):
    reader = FakeReader()
    task = asyncio.ensure_future(manager.search("k", reader, "needle", None, CAPS))
    await _until(reader.started.is_set)
    task.cancel()  # what the framework does to the handler when the client disconnects
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reader.cancelled_seen.wait(2)
    assert manager.disconnected == 1
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_one_of_two_waiters_leaving_does_not_cancel_the_shared_scan(manager):
    reader = FakeReader()
    gone = {"v": False}

    async def is_disconnected():
        return gone["v"]

    leaver = asyncio.ensure_future(
        manager.search("k", reader, "needle", None, CAPS, is_disconnected=is_disconnected)
    )
    stayer = asyncio.ensure_future(manager.search("k", reader, "needle", None, CAPS))
    await _until(reader.started.is_set)
    gone["v"] = True
    assert await leaver is None
    assert not reader.cancelled_seen.is_set()
    reader.release.set()
    assert (await stayer).results
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_waiter_cap_raises_busy_and_queued_job_never_starts():
    m = SearchScanManager(workers=1)
    try:
        blocker = FakeReader("blocker")
        hold = asyncio.ensure_future(
            m.search("a", blocker, "block", None, SearchCaps(max_wall_s=30))
        )
        await _until(blocker.started.is_set)
        queued = FakeReader("queued")
        caps = SearchCaps(max_wall_s=0.0)  # waiter cap = 0 + 2 s slack
        t0 = time.monotonic()
        with pytest.raises(SearchBusyError):
            await m.search("b", queued, "needle", None, caps)
        waited = time.monotonic() - t0
        assert 1.5 < waited < 4.0
        assert m.timed_out == 1
        blocker.release.set()
        await hold
        await asyncio.sleep(0.1)
        assert queued.calls == []  # abandoned before it ever started
        await _until(lambda: _settled(m))
    finally:
        m.shutdown()


@pytest.mark.asyncio
async def test_invalidate_drops_inflight_search(manager):
    reader = FakeReader()
    task = asyncio.ensure_future(manager.search("k", reader, "needle", None, CAPS))
    await _until(reader.started.is_set)
    manager.invalidate("k")
    assert await task is None
    assert reader.cancelled_seen.wait(2)
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_pools_are_separate_from_cold_scans_and_default_executor():
    search = SearchScanManager(workers=1)
    cold = ColdScanManager(workers=1)
    try:
        # saturate the cold pool; search still answers
        gate = threading.Event()

        async def cold_flight(handle):
            return await handle.run(lambda cancel: gate.wait(5))

        cold_task = asyncio.ensure_future(cold.single_flight("s", "index", cold_flight, empty=lambda: None))
        await _until(lambda: cold.running == 1)
        fast = FakeReader()
        fast.release.set()
        t0 = time.monotonic()
        assert (await search.search("s", fast, "needle", None, CAPS)).results
        assert time.monotonic() - t0 < 1.0

        # saturate the search pool; the cold pool and the default executor still answer
        blocker = FakeReader("b")
        hold = asyncio.ensure_future(search.search("x", blocker, "block", None, SearchCaps(max_wall_s=30)))
        await _until(blocker.started.is_set)
        t0 = time.monotonic()
        assert await asyncio.to_thread(lambda: 42) == 42
        assert time.monotonic() - t0 < 0.5
        gate.set()
        await cold_task
        blocker.release.set()
        await hold
    finally:
        search.shutdown()
        cold.shutdown()


def test_search_workers_default_and_clamp():
    assert PerformanceConfig().search_workers == 2
    assert SearchScanManager(workers=0).workers == 1
    assert SearchScanManager(workers=-3).workers == 1
    assert SearchScanManager(workers=5).workers == 5
    assert SearchScanManager(workers=None).workers >= 1


def test_search_workers_roundtrips_through_app_config():
    from backend.config_manager import AppConfig

    config = AppConfig.from_dict({"performance": {"search_workers": 3}})
    assert config.performance.search_workers == 3
    assert AppConfig.from_dict({}).performance.search_workers == 2


@pytest.mark.asyncio
async def test_real_reader_wall_cap_is_enforced_in_the_pool(tmp_path):
    path = tmp_path / "messages.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(3000):
            f.write(json.dumps({"type": "user", "message_id": f"u{i}", "content": f"needle {i}"}) + "\n")
    reader = FileMessageHistoryReader(path)
    real = reader._try_json

    def slow(line):
        time.sleep(0.01)
        return real(line)

    reader._try_json = slow
    m = SearchScanManager(workers=2)
    try:
        t0 = time.monotonic()
        page = await m.search(
            "k", reader, "needle", None, SearchCaps(max_results=10**6, max_wall_s=0.3)
        )
        assert time.monotonic() - t0 < 1.5
        assert page.stopped_by == "time" and 0 < len(page.results) < 3000
        assert page.next_cursor
    finally:
        m.shutdown()


# ---------------------------------------------------------------------- coordinator + routes


async def _coordinator(tmp_path, records, sid=SID):
    import backend.session_coordinator as sc_module
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    session_dir = tmp_path / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    storage = sc_module.DataStorageManager(session_dir)
    await storage.initialize()
    coordinator._apply_history_index_feed(storage, sid)
    for rec in records:
        await storage.append_message(dict(rec))
    coordinator._storage_managers[sid] = storage
    return coordinator, storage


def _history(n=120):
    out = []
    for i in range(n):
        out.append({"type": "user", "message_id": f"u{i}", "content": f"Needle number {i}", "session_id": SID})
        out.append({"type": "assistant", "message_id": f"a{i}", "content": f"reply {i}", "session_id": SID,
                    "metadata": {"tool_uses": []}})
    return out


def _app(coordinator, *, exists=True):
    from backend.application_service import ApplicationService
    from backend.routers.archives import build_router as archives_router
    from backend.routers.sessions import build_router as sessions_router

    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.get_session_exists = AsyncMock(return_value=exists)
    webui.service.validate_project_exists = AsyncMock(return_value=True)
    app = FastAPI()
    app.include_router(sessions_router(webui))
    app.include_router(archives_router(webui))
    return app


async def _get(app, path, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path, params=params)


@pytest.mark.asyncio
async def test_search_route_pages_and_shape(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, _history())
    try:
        app = _app(coordinator)
        resp = await _get(app, f"/api/sessions/{SID}/search", q="needle", limit=50)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 50 and body["exhausted"] is False and body["next_cursor"]
        assert body["superseded"] is False
        first = body["results"][0]
        assert first["message_id"] == "u119" and first["field"] == "content"
        assert set(first) >= {"message_id", "tool_id", "field", "match_start", "match_length",
                              "snippet", "occurrence_index", "cursor", "type", "timestamp"}
        seen = [r["message_id"] for r in body["results"]]
        cursor = body["next_cursor"]
        while cursor:
            body = (await _get(app, f"/api/sessions/{SID}/search", q="needle", cursor=cursor)).json()
            seen += [r["message_id"] for r in body["results"]]
            cursor = body["next_cursor"]
        assert seen == [f"u{i}" for i in reversed(range(120))]
        assert (await _get(app, f"/api/sessions/{SID}/search", q="needle", limit=3)).json()["results"].__len__() == 3
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_search_route_errors(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, _history(5))
    try:
        app = _app(coordinator)
        url = f"/api/sessions/{SID}/search"
        assert (await _get(app, url, q="a")).status_code == 400
        assert (await _get(app, url, q="x" * 300)).json()["detail"] == "query_too_long"
        assert (await _get(app, url)).status_code == 422  # q is required
        resp = await _get(app, url, q="needle", cursor="s1:deadbeef:5")
        assert resp.status_code == 409 and resp.json()["detail"] == "cursor_invalid"
        assert (await _get(_app(coordinator, exists=False), url, q="needle")).status_code == 404

        coordinator._session_query._searches.search = AsyncMock(side_effect=SearchBusyError("k"))
        resp = await _get(app, url, q="needle")
        assert resp.status_code == 503 and resp.json()["detail"] == "search_busy"
        assert resp.headers["retry-after"]

        coordinator._session_query._searches.search = AsyncMock(return_value=None)
        resp = await _get(app, url, q="needle")
        assert resp.status_code == 200 and resp.json()["superseded"] is True
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_route_client_id_supersede_and_cap(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, _history(5))
    try:
        app = _app(coordinator)
        url = f"/api/sessions/{SID}/search"
        searches = coordinator._session_query._searches
        calls = []

        async def fake(key, reader, query, cursor, caps, *, client_id=None, is_disconnected=None):
            calls.append((query, client_id))
            return None

        searches.search = fake
        resp = await _get(app, url, q="needle", client_id="tab-9")
        assert resp.json()["superseded"] is True and calls == [("needle", "tab-9")]
        await _get(app, url, q="needle")
        assert calls[-1] == ("needle", None)
        await _get(app, url, q="needle", client_id="x" * 500)
        assert calls[-1][1] == "x" * 500  # (bounded to 64 inside SearchScanManager)
    finally:
        coordinator._session_query.shutdown()


def test_search_max_flights_config_roundtrip():
    from backend.config_manager import AppConfig

    assert PerformanceConfig().search_max_flights_per_session == 2
    config = AppConfig.from_dict({"performance": {"search_max_flights_per_session": 3}})
    assert config.performance.search_max_flights_per_session == 3
    assert SearchScanManager(workers=1, max_flights_per_key=0).max_flights_per_key == 1


@pytest.mark.asyncio
async def test_reset_or_delete_mid_search_drops_the_scan(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history(50))
    query = coordinator._session_query
    reader_cls = type(storage.reader)
    started, release = threading.Event(), threading.Event()
    seen_cancel = threading.Event()
    real = reader_cls.search

    def blocked(self, q, cursor, caps, cancel=None):
        started.set()
        while not release.is_set():
            if cancel is not None and cancel.is_set():
                seen_cancel.set()
                return real(self, q, cursor, caps, cancel)
            time.sleep(0.005)
        return real(self, q, cursor, caps, cancel)

    reader_cls.search = blocked
    try:
        task = asyncio.ensure_future(coordinator.search_history(SID, "needle", None, None, None, None))
        await _until(started.is_set)
        coordinator._clear_task_list(SID)  # the reset / delete hook
        assert await task is None
        assert seen_cancel.wait(2)
        await _until(lambda: _settled(query._searches))
    finally:
        release.set()
        reader_cls.search = real
        query.shutdown()


@pytest.mark.asyncio
async def test_stop_does_not_cancel_searches_and_terminated_session_uses_directory(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history(10))
    query = coordinator._session_query
    reader_cls = type(storage.reader)
    started, release = threading.Event(), threading.Event()
    seen_cancel = threading.Event()
    real = reader_cls.search

    def blocked(self, q, cursor, caps, cancel=None):
        started.set()
        while not release.is_set():
            if cancel is not None and cancel.is_set():
                seen_cancel.set()
                break
            time.sleep(0.005)
        return real(self, q, cursor, caps, cancel)

    reader_cls.search = blocked
    try:
        task = asyncio.ensure_future(coordinator.search_history(SID, "needle", None, None, "c1", None))
        await _until(started.is_set)
        query.drop_history_index(SID, abandon_scans=True)  # stop / terminate path
        coordinator._storage_managers.pop(SID)  # terminated: storage manager dropped
        await asyncio.sleep(0.1)
        assert not seen_cancel.is_set() and not task.done()
        release.set()
        page = await task  # the search survived the stop: the history file did not change
        assert page is not None and len(page["results"]) == 10
    finally:
        release.set()
        reader_cls.search = real
    try:
        coordinator.session_manager.get_session_directory = AsyncMock(return_value=tmp_path / "sessions" / SID)
        page = await coordinator.search_history(SID, "needle", None, None, None, None)
        assert len(page["results"]) == 10  # a terminated session is searched through its directory
        coordinator.session_manager.get_session_directory = AsyncMock(return_value=None)
        assert await coordinator.search_history(SID, "needle", None, None, None, None) is None
    finally:
        query.shutdown()


@pytest.mark.asyncio
async def test_archive_search_route(tmp_path):
    coordinator, _ = await _coordinator(tmp_path, [])
    try:
        archive_dir = tmp_path / "archives" / SID / "20260101"
        archive_dir.mkdir(parents=True)
        (archive_dir / "messages.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _history(8)), encoding="utf-8"
        )
        coordinator.legion_system = SimpleNamespace(
            archive_manager=SimpleNamespace(
                archive_messages_path=lambda s, a: archive_dir / "messages.jsonl"
            )
        )
        app = _app(coordinator)
        resp = await _get(app, f"/api/projects/p1/archives/{SID}/20260101/search", q="needle")
        assert resp.status_code == 200
        assert [r["message_id"] for r in resp.json()["results"]] == [f"u{i}" for i in reversed(range(8))]
        assert resp.json()["exhausted"] is True
        resp = await _get(app, f"/api/projects/p1/archives/{SID}/20260101/search", q="n")
        assert resp.status_code == 400

        def bad(s, a):
            raise ValueError("invalid archive path component")

        coordinator.legion_system.archive_manager.archive_messages_path = bad
        resp = await _get(app, f"/api/projects/p1/archives/{SID}/..%2Fx/search", q="needle")
        assert resp.status_code == 404
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_search_does_not_use_default_executor_or_cold_pool(tmp_path):
    coordinator, storage = await _coordinator(tmp_path, _history(200))
    query = coordinator._session_query
    try:
        # occupy every default-executor thread: the search must still complete (own pool)
        loop = asyncio.get_running_loop()
        release = threading.Event()
        blockers = [loop.run_in_executor(None, release.wait, 5) for _ in range(64)]
        try:
            page = await asyncio.wait_for(
                coordinator.search_history(SID, "needle", None, None, None, None), timeout=3
            )
            assert page["results"]
        finally:
            release.set()
            await asyncio.gather(*blockers)
        assert query._cold_scans.max_running == 0  # the cold-scan pool was never used
        assert query._searches.max_running >= 1
    finally:
        query.shutdown()


async def _refused_request_keeps_own_older_query(manager):
    r1, r2, r3 = FakeReader("1"), FakeReader("2"), FakeReader("3")
    own = asyncio.ensure_future(manager.search("kk", r1, "own old", None, CAPS, client_id="a"))
    other = asyncio.ensure_future(manager.search("kk", r2, "other tab", None, CAPS, client_id="b"))
    await _until(lambda: r1.started.is_set() and r2.started.is_set())
    third = asyncio.ensure_future(manager.search("kk", FakeReader("x"), "third", None, CAPS, client_id="c"))
    await asyncio.sleep(0.05)  # cap reached: refused without touching anyone
    with pytest.raises(SearchBusyError):
        await third
    # tab a's NEW query replaces its own older one (frees its own slot), so it is admitted
    r3.release.set()
    page = await manager.search("kk", r3, "own new", None, CAPS, client_id="a")
    assert page is not None and await own is None
    r2.release.set()
    await other
    await _until(lambda: _settled(manager))


@pytest.mark.asyncio
async def test_refused_request_does_not_supersede_the_clients_older_query():
    manager = SearchScanManager(workers=4, max_flights_per_key=1)
    try:
        r1 = FakeReader("1")
        old = asyncio.ensure_future(manager.search("k", r1, "old query", None, CAPS, client_id="a"))
        await _until(r1.started.is_set)
        # another client's request is refused (cap 1, a's flight is live) and touches nothing
        with pytest.raises(SearchBusyError):
            await manager.search("k", FakeReader("b"), "b query", None, CAPS, client_id="b")
        assert not r1.cancelled_seen.is_set() and manager.superseded == 0
        # the same client's replacement query is admitted: its own old flight does not count
        r2 = FakeReader("2")
        r2.release.set()
        assert (await manager.search("k", r2, "new query", None, CAPS, client_id="a")) is not None
        assert await old is None and manager.superseded == 1
        await _until(lambda: _settled(manager))
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_client_id_is_bounded_in_the_manager(manager):
    long_id = "x" * 500
    reader = FakeReader()
    t = asyncio.ensure_future(manager.search("k", reader, "one one", None, CAPS, client_id=long_id))
    await _until(reader.started.is_set)
    other = FakeReader("o")
    other.release.set()
    # a different 500-char id sharing the first 64 chars is the same client
    await manager.search("k", other, "two two", None, CAPS, client_id="x" * 64 + "y" * 436)
    assert await t is None and manager.superseded == 1
