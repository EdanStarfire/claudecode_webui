"""Issue #2030 (stage 2 PR A, §3.3): cold-scan pool, per-(session, kind) single-flight,
per-session ordering, abandonment, waiter caps. No process-wide gate."""

import asyncio
import os
import threading
import time

import pytest

from backend.cold_scan import ColdScanManager, resolve_pool_size


@pytest.fixture
def manager():
    m = ColdScanManager(workers=2)
    yield m
    m.shutdown()


class Gate:
    """A blocking worker-thread job controllable from the test."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def job(self, value="done"):
        def run(cancel):
            self.started.set()
            self.release.wait(10)
            return value

        return run


async def _wait_set(event: threading.Event, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("event never set")
        await asyncio.sleep(0.005)


def test_pool_size_default_and_clamp():
    assert resolve_pool_size(None) == min(4, os.cpu_count() or 2)
    assert resolve_pool_size(0) == 1
    assert resolve_pool_size(-5) == 1
    assert resolve_pool_size(8) == 8
    assert resolve_pool_size("3") == 3
    assert resolve_pool_size("junk") >= 1
    assert ColdScanManager(workers=0).workers == 1


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_scan(manager):
    gate = Gate()
    runs = []

    async def flight(handle):
        runs.append(1)
        return await handle.run(gate.job("r"))

    tasks = [
        asyncio.ensure_future(manager.single_flight("s1", "tasks", flight, empty=lambda: None))
        for _ in range(5)
    ]
    await _wait_set(gate.started)
    gate.release.set()
    assert await asyncio.gather(*tasks) == ["r"] * 5
    assert runs == [1]
    assert manager.inflight_count == 0


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_cancel_scan(manager):
    gate = Gate()

    async def flight(handle):
        return await handle.run(gate.job("kept"))

    first = asyncio.ensure_future(manager.single_flight("s1", "tasks", flight, empty=lambda: None))
    await _wait_set(gate.started)
    second = asyncio.ensure_future(manager.single_flight("s1", "tasks", flight, empty=lambda: None))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    gate.release.set()
    assert await second == "kept"


@pytest.mark.asyncio
async def test_waiter_timeout_leaves_scan_running_and_result_cached(manager):
    gate = Gate()
    cache = {}

    async def flight(handle):
        result = await handle.run(gate.job("late"))
        cache["v"] = result
        return result

    with pytest.raises(TimeoutError):
        await manager.single_flight("s1", "index", flight, empty=lambda: None, timeout=0.05)
    assert manager.inflight_kinds("s1") == ["index"]  # still running
    gate.release.set()
    # a later waiter joins the same flight and gets the result
    assert await manager.single_flight("s1", "index", flight, empty=lambda: None, timeout=5) == "late"
    assert cache == {"v": "late"}


@pytest.mark.asyncio
async def test_failure_reaches_all_waiters_and_flight_is_cleared(manager):
    async def flight(handle):
        def boom(cancel):
            raise RuntimeError("scan failed")

        return await handle.run(boom)

    results = await asyncio.gather(
        manager.single_flight("s1", "tasks", flight, empty=lambda: None),
        manager.single_flight("s1", "tasks", flight, empty=lambda: None),
        return_exceptions=True,
    )
    assert all(isinstance(r, RuntimeError) for r in results)
    assert manager.inflight_count == 0


@pytest.mark.asyncio
async def test_invalidate_drops_queued_scan_before_it_starts():
    manager = ColdScanManager(workers=1)
    try:
        blocker = Gate()
        ran = []

        async def blocking(handle):
            return await handle.run(blocker.job())

        async def queued(handle):
            def job(cancel):
                ran.append(1)
                return "should-not-run"

            return await handle.run(job)

        hold = asyncio.ensure_future(
            manager.single_flight("other", "tasks", blocking, empty=lambda: None)
        )
        await _wait_set(blocker.started)
        waiter = asyncio.ensure_future(
            manager.single_flight("gone", "agents", queued, empty=lambda: "empty")
        )
        await asyncio.sleep(0.05)  # job is queued behind the blocker in the single-worker pool
        manager.invalidate("gone")
        blocker.release.set()
        assert await waiter == "empty"
        assert await hold == "done"
        assert ran == []
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_running_scan_sees_cancel_flag_and_result_is_discarded(manager):
    saw_cancel = threading.Event()
    started = threading.Event()

    async def flight(handle):
        def job(cancel):
            started.set()
            if cancel.wait(5):
                saw_cancel.set()
            return "partial"

        return await handle.run(job)

    waiter = asyncio.ensure_future(
        manager.single_flight("s1", "tasks", flight, empty=lambda: "empty")
    )
    await _wait_set(started)
    manager.invalidate("s1")
    assert await waiter == "empty"
    assert saw_cancel.is_set()


@pytest.mark.asyncio
async def test_invalidate_by_kind_only_touches_that_kind(manager):
    gate = Gate()

    async def flight(handle):
        return await handle.run(gate.job("x"))

    a = asyncio.ensure_future(manager.single_flight("s1", "tasks", flight, empty=lambda: "e"))
    await _wait_set(gate.started)
    manager.invalidate("s1", kinds=("index",))
    assert manager.inflight_kinds("s1") == ["tasks"]
    gate.release.set()
    assert await a == "x"


@pytest.mark.asyncio
async def test_after_chains_behind_same_session_scans_without_a_lock(manager):
    dep_gate = Gate()
    order = []

    async def dep(handle):
        result = await handle.run(dep_gate.job("dep"))
        order.append("dep-done")
        return result

    def index_for(label):
        async def index(handle):
            order.append(label)
            return label

        return index

    d = asyncio.ensure_future(manager.single_flight("s1", "tasks", dep, empty=lambda: None))
    await _wait_set(dep_gate.started)
    i = asyncio.ensure_future(
        manager.single_flight(
            "s1", "index", index_for("s1-index"), empty=lambda: None, after=("agents", "tasks")
        )
    )
    await asyncio.sleep(0.1)
    assert order == []  # the s1 index flight has not started: per-session ordering
    # ... but another session is completely unaffected
    other = await manager.single_flight(
        "s2", "index", index_for("s2-index"), empty=lambda: None, after=("tasks",)
    )
    assert other == "s2-index"
    dep_gate.release.set()
    assert await i == "s1-index"
    assert await d == "dep"
    assert order == ["s2-index", "dep-done", "s1-index"]


@pytest.mark.asyncio
async def test_after_ignores_dependency_failure(manager):
    async def dep(handle):
        def boom(cancel):
            raise RuntimeError("dep failed")

        return await handle.run(boom)

    async def index(handle):
        return "index"

    d = asyncio.ensure_future(manager.single_flight("s1", "tasks", dep, empty=lambda: None))
    await asyncio.sleep(0)
    i = asyncio.ensure_future(
        manager.single_flight("s1", "index", index, empty=lambda: None, after=("tasks",))
    )
    assert await i == "index"
    with pytest.raises(RuntimeError):
        await d


@pytest.mark.asyncio
async def test_sessions_do_not_wait_on_each_other(manager):
    slow = Gate()

    async def slow_flight(handle):
        return await handle.run(slow.job("x"))

    async def quick_flight(handle):
        return await handle.run(lambda cancel: "y")

    x = asyncio.ensure_future(manager.single_flight("X", "index", slow_flight, empty=lambda: None))
    await _wait_set(slow.started)
    t0 = time.monotonic()
    assert await manager.single_flight("Y", "index", quick_flight, empty=lambda: None) == "y"
    assert time.monotonic() - t0 < 1.0
    slow.release.set()
    await x


@pytest.mark.asyncio
async def test_default_executor_unaffected_when_cold_pool_is_saturated():
    manager = ColdScanManager(workers=1)
    gate = Gate()

    async def flight(handle):
        return await handle.run(gate.job())

    try:
        hogs = [
            asyncio.ensure_future(manager.single_flight(f"s{i}", "index", flight, empty=lambda: None))
            for i in range(3)
        ]
        await _wait_set(gate.started)
        t0 = time.monotonic()
        # critical-path work (window/tail, warm /tasks) uses asyncio.to_thread -> default executor
        assert await asyncio.to_thread(lambda: threading.current_thread().name) != ""
        assert time.monotonic() - t0 < 1.0
        assert not threading.current_thread().name.startswith("cold-scan")
        assert manager.running == 1  # the pool really is saturated (1 worker, 3 flights)
        gate.release.set()
        await asyncio.gather(*hogs)
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_pool_is_bounded_and_jobs_run_on_cold_scan_threads():
    manager = ColdScanManager(workers=2)
    names = []
    release = threading.Event()

    def job(cancel):
        names.append(threading.current_thread().name)
        release.wait(5)

    async def flight(handle):
        return await handle.run(job)

    try:
        flights = [
            asyncio.ensure_future(manager.single_flight(f"s{i}", "index", flight, empty=lambda: None))
            for i in range(5)
        ]
        deadline = time.monotonic() + 5
        while manager.running < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        assert manager.running == 2  # never more than `workers` at once
        release.set()
        await asyncio.gather(*flights)
        assert manager.max_running == 2
        assert all(n.startswith("cold-scan") for n in names)
    finally:
        manager.shutdown()


def test_performance_config_defaults_roundtrip_and_clamping():
    from backend.config_manager import AppConfig

    default = AppConfig.from_dict({}).performance
    assert (default.cold_scan_workers, default.history_index_cache_max, default.history_index_wait_s) == (
        None, 32, 20.0)
    cfg = AppConfig.from_dict({"performance": {"cold_scan_workers": 0, "history_index_cache_max": 0,
                                               "history_index_wait_s": 3}})
    assert AppConfig.from_dict(cfg.to_dict()).performance == cfg.performance
    assert resolve_pool_size(cfg.performance.cold_scan_workers) == 1  # clamped at use


@pytest.mark.asyncio
async def test_session_query_clamps_configured_values(tmp_path):
    from backend.config_manager import PerformanceConfig
    from backend.session_coordinator import SessionCoordinator
    from backend.session_query import SessionQuery

    coordinator = SessionCoordinator(data_dir=tmp_path)
    query = SessionQuery(
        coordinator=coordinator,
        performance=PerformanceConfig(cold_scan_workers=-3, history_index_cache_max=0),
    )
    try:
        assert query._cold_scans.workers == 1 and query._history_index_cache_max == 1
    finally:
        query.shutdown()


@pytest.mark.asyncio
async def test_job_after_pool_shutdown_is_abandoned_not_a_500():
    manager = ColdScanManager(workers=1)
    manager.shutdown()

    async def flight(handle):
        return await handle.run(lambda cancel: "x")

    assert await manager.single_flight("s", "tasks", flight, empty=lambda: "empty") == "empty"
