"""In-process real Backend + load-test instrumentation (issue #2030 stage 2 PR A, §3.5).

Runs the real ``BackendApp`` (real SessionCoordinator, routers, auth) under uvicorn on a TCP
port, and adds in the SAME event loop:

- a 10 ms ticker recording event-loop lag (actual - expected wake-up),
- ``GET  /api/__load/metrics[?reset=1]``: lag stats, RSS now / peak, thread count, cold-scan and
  history-index cache state (tolerant of a Backend without those features, so the same script
  measures ``main`` as the baseline),
- ``POST /api/__load/attach/{session_id}``: creates the session's storage manager the way a
  started session has one (the browser path needs it for ``window=tail``),
- ``POST /api/__load/stop/{session_id}``: drop the storage manager + the stop-time cache hooks
  (what ``terminate`` does), without needing an SDK.

Usage: ``uv run python -m backend.tests.load.serve_backend --data-dir D --port P --token T``
(set HOME to a scratch dir so the real ~/.config/cc_webui/config.json is untouched).
"""

import argparse
import asyncio
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

TICK = 0.010


class BlockSampler(threading.Thread):
    """Attribution aid: when the loop heartbeat is stale > ``threshold`` s, sample the main
    thread's stack every 10 ms and count the innermost frames (reported via /metrics)."""

    def __init__(self, main_ident: int, monitor: "LagMonitor", threshold: float = 0.04):
        super().__init__(daemon=True)
        self.main_ident, self.monitor, self.threshold = main_ident, monitor, threshold
        self.counts: dict[str, int] = {}

    def run(self) -> None:
        import traceback

        while True:
            time.sleep(0.01)
            if time.monotonic() - self.monitor.heartbeat < self.threshold:
                continue
            frame = sys._current_frames().get(self.main_ident)
            if frame is None:
                continue
            stack = traceback.extract_stack(frame)
            repo = [f for f in stack if "/backend/" in f.filename or "/shared/" in f.filename]
            inner = stack[-1]
            key = " <- ".join(
                f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in ([inner] + repo[::-1][:3])
            )
            self.counts[key] = self.counts.get(key, 0) + 1


class LagMonitor:
    def __init__(self):
        self.samples: list[float] = []
        self._stop = False
        self.heartbeat = time.monotonic()

    async def run(self):
        loop = asyncio.get_running_loop()
        expected = loop.time() + TICK
        while not self._stop:
            await asyncio.sleep(TICK)
            self.heartbeat = time.monotonic()
            now = loop.time()
            self.samples.append(max(0.0, now - expected) * 1000.0)
            expected = now + TICK

    def snapshot(self, reset: bool) -> dict:
        data = sorted(self.samples)
        if reset:
            self.samples = []
        if not data:
            return {"n": 0, "max_ms": 0.0, "p99_ms": 0.0, "p50_ms": 0.0}
        return {
            "n": len(data),
            "max_ms": round(data[-1], 2),
            "p99_ms": round(data[min(len(data) - 1, int(len(data) * 0.99))], 2),
            "p50_ms": round(data[len(data) // 2], 2),
        }


def _proc_status() -> dict:
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith(("VmRSS", "VmHWM")):
                key, value = line.split(":")
                out[key] = int(value.split()[0]) / 1024.0  # MiB
    return out


def _reset_peak_rss() -> None:
    try:
        with open("/proc/self/clear_refs", "w") as f:
            f.write("5")
    except OSError:
        pass


def build(args):
    if os.environ.get("LOAD_INDEX_THROTTLE") is not None:  # experiment knob
        from backend import history_index

        history_index.INDEX_SCAN_THROTTLE = float(os.environ["LOAD_INDEX_THROTTLE"])
    if os.environ.get("LOAD_SEARCH_THROTTLE") is not None:  # experiment knobs (search pacing; defaults 0.003 s / 3.0)
        from backend import message_history_reader

        message_history_reader._PACE_THROTTLE = float(os.environ["LOAD_SEARCH_THROTTLE"])
        message_history_reader._PACE_AFTER_S = float(os.environ.get("LOAD_SEARCH_PACE_AFTER", "0.003"))
    if os.environ.get("LOAD_RESOLVE_THROTTLE") is not None:  # experiment knob (id lookup pacing)
        from backend import message_history_reader as _mhr

        _mhr.RESOLVE_THROTTLE = float(os.environ["LOAD_RESOLVE_THROTTLE"])
    from backend.tenant_context import TenantContext
    from backend.web_server import BackendApp

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    from backend.config_manager import default_config_file, ensure_config_file

    config_file = default_config_file()
    ensure_config_file(config_file)
    tenant = TenantContext(data_dir=data_dir, config_file=config_file, host="127.0.0.1")
    backend = BackendApp(
        data_dir, auth_token=args.token, host="127.0.0.1", port=args.port, tenant_context=tenant
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await backend.initialize()
        yield
        await backend.cleanup()

    backend.app.router.lifespan_context = lifespan
    return backend


def install_routes(backend, monitor: LagMonitor) -> None:
    app = backend.app
    coordinator = backend.service.coordinator

    @app.get("/api/__load/metrics")
    async def metrics(reset: int = 0):
        query = coordinator._session_query
        scans = getattr(query, "_cold_scans", None)
        indexes = getattr(query, "_history_indexes", {})
        out = {
            "lag": monitor.snapshot(bool(reset)),
            "mem": _proc_status(),
            "threads": threading.active_count(),
            "task_lists": len(query._task_lists),
            "task_list_keys": list(query._task_lists),
            "leg_registries": len(coordinator._task_leg_registries),
            "leg_registry_keys": list(coordinator._task_leg_registries),
            "background_keys": list(coordinator._background_tasks),
            "leg_registry_nonempty": [
                k for k, reg in coordinator._task_leg_registries.items() if reg.snapshot()
            ],
            "background_nonempty": [k for k, v in coordinator._background_tasks.items() if v],
            "task_list_nonempty": [k for k, st in query._task_lists.items() if st.snapshot()],
            "index_nonempty": [k for k, st in indexes.items() if st.turn_count or st.end],
            "index_cached": len(indexes),
            "index_cached_keys": list(indexes),
            "index_pending": len(getattr(query, "_history_index_pending", {})),
            "task_list_pending": len(query._task_list_pending),
        }
        if indexes:
            out["index_bytes_est"] = sum(s.approx_bytes() for s in indexes.values())
            out["index_turns"] = sum(s.turn_count for s in indexes.values())
        if scans is not None:
            out["scans"] = {
                "workers": scans.workers,
                "inflight": scans.inflight_count,
                "inflight_keys": sorted(scans.inflight_keys()),
                "running": scans.running,
                "max_running": scans.max_running,
                "max_running_per_session": max(scans.max_running_per_key.values(), default=1),
                "index_over_snapshot": scans.index_over_snapshot,
                "snapshot_over_index": scans.snapshot_over_index,
            }
        searches = getattr(query, "_searches", None)
        if searches is not None:  # issue #2030 stage 3 PR 3B
            out["search"] = {
                "workers": searches.workers,
                "inflight": searches.inflight_count,
                "inflight_keys": sorted(searches.inflight_keys()),
                "waiters": searches.waiter_count,
                "running": searches.running,
                "max_running": searches.max_running,
                "superseded": searches.superseded,
                "disconnected": searches.disconnected,
                "timed_out": searches.timed_out,
            }
        sampler = getattr(monitor, "sampler", None)
        if sampler is not None:
            out["block_stacks"] = sorted(sampler.counts.items(), key=lambda kv: -kv[1])[:12]
            if reset:
                sampler.counts.clear()
        if reset:
            _reset_peak_rss()
            if searches is not None:
                searches._scans.max_running = 0
            if scans is not None:
                scans.max_running = 0
                scans.max_running_per_key.clear()
                scans.index_over_snapshot = scans.snapshot_over_index = 0
        return out

    @app.post("/api/__load/attach/{session_id}")
    async def attach(session_id: str):
        storage = await coordinator.get_or_create_storage_manager(session_id)
        if storage is None:
            raise HTTPException(status_code=404, detail="no session dir")
        return {"ok": True}

    @app.post("/api/__load/stop/{session_id}")
    async def stop(session_id: str):
        coordinator._storage_managers.pop(session_id, None)
        query = coordinator._session_query
        drop = getattr(query, "drop_history_index", None)
        if drop is not None:
            drop(session_id, abandon_scans=True)
        return {"ok": True}


async def amain(args) -> None:
    backend = build(args)
    monitor = LagMonitor()
    install_routes(backend, monitor)
    config = uvicorn.Config(
        backend.app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    ticker = asyncio.create_task(monitor.run())
    monitor.sampler = BlockSampler(threading.get_ident(), monitor)
    monitor.sampler.start()
    try:
        await server.serve()
    finally:
        monitor._stop = True
        ticker.cancel()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    started = time.time()
    asyncio.run(amain(args))
    print(f"serve_backend exited after {time.time() - started:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
