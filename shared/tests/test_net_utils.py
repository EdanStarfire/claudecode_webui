"""Tests for shared.net_utils.allocate_free_port (issue #2358)."""

import threading
from unittest.mock import MagicMock, patch

from shared import net_utils


def _fake_socket(port: int) -> MagicMock:
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.getsockname.return_value = ("127.0.0.1", port)
    return sock


def test_retries_when_kernel_reissues_a_recent_port():
    with net_utils._recent_lock:
        net_utils._recent_ports.clear()
    ports = iter([41001, 41001, 41001, 41002])
    with patch.object(net_utils.socket, "socket", side_effect=lambda *a, **k: _fake_socket(next(ports))):
        assert net_utils.allocate_free_port() == 41001
        assert net_utils.allocate_free_port() == 41002


def test_gives_up_after_bounded_attempts():
    with net_utils._recent_lock:
        net_utils._recent_ports.clear()
        net_utils._recent_ports.append(41003)
    with patch.object(net_utils.socket, "socket", side_effect=lambda *a, **k: _fake_socket(41003)):
        try:
            net_utils.allocate_free_port()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")


def test_sequential_calls_are_distinct_within_the_recent_window():
    ports = [net_utils.allocate_free_port() for _ in range(200)]
    window = net_utils._RECENT_PORT_MEMORY
    for i in range(len(ports)):
        chunk = ports[i : i + window]
        assert len(set(chunk)) == len(chunk)


def test_concurrent_calls_are_distinct():
    # 8 threads x 8 calls = 64 allocations, exactly the recent-port window, so all must differ.
    results: list[int] = []
    lock = threading.Lock()

    def work():
        local = [net_utils.allocate_free_port() for _ in range(8)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 64
    assert len(set(results)) == 64
