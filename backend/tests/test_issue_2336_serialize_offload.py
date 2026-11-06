"""Issue #2336: large JSON responses are serialized off the event loop.

Covers: byte-identical bodies/headers vs the inline path, the size gate, the bounded pool,
loop lag during a large build, the size hint on window payloads, and why records cannot be
spliced from their stored lines. The real-Backend lag measurement is the slow test at the bottom.
"""

import asyncio
import gc
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from backend import gc_tuning, response_offload
from backend.message_history_reader import HistoryWindow
from backend.response_offload import (
    OFFLOAD_MIN_BYTES,
    SERIALIZE_WORKERS,
    SizedPayload,
    estimate_json_bytes,
    offload_large_json,
)

REPO = Path(__file__).resolve().parents[2]


def _page(records: int, text: int = 2000) -> dict:
    return {
        "messages": [
            {"message_id": f"id-{i}", "type": "assistant", "timestamp": 1_700_000_000.5 + i,
             "content": ("é✓   quote\" back\\slash " * 80)[:text], "ratio": 1e-7 * i,
             "metadata": {"zeta": 1, "alpha": [True, None, 3.0, "ü"], "b": {"y": 1, "x": 2}}}
            for i in range(records)
        ],
        "start_cursor": "b:123", "has_more_before": True, "history_id": "abc", "snapped": True,
        "total_bytes": 987654321,
    }


def _app(payload: dict) -> FastAPI:
    app = FastAPI()

    @app.get("/inline")
    async def inline():
        return payload

    @app.get("/offloaded")
    async def offloaded():
        return await offload_large_json(SizedPayload(payload, OFFLOAD_MIN_BYTES))

    @app.get("/offloaded_small_hint")
    async def small():
        return await offload_large_json(SizedPayload(payload, 10))

    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path)


async def test_offloaded_body_and_headers_identical_to_inline():
    app = _app(_page(300))
    inline = await _get(app, "/inline")
    off = await _get(app, "/offloaded")
    small = await _get(app, "/offloaded_small_hint")
    assert off.status_code == inline.status_code == 200
    assert off.content == inline.content == small.content
    assert off.headers["content-type"] == inline.headers["content-type"] == "application/json"
    assert off.headers["content-length"] == inline.headers["content-length"]
    # key order and non-ASCII are preserved verbatim
    assert list(json.loads(off.content)["messages"][0]) == list(_page(1)["messages"][0])
    assert "é✓".encode() in off.content


async def test_non_finite_float_still_errors_like_inline():
    payload = {"messages": [], "x": float("nan")}
    with pytest.raises(ValueError):
        JSONResponse(jsonable_encoder(payload))
    with pytest.raises(ValueError):
        await offload_large_json(SizedPayload(payload, OFFLOAD_MIN_BYTES))


async def test_size_gate_small_stays_inline(monkeypatch):
    calls = []
    monkeypatch.setattr(response_offload, "_build", lambda c: calls.append(c))
    small = SizedPayload({"messages": []}, OFFLOAD_MIN_BYTES - 1)
    assert await offload_large_json(small) is small
    plain = {"messages": [1, 2, 3]}  # no hint: inline
    assert await offload_large_json(plain) is plain
    assert calls == []
    assert estimate_json_bytes({"turns": [1] * 10_000}) >= OFFLOAD_MIN_BYTES
    assert estimate_json_bytes({"turns": [1] * 10}) < OFFLOAD_MIN_BYTES


async def test_large_goes_to_pool_thread(monkeypatch):
    seen = []

    def build(content):
        seen.append(threading.current_thread().name)
        return JSONResponse(jsonable_encoder(content))

    monkeypatch.setattr(response_offload, "_build", build)
    resp = await offload_large_json(SizedPayload(_page(3), OFFLOAD_MIN_BYTES))
    assert isinstance(resp, JSONResponse)
    assert seen and seen[0].startswith("json-serialize")
    assert threading.current_thread().name not in seen


async def test_pool_is_bounded(monkeypatch):
    running = peak = 0
    lock = threading.Lock()

    def build(content):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return JSONResponse({})

    monkeypatch.setattr(response_offload, "_build", build)
    big = SizedPayload({}, OFFLOAD_MIN_BYTES)
    await asyncio.gather(*(offload_large_json(big) for _ in range(8)))
    assert peak == SERIALIZE_WORKERS == 2


async def test_loop_not_blocked_while_building(monkeypatch):
    """A blocking build (sleep stands in for the encoder) must not stall the ticker."""
    monkeypatch.setattr(
        response_offload, "_build", lambda c: (time.sleep(0.3), JSONResponse({}))[1]
    )
    lags = []

    async def ticker():
        expected = time.perf_counter() + 0.01
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            lags.append(now - expected)
            expected = now + 0.01

    task = asyncio.create_task(ticker())
    await offload_large_json(SizedPayload({}, OFFLOAD_MIN_BYTES))
    task.cancel()
    assert len(lags) >= 10 and max(lags) < 0.25


def test_window_payload_carries_size_hint_but_equals_plain_dict():
    win = HistoryWindow(
        records=[{"a": 1}], start_cursor="b:0", has_more_before=False, history_id="h",
        snapped=True, total_bytes=10, bytes_read=777,
    )
    payload = win.to_payload()
    assert isinstance(payload, SizedPayload) and payload.approx_json_bytes == 777
    assert payload == {"messages": [{"a": 1}], "start_cursor": "b:0", "has_more_before": False,
                       "history_id": "h", "snapped": True, "total_bytes": 10}
    assert json.dumps(payload) == json.dumps(dict(payload))
    assert "approx_json_bytes" not in jsonable_encoder(payload)


def test_stored_lines_cannot_be_spliced_into_the_response():
    """Raw-line assembly is rejected: the writer's separators (and, per writer, ensure_ascii)
    differ from the response's compact form, so the bytes would not match today's output."""
    record = {"type": "user", "content": "é ü", "metadata": {"a": [1, 2]}, "ts": 1.0}
    stored = json.dumps(record, ensure_ascii=False)  # DataStorageManager / fixture_gen form
    served = JSONResponse(jsonable_encoder(record)).body.decode()
    assert stored != served
    assert json.loads(stored) == json.loads(served)


def test_freeze_startup_heap_is_applied_once(monkeypatch):
    calls = []
    monkeypatch.delenv("BACKEND_DISABLE_GC_FREEZE", raising=False)
    monkeypatch.setattr(gc_tuning, "_frozen", False)
    monkeypatch.setattr(gc_tuning.gc, "freeze", lambda: calls.append("freeze"))
    monkeypatch.setattr(gc_tuning.gc, "collect", lambda *a: calls.append("collect") or 0)
    assert gc_tuning.freeze_startup_heap() is True
    assert gc_tuning.freeze_startup_heap() is False
    assert calls == ["collect", "freeze"]  # collect first: cyclic garbage is dropped, not frozen


def test_freeze_disabled_by_env(monkeypatch):
    monkeypatch.setenv("BACKEND_DISABLE_GC_FREEZE", "1")
    monkeypatch.setattr(gc_tuning, "_frozen", False)
    monkeypatch.setattr(gc_tuning.gc, "freeze", lambda: pytest.fail("must not freeze"))
    assert gc_tuning.freeze_startup_heap() is False


async def test_server_lifespan_freezes_after_initialize_but_initialize_alone_does_not(
    monkeypatch, tmp_path
):
    from backend import web_server

    order = []

    async def fake_initialize(self):
        order.append("initialize")

    async def fake_cleanup(self):
        order.append("cleanup")

    monkeypatch.setattr(web_server.BackendApp, "initialize", fake_initialize)
    monkeypatch.setattr(web_server.BackendApp, "cleanup", fake_cleanup)
    monkeypatch.setattr(web_server, "freeze_startup_heap", lambda: order.append("freeze"))
    app = web_server.create_app(data_dir=tmp_path / "data", auth_token="t")
    async with app.router.lifespan_context(app):
        assert order == ["initialize", "freeze"]
    assert order == ["initialize", "freeze", "cleanup"]


@pytest.mark.slow
def test_frozen_heap_makes_full_collection_cheap():
    """Lag-bound for the gen-2 pause (the source of the ~300 ms stalls): a full collection over
    a large long-lived graph is the pause; after the freeze the same collection is a small
    fraction of it."""
    graph = [[i, [i]] for i in range(400_000)]  # ~800k tracked objects, like the idle Backend

    def pause() -> float:
        t0 = time.perf_counter()
        gc.collect()
        return time.perf_counter() - t0

    try:
        unfrozen = min(pause() for _ in range(3))
        gc.freeze()
        frozen = min(pause() for _ in range(3))
    finally:
        gc.unfreeze()
    assert len(graph) == 400_000
    assert unfrozen > 0.02, unfrozen  # the stall the freeze removes
    assert frozen < unfrozen / 5, (frozen, unfrozen)


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_serialize_offload_quick_mode_lag(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "backend.tests.load.serialize_offload_load", "--records", "8000",
         "--mib", "12", "--repeats", "6", "--n", "3", "--clients", "4", "--per-client", "2",
         "--port", "8097", "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=540,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(out.read_text())
    for name, row in report["lag"].items():
        assert row["status"] == 200, name
    # Median of the per-request lag maxima: robust against an unrelated gen-2 GC pause.
    for name in ("before_4MiB", "after_4MiB", "around_4MiB"):
        lags = sorted(report["lag"][name]["lags"])
        assert lags[len(lags) // 2] < 50, (name, lags)
