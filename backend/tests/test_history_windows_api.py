"""Issue #2030 (stage 3 PR 3A): ``around=`` / ``around_id=`` / ``after=`` on the session and archive
message routes, the cold-pool id lookup (single-flight, waiter cap, flight cap, abandonment) and the
guarantee that the default executor is unaffected by a saturated cold-scan pool."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from backend.message_history_reader import (
    MessageNotFoundError,
    ResolveBusyError,
    ResolvedId,
)
from backend.tests.test_issue_2030_history_window_api import SID, _app, _coordinator, _get


def _history(turns=30):
    out, n = [], 0
    for t in range(turns):
        out.append({"type": "user", "message_id": f"u{t}", "content": f"turn {t}", "session_id": SID})
        out.append({"type": "assistant", "message_id": f"a{t}", "content": f"reply {t}",
                    "session_id": SID, "metadata": {"tool_uses": [{"id": f"toolu_{t}", "name": "Read"}]}})
        out.append({"type": "tool_call", "message_id": f"tc{t}", "tool_use_id": f"toolu_{t}",
                    "name": "Read", "input": {"file": f"needle{t}.txt"}, "session_id": SID})
        out.append({"type": "user", "message_id": f"r{t}", "content": "Tool results: 1 result",
                    "session_id": SID, "metadata": {"has_tool_results": True}})
        n += 4
    return out


def _ids(body):
    return [m["message_id"] for m in body["messages"]]


# --------------------------------------------------------------------------- routes


@pytest.mark.asyncio
async def test_around_after_responses_have_no_event_cursor_and_tile(tmp_path):
    records = _history()
    coordinator = await _coordinator(tmp_path, records)
    try:
        app = _app(coordinator)
        base = f"/api/sessions/{SID}/messages"
        tail = (await _get(app, f"{base}?window=tail")).json()
        anchor = tail["start_cursor"]
        win = await _get(app, f"{base}?around={anchor}&before_bytes=2000&after_bytes=1500")
        assert win.status_code == 200
        body = win.json()
        assert set(body) == {
            "messages", "start_cursor", "end_cursor", "has_more_before", "has_more_after",
            "history_id", "snapped", "total_bytes", "anchor_cursor",
        }
        assert body["history_id"] == tail["history_id"] and body["anchor_cursor"] == anchor
        got = _ids(body)
        cursor = body["end_cursor"]
        while True:
            page = (await _get(app, f"{base}?after={cursor}&max_bytes=2048")).json()
            assert "event_cursor" not in page and "epoch" not in page
            assert set(page) == {
                "messages", "start_cursor", "end_cursor", "has_more_before", "has_more_after",
                "history_id", "snapped", "total_bytes",
            }
            got += _ids(page)
            cursor = page["end_cursor"]
            if not page["has_more_after"]:
                break
        while body["has_more_before"]:
            body = (await _get(
                app, f"{base}?before={body['start_cursor']}&max_bytes=2048"
            )).json()
            got = _ids(body) + got
        assert got == [r["message_id"] for r in records]
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_around_id_by_message_id_and_tool_use_id(tmp_path):
    records = _history()
    coordinator = await _coordinator(tmp_path, records)
    try:
        app = _app(coordinator)
        base = f"/api/sessions/{SID}/messages"
        by_msg = (await _get(app, f"{base}?around_id=a17&before_bytes=1500&after_bytes=1500")).json()
        assert "a17" in _ids(by_msg)
        by_tool = (await _get(app, f"{base}?around_id=toolu_17&before_bytes=1500&after_bytes=1500")).json()
        assert by_tool["anchor_cursor"] == by_msg["anchor_cursor"]  # the launching message
        by_cursor = (await _get(app, f"{base}?around={by_msg['anchor_cursor']}&before_bytes=1500&after_bytes=1500")).json()
        assert by_cursor["messages"] == by_msg["messages"]
        assert coordinator._session_query._cold_scans.inflight_count == 0
        miss = await _get(app, f"{base}?around_id=nope")
        assert miss.status_code == 404 and miss.json()["detail"] == "message_not_found"
        assert coordinator._session_query._cold_scans.inflight_count == 0
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_around_id_resolves_a_parent_message_id_returned_by_server_search(tmp_path):
    records = _history(12)
    coordinator = await _coordinator(tmp_path, records)
    try:
        async def alive():
            return False

        page = await coordinator.search_history(SID, "needle5.txt", None, None, None, alive)
        hit = page["results"][0]
        assert hit["message_id"] == "a5" and hit["tool_id"] == "toolu_5"  # tool hit -> parent id
        app = _app(coordinator)
        body = (await _get(
            app, f"/api/sessions/{SID}/messages?around_id={hit['message_id']}&before_bytes=2000"
        )).json()
        assert "a5" in _ids(body)
        parent_offset = int(body["anchor_cursor"][2:])
        assert parent_offset <= int(hit["cursor"][2:])  # the hit's tool_call sits at/after the parent
        by_tool = (await _get(app, f"/api/sessions/{SID}/messages?around_id={hit['tool_id']}")).json()
        assert by_tool["anchor_cursor"] == body["anchor_cursor"]
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,status",
    [
        ("window=tail&around=b:0", 422), ("before=b:0&after=b:0", 422),
        ("around=b:0&around_id=x", 422), ("around=", 422), ("around_id=%20", 422), ("after=", 422),
        ("until=b:0", 422), ("before_bytes=5", 422), ("around_id=a%22b", 422),
        ("around_id=a%5Cb", 422), ("around_id=a%0Ab", 422), ("around_id=caf%C3%A9", 422),
        ("around_id=" + "x" * 257, 422), ("after=b:0&after_bytes=5", 422),
        ("around=b:3", 409), ("around=junk", 409), ("after=b:3", 409), ("after=b:0&until=b:7", 409),
        ("around=b:999999999", 409), ("around_id=missing", 404),
    ],
)
async def test_validation_and_error_mapping(tmp_path, query, status):
    coordinator = await _coordinator(tmp_path, _history(4))
    try:
        resp = await _get(_app(coordinator), f"/api/sessions/{SID}/messages?{query}")
        assert resp.status_code == status, resp.text
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_rewrite_changes_history_id_in_around(tmp_path):
    coordinator = await _coordinator(tmp_path, _history(4))
    try:
        app = _app(coordinator)
        base = f"/api/sessions/{SID}/messages"
        first = (await _get(app, f"{base}?around=b:0")).json()
        path = coordinator._storage_managers[SID].messages_file
        path.write_text(
            json.dumps({"type": "user", "message_id": "new", "content": "x"}) + "\n" + path.read_text()
        )
        second = (await _get(app, f"{base}?around=b:0")).json()
        assert first["history_id"] != second["history_id"]
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_unstarted_session_reads_through_its_directory(tmp_path):
    from unittest.mock import AsyncMock

    records = _history(4)
    coordinator = await _coordinator(tmp_path, records)
    try:
        path = coordinator._storage_managers[SID].messages_file
        del coordinator._storage_managers[SID]  # terminated: the storage manager is gone
        coordinator.session_manager.get_session_directory = AsyncMock(return_value=path.parent)
        body = (await _get(_app(coordinator), f"/api/sessions/{SID}/messages?around_id=a2")).json()
        assert "a2" in _ids(body)
        coordinator.session_manager.get_session_directory = AsyncMock(return_value=None)
        resp = await _get(_app(coordinator), f"/api/sessions/{SID}/messages?around_id=a2")
        assert resp.status_code == 404
    finally:
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_archive_parity(tmp_path):
    records = _history(10)
    coordinator = await _coordinator(tmp_path, [])
    try:
        archive_dir = tmp_path / "archives" / SID / "20260101"
        archive_dir.mkdir(parents=True)
        (archive_dir / "messages.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )
        coordinator.legion_system = SimpleNamespace(
            archive_manager=SimpleNamespace(
                archive_messages_path=lambda s, a: archive_dir.parent / a / "messages.jsonl"
            )
        )
        app = _app(coordinator)
        base = f"/api/projects/p1/archives/{SID}/20260101/messages"
        body = (await _get(app, f"{base}?around_id=toolu_4&before_bytes=900&after_bytes=900")).json()
        assert "a4" in _ids(body) and "event_cursor" not in body
        nxt = (await _get(app, f"{base}?after={body['end_cursor']}&max_bytes=1500")).json()
        assert nxt["messages"] and nxt["start_cursor"] == body["end_cursor"]
        assert (await _get(app, f"{base}?around_id=zzz")).status_code == 404
        assert (await _get(app, f"{base}?around=b:2")).status_code == 409
        assert (await _get(app, f"{base}?until=b:0")).status_code == 422
        missing = f"/api/projects/p1/archives/{SID}/20990101/messages"
        for q in ("around=b:5", "around_id=a1", "after=b:5"):  # a missing archive is not a cursor error
            resp = await _get(app, f"{missing}?{q}")
            assert resp.status_code == 404 and resp.json()["detail"] == "Archive not found"
        # the archive lookup runs under its own key, leaving no flight behind
        assert coordinator._session_query._cold_scans.inflight_count == 0

        def bad(s, a):
            raise ValueError("invalid archive path component")

        coordinator.legion_system.archive_manager.archive_messages_path = bad
        assert (await _get(app, f"{base}?around=b:0")).status_code == 404
    finally:
        coordinator._session_query.shutdown()


# --------------------------------------------------------------------------- resolve pool


class BlockingReader:
    """Stand-in reader whose id lookup blocks until released and honours the cancel flag."""

    def __init__(self, cursor="b:0"):
        self.cursor = cursor
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()
        self.calls: list[str] = []

    def lookup_id(self, ident, cancel=None):
        self.calls.append(ident)
        self.started.set()
        deadline = time.monotonic() + 10
        while not self.release.is_set() and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.cancel_seen.set()
                return None
            time.sleep(0.005)
        return None if self.cursor is None else ResolvedId(self.cursor)


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.005)


@pytest.fixture
async def query(tmp_path):
    coordinator = await _coordinator(tmp_path, _history(3))
    yield coordinator._session_query
    coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_resolve_is_single_flight_per_session_and_id(query):
    reader = BlockingReader("b:42")
    a = asyncio.ensure_future(query.resolve_message_id("k", reader, "x"))
    b = asyncio.ensure_future(query.resolve_message_id("k", reader, "x"))
    await _until(reader.started.is_set)
    assert query._cold_scans.inflight_count == 1
    reader.release.set()
    assert (await a).cursor == (await b).cursor == "b:42"
    assert reader.calls == ["x"]
    assert query._cold_scans.inflight_count == 0  # no leaked flight


@pytest.mark.asyncio
async def test_resolve_not_found_and_other_session_unaffected(query):
    slow, fast = BlockingReader("b:1"), BlockingReader("b:2")
    fast.release.set()
    t = asyncio.ensure_future(query.resolve_message_id("big", slow, "x"))
    await _until(slow.started.is_set)
    assert (await query.resolve_message_id("small", fast, "y")).cursor == "b:2"  # session Y not behind X
    fast.cursor = None
    with pytest.raises(MessageNotFoundError):
        await query.resolve_message_id("small", fast, "z")
    slow.release.set()
    await t


@pytest.mark.asyncio
async def test_resolve_waiter_cap_raises_busy_but_flight_finishes(query, monkeypatch):
    import backend.session_query as sq

    monkeypatch.setattr(sq, "RESOLVE_WAIT_S", 0.05)
    reader = BlockingReader("b:7")
    with pytest.raises(ResolveBusyError):
        await query.resolve_message_id("k", reader, "x")
    assert query._cold_scans.inflight_count == 1  # the scan itself keeps going (bounded, shared)
    reader.release.set()
    await _until(lambda: query._cold_scans.inflight_count == 0)


@pytest.mark.asyncio
async def test_resolve_flight_cap_per_session(query, monkeypatch):
    import backend.session_query as sq

    monkeypatch.setattr(sq, "RESOLVE_MAX_FLIGHTS_PER_KEY", 2)
    readers = [BlockingReader() for _ in range(3)]
    tasks = [
        asyncio.ensure_future(query.resolve_message_id("k", readers[i], f"id{i}")) for i in range(2)
    ]
    await _until(lambda: query._cold_scans.inflight_count == 2)
    with pytest.raises(ResolveBusyError):
        await query.resolve_message_id("k", readers[2], "id2")
    assert (await query.resolve_message_id("other", _instant("b:9"), "id9")).cursor == "b:9"  # other key free
    for r in readers:
        r.release.set()
    await asyncio.gather(*tasks)
    assert query._cold_scans.inflight_count == 0


def _instant(cursor="b:0"):
    r = BlockingReader(cursor)
    r.release.set()
    return r


@pytest.mark.asyncio
async def test_resolve_cancelled_caller_leaves_no_flight_and_abandon_cancels_scan(query):
    reader = BlockingReader()
    t = asyncio.ensure_future(query.resolve_message_id("k", reader, "x"))
    await _until(reader.started.is_set)
    t.cancel()  # the HTTP handler went away
    with pytest.raises(asyncio.CancelledError):
        await t
    assert query._cold_scans.inflight_count == 1  # shielded: other waiters may still need it
    query._cold_scans.invalidate("k")  # delete / reset of the session
    await _until(reader.cancel_seen.is_set)
    await _until(lambda: query._cold_scans.running == 0)
    assert query._cold_scans.inflight_count == 0

    waiter = BlockingReader()
    t2 = asyncio.ensure_future(query.resolve_message_id("k2", waiter, "y"))
    await _until(waiter.started.is_set)
    query.clear_task_list("k2")  # the real reset hook abandons every flight of the key
    with pytest.raises(MessageNotFoundError):
        await t2


@pytest.mark.asyncio
async def test_saturated_cold_pool_does_not_delay_around_reads(tmp_path):
    records = _history(40)
    coordinator = await _coordinator(tmp_path, records)
    query = coordinator._session_query
    try:
        gate = threading.Event()
        started = threading.Barrier(query._cold_scans.workers + 1, timeout=5)

        def hog(cancel):
            started.wait()
            gate.wait(10)

        async def run_hog(i):
            async def body(handle):
                return await handle.run(hog)

            return await query._cold_scans.single_flight(f"hog{i}", "agents", body, empty=lambda: None)

        hogs = [asyncio.ensure_future(run_hog(i)) for i in range(query._cold_scans.workers)]
        await asyncio.to_thread(started.wait)
        app = _app(coordinator)
        t0 = time.monotonic()
        by_cursor = await _get(app, f"/api/sessions/{SID}/messages?around=b:0&after_bytes=2000")
        elapsed = time.monotonic() - t0
        assert by_cursor.status_code == 200 and elapsed < 1.0
        # the id lookup (the only pool user) waits for a worker and hits its waiter cap instead
        import backend.session_query as sq

        sq.RESOLVE_WAIT_S, saved = 0.1, sq.RESOLVE_WAIT_S
        try:
            resp = await _get(app, f"/api/sessions/{SID}/messages?around_id=a3")
        finally:
            sq.RESOLVE_WAIT_S = saved
        assert resp.status_code == 503 and resp.json()["detail"] == "resolve_busy"
        assert resp.headers["retry-after"]
        gate.set()
        await asyncio.gather(*hogs)
    finally:
        gate.set()
        coordinator._session_query.shutdown()


@pytest.mark.asyncio
async def test_around_id_flags_an_unresolved_parent(tmp_path):
    records = [
        {"type": "user", "message_id": "u0", "content": "hi", "session_id": SID},
        {"type": "tool_call", "message_id": "tc1", "tool_use_id": "toolu_orphan", "name": "Read",
         "session_id": SID},
        {"type": "assistant", "message_id": "a1", "content": "x", "session_id": SID},
    ]
    coordinator = await _coordinator(tmp_path, records)
    try:
        app = _app(coordinator)
        body = (await _get(app, f"/api/sessions/{SID}/messages?around_id=toolu_orphan")).json()
        assert body["parent_unresolved"] is True and "tc1" in _ids(body)
        plain = (await _get(app, f"/api/sessions/{SID}/messages?around_id=a1")).json()
        assert "parent_unresolved" not in plain
    finally:
        coordinator._session_query.shutdown()
