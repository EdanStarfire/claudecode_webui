"""Audit filters, aggregated scan and lock behavior (issue #2103 stage 3d-i)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from backend.relay.audit import RelayAuditLog, event_matcher, validate_events

RELAY = "r1"
BASE = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def ts(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


async def seed(log: RelayAuditLog, rows: list[dict]) -> None:
    for row in rows:
        await log.record(RELAY, **row)


@pytest.fixture
def log(tmp_path):
    return RelayAuditLog(tmp_path)


def test_validate_events():
    assert validate_events(None) is None and validate_events([]) is None
    assert validate_events(["inbound_*", "monitor_flood"]) == ["inbound_*", "monitor_flood"]
    for bad in (["Inbound"], ["a b"], ["*x"], ["x" * 41], [""], ["a"] * 21):
        with pytest.raises(ValueError):
            validate_events(bad)


def test_event_matcher_exact_and_prefix():
    m = event_matcher(["inbound_*", "monitor_flood"])
    assert m("inbound_connection") and m("monitor_flood")
    assert not m("monitor_delivered") and not m("tool")
    assert event_matcher(None) is None


async def test_query_filters_events_outcome_since_before(log):
    await seed(log, [
        {"ts": ts(0), "event": "inbound_instruction", "outcome": "delivered"},
        {"ts": ts(1), "event": "inbound_instruction", "outcome": "refused"},
        {"ts": ts(2), "event": "monitor_flood", "outcome": "dropped"},
        {"ts": ts(3), "event": "tool", "outcome": "ok"},
    ])
    rows = await log.query(RELAY, events=["inbound_*", "monitor_flood"])
    assert [r["ts"] for r in rows] == [ts(2), ts(1), ts(0)]
    assert [r["event"] for r in await log.query(RELAY, events=["monitor_flood"])] == ["monitor_flood"]
    assert len(await log.query(RELAY, outcome="refused")) == 1
    assert [r["ts"] for r in await log.query(RELAY, since=ts(1))] == [ts(3), ts(2), ts(1)]
    both = await log.query(RELAY, events=["inbound_*"], before=ts(1))
    assert [r["ts"] for r in both] == [ts(0)]
    assert await log.query(RELAY, events=["monitor_*"], outcome="ok") == []


async def test_filters_span_rotated_files_and_skip_malformed(tmp_path):
    log = RelayAuditLog(tmp_path, max_bytes=1500, backups=3)
    for i in range(12):
        await log.record(RELAY, ts=ts(i), event="monitor_delivered", outcome="ok", count=i)
    assert (tmp_path / "relays" / RELAY / "audit.jsonl.1").exists()
    with (tmp_path / "relays" / RELAY / "audit.jsonl").open("a") as f:
        f.write("not json\n")
    rows = await log.query(RELAY, events=["monitor_delivered"], limit=500)
    assert len(rows) >= 4 and [r["ts"] for r in rows] == sorted((r["ts"] for r in rows), reverse=True)
    assert await RelayAuditLog(tmp_path / "none").query(RELAY) == []


async def test_scan_since_aggregates_and_early_exits(log):
    await seed(log, [
        {"ts": ts(-500), "event": "monitor_delivered", "outcome": "ok", "count": 99},  # outside
        {"ts": ts(1), "event": "monitor_delivered", "outcome": "ok", "count": 3},
        {"ts": ts(2), "event": "monitor_delivered", "outcome": "ok", "count": 4},
        {"ts": ts(3), "event": "monitor_backfill", "outcome": "truncated", "code": "gap_notice",
         "reason_counts": {"non_member": 2}},
        {"ts": ts(4), "event": "tool", "outcome": "ok"},
        {"ts": ts(5), "event": "inbound_connection", "inbound_status": "connected"},
    ])
    scan = await log.scan_since(RELAY, ts(0), events=["monitor_*", "inbound_connection"])
    assert scan.rows_read == 5 and not scan.truncated  # the old row is never read
    assert scan.rows_for("monitor_delivered") == 2 and scan.sum_for("monitor_delivered") == 7
    assert scan.rows_for("monitor_backfill", code="gap_notice") == 1
    assert scan.reasons["monitor_backfill"]["non_member"] == 2
    assert scan.latest["inbound_connection"]["inbound_status"] == "connected"
    assert scan.newest_for("monitor_delivered") == ts(2)
    assert "tool" not in scan.latest


async def test_scan_since_truncates_at_max_rows(log):
    await seed(log, [{"ts": ts(i), "event": "monitor_flood", "outcome": "dropped"} for i in range(10)])
    scan = await log.scan_since(RELAY, ts(-1), events=None, max_rows=4)
    assert scan.truncated and scan.rows_read == 4 and scan.rows_for("monitor_flood") == 4
    exact = await log.scan_since(RELAY, ts(-1), max_rows=10)
    assert not exact.truncated and exact.rows_read == 10


async def test_scan_since_empty_or_missing(tmp_path):
    scan = await RelayAuditLog(tmp_path).scan_since(RELAY, ts(0))
    assert scan.rows_read == 0 and not scan.truncated and scan.latest == {}


async def test_scan_cache_and_single_flight(tmp_path):
    clock = [0.0]
    log = RelayAuditLog(tmp_path, mono=lambda: clock[0])
    await seed(log, [{"ts": ts(1), "event": "monitor_flood", "outcome": "dropped"}])
    calls = 0
    real = log._scan_sync

    def counting(*a, **k):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return real(*a, **k)

    log._scan_sync = counting
    results = await asyncio.gather(*[
        log.scan_since(RELAY, ts(0), events=["monitor_*"], cache_seconds=30) for _ in range(5)
    ])
    assert calls == 1 and all(r is results[0] for r in results)
    clock[0] = 29
    await log.scan_since(RELAY, ts(0), events=["monitor_*"], cache_seconds=30)
    assert calls == 1
    clock[0] = 31
    await log.scan_since(RELAY, ts(0), events=["monitor_*"], cache_seconds=30)
    assert calls == 2
    # a different event set is a different cache entry
    await log.scan_since(RELAY, ts(0), events=["inbound_*"], cache_seconds=30)
    assert calls == 3


async def test_parsing_happens_outside_the_lock(log, monkeypatch):
    await seed(log, [{"ts": ts(i), "event": "monitor_flood", "outcome": "dropped"} for i in range(5)])
    held_during_parse: list[bool] = []
    real_loads = json.loads

    def slow_loads(line, *a, **k):
        held_during_parse.append(log._lock.locked())
        return real_loads(line, *a, **k)

    monkeypatch.setattr("backend.relay.audit.json.loads", slow_loads)
    await log.scan_since(RELAY, ts(-1))
    await log.query(RELAY)
    assert held_during_parse and not any(held_during_parse)


async def test_writer_not_blocked_by_a_slow_scan(log, monkeypatch):
    await seed(log, [{"ts": ts(i), "event": "monitor_flood", "outcome": "dropped"} for i in range(200)])
    started, release = threading.Event(), threading.Event()
    real_loads = json.loads

    def blocking_loads(line, *a, **k):
        started.set()
        release.wait(5)
        return real_loads(line, *a, **k)

    monkeypatch.setattr("backend.relay.audit.json.loads", blocking_loads)
    scan = asyncio.create_task(log.scan_since(RELAY, ts(-1)))
    await asyncio.to_thread(started.wait, 5)
    # the parse is stuck inside the scan: a write must still complete
    await asyncio.wait_for(log.record(RELAY, event="tool", outcome="ok"), 2)
    release.set()
    await scan


async def test_rotation_between_file_reads_does_not_duplicate_rows(tmp_path):
    log = RelayAuditLog(tmp_path, max_bytes=1500, backups=3)
    for i in range(6):
        await log.record(RELAY, ts=ts(i), event="monitor_delivered", outcome="ok")
    real = log._read_lines
    fired = False

    def read_then_rotate(f):
        nonlocal fired
        out = real(f)
        if not fired:  # a writer rotates right after the first file was read
            fired = True
            with log._lock:
                log._rotate(log._path(RELAY))
        return out

    log._read_lines = read_then_rotate
    rows = await asyncio.to_thread(log._query_sync, RELAY, None, None, 500, None)
    stamps = [r["ts"] for r in rows]
    assert len(stamps) == len(set(stamps))


async def test_scan_counts_cap_runs_not_messages(log):
    await seed(log, [
        {"ts": ts(1), "event": "monitor_backfill", "outcome": "truncated", "code": "gap_notice",
         "reason_counts": {"hydration_cap": 12}},
    ])
    scan = await log.scan_since(RELAY, ts(0))
    assert scan.reason_rows[("monitor_backfill", "hydration_cap")] == 1
    assert scan.reasons["monitor_backfill"]["hydration_cap"] == 12
