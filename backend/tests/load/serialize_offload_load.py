"""Loop-lag / latency / throughput harness for large JSON responses (issue #2336), against a REAL
Backend (``serve_backend.py``: real BackendApp, GZipMiddleware, auth, 10 ms ticker + block
sampler). Not collected by pytest (see ``test_issue_2336_serialize_offload.py`` for the slow
quick-mode wrapper).

    uv run python -m backend.tests.load.serialize_offload_load --out pr.json
    uv run python -m backend.tests.load.serialize_offload_load --tree /path/to/main_wt --out main.json

Phases: ``lag`` (event-loop lag max / p99 while ONE large response is served; per response kind),
``latency`` (median / p95 of N sequential requests per page size), ``concurrent`` (K clients
pulling pages back to back: throughput), ``bodies`` (sha256 of every identity-encoded body, to
prove main and the PR serve byte-identical payloads). ``--tree`` runs the same script against
another checkout (the baseline).
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
import tempfile
import time
from pathlib import Path

from .fixture_gen import generate_session_file
from .history_index_load import REPO, Backend

MIB = 1024 * 1024
SIZES = {"256KiB": 256 * 1024, "1MiB": MIB, "2MiB": 2 * MIB, "4MiB": 4 * MIB}
_HISTORY_ID = re.compile(rb'"history_id":"[0-9a-f]*"')
IDENTITY = {"Accept-Encoding": "identity"}


async def tail_cursor(be: Backend, sid: str) -> str:
    r = await be.client.get(f"/api/sessions/{sid}/messages", params={"window": "tail"})
    r.raise_for_status()
    return r.json()["start_cursor"]


def kinds(sid: str, cursor: str) -> dict[str, tuple[str, dict]]:
    """Response kinds under test: (url, params)."""
    base = f"/api/sessions/{sid}"
    out = {f"before_{n}": (f"{base}/messages", {"before": cursor, "max_bytes": b, "max_records": 5000})
           for n, b in SIZES.items()}
    out["around_4MiB"] = (f"{base}/messages", {
        "around": cursor, "before_bytes": 2 * MIB, "after_bytes": 2 * MIB})
    out["after_4MiB"] = (f"{base}/messages", {"after": "b:0", "max_bytes": 4 * MIB})
    out["tail"] = (f"{base}/messages", {"window": "tail"})
    out["search_page"] = (f"{base}/search", {"q": "alpha beta gamma delta epsilon"})
    out["history_index"] = (f"{base}/history_index", {})
    out["tasks"] = (f"{base}/tasks", {})
    return out


async def get(be: Backend, url: str, params: dict, headers: dict | None = None):
    t0 = time.perf_counter()
    r = await be.client.get(url, params=params, headers=headers or {})
    return (time.perf_counter() - t0) * 1000.0, r


async def phase_steady(be, fixture: Path, sessions: int, report: dict) -> None:
    """Grow the heap like a long-running Backend: ``sessions`` mock-SDK sessions started and
    given a message, plus three more large history files opened (tail / index / tasks / pages)."""
    before = await be.client.get("/api/__load/metrics", params={"gc_objects": 1})
    before = before.json()
    started = 0
    for _ in range(sessions):
        sid = await be.new_session(None, "single_turn")
        r = await be.client.post(f"/api/sessions/{sid}/start")
        if r.status_code == 200:
            started += 1
            await be.client.post(f"/api/sessions/{sid}/messages", json={"message": "hi"})
    for i in range(3):
        sid = await be.new_session(fixture, f"big{i}")
        cur = await tail_cursor(be, sid)
        await get(be, f"/api/sessions/{sid}/history_index", {})
        await get(be, f"/api/sessions/{sid}/tasks", {})
        for p in ({"before": cur, "max_bytes": 4 * MIB, "max_records": 5000},
                  {"around": cur, "before_bytes": MIB, "after_bytes": MIB}):
            await get(be, f"/api/sessions/{sid}/messages", p)
    await asyncio.sleep(3.0)  # let the mock replays finish
    after = (await be.client.get("/api/__load/metrics", params={"gc_objects": 1})).json()
    report["steady"] = {
        "sessions_started": started,
        "tracked_objects_before": before["gc_tracked_objects"],
        "tracked_objects_after": after["gc_tracked_objects"],
        "rss_mib_before": round(before["mem"]["VmRSS"], 1),
        "rss_mib_after": round(after["mem"]["VmRSS"], 1),
    }


async def phase_soak(be, fixture: Path, seconds: float, clients: int, report: dict) -> None:
    """Sustained mixed large-page traffic; reports loop lag, gen-2 pauses, heap growth, RSS."""
    sids = []
    for i in range(3):
        sid = await be.new_session(fixture, f"soak{i}")
        sids.append((sid, await tail_cursor(be, sid)))
    probe = {"gc_objects": 1}
    start = (await be.client.get("/api/__load/metrics", params={"reset": 1, **probe})).json()
    stop_at = time.perf_counter() + seconds
    count = 0

    async def worker(k: int):
        nonlocal count
        i = k
        while time.perf_counter() < stop_at:
            sid, cur = sids[i % 3]
            kind = i % 4
            i += 1
            if kind == 0:
                p = {"before": cur, "max_bytes": 4 * MIB, "max_records": 5000}
            elif kind == 1:
                p = {"after": "b:0", "max_bytes": 4 * MIB}
            elif kind == 2:
                p = {"around": cur, "before_bytes": MIB, "after_bytes": MIB}
            else:
                await get(be, f"/api/sessions/{sid}/history_index", {})
                count += 1
                continue
            await get(be, f"/api/sessions/{sid}/messages", p)
            count += 1
            await asyncio.sleep(0.05)

    await asyncio.gather(*(worker(k) for k in range(clients)))
    end = (await be.client.get("/api/__load/metrics", params=probe)).json()
    report["soak"] = {
        "seconds": seconds, "requests": count, "lag_max_ms": end["lag"]["max_ms"],
        "lag_p99_ms": end["lag"]["p99_ms"], "gc_pauses_over_20ms": end["gc_pauses_over_20ms"],
        "gc_stats": end["gc_stats"], "tracked_objects_start": start["gc_tracked_objects"],
        "tracked_objects_end": end["gc_tracked_objects"],
        "rss_mib_start": round(start["mem"]["VmRSS"], 1), "rss_mib_end": round(end["mem"]["VmRSS"], 1),
        "rss_mib_peak": round(end["mem"]["VmHWM"], 1),
    }


async def phase_lag(be, sid, cursor, repeats: int, report: dict) -> None:
    rows = {}
    for name, (url, params) in kinds(sid, cursor).items():
        await get(be, url, params, IDENTITY)  # warm (index build, page cache)
        lat, lags = [], []
        status = size = 0
        stacks: dict[str, int] = {}
        gc_pauses: list = []
        for _ in range(repeats):
            await be.metrics(reset=True)
            t, r = await get(be, url, params, IDENTITY)
            lat.append(t)
            status, size = r.status_code, len(r.content)
            m = await be.metrics()
            lags.append(m["lag"]["max_ms"])
            gc_pauses.extend(m.get("gc_pauses_over_20ms", []))
            for k, c in m.get("block_stacks", []):
                stacks[k] = stacks.get(k, 0) + c
        rows[name] = {"status": status, "body_kib": round(size / 1024), "req_ms": round(min(lat)),
                      "lag_max_ms": max(lags), "lag_median_of_max_ms": statistics.median(lags),
                      "lag_p99_ms": max(lags), "lags": lags, "gc_pauses_over_20ms": gc_pauses,
                      "block_stacks": sorted(stacks.items(), key=lambda kv: -kv[1])[:3]}
    report["lag"] = rows


async def phase_latency(be, sid, cursor, n: int, report: dict) -> None:
    rows = {}
    for name, size in SIZES.items():
        url, params = f"/api/sessions/{sid}/messages", {"before": cursor, "max_bytes": size, "max_records": 5000}
        await get(be, url, params)
        lat = sorted([(await get(be, url, params))[0] for _ in range(n)])
        rows[name] = {"median_ms": round(statistics.median(lat), 1),
                      "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 1)}
    report["latency"] = rows


async def phase_concurrent(be, sid, cursor, clients: int, per_client: int, report: dict) -> None:
    url, params = f"/api/sessions/{sid}/messages", {"before": cursor, "max_bytes": 4 * MIB, "max_records": 5000}
    await get(be, url, params)
    await be.metrics(reset=True)
    lats: list[float] = []

    async def worker():
        for _ in range(per_client):
            lats.append((await get(be, url, params))[0])

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(clients)))
    wall = time.perf_counter() - t0
    m = await be.metrics()
    lats.sort()
    report["concurrent"] = {
        "clients": clients, "requests": len(lats), "wall_s": round(wall, 2),
        "req_per_s": round(len(lats) / wall, 2), "median_ms": round(statistics.median(lats)),
        "p95_ms": round(lats[int(len(lats) * 0.95) - 1]), "lag_max_ms": m["lag"]["max_ms"],
        "lag_p99_ms": m["lag"]["p99_ms"],
    }


async def phase_bodies(be, sid, cursor, report: dict) -> None:
    out = {}
    for name, (url, params) in kinds(sid, cursor).items():
        _, r = await get(be, url, params, IDENTITY)
        # session id and the inode-derived history_id differ between runs
        body = _HISTORY_ID.sub(b'"history_id":"H"', r.content.replace(sid.encode(), b"SID"))
        out[name] = {
            "status": r.status_code, "sha256": hashlib.sha256(body).hexdigest(),
            "len": len(body),
            "headers": {k: v for k, v in r.headers.items()
                        if k.lower() in ("content-type", "content-encoding", "content-length")},
        }
    report["bodies"] = out


async def amain(args) -> None:
    os.environ.pop("BACKEND_DISABLE_GC_FREEZE", None)  # the harness measures the real server
    tree = Path(args.tree).resolve() if args.tree else REPO
    work = Path(tempfile.mkdtemp(prefix="load2336_"))
    fixture = Path(args.fixture) if args.fixture else work / "fixture.jsonl"
    if not fixture.exists():  # a shared --fixture makes main/PR bodies comparable
        generate_session_file(
            fixture, records=args.records, target_bytes=args.mib * MIB, session_id="fx", seed=7,
            tool_results_metadata=True,
        )
    be = Backend(tree, args.port, work)
    report: dict = {"tree": str(tree)}
    await be.start()
    try:
        sid = await be.new_session(fixture, "big")
        cursor = await tail_cursor(be, sid)
        await asyncio.sleep(1.0)
        if args.steady:
            await phase_steady(be, fixture, args.steady, report)
        if args.soak:
            await phase_soak(be, fixture, args.soak, args.clients, report)
        await phase_bodies(be, sid, cursor, report)
        await phase_lag(be, sid, cursor, args.repeats, report)
        await phase_latency(be, sid, cursor, args.n, report)
        await phase_concurrent(be, sid, cursor, args.clients, args.per_client, report)
    finally:
        await be.stop()
    Path(args.out).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "bodies"}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tree", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--soak", type=float, default=0, help="seconds of sustained mixed traffic")
    p.add_argument("--steady", type=int, default=0, help="mock-SDK sessions to start first")
    p.add_argument("--records", type=int, default=90_000)
    p.add_argument("--mib", type=int, default=113)
    p.add_argument("--fixture", default=None, help="reuse/create this fixture file")
    p.add_argument("--port", type=int, default=18336)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--clients", type=int, default=8)
    p.add_argument("--per-client", type=int, default=5)
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
