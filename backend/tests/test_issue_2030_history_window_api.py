"""Issue #2030 (stage 1a): window API on the session + archive message routes, snapshot
hydrator parity, and legacy offset/limit behaviour."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

SID = "win-session"


def _rec(n, kind="assistant", **extra):
    rec = {"type": kind, "timestamp": 1000.0 + n, "session_id": SID, "message_id": f"m{n}",
           "content": f"c{n}", "metadata": extra.pop("metadata", {})}
    rec.update(extra)
    return rec


def _records(turns=8):
    out, n = [], 1
    out.append(_rec(0, "system", subtype="init",
                    metadata={"subtype": "init", "init_data": {"tools": ["Read"], "model": "m"}}))
    for _ in range(turns):
        out.append(_rec(n, "user"))
        n += 1
        out.append(_rec(n, "assistant"))
        n += 1
        out.append(_rec(n, "user", metadata={"has_tool_results": True}))
        n += 1
    return out


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
    return coordinator


def _app(coordinator, *, queue_cursor=42):
    from backend.application_service import ApplicationService
    from backend.routers.archives import build_router as archives_router
    from backend.routers.sessions import build_router as sessions_router

    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.get_session_exists = AsyncMock(return_value=True)
    webui.service.validate_project_exists = AsyncMock(return_value=True)
    queue = SimpleNamespace(current_cursor=queue_cursor, generation=0)
    webui.service.get_session_queue = MagicMock(return_value=queue)
    app = FastAPI()
    app.include_router(sessions_router(webui))
    app.include_router(archives_router(webui))
    return app


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_window_tail_response_shape_and_event_cursor(tmp_path):
    coordinator = await _coordinator(tmp_path, _records())
    resp = await _get(_app(coordinator), f"/api/sessions/{SID}/messages?window=tail")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "messages", "start_cursor", "has_more_before", "history_id", "snapped", "total_bytes",
        "latest_init", "event_cursor", "epoch",
    }
    assert body["event_cursor"] == 42 and body["epoch"]
    assert body["latest_init"] == {"tools": ["Read"], "model": "m"}
    assert body["has_more_before"] is True
    assert body["messages"][0]["type"] == "user"
    assert len(body["messages"]) == 6  # 2 turns x 3 records


@pytest.mark.asyncio
async def test_before_page_has_no_event_cursor_or_latest_init(tmp_path):
    coordinator = await _coordinator(tmp_path, _records())
    app = _app(coordinator)
    tail = (await _get(app, f"/api/sessions/{SID}/messages?window=tail")).json()
    resp = await _get(
        app, f"/api/sessions/{SID}/messages?before={tail['start_cursor']}&max_records=6"
    )
    body = resp.json()
    assert resp.status_code == 200
    assert "event_cursor" not in body and "epoch" not in body and "latest_init" not in body
    assert body["history_id"] == tail["history_id"]
    assert body["messages"] and body["messages"][-1]["message_id"] != tail["messages"][0]["message_id"]


@pytest.mark.asyncio
async def test_backfill_walk_reconstructs_full_history(tmp_path):
    records = _records(30)
    coordinator = await _coordinator(tmp_path, records)
    app = _app(coordinator)
    body = (await _get(app, f"/api/sessions/{SID}/messages?window=tail")).json()
    ids = [m["message_id"] for m in body["messages"]]
    while body["has_more_before"]:
        body = (await _get(
            app, f"/api/sessions/{SID}/messages?before={body['start_cursor']}&max_records=20"
        )).json()
        ids = [m["message_id"] for m in body["messages"]] + ids
    assert ids == [r["message_id"] for r in records]


@pytest.mark.asyncio
async def test_invalid_cursor_is_409_and_bad_params_are_422(tmp_path):
    coordinator = await _coordinator(tmp_path, _records())
    app = _app(coordinator)
    assert (await _get(app, f"/api/sessions/{SID}/messages?before=b:3")).status_code == 409
    assert (await _get(app, f"/api/sessions/{SID}/messages?before=junk")).status_code == 409
    assert (await _get(app, f"/api/sessions/{SID}/messages?window=head")).status_code == 422
    assert (await _get(
        app, f"/api/sessions/{SID}/messages?window=tail&before=b:0"
    )).status_code == 422


@pytest.mark.asyncio
async def test_legacy_offset_limit_route_unchanged(tmp_path):
    records = _records()
    coordinator = await _coordinator(tmp_path, records)
    body = (await _get(_app(coordinator), f"/api/sessions/{SID}/messages?limit=5&offset=2")).json()
    assert [m["message_id"] for m in body["messages"]] == [r["message_id"] for r in records[2:7]]
    assert body["total_count"] == len(records)
    assert body["has_more"] is True and body["offset"] == 2 and body["limit"] == 5
    assert body["event_cursor"] == 42
    assert not ({"start_cursor", "history_id", "latest_init"} & set(body))


@pytest.mark.asyncio
async def test_window_unknown_session_storage_returns_empty_legacy_shape(tmp_path):
    from backend.session_coordinator import SessionCoordinator

    coordinator = SessionCoordinator(data_dir=tmp_path)
    result = await coordinator.get_session_messages("nope", window="tail")
    assert result["messages"] == []


# --------------------------------------------------------------------------- archives


def _archive_setup(tmp_path, records):
    from backend.application_service import ApplicationService
    from backend.legion.archive_manager import ArchiveManager

    coordinator = MagicMock()
    manager = ArchiveManager(SimpleNamespace(
        session_port=SimpleNamespace(session_manager=SimpleNamespace(data_dir=tmp_path))
    ))
    archive_dir = manager.archives_dir / SID / "20260101T000000"
    archive_dir.mkdir(parents=True)
    (archive_dir / "messages.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    from backend.archive_coordinator import ArchiveCoordinator

    archive_coordinator = ArchiveCoordinator(coordinator=coordinator)
    coordinator.legion_system = SimpleNamespace(archive_manager=manager)
    return archive_coordinator, ApplicationService, manager


@pytest.mark.asyncio
async def test_archive_window_uses_same_reader_and_has_no_event_cursor(tmp_path):
    records = _records(2000 // 3)  # > 1000 records: today's route truncates to the oldest 1000
    archive_coordinator, _, _ = _archive_setup(tmp_path, records)
    tail = await archive_coordinator.get_archive_messages(SID, "20260101T000000", window="tail")
    assert tail["messages"][-1]["message_id"] == records[-1]["message_id"]
    assert tail["latest_init"]["tools"] == ["Read"]
    assert "event_cursor" not in tail and "epoch" not in tail
    ids = [m["message_id"] for m in tail["messages"]]
    page = tail
    while page["has_more_before"]:
        page = await archive_coordinator.get_archive_messages(
            SID, "20260101T000000", before=page["start_cursor"], max_records=300
        )
        ids = [m["message_id"] for m in page["messages"]] + ids
    assert ids == [r["message_id"] for r in records]  # archive no longer silently truncated


@pytest.mark.asyncio
async def test_archive_legacy_offset_limit_unchanged_and_missing_archive_empty(tmp_path):
    records = _records(5)
    archive_coordinator, _, _ = _archive_setup(tmp_path, records)
    legacy = await archive_coordinator.get_archive_messages(
        SID, "20260101T000000", offset=1, limit=3
    )
    assert [m["message_id"] for m in legacy["messages"]] == [r["message_id"] for r in records[1:4]]
    missing = await archive_coordinator.get_archive_messages(SID, "nope", window="tail")
    assert missing["messages"] == [] and missing["has_more_before"] is False


@pytest.mark.asyncio
async def test_archive_route_maps_cursor_invalid_to_409(tmp_path):
    from backend.application_service import ApplicationService
    from backend.routers.archives import build_router

    records = _records(5)
    archive_coordinator, _, _ = _archive_setup(tmp_path, records)
    coordinator = MagicMock()
    coordinator.get_archive_messages = archive_coordinator.get_archive_messages
    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.validate_project_exists = AsyncMock(return_value=True)
    app = FastAPI()
    app.include_router(build_router(webui))
    url = f"/api/projects/p/archives/{SID}/20260101T000000/messages"
    ok = await _get(app, url + "?window=tail")
    assert ok.status_code == 200 and "latest_init" in ok.json()
    assert (await _get(app, url + "?before=b:7")).status_code == 409


# --------------------------------------------------------------------------- hydrators


@pytest.mark.asyncio
async def test_snapshot_hydrators_match_old_full_read_versions(tmp_path):
    """The streamed hydrators return exactly what the old whole-file read produced."""
    from backend.background_tasks import latest_background_tasks
    from backend.task_registry import TaskLegRegistry

    def sysrec(n, subtype, **md):
        return _rec(n, "system", subtype=subtype, metadata={"subtype": subtype, **md})

    records = [
        _rec(0, "user"),
        sysrec(1, "client_launched"),
        sysrec(2, "task_started", task_id="t1", tool_use_id="u1", description="d"),
        sysrec(3, "task_progress", task_id="t1", description="p"),
        _rec(4, "assistant", content="mentions \"task_started\" in text"),
        sysrec(5, "background_tasks_changed",
               init_data={"tasks": [{"task_id": "b1", "task_type": "local_bash", "description": "x"}]}),
        sysrec(6, "task_notification", task_id="t1", status="completed"),
        sysrec(7, "task_started", task_id="t2", task_type="local_bash"),
        {"type": "system", "message_id": "mx", "timestamp": 1.0, "subtype": "task_started",
         "content": "top-level subtype only, no metadata.subtype"},
    ]
    coordinator = await _coordinator(tmp_path, records)
    storage = coordinator._storage_managers[SID]

    old_registry = TaskLegRegistry()
    full = await storage.read_messages()
    for raw in full:
        if raw.get("type") != "system":
            continue
        md = raw.get("metadata") or {}
        if md.get("subtype"):
            old_registry.apply_frame(md["subtype"], md, raw.get("timestamp"))
    old_tasks = latest_background_tasks(full)

    new_registry = await coordinator._get_task_leg_registry(SID)
    assert new_registry.snapshot() == old_registry.snapshot()
    assert await coordinator._session_query._get_background_tasks(SID) == old_tasks
    assert old_tasks and old_registry.snapshot()  # non-vacuous


@pytest.mark.asyncio
async def test_tasks_route_returns_snapshot_and_404(tmp_path):
    create = {"type": "tool_call", "tool_use_id": "t1", "name": "TaskCreate", "status": "completed",
              "input": {"subject": "A"}, "result": "Task #1 created", "session_id": SID,
              "message_id": "tc1"}
    coordinator = await _coordinator(tmp_path, [_rec(0, "user"), create])
    app = _app(coordinator)
    resp = await _get(app, f"/api/sessions/{SID}/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == SID
    assert body["tasks"] == [{
        "id": "1", "subject": "A", "description": "", "status": "pending", "activeForm": None,
        "owner": None, "blocks": [], "blockedBy": [], "metadata": {},
    }]
    from backend.application_service import ApplicationService

    webui = MagicMock()
    webui.service = ApplicationService(coordinator)
    webui.service.get_session_exists = AsyncMock(return_value=False)
    from backend.routers.sessions import build_router

    missing_app = FastAPI()
    missing_app.include_router(build_router(webui))
    assert (await _get(missing_app, "/api/sessions/zzz/tasks")).status_code == 404
