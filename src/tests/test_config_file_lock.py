"""Tests for shared/config_file_lock.py (issue #498 review finding).

The shared config.json is written by both the Frontend and Backend processes,
each doing a read-modify-write on save. Without cross-process mutual exclusion,
two saves racing at the same moment can interleave: both read the same
snapshot, then each writes back a merge that silently drops the other's most
recent update. locked_config_write() closes that gap with an flock-based
advisory lock; atomic_write_text() ensures a concurrent reader never observes
a half-written file.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

from shared.config_file_lock import atomic_write_text, locked_config_write


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "config.json"
    atomic_write_text(target, '{"a": 1}')

    assert target.read_text() == '{"a": 1}'
    assert not target.with_suffix(".tmp").exists()


def test_atomic_write_overwrites_existing_file(tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"a": 1}')

    atomic_write_text(target, '{"a": 2}')

    assert json.loads(target.read_text()) == {"a": 2}


def test_locked_config_write_creates_lock_sidecar(tmp_path):
    target = tmp_path / "sub" / "config.json"

    with locked_config_write(target):
        pass

    assert (tmp_path / "sub" / "config.json.lock").exists()


def test_concurrent_read_modify_writes_serialize_without_lost_updates(tmp_path):
    """Regression test for the exact race: two writers (real OS threads, standing in
    for the two separate processes this guards in production) doing
    read-then-merge-then-write on overlapping keys must not lose either update
    when the lock serializes them."""
    target = tmp_path / "config.json"
    atomic_write_text(target, json.dumps({"a": {}, "b": {}}))

    def writer(key: str, value: int, delay: float) -> None:
        with locked_config_write(target):
            time.sleep(delay)  # widen the window an unlocked version would race in
            data = json.loads(target.read_text())
            data[key] = {"value": value}
            atomic_write_text(target, json.dumps(data))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(writer, "a", 1, 0.02)
        f2 = pool.submit(writer, "b", 2, 0.0)
        f1.result(timeout=5)
        f2.result(timeout=5)

    result = json.loads(target.read_text())
    assert result == {"a": {"value": 1}, "b": {"value": 2}}
