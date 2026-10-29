"""Load / concurrency acceptance harness for the history index + cold-scan pool (issue #2030,
stage 2 PR A, plan §3.5). Not collected by pytest (see ``test_history_index_load.py`` for the
slow quick-mode wrapper).

Runs a REAL Backend (``serve_backend.py``: real BackendApp/coordinator/routers/auth under
uvicorn, plus a 10 ms event-loop ticker and a metrics route) against generated fixtures and
simulated browser clients over HTTP.

    uv run python -m backend.tests.load.history_index_load --phase all --out report.json
    uv run python -m backend.tests.load.history_index_load --phase firstpaint --tree /tmp/main_wt

Phases: ``cold`` (cold index scan time / peak RSS / loop lag / response size per fixture class),
``firstpaint`` (window + agents + tasks latency with every cold scan of 1 and of 4 sessions
triggered together; run it against ``--tree`` = a ``main`` checkout for the baseline), ``load``
(N simulated browsers + cancellation / delete / reset churn + probes; asserts a-g), ``memory``
(~70 sessions touched).
"""

import argparse
import asyncio
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from .fixture_gen import PRESETS, generate_session_file

REPO = Path(__file__).resolve().parents[3]
TOKEN = "loadtest-token"


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    return data[min(len(data) - 1, int(len(data) * 0.95))]


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    return data[min(len(data) - 1, int(len(data) * q))]


class Backend:
    """One Backend subprocess over a scratch data dir and HOME."""

    def __init__(self, tree: Path, port: int, workdir: Path, *, perf: dict | None = None):
        self.tree = tree
        self.port = port
        self.workdir = workdir
        self.data_dir = workdir / "data"
        self.home = workdir / "home"
        self.perf = perf
        self.proc: subprocess.Popen | None = None
        self.client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        load_src = REPO / "backend" / "tests" / "load"
        load_dst = self.tree / "backend" / "tests" / "load"
        if load_src.resolve() != load_dst.resolve():
            shutil.copytree(load_src, load_dst, dirs_exist_ok=True)
            (self.tree / "backend" / "tests" / "__init__.py").touch()
        if self.perf:
            cfg_dir = self.home / ".config" / "cc_webui"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "config.json").write_text(json.dumps({"performance": self.perf}))
        env = dict(os.environ, HOME=str(self.home), UV_NO_PROGRESS="1")
        log = open(self.workdir / "backend.log", "w")
        self.proc = subprocess.Popen(
            ["uv", "run", "python", "-m", "backend.tests.load.serve_backend",
             "--data-dir", str(self.data_dir), "--port", str(self.port), "--token", TOKEN],
            cwd=self.tree, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        self.client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.port}",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                r = await self.client.get("/ready")
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("backend did not become ready")
        r = await self.client.post(
            "/api/projects", json={"name": "load", "working_directory": str(self.workdir)}
        )
        r.raise_for_status()
        self.project_id = r.json()["project"]["project_id"]

    async def stop(self) -> None:
        if self.client:
            await self.client.aclose()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # -------------------------------------------------------------------- sessions

    async def new_session(self, source: Path | None = None, name: str = "s") -> str:
        r = await self.client.post(
            "/api/sessions", json={"project_id": self.project_id, "name": name}
        )
        r.raise_for_status()
        sid = r.json()["session_id"]
        session_dir = self.data_dir / "sessions" / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        if source is not None:
            await asyncio.to_thread(shutil.copyfile, source, session_dir / "messages.jsonl")
        r = await self.client.post(f"/api/__load/attach/{sid}")
        r.raise_for_status()
        return sid

    async def metrics(self, reset: bool = False) -> dict:
        r = await self.client.get("/api/__load/metrics", params={"reset": int(reset)})
        r.raise_for_status()
        data = r.json()
        # A baseline (main) Backend has no cold-scan manager / index: neutral defaults.
        data.setdefault("scans", {"workers": 0, "inflight": 0, "inflight_keys": [], "running": 0,
                                  "max_running": 0, "max_running_per_session": 0,
                                  "index_over_snapshot": 0, "snapshot_over_index": 0})
        for key in ("index_cached_keys", "index_nonempty"):
            data.setdefault(key, [])
        return data

    async def timed(self, method: str, url: str, **kw) -> tuple[float, httpx.Response | None]:
        t0 = time.perf_counter()
        try:
            r = await self.client.request(method, url, **kw)
        except httpx.HTTPError:
            return (time.perf_counter() - t0) * 1000.0, None
        return (time.perf_counter() - t0) * 1000.0, r


def window_url(sid: str) -> str:
    return f"/api/sessions/{sid}/messages?window=tail"


async def open_session(be: Backend, sid: str, *, with_index: bool) -> dict:
    """The browser's open order: window, then agents/background/tasks, then (low priority) the
    index after the snapshots landed. Returns per-request latencies (ms)."""
    out: dict[str, float] = {}
    t, _ = await be.timed("GET", window_url(sid))
    out["window"] = t
    snaps = await asyncio.gather(
        be.timed("GET", f"/api/sessions/{sid}/background_agents"),
        be.timed("GET", f"/api/sessions/{sid}/tasks"),
        be.timed("GET", f"/api/sessions/{sid}/background_tasks"),
    )
    out["agents"], out["tasks"], out["background"] = (s[0] for s in snaps)
    if with_index:
        t, r = await be.timed("GET", f"/api/sessions/{sid}/history_index")
        out["index"] = t
        out["index_status"] = r.status_code if r is not None else 0
    return out


# ============================================================================ phases


async def phase_cold(be: Backend, fixtures: dict[str, Path], report: dict) -> None:
    """Cold index scan time / peak RSS / loop lag / sizes per fixture class (idle backend)."""
    rows = {}
    for name, path in fixtures.items():
        sid = await be.new_session(path, name)
        await asyncio.sleep(1.0)
        before = await be.metrics(reset=True)
        t, r = await be.timed("GET", f"/api/sessions/{sid}/history_index")
        after = await be.metrics()
        raw = r.content if r is not None and r.status_code == 200 else b""
        warm_t, _ = await be.timed("GET", f"/api/sessions/{sid}/history_index")
        body = json.loads(raw) if raw else {}
        rows[name] = {
            "file_mb": round(path.stat().st_size / 2**20, 1),
            "status": r.status_code if r is not None else 0,
            "cold_ms": round(t, 1),
            "warm_ms": round(warm_t, 1),
            "rss_before_mb": round(before["mem"]["VmRSS"], 1),
            "rss_peak_mb": round(after["mem"]["VmHWM"], 1),
            "rss_peak_delta_mb": round(after["mem"]["VmHWM"] - before["mem"]["VmRSS"], 1),
            "loop_lag_max_ms": after["lag"]["max_ms"],
            "loop_lag_p99_ms": after["lag"]["p99_ms"],
            "turns": len(body.get("turns", [])),
            "compactions": len(body.get("compactions", [])),
            "response_raw_bytes": len(raw),
            "response_gzip_bytes": len(gzip.compress(raw)) if raw else 0,
        }
    report["cold"] = rows


async def phase_firstpaint(be: Backend, fixtures: dict[str, Path], report: dict, *, with_index: bool) -> None:
    """Window / agents / tasks latency with ALL cold scans of a session triggered together
    (agents, background, tasks, window and — on the branch — the index), for one session per
    class and for four sessions at once (2 small + medium + huge)."""
    single = {}
    for name, path in fixtures.items():
        sid = await be.new_session(path, name)
        await be.metrics(reset=True)
        results = await asyncio.gather(
            be.timed("GET", window_url(sid)),
            be.timed("GET", f"/api/sessions/{sid}/background_agents"),
            be.timed("GET", f"/api/sessions/{sid}/tasks"),
            be.timed("GET", f"/api/sessions/{sid}/background_tasks"),
            *( [be.timed("GET", f"/api/sessions/{sid}/history_index")] if with_index else [] ),
        )
        m = await be.metrics()
        keys = ["window", "agents", "tasks", "background", "index"]
        single[name] = {k: round(r[0], 1) for k, r in zip(keys, results, strict=False)}
        single[name]["loop_lag_max_ms"] = m["lag"]["max_ms"]
        single[name]["rss_peak_mb"] = round(m["mem"]["VmHWM"], 1)
        await asyncio.sleep(0.5)
    multi_sids = [
        await be.new_session(fixtures["small"], "m-small-a"),
        await be.new_session(fixtures["small"], "m-small-b"),
        await be.new_session(fixtures["medium"], "m-medium"),
        await be.new_session(fixtures["huge"], "m-huge"),
    ]
    await be.metrics(reset=True)
    t0 = time.perf_counter()
    opened = await asyncio.gather(*[
        open_cold_together(be, sid, with_index=with_index) for sid in multi_sids
    ])
    m = await be.metrics()
    report["firstpaint"] = {
        "single_session_all_scans_together": single,
        "four_sessions_together": {
            label: {k: round(v, 1) for k, v in res.items() if isinstance(v, float)}
            for label, res in zip(["small-a", "small-b", "medium", "huge"], opened, strict=True)
        },
        "four_sessions_wall_s": round(time.perf_counter() - t0, 2),
        "four_sessions_loop_lag_max_ms": m["lag"]["max_ms"],
        "four_sessions_rss_peak_mb": round(m["mem"]["VmHWM"], 1),
    }


async def open_cold_together(be: Backend, sid: str, *, with_index: bool) -> dict:
    results = await asyncio.gather(
        be.timed("GET", window_url(sid)),
        be.timed("GET", f"/api/sessions/{sid}/background_agents"),
        be.timed("GET", f"/api/sessions/{sid}/tasks"),
        be.timed("GET", f"/api/sessions/{sid}/background_tasks"),
        *( [be.timed("GET", f"/api/sessions/{sid}/history_index")] if with_index else [] ),
    )
    keys = ["window", "agents", "tasks", "background", "index"]
    return {k: r[0] for k, r in zip(keys, results, strict=False)}


def _probe_worker(args) -> None:
    """Separate-process probe (its own interpreter/event loop, so client-side loop lag never
    pollutes the latencies): window=tail on Y and warm /tasks on W every ``interval`` until the
    stop file appears; writes the raw latencies to ``out``."""
    window_ms: list[float] = []
    tasks_ms: list[float] = []

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{args.port}", headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30.0,
        ) as client:
            while not Path(args.probe_stop).exists():
                for url, sink in ((window_url(args.probe_y), window_ms),
                                  (f"/api/sessions/{args.probe_w}/tasks", tasks_ms)):
                    t0 = time.perf_counter()
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            sink.append((time.perf_counter() - t0) * 1000.0)
                    except httpx.HTTPError:
                        pass
                await asyncio.sleep(args.probe_interval)

    asyncio.run(run())
    Path(args.probe_out).write_text(json.dumps({"window": window_ms, "tasks": tasks_ms}))


class Prober:
    """Window=tail on a pinned small session Y and warm /tasks on W, from a separate process."""

    def __init__(self, be: Backend, sid_y: str, sid_w: str, interval: float = 0.05):
        self.be, self.y, self.w, self.interval = be, sid_y, sid_w, interval
        self.window: list[float] = []
        self.tasks: list[float] = []
        self._dir = Path(tempfile.mkdtemp(prefix="hi_probe_"))
        self._proc: subprocess.Popen | None = None

    def start(self):
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "backend.tests.load.history_index_load", "--probe",
             "--port", str(self.be.port), "--probe-y", self.y, "--probe-w", self.w,
             "--probe-interval", str(self.interval), "--probe-stop", str(self._dir / "stop"),
             "--probe-out", str(self._dir / "out.json")],
            cwd=REPO,
        )

    async def stop(self):
        (self._dir / "stop").write_text("1")
        await asyncio.to_thread(self._proc.wait, 60)
        data = json.loads((self._dir / "out.json").read_text())
        self.window, self.tasks = data["window"], data["tasks"]
        shutil.rmtree(self._dir, ignore_errors=True)

    def summary(self) -> dict:
        return {
            "window_n": len(self.window), "window_p50_ms": round(pct(self.window, 0.5), 2),
            "window_p95_ms": round(p95(self.window), 2), "window_max_ms": round(max(self.window or [0]), 2),
            "tasks_n": len(self.tasks), "tasks_p50_ms": round(pct(self.tasks, 0.5), 2),
            "tasks_p95_ms": round(p95(self.tasks), 2), "tasks_max_ms": round(max(self.tasks or [0]), 2),
        }


async def phase_load(be: Backend, fixtures: dict[str, Path], report: dict, *,
                     duration: float, browsers: int, seed: int, args_scale: float = 1.0,
                     with_index: bool = True, churn: bool = True) -> None:
    sid_y = await be.new_session(fixtures["small"], "Y")
    sid_w = await be.new_session(fixtures["small"], "W")
    await open_session(be, sid_y, with_index=with_index)
    await open_session(be, sid_w, with_index=with_index)  # W's /tasks is warm from here on
    await asyncio.sleep(1.0)

    # ---- idle baseline
    idle = Prober(be, sid_y, sid_w, interval=0.02)
    idle.start()
    await asyncio.sleep(max(6.0, duration / 5) + 1.5)  # +1.5: probe interpreter start-up
    await idle.stop()
    idle_s = idle.summary()

    # ---- (f) session Y vs a cold 113 MB scan on X: cold X = all scans + index
    sid_x = await be.new_session(fixtures["huge"], "X")
    await asyncio.sleep(0.5)
    probe_x = Prober(be, sid_y, sid_w, interval=0.02)
    probe_x.start()
    await asyncio.sleep(1.5)  # probe interpreter start-up
    x_open = await open_session(be, sid_x, with_index=with_index)
    await probe_x.stop()
    x_s = probe_x.summary()

    # ---- main load: N browsers + churn + probes. Every cold session is created BEFORE the
    # measured window (session creation is setup, not load), so the probes see scan load only.
    fresh_queue: list[str] = []
    for cls, count in (("small", 14), ("medium", 12), ("huge", 3)):
        for _ in range(max(1, int(count * max(args_scale, 0.3)))):
            fresh_queue.append(await be.new_session(fixtures[cls], f"fresh-{cls}"))
    random.Random(seed).shuffle(fresh_queue)
    churn_queue: list[str] = []
    for cls, count in (("small", 8), ("medium", 10), ("huge", 4)):
        for _ in range(max(1, int(count * max(args_scale, 0.3)))):
            churn_queue.append(await be.new_session(fixtures[cls], f"churn-{cls}"))
    random.Random(seed + 1).shuffle(churn_queue)
    await be.metrics(reset=True)
    pool_ids = [await be.new_session(fixtures[c], f"pool-{c}-{i}")
                for i, c in enumerate(["small", "small", "medium", "medium", "small", "medium",
                                        "small", "medium", "huge", "small", "medium", "small"])]
    deleted: set[str] = set()
    reset_ids: set[str] = set()
    ops = {"opens": 0, "abandoned": 0, "aba": 0, "deleted": 0, "reset": 0, "reset_failed": 0,
           "stops": 0, "fresh": 0}
    index_durations: list[float] = []
    other_durations: list[float] = []
    index_status: dict[int, int] = {}
    stop_at = time.time() + duration
    prober = Prober(be, sid_y, sid_w, interval=0.02)
    prober.start()
    await asyncio.sleep(1.5)

    async def timed_open(sid: str, abandon_after: float | None) -> None:
        coro = open_session(be, sid, with_index=with_index)
        if abandon_after is None:
            res = await coro
            ops["opens"] += 1
            if "index" in res:
                index_durations.append(res["index"])
                index_status[res["index_status"]] = index_status.get(res["index_status"], 0) + 1
            for k in ("window", "agents", "tasks", "background"):
                other_durations.append(res[k])
        else:
            try:
                await asyncio.wait_for(coro, abandon_after)
            except TimeoutError:
                ops["abandoned"] += 1  # client disconnected mid-request

    async def browser(idx: int) -> None:
        r = random.Random(seed * 100 + idx)
        while time.time() < stop_at:
            roll = r.random()
            if roll < 0.35 and fresh_queue:  # a brand new cold session (sessions come and go)
                sid = fresh_queue.pop()
                ops["fresh"] += 1
                pool_ids.append(sid)
            else:
                sid = r.choice([s for s in pool_ids if s not in deleted])
            if r.random() < 0.2:  # stopped / restarted session: index cache dropped
                await be.client.post(f"/api/__load/stop/{sid}")
                await be.client.post(f"/api/__load/attach/{sid}")
                ops["stops"] += 1
            mode = r.random()
            if mode < 0.15:  # rapid A -> B -> A, each abandoned quickly
                other = r.choice([s for s in pool_ids if s not in deleted])
                for target in (sid, other, sid):
                    await timed_open(target, r.uniform(0.01, 0.08))
                ops["aba"] += 1
            elif mode < 0.40:
                await timed_open(sid, r.uniform(0.02, 0.4))
            else:
                await timed_open(sid, None)
            await asyncio.sleep(r.uniform(0.0, 0.2))

    async def churner() -> None:
        r = random.Random(seed + 999)
        while time.time() < stop_at and churn_queue:
            sid = churn_queue.pop()
            # kick every cold scan, then kill the session mid-scan
            kick = [asyncio.create_task(open_session(be, sid, with_index=with_index))]
            await asyncio.sleep(r.uniform(0.05, 0.6))
            if r.random() < 0.5:
                rr = await be.client.delete(f"/api/sessions/{sid}")
                if rr.status_code == 200:
                    deleted.add(sid)
                    ops["deleted"] += 1
            else:
                reset_ids.add(sid)
                rr = await be.client.post(f"/api/sessions/{sid}/reset")
                if rr.status_code == 200:
                    ops["reset"] += 1
                else:
                    ops["reset_failed"] += 1
            await asyncio.gather(*kick, return_exceptions=True)
            await asyncio.sleep(r.uniform(0.2, 1.0))

    await asyncio.gather(*[browser(i) for i in range(browsers)], *([churner()] if churn else []))
    await prober.stop()
    during = await be.metrics()
    load_s = prober.summary()

    # ---- settle + invariants (d)
    settle_deadline = time.time() + 180
    final = during
    while time.time() < settle_deadline:
        final = await be.metrics()
        if final["scans"]["inflight"] == 0 and final["scans"]["running"] == 0:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)
    final = await be.metrics()
    # Deleted sessions: nothing may be cached. Reset sessions: a fresh (empty) cache is legal
    # (history wiped, re-hydrated from the empty file) but pre-reset content must not survive.
    leaked = {
        "deleted_index": [k for k in final["index_cached_keys"] if k in deleted],
        "deleted_task_list": [k for k in final["task_list_keys"] if k in deleted],
        "deleted_leg_registry": [k for k in final["leg_registry_keys"] if k in deleted],
        "deleted_background": [k for k in final["background_keys"] if k in deleted],
        "reset_index_nonempty": [k for k in final["index_nonempty"] if k in reset_ids],
        "reset_task_list_nonempty": [k for k in final["task_list_nonempty"] if k in reset_ids],
        "reset_leg_registry_nonempty": [k for k in final["leg_registry_nonempty"] if k in reset_ids],
        "reset_background_nonempty": [k for k in final["background_nonempty"] if k in reset_ids],
    }
    idle_cap = max(1.25 * idle_s["window_p95_ms"], 1.0)
    idle_cap_t = max(1.25 * idle_s["tasks_p95_ms"], 1.0)
    report["load"] = {
        "params": {"duration_s": duration, "browsers": browsers, "distinct_sessions": len(set(pool_ids)),
                   "workers": final["scans"]["workers"]},
        "ops": ops,
        "idle_probe": idle_s,
        "cold_113mb_on_X_probe_of_Y_and_W": {**x_s, "X_open_ms": {k: round(v, 1) if isinstance(v, float) else v for k, v in x_open.items()}},
        "load_probe": load_s,
        "loop_lag_during_load": during["lag"],
        "peak_rss_mb": round(during["mem"]["VmHWM"], 1),
        "waiters": {
            "index_n": len(index_durations), "index_max_s": round(max(index_durations or [0]) / 1000, 2),
            "index_p95_ms": round(p95(index_durations), 1), "index_status_counts": index_status,
            "other_n": len(other_durations), "other_max_s": round(max(other_durations or [0]) / 1000, 2),
            "other_p95_ms": round(p95(other_durations), 1),
        },
        "scans_during_load": during["scans"],
        "final_state": {"scans": final["scans"], "threads": final["threads"], "index_pending": final["index_pending"],
                        "task_list_pending": final["task_list_pending"], "leaked_for_deleted_or_reset": leaked},
        "checks": {
            "a_window_p95_within_25pct_or_abs500": load_s["window_p95_ms"] <= max(idle_cap, 0) or load_s["window_p95_ms"] <= 500,
            "a_window_p95_within_25pct_strict": load_s["window_p95_ms"] <= idle_cap,
            "a_tasks_p95_within_25pct_strict": load_s["tasks_p95_ms"] <= idle_cap_t,
            "a_abs_cap_500ms": load_s["window_p95_ms"] <= 500 and load_s["tasks_p95_ms"] <= 500,
            "b_lag_max_lt_100ms": during["lag"]["max_ms"] < 100,
            "b_lag_p99_lt_30ms": during["lag"]["p99_ms"] < 30,
            "c_index_waiters_le_20s_plus": max(index_durations or [0]) <= 21_000,
            "c_other_waiters_le_5s": max(other_durations or [0]) <= 5_000,
            "d_no_inflight_or_running": final["scans"]["inflight"] == 0 and final["scans"]["running"] == 0,
            "d_no_pending_buffers": final["index_pending"] == 0 and final["task_list_pending"] == 0,
            "d_no_leaked_state": not any(leaked.values()),
            "f_Y_window_p95_unaffected_by_cold_X": x_s["window_p95_ms"] <= max(1.25 * idle_s["window_p95_ms"], 1.0) or x_s["window_p95_ms"] <= 50,
            "g_index_never_overlaps_snapshots": during["scans"]["index_over_snapshot"] == 0 and final["scans"]["snapshot_over_index"] == 0,
        },
    }


async def phase_memory(be: Backend, report: dict, *, sessions: int = 70) -> None:
    """~70 small/medium sessions touched: cache memory vs ~55 B/turn and the LRU cap."""
    work = Path(tempfile.mkdtemp(prefix="hi_mem_"))
    try:
        base = await be.metrics(reset=True)
        sids = []
        total_turns = 0
        for i in range(sessions):
            records = random.Random(i).choice([400, 1500, 3000])
            p = work / f"s{i}.jsonl"
            info = generate_session_file(p, records=records, target_bytes=records * 900,
                                         session_id=f"mem{i}", seed=i)
            total_turns += info["turns"]
            sids.append(await be.new_session(p, f"mem-{i}"))
        raw_bytes = 0
        for sid in sids:
            r = await be.client.get(f"/api/sessions/{sid}/history_index")
            r.raise_for_status()
            raw_bytes += len(r.content)
        m = await be.metrics()
        cached_turns = m.get("index_turns", 0)
        report["memory"] = {
            "sessions_touched": sessions, "turns_total_generated": total_turns,
            "lru_cap_holds": m["index_cached"] <= 32, "index_cached": m["index_cached"],
            "cached_turns": cached_turns, "cache_bytes_est": m.get("index_bytes_est"),
            "cache_bytes_per_turn_est": round(m["index_bytes_est"] / max(1, cached_turns), 1) if cached_turns else None,
            "response_raw_bytes_per_turn": round(raw_bytes / max(1, total_turns), 1),
            "rss_start_mb": round(base["mem"]["VmRSS"], 1), "rss_end_mb": round(m["mem"]["VmRSS"], 1),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ============================================================================ main


def make_fixtures(root: Path, scale: float) -> dict[str, Path]:
    out = {}
    for name, p in PRESETS.items():
        path = root / f"{name}.jsonl"
        generate_session_file(path, records=max(200, int(p["records"] * scale)),
                              target_bytes=max(1 << 20, int(p["bytes"] * scale)),
                              session_id=name, seed={"small": 3, "medium": 5, "huge": 7}[name])
        out[name] = path
    return out


async def amain(args) -> int:
    tree = Path(args.tree).resolve() if args.tree else REPO
    work = Path(tempfile.mkdtemp(prefix="hi_load_"))
    fixtures = make_fixtures(work / "fixtures", args.scale)
    report: dict = {"tree": str(tree), "scale": args.scale,
                    "fixtures_mb": {k: round(v.stat().st_size / 2**20, 1) for k, v in fixtures.items()},
                    "cpu_count": os.cpu_count()}
    perf = {"cold_scan_workers": args.workers} if args.workers else None
    be = Backend(tree, args.port, work / "run", perf=perf)
    rc = 0
    try:
        await be.start()
        phases = {"cold", "firstpaint", "load", "memory"} if args.phase == "all" else {args.phase}
        with_index = not args.baseline
        if "cold" in phases and with_index:
            await phase_cold(be, fixtures, report)
        if "firstpaint" in phases:
            await phase_firstpaint(be, fixtures, report, with_index=with_index)
        if "load" in phases:
            await phase_load(be, fixtures, report, duration=args.duration, browsers=args.browsers, seed=args.seed,
                             args_scale=args.scale, with_index=with_index,
                             churn=not args.no_churn)
            if args.assert_checks and not all(report["load"]["checks"].values()):
                rc = 1
        if "memory" in phases and with_index:
            await phase_memory(be, report, sessions=args.memory_sessions)
    finally:
        await be.stop()
        shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return rc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "cold", "firstpaint", "load", "memory"])
    ap.add_argument("--tree", default=None, help="checkout to run the Backend from (e.g. a main worktree)")
    ap.add_argument("--baseline", action="store_true", help="tree has no /history_index (main)")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--scale", type=float, default=1.0, help="fixture size multiplier (quick mode < 1)")
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--browsers", type=int, default=4)
    ap.add_argument("--workers", type=int, default=None, help="cold_scan_workers override")
    ap.add_argument("--memory-sessions", type=int, default=70)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-churn", action="store_true", help="skip delete/reset churn (isolates scan load)")
    ap.add_argument("--assert-checks", action="store_true")
    ap.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--probe-y", default="", help=argparse.SUPPRESS)
    ap.add_argument("--probe-w", default="", help=argparse.SUPPRESS)
    ap.add_argument("--probe-interval", type=float, default=0.02, help=argparse.SUPPRESS)
    ap.add_argument("--probe-stop", default="", help=argparse.SUPPRESS)
    ap.add_argument("--probe-out", default="", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.probe:
        _probe_worker(args)
        return
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
