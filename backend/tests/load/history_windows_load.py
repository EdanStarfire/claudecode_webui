"""Load / concurrency harness for the windowed history reads (issue #2030, stage 3 PR 3A): the
``around=`` / ``around_id=`` / ``after=`` / ``before=`` routes and the id lookup (``resolve``) against
a REAL Backend (``serve_backend.py``: real BackendApp with the real GZipMiddleware, auth, routers),
on top of the helpers of ``history_index_load.py``. Not collected by pytest (see
``test_history_windows_load.py`` for the slow quick-mode wrapper).

    uv run python -m backend.tests.load.history_windows_load --phase all --out report.json
    uv run python -m backend.tests.load.history_windows_load --phase load --parts control \\
        --tree /tmp/main_wt          # the same open + before-page traffic on main (baseline)

Phases: ``single`` (idle single-request latencies per fixture class: tail / before page / around
warm and cold (page cache dropped with ``posix_fadvise``) / after pages / id lookup found, not-found,
tool_use_id), ``isolation`` (session Y probes while a 113 MB session X takes back-to-back id lookups
that never match, i.e. full-file scans), ``load`` (``--parts control`` = open + before traffic only,
runnable against main; ``--parts windows`` = the same plus around / around_id / after / id lookups,
cancellation churn and delete / reset churn; N simulated browsers + the two pinned probe sessions).
"""

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

from .history_index_load import (
    PRESETS,
    Backend,
    Prober,
    generate_session_file,
    open_session,
    p95,
    pct,
)

_ID_RE = re.compile(rb'"message_id"\s*:\s*"([^"]+)"')
_USER_RE = re.compile(rb'"type"\s*:\s*"user"')
WINDOW_CAP_MS = 5_000  # resolve waiter cap (5 s) + margin: no request may take longer


class FileIndex:
    """Line offsets + a sampler of ids for one fixture file (shared by sessions copied from it)."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        self.offsets = [0]
        pos = data.find(b"\n")
        while pos != -1 and pos + 1 < len(data):
            self.offsets.append(pos + 1)
            pos = data.find(b"\n", pos + 1)
        self.size = len(data)
        self.data = data
        self.tool_ids = len(re.findall(rb'"tool_use_id"', data))

    def line(self, i: int) -> bytes:
        end = self.data.find(b"\n", self.offsets[i])
        return self.data[self.offsets[i]:end if end != -1 else len(self.data)]

    def message_id(self, i: int) -> str:
        m = _ID_RE.search(self.line(i))
        return m.group(1).decode() if m else ""

    def drop(self) -> None:
        self.data = b""


def drop_page_cache(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


async def get(be: Backend, sid: str, query: str, *, abandon_after: float | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        coro = be.client.get(f"/api/sessions/{sid}/messages?{query}")
        r = await (coro if abandon_after is None else asyncio.wait_for(coro, abandon_after))
    except TimeoutError:
        return {"ms": (time.perf_counter() - t0) * 1000.0, "status": "abandoned"}
    except httpx.HTTPError:
        return {"ms": (time.perf_counter() - t0) * 1000.0, "status": "error"}
    ms = (time.perf_counter() - t0) * 1000.0
    body = r.json() if r.status_code == 200 else {}
    return {"ms": ms, "status": r.status_code, "body": body, "bytes": len(r.content),
            "wire_bytes": r.num_bytes_downloaded}


def _pcts(values: list[float]) -> dict:
    return {"n": len(values), "p50": round(pct(values, 0.5), 1), "p95": round(p95(values), 1),
            "max": round(max(values or [0.0]), 1)}


# ============================================================================ single


async def _single_class(be: Backend, cls: str, path: Path, fi: FileIndex, rnd: random.Random,
                        reps: int) -> dict:
    sid = await be.new_session(path, f"single-{cls}")
    session_file = be.data_dir / "sessions" / sid / "messages.jsonl"
    tail = (await get(be, sid, "window=tail"))["body"]
    row: dict = {"file_mb": round(fi.size / 2**20, 1), "records": len(fi.offsets)}

    async def measure(label: str, make_query, *, cold: bool = False) -> None:
        vals, sizes, wire = [], [], []
        for _ in range(reps):
            if cold:
                await asyncio.to_thread(drop_page_cache, session_file)
            res = await get(be, sid, make_query())
            if res["status"] == (404 if "not_found" in label else 200):
                vals.append(res["ms"])
                sizes.append(res.get("bytes", 0))
                wire.append(res.get("wire_bytes", 0))
        row[label] = {**_pcts(vals), "kb": round(sum(sizes) / max(1, len(sizes)) / 1024, 1),
                      "wire_kb": round(sum(wire) / max(1, len(wire)) / 1024, 1)}

    def rand_cursor() -> str:
        return f"b:{fi.offsets[rnd.randrange(len(fi.offsets))]}"

    await measure("tail", lambda: "window=tail")
    await measure("before_warm", lambda: f"before={tail['start_cursor']}")
    await measure("around_warm", lambda: f"around={rand_cursor()}")
    await measure("around_cold", lambda: f"around={rand_cursor()}", cold=True)
    await measure("after_page", lambda: f"after={rand_cursor()}")
    # id lookups: found early / middle / late, tool_use_id, not found (the full scan)
    for label, at in (("found_early", 0.02), ("found_mid", 0.5), ("found_late", 0.98)):
        mid = fi.message_id(int(len(fi.offsets) * at))
        await measure(f"resolve_{label}", lambda mid=mid: f"around_id={mid}")
    await measure("resolve_tool_use_id", lambda: f"around_id=tu{max(1, fi.tool_ids // 2)}")
    await measure("resolve_not_found_warm", lambda: "around_id=does-not-exist")
    await measure("resolve_not_found_cold", lambda: "around_id=does-not-exist", cold=True)
    return row


async def phase_single(be: Backend, fixtures: dict[str, Path], idx: dict[str, FileIndex],
                       report: dict, *, reps: int = 12) -> None:
    rnd = random.Random(11)
    report["single"] = {
        cls: await _single_class(be, cls, path, idx[cls], rnd, reps) for cls, path in fixtures.items()
    }


# ============================================================================ isolation


async def phase_isolation(be: Backend, fixtures: dict[str, Path], report: dict, *,
                          seconds: float) -> None:
    """Y / W probes idle vs while a 113 MB session X takes back-to-back full-scan id lookups
    (distinct ids that never match, from several clients) plus found lookups."""
    sid_y = await be.new_session(fixtures["small"], "Y")
    sid_w = await be.new_session(fixtures["small"], "W")
    await open_session(be, sid_y, with_index=True)
    await open_session(be, sid_w, with_index=True)
    sid_x = await be.new_session(fixtures["huge"], "X")
    await asyncio.sleep(1.0)
    idle = Prober(be, sid_y, sid_w, interval=0.02)
    idle.start()
    await asyncio.sleep(seconds + 1.5)
    await idle.stop()
    stop_at = time.time() + seconds + 1.5
    await be.metrics(reset=True)
    busy = Prober(be, sid_y, sid_w, interval=0.02)
    busy.start()
    served = {"requests": 0, "busy_503": 0, "not_found": 0, "max_ms": 0.0}

    async def hammer(tag: int) -> None:
        n = 0
        while time.time() < stop_at:
            n += 1
            res = await get(be, sid_x, f"around_id=miss-{tag}-{n}")
            served["requests"] += 1
            served["max_ms"] = max(served["max_ms"], res["ms"])
            served["busy_503"] += res["status"] == 503
            if res["status"] == 503:
                await asyncio.sleep(0.25)  # (a real browser honours Retry-After; this stays hot)
            served["not_found"] += res["status"] == 404

    await asyncio.gather(*(hammer(t) for t in range(6)))
    await busy.stop()
    m = await be.metrics()
    idle_s, busy_s = idle.summary(), busy.summary()
    report["isolation"] = {
        "idle_probe": idle_s, "during_113mb_resolve_probe": busy_s, "resolve_requests": served,
        "loop_lag": m["lag"], "scans": m["scans"],
        "checks": {
            "Y_window_p95_unaffected": busy_s["window_p95_ms"] <= max(1.25 * idle_s["window_p95_ms"], 50.0),
            "W_tasks_p95_unaffected": busy_s["tasks_p95_ms"] <= max(1.25 * idle_s["tasks_p95_ms"], 50.0),
            "no_request_beyond_cap": served["max_ms"] <= WINDOW_CAP_MS + 1_000,
        },
    }


# ============================================================================ saturated pool


async def phase_saturation(be: Backend, fixtures: dict[str, Path], idx: dict[str, FileIndex],
                           report: dict, *, seconds: float, scale: float) -> None:
    """Id lookups (a user's deep link / jump to launch) while the cold-scan pool is saturated by
    other sessions' scans: 6+ 113 MB sessions are repeatedly dropped from the caches and re-opened
    (agents / tasks / background / index scans of every one of them), while 3 clients look up
    found (early / mid / late), tool_use_id and missing ids on a 113 MB target. Reports the lookup
    p50 / p95 / max, the 503 rate and the pool occupancy; the 5 s waiter cap must hold."""
    target = await be.new_session(fixtures["huge"], "target")
    savers = [await be.new_session(fixtures["huge"], f"sat-{i}") for i in range(max(6, int(8 * scale)))]
    await be.metrics(reset=True)
    stop_at = time.time() + seconds
    cycles = {"n": 0}

    async def saturate(sid: str) -> None:
        while time.time() < stop_at:
            await be.client.post(f"/api/__load/stop/{sid}")  # drop the caches: next open is cold
            await be.client.post(f"/api/__load/attach/{sid}")
            await open_session(be, sid, with_index=True)
            cycles["n"] += 1

    fi = idx["huge"]
    lat: dict[str, list[float]] = {"found": [], "tool_use": [], "miss": []}
    status = {"ok": 0, "busy_503": 0, "other": 0}

    async def lookups(tag: int) -> None:
        r = random.Random(100 + tag)
        while time.time() < stop_at:
            roll = r.random()
            if roll < 0.5:
                kind, q = "found", f"around_id={fi.message_id(r.randrange(len(fi.offsets)))}"
            elif roll < 0.8:
                kind, q = "tool_use", f"around_id=tu{r.randint(1, max(1, fi.tool_ids))}"
            else:
                kind, q = "miss", f"around_id=sat-miss-{r.getrandbits(40)}"
            res = await get(be, target, q)
            if res["status"] in (200, 404):
                status["ok"] += 1
                lat[kind].append(res["ms"])
            elif res["status"] == 503:
                status["busy_503"] += 1
                lat[kind].append(res["ms"])
            else:
                status["other"] += 1
            await asyncio.sleep(r.uniform(0.1, 0.4))

    await asyncio.gather(*(saturate(s) for s in savers), *(lookups(t) for t in range(3)))
    m = await be.metrics()
    everything = [v for vals in lat.values() for v in vals]
    total = sum(status.values())
    report["saturation"] = {
        "saturating_sessions": len(savers), "cold_cycles": cycles["n"], "lookups": status,
        "busy_503_rate": round(status["busy_503"] / max(1, total), 3),
        "lookup_ms": {k: _pcts(v) for k, v in lat.items() if v}, "all_ms": _pcts(everything),
        "scans": m["scans"], "loop_lag": m["lag"],
        "checks": {
            "no_lookup_beyond_cap": max(everything or [0]) <= WINDOW_CAP_MS + 1_000,
            "no_unexpected_status": status["other"] == 0,
            "pool_was_saturated": m["scans"]["max_running"] >= m["scans"]["workers"],
        },
    }


# ============================================================================ load


async def phase_load(be: Backend, fixtures: dict[str, Path], idx: dict[str, FileIndex], report: dict,
                     *, label: str, duration: float, browsers: int, seed: int, scale: float,
                     windows: bool, churn: bool = True, only_windows: bool = False) -> None:
    """N simulated browsers over sessions of mixed size. Every arm does opens (window + snapshots +
    index) and ``before=`` pages; with ``windows`` they also fire around / around_id / after /
    id-miss requests (some abandoned after a short wait) and the churner deletes / resets sessions
    with those requests in flight. Probes Y and W run in a separate process."""
    sid_y = await be.new_session(fixtures["small"], "Y")
    sid_w = await be.new_session(fixtures["small"], "W")
    await open_session(be, sid_y, with_index=True)
    await open_session(be, sid_w, with_index=True)
    await asyncio.sleep(1.0)
    rng_pool = random.Random(seed)
    pool: list[tuple[str, str]] = []
    for cls, count in (("small", 5), ("medium", 4), ("huge", 3)):
        for i in range(max(1, int(count * max(scale, 0.3)))):
            pool.append((await be.new_session(fixtures[cls], f"{label}-{cls}-{i}"), cls))
    churn_queue: list[tuple[str, str]] = []
    for cls, count in (("small", 4), ("medium", 5), ("huge", 3)):
        for i in range(max(1, int(count * max(scale, 0.3)))):
            churn_queue.append((await be.new_session(fixtures[cls], f"{label}-churn-{cls}-{i}"), cls))
    rng_pool.shuffle(churn_queue)
    deleted: set[str] = set()
    stats = {"opens": 0, "before": 0, "around": 0, "around_id": 0, "after": 0, "resolve_miss": 0,
             "abandoned": 0, "busy_503": 0, "not_found_404": 0, "wrong_window": 0, "errors": 0,
             "deleted": 0, "reset": 0, "reset_failed": 0, "stops": 0}
    ms: dict[str, list[float]] = {k: [] for k in
                                  ("before", "around", "around_id", "after", "resolve_miss")}
    all_ms: list[float] = []
    sizes: list[int] = []
    other: list[float] = []
    index_ms: list[float] = []
    await be.metrics(reset=True)
    prober = Prober(be, sid_y, sid_w, interval=0.02)
    prober.start()
    await asyncio.sleep(1.5)
    stop_at = time.time() + duration

    def live_pool() -> list[tuple[str, str]]:
        return [p for p in pool if p[0] not in deleted]

    def record(kind: str, res: dict, *, expect_id: str | None = None) -> dict:
        if res["status"] == "abandoned":
            stats["abandoned"] += 1
        elif res["status"] == 503:
            stats["busy_503"] += 1
            all_ms.append(res["ms"])
        elif res["status"] == 404 and kind in ("resolve_miss", "around_id"):
            stats["not_found_404"] += 1
            ms[kind].append(res["ms"])
            all_ms.append(res["ms"])
        elif res["status"] != 200:
            if res["status"] in (404, 409):  # session deleted / reset underneath the request
                pass
            else:
                stats["errors"] += 1
        else:
            ms[kind].append(res["ms"])
            all_ms.append(res["ms"])
            sizes.append(res["bytes"])
            if expect_id is not None and expect_id not in {
                m.get("message_id") for m in res["body"]["messages"]
            }:
                stats["wrong_window"] += 1
        return res

    async def window_ops(r: random.Random, sid: str, cls: str) -> None:
        fi = idx[cls]
        choice = r.random()
        abandon = r.uniform(0.005, 0.2) if r.random() < 0.15 else None
        if choice < 0.30:
            stats["around"] += 1
            record("around", await get(
                be, sid, f"around=b:{fi.offsets[r.randrange(len(fi.offsets))]}", abandon_after=abandon))
        elif choice < 0.55:
            stats["around_id"] += 1
            i = r.randrange(len(fi.offsets))
            mid = fi.message_id(i)
            if mid:
                record("around_id", await get(be, sid, f"around_id={mid}", abandon_after=abandon),
                       expect_id=mid)
        elif choice < 0.65:
            stats["around_id"] += 1
            record("around_id", await get(
                be, sid, f"around_id=tu{r.randint(1, max(1, fi.tool_ids))}", abandon_after=abandon))
        elif choice < 0.85:
            stats["after"] += 1
            res = record("after", await get(
                be, sid, f"after=b:{fi.offsets[r.randrange(len(fi.offsets))]}", abandon_after=abandon))
            for _ in range(r.choice([0, 1, 2])):  # scroll down: follow the end cursor
                body = res.get("body") or {}
                if res["status"] != 200 or not body.get("has_more_after"):
                    break
                res = record("after", await get(be, sid, f"after={body['end_cursor']}"))
        else:
            stats["resolve_miss"] += 1
            record("resolve_miss", await get(
                be, sid, f"around_id=gone-{r.getrandbits(40)}", abandon_after=abandon))

    async def before_op(r: random.Random, sid: str) -> None:
        stats["before"] += 1
        tail = await get(be, sid, "window=tail")
        if tail["status"] == 200 and tail["body"].get("has_more_before"):
            record("before", await get(be, sid, f"before={tail['body']['start_cursor']}"))

    async def browser(idx_: int) -> None:
        r = random.Random(seed * 100 + idx_)
        while time.time() < stop_at:
            sid, cls = r.choice(live_pool())
            if r.random() < 0.08:
                await be.client.post(f"/api/__load/stop/{sid}")
                await be.client.post(f"/api/__load/attach/{sid}")
                stats["stops"] += 1
            roll = 1.0 if only_windows else r.random()
            if roll < (0.45 if windows else 0.7):
                res = await open_session(be, sid, with_index=True)
                stats["opens"] += 1
                if "index" in res:
                    index_ms.append(res["index"])
                other.extend(res[k] for k in ("window", "agents", "tasks", "background"))
            elif roll < (0.6 if windows else 1.0):
                await before_op(r, sid)
            else:
                await window_ops(r, sid, cls)
            await asyncio.sleep(r.uniform(0.0, 0.2))

    async def churner() -> None:
        r = random.Random(seed + 999)
        while time.time() < stop_at and churn_queue:
            sid, cls = churn_queue.pop()
            kick = [asyncio.create_task(open_session(be, sid, with_index=True))]
            if windows:
                kick += [asyncio.create_task(window_ops(random.Random(r.getrandbits(32)), sid, cls))
                         for _ in range(4)]
                kick.append(asyncio.create_task(get(be, sid, "around_id=never-matches")))
            await asyncio.sleep(r.uniform(0.02, 0.4))
            if r.random() < 0.5:
                rr = await be.client.delete(f"/api/sessions/{sid}")
                if rr.status_code == 200:
                    deleted.add(sid)
                    stats["deleted"] += 1
            else:
                rr = await be.client.post(f"/api/sessions/{sid}/reset")
                stats["reset" if rr.status_code == 200 else "reset_failed"] += 1
            await asyncio.gather(*kick, return_exceptions=True)
            await asyncio.sleep(r.uniform(0.2, 1.0))

    await asyncio.gather(*[browser(i) for i in range(browsers)], *([churner()] if churn else []))
    await prober.stop()
    during = await be.metrics()
    probe = prober.summary()
    final = during
    settle = time.time() + 120
    while time.time() < settle:
        final = await be.metrics()
        if final["scans"]["inflight"] == 0 and final["scans"]["running"] == 0:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)
    final = await be.metrics()
    leaked = {
        "cold_inflight_keys": final["scans"]["inflight_keys"],
        "deleted_index": [k for k in final["index_cached_keys"] if k in deleted],
        "deleted_task_list": [k for k in final["task_list_keys"] if k in deleted],
    }
    report.setdefault("load", {})[label] = {
        "params": {"duration_s": duration, "browsers": browsers, "windows": windows,
                   "pool": len(pool), "cold_scan_workers": final["scans"].get("workers")},
        "ops": stats, "probe": probe, "loop_lag": during["lag"],
        "loop_block_stacks": (during.get("block_stacks") or [])[:6],
        "peak_rss_mb": round(during["mem"]["VmHWM"], 1),
        "latency_ms": {k: _pcts(v) for k, v in ms.items() if v},
        "all_requests_ms": _pcts(all_ms),
        "response_kb_max": round(max(sizes or [0]) / 1024, 1),
        "open_waiters": {"index_max_s": round(max(index_ms or [0]) / 1000, 2),
                         "other_max_s": round(max(other or [0]) / 1000, 2),
                         "other_p95_ms": round(p95(other), 1)},
        "final_state": {"scans": final["scans"], "threads": final["threads"], "leaked": leaked},
        "checks": {
            "no_request_beyond_cap": max(all_ms or [0]) <= WINDOW_CAP_MS + 1_000,
            "lag_max_lt_100ms": during["lag"]["max_ms"] < 100,
            "lag_p99_lt_30ms": during["lag"]["p99_ms"] < 30,
            "open_waiters_ok": max(index_ms or [0]) <= 21_000 and max(other or [0]) <= 5_000,
            "no_leaked_flights": final["scans"]["inflight"] == 0 and final["scans"]["running"] == 0,
            "no_leaked_state": not any(leaked.values()),
            "windows_contain_their_target": stats["wrong_window"] == 0,
            "no_unexpected_errors": stats["errors"] == 0,
        },
    }


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
    tree = Path(args.tree).resolve() if args.tree else Path(__file__).resolve().parents[3]
    work = Path(tempfile.mkdtemp(prefix="hw_load_"))
    fixtures = make_fixtures(work / "fixtures", args.scale)
    idx = {k: FileIndex(v) for k, v in fixtures.items()}
    report: dict = {"tree": str(tree), "scale": args.scale, "cpu_count": os.cpu_count(),
                    "fixtures_mb": {k: round(v.stat().st_size / 2**20, 1) for k, v in fixtures.items()}}
    be = Backend(tree, args.port, work / "run")
    rc = 0
    try:
        await be.start()
        phases = {"single", "isolation", "saturation", "load"} if args.phase == "all" else {args.phase}
        parts = args.parts.split(",")
        if "single" in phases:
            await phase_single(be, fixtures, idx, report)
        if "isolation" in phases:
            await phase_isolation(be, fixtures, report, seconds=max(8.0, args.duration / 3))
        if "saturation" in phases:
            await phase_saturation(be, fixtures, idx, report, seconds=max(20.0, args.duration),
                                   scale=args.scale)
        if "load" in phases:
            if "control" in parts:
                await phase_load(be, fixtures, idx, report, label="control_open_and_before",
                                 duration=args.duration, browsers=args.browsers, seed=args.seed,
                                 scale=args.scale, windows=False, churn=not args.no_churn)
            if "windows_only" in parts:  # diagnostic: window requests only, no opens / before pages
                await phase_load(be, fixtures, idx, report, label="windows_only",
                                 duration=args.duration, browsers=args.browsers, seed=args.seed,
                                 scale=args.scale, windows=True, churn=False, only_windows=True)
            if "windows" in parts:
                await phase_load(be, fixtures, idx, report, label="with_windows",
                                 duration=args.duration, browsers=args.browsers, seed=args.seed,
                                 scale=args.scale, windows=True, churn=not args.no_churn)
            if args.assert_checks and not all(
                all(v["checks"].values()) for v in report.get("load", {}).values()
            ):
                rc = 1
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
    ap.add_argument("--phase", default="all", choices=["all", "single", "isolation", "saturation", "load"])
    ap.add_argument("--parts", default="control,windows",
                    help="load arms: control (open + before only; runs against main), windows,"
                         " windows_only (diagnostic)")
    ap.add_argument("--tree", default=None, help="checkout to run the Backend from (e.g. a main worktree)")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--browsers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-churn", action="store_true")
    ap.add_argument("--assert-checks", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
