"""REMOTE-relay + local-execution-guard tests for Batch 3 (queue.py, schedules.py)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from ..queue_processor import QueueProcessor
from ..remote_backend import RemoteBackend
from ..session_backend import BackendMode
from ..web_server import ClaudeWebUI

REMOTE_TOKEN = "remote-test-token"


def _make_remote(tmp_path):
    return ClaudeWebUI(
        data_dir=tmp_path / "remote_data", backend_mode="headless", backend_auth_token=REMOTE_TOKEN
    )


def _wire_hub_to_remote(hub: ClaudeWebUI, remote: ClaudeWebUI) -> None:
    backend = RemoteBackend("http://remote.test", REMOTE_TOKEN, hub.session_queues)
    backend._client = AsyncClient(
        base_url="http://remote.test/api/backend",
        headers={"Authorization": f"Bearer {REMOTE_TOKEN}"},
        transport=ASGITransport(app=remote.app),
    )
    hub.coordinator.backend = backend
    hub.coordinator.backend_mode = BackendMode.REMOTE


@pytest.mark.asyncio
async def test_get_queue_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data")
    _wire_hub_to_remote(hub, remote)

    expected = {"items": [], "total": 0}
    with patch.object(remote.coordinator, "get_queue", new_callable=AsyncMock, return_value=expected):
        with patch.object(remote.service, "get_session_exists", new_callable=AsyncMock, return_value=True):
            async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
                response = await client.get("/api/sessions/s1/queue")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_enqueue_message_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data2")
    _wire_hub_to_remote(hub, remote)

    item = {"queue_id": "q1", "position": 0}
    with patch.object(
        remote.coordinator, "enqueue_message", new_callable=AsyncMock,
        return_value={"queue_id": "q1", "position": 0, "content": "hi"},
    ):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.post("/api/sessions/s1/queue-message", json={"content": "hi"})

    assert response.status_code == 200
    assert response.json()["queue_id"] == item["queue_id"]


@pytest.mark.asyncio
async def test_list_schedules_relays_to_remote(tmp_path):
    remote = _make_remote(tmp_path)
    hub = ClaudeWebUI(data_dir=tmp_path / "hub_data3")
    _wire_hub_to_remote(hub, remote)

    with patch.object(remote.service, "validate_project_exists", new_callable=AsyncMock, return_value=False):
        async with AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test") as client:
            response = await client.get("/api/legions/leg1/schedules")

    # REMOTE answered (404 Project not found) — proves the relay crossed.
    assert response.status_code == 404


def test_queue_processor_ensure_running_guarded_in_remote_mode():
    """QueueProcessor.ensure_running() must no-op when backend_mode == REMOTE,
    even with pre-existing stale local task-tracking state."""

    class _FakeCoordinator:
        backend_mode = BackendMode.REMOTE

    processor = QueueProcessor(_FakeCoordinator())
    processor.ensure_running("some-session")
    assert processor.is_running("some-session") is False
    assert "some-session" not in processor._tasks


@pytest.mark.asyncio
async def test_queue_processor_ensure_running_starts_in_local_mode():
    class _FakeCoordinator:
        backend_mode = BackendMode.LOCAL

    processor = QueueProcessor(_FakeCoordinator())

    async def _noop_loop(session_id):
        import asyncio
        await asyncio.sleep(100)

    processor._process_loop = _noop_loop
    processor.ensure_running("some-session")
    assert processor.is_running("some-session") is True
    processor.stop("some-session")


@pytest.mark.asyncio
async def test_scheduler_tick_guarded_in_remote_mode():
    """SchedulerService._tick() must no-op when backend_mode == REMOTE, even with
    pre-existing stale local schedule state (seeded directly into self._schedules)."""
    from unittest.mock import MagicMock

    from ..legion.scheduler_service import SchedulerService
    from ..models.schedule_models import Schedule, ScheduleStatus

    fake_coordinator = MagicMock()
    fake_coordinator.backend_mode = BackendMode.REMOTE
    fake_system = MagicMock()
    fake_system.session_coordinator = fake_coordinator

    svc = SchedulerService(fake_system)
    stale_schedule = Schedule(
        schedule_id="sched1",
        legion_id="leg1",
        name="stale",
        cron_expression="* * * * *",
        prompt="stale prompt",
        status=ScheduleStatus.ACTIVE,
        next_run=0.0,  # already due
    )
    svc._schedules["sched1"] = stale_schedule

    fire_called = False

    async def _fire(*args, **kwargs):
        nonlocal fire_called
        fire_called = True

    svc._fire_schedule = _fire
    await svc._tick()

    assert fire_called is False, "REMOTE mode must not fire stale local schedules"
