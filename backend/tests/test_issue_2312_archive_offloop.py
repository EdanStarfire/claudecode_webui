"""Issue #2312: distill / message count / archive copy of a large session run off the event loop.

Covers: loop lag stays bounded while the three steps run, results unchanged, a cancelled
snapshot leaves no partial archive, and the dedicated archive pool is small and separate from
the default executor. The 113 MB end-to-end delete / reset lag assertion is the slow harness
test at the bottom.
"""

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend import archive_io
from backend.data_storage import DataStorageManager
from backend.history_distiller import distill_session_history
from backend.legion import archive_manager as archive_manager_module
from backend.legion.archive_manager import ArchiveManager, SnapshotContext

REPO = Path(__file__).resolve().parents[2]


def _write_messages(path: Path, records: int) -> None:
    pad = "x" * 600
    with open(path, "w", encoding="utf-8") as f:
        for i in range(records):
            kind = "user" if i % 2 == 0 else "assistant"
            f.write(json.dumps({"type": kind, "timestamp": 1_700_000_000.0 + i,
                                "content": f"message {i} {pad}", "metadata": {}}) + "\n")


def _manager(tmp_path: Path) -> ArchiveManager:
    system = Mock()
    system.session_port.session_manager.data_dir = tmp_path
    system.session_port.session_manager.sessions_dir = tmp_path / "sessions"
    return ArchiveManager(system)


def _ctx(is_reset: bool = True) -> SnapshotContext:
    return SnapshotContext(session_id="s", legion_id=None, auto_memory_directory=None,
                           docker_enabled=False, proxy_enabled=False, is_reset=is_reset,
                           will_be_deleted=not is_reset)


async def _ticker(samples: list[float], stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    expected = loop.time() + 0.01
    while not stop.is_set():
        await asyncio.sleep(0.01)
        now = loop.time()
        samples.append(max(0.0, now - expected) * 1000.0)
        expected = now + 0.01


@pytest.mark.asyncio
async def test_issue_2312_large_session_steps_do_not_block_event_loop(tmp_path):
    session_dir = tmp_path / "sessions" / "s"
    session_dir.mkdir(parents=True)
    _write_messages(session_dir / "messages.jsonl", 40_000)  # ~27 MB
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    storage = DataStorageManager(session_dir)
    manager = _manager(tmp_path)

    samples: list[float] = []
    stop = asyncio.Event()
    ticker = asyncio.create_task(_ticker(samples, stop))
    await asyncio.sleep(0.05)
    count, archived, ok = await asyncio.gather(
        storage.get_message_count(),
        manager.snapshot_artifacts(session_dir, archive_dir, _ctx()),
        distill_session_history(session_dir / "messages.jsonl", tmp_path / "out.md", "s", "ts"),
    )
    stop.set()
    await ticker

    assert count == 40_000
    assert "messages.jsonl" in archived and ok is True
    assert (archive_dir / "messages.jsonl").read_bytes() == (session_dir / "messages.jsonl").read_bytes()
    assert "- Total messages: 40000" in (tmp_path / "out.md").read_text()
    assert max(samples) < 100, f"event loop blocked for {max(samples):.0f} ms"
    assert not list(archive_dir.glob(".*.tmp")) and not list(tmp_path.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_issue_2312_cancelled_snapshot_leaves_no_partial_archive(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions" / "s"
    session_dir.mkdir(parents=True)
    _write_messages(session_dir / "messages.jsonl", 2_000)
    archive_dir = tmp_path / "archives" / "s" / "ts"
    archive_dir.mkdir(parents=True)
    manager = _manager(tmp_path)

    started = threading.Event()
    real_open = open

    class SlowReader:
        def __init__(self, f):
            self._f = f

        def read(self, n):
            started.set()
            time.sleep(0.02)
            return self._f.read(n)

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *exc):
            return self._f.__exit__(*exc)

    def fake_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        return SlowReader(f) if mode == "rb" and Path(path).name == "messages.jsonl" else f

    monkeypatch.setattr(archive_manager_module, "open", fake_open, raising=False)
    monkeypatch.setattr(archive_manager_module, "_COPY_CHUNK_BYTES", 4096)

    task = asyncio.create_task(manager.snapshot_artifacts(session_dir, archive_dir, _ctx()))
    while not started.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not archive_dir.exists()
    assert (session_dir / "messages.jsonl").exists()  # the source is untouched


def test_issue_2312_archive_pool_is_small_and_separate_from_default_executor():
    assert archive_io.ARCHIVE_IO_WORKERS == 2
    assert archive_io._get_executor()._max_workers == 2


@pytest.mark.asyncio
async def test_issue_2312_concurrent_archive_jobs_do_not_starve_default_executor():
    release = threading.Event()
    running = 0
    peak = 0
    lock = threading.Lock()

    def blocking() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(5)
        with lock:
            running -= 1

    jobs = [asyncio.create_task(archive_io.run_archive_io(blocking)) for _ in range(4)]
    await asyncio.sleep(0.1)
    # Latency-sensitive default-executor work still completes while the archive pool is saturated.
    assert await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), 1.0) == "ok"
    assert peak == 2
    release.set()
    await asyncio.gather(*jobs)


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_issue_2312_delete_reset_of_113mb_session_lag_under_100ms(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "backend.tests.load.history_index_load", "--phase", "archive",
         "--port", "8098", "--assert-checks", "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=800,
    )
    report = json.loads(out.read_text())["archive"]
    lags = {k: report[k]["lag"]["max_ms"] for k in
            ("delete", "reset", "delete_concurrent", "reset_concurrent")}
    assert proc.returncode == 0 and all(report["checks"].values()), (lags, proc.stderr[-1500:])
