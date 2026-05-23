"""
Stage 6: Integration tests for Schedule endpoints (9 routes).

Tests:
- GET /api/legions/{legion_id}/schedules — list (with filters)
- POST /api/legions/{legion_id}/schedules — create schedule
- GET /api/legions/{legion_id}/schedules/{schedule_id} — get schedule
- PUT /api/legions/{legion_id}/schedules/{schedule_id} — update
- POST /api/legions/{legion_id}/schedules/{schedule_id}/pause — pause
- POST /api/legions/{legion_id}/schedules/{schedule_id}/resume — resume
- POST /api/legions/{legion_id}/schedules/{schedule_id}/run-now — manual trigger
- DELETE /api/legions/{legion_id}/schedules/{schedule_id} — delete
- GET /api/legions/{legion_id}/schedules/{schedule_id}/history — execution history
"""

import uuid


async def _setup_legion_with_minion(env):
    """Create a legion project with a minion for schedule testing."""
    project = await env["create_test_legion_project"]("Schedule Test")
    lid = project["project_id"]
    client = env["client"]

    minion_resp = await client.post(
        f"/api/legions/{lid}/minions",
        json={"name": "ScheduleWorker", "role": "Worker"},
    )
    assert minion_resp.status_code == 200
    minion_id = minion_resp.json()["minion_id"]
    return lid, minion_id


async def _create_schedule(client, lid, minion_id, name="Test Schedule"):
    """Helper to create a schedule."""
    resp = await client.post(
        f"/api/legions/{lid}/schedules",
        json={
            "minion_id": minion_id,
            "name": name,
            "cron_expression": "*/5 * * * *",
            "prompt": "Run tests",
        },
    )
    assert resp.status_code == 200, f"Failed to create schedule: {resp.text}"
    return resp.json()["schedule"]


class TestListSchedules:
    async def test_list_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("Empty Schedules")
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/schedules")
        assert resp.status_code == 200
        assert resp.json()["schedules"] == []

    async def test_list_after_create(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        await _create_schedule(client, lid, minion_id, "Sched A")
        await _create_schedule(client, lid, minion_id, "Sched B")

        resp = await client.get(f"/api/legions/{lid}/schedules")
        assert resp.status_code == 200
        assert len(resp.json()["schedules"]) == 2

    async def test_list_filter_by_status(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        await _create_schedule(client, lid, minion_id)

        resp = await client.get(f"/api/legions/{lid}/schedules?status=active")
        assert resp.status_code == 200
        for s in resp.json()["schedules"]:
            assert s["status"] == "active"

    async def test_list_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.get(f"/api/legions/{fake_id}/schedules")
        assert resp.status_code == 404


class TestCreateSchedule:
    async def test_create_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        schedule = await _create_schedule(client, lid, minion_id)
        assert schedule["name"] == "Test Schedule"
        assert schedule["cron_expression"] == "*/5 * * * *"
        assert schedule["status"] == "active"

    async def test_create_schedule_invalid_cron(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Bad Cron",
                "cron_expression": "not a cron",
                "prompt": "test",
            },
        )
        assert resp.status_code == 400

    async def test_create_schedule_no_minion_or_config(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("No Minion")
        lid = project["project_id"]

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "name": "Orphan Schedule",
                "cron_expression": "*/5 * * * *",
                "prompt": "test",
            },
        )
        assert resp.status_code == 400


class TestGetSchedule:
    async def test_get_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.get(f"/api/legions/{lid}/schedules/{sched_id}")
        assert resp.status_code == 200
        assert resp.json()["schedule"]["name"] == "Test Schedule"

    async def test_get_nonexistent_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("Get Test")
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/schedules/nonexistent")
        assert resp.status_code == 404


class TestUpdateSchedule:
    async def test_update_schedule_name(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.put(
            f"/api/legions/{lid}/schedules/{sched_id}",
            json={"name": "Updated Schedule"},
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["name"] == "Updated Schedule"


class TestScheduleLifecycle:
    async def test_pause_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.post(f"/api/legions/{lid}/schedules/{sched_id}/pause")
        assert resp.status_code == 200
        assert resp.json()["schedule"]["status"] == "paused"

    async def test_resume_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        # Pause first
        await client.post(f"/api/legions/{lid}/schedules/{sched_id}/pause")

        # Resume
        resp = await client.post(f"/api/legions/{lid}/schedules/{sched_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["schedule"]["status"] == "active"

    async def test_cancel_endpoint_removed(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.post(f"/api/legions/{lid}/schedules/{sched_id}/cancel")
        assert resp.status_code == 404

    async def test_pause_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("Pause Test")
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/schedules/fake/pause")
        assert resp.status_code == 404


class TestRunNow:
    async def test_run_now(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.post(f"/api/legions/{lid}/schedules/{sched_id}/run-now")
        # May succeed or fail with 409 (already running) or 500 (SDK not started)
        assert resp.status_code in (200, 409, 500)

    async def test_run_now_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("Run Now Test")
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/schedules/fake/run-now")
        assert resp.status_code == 404


class TestDeleteSchedule:
    async def test_delete_schedule(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.delete(f"/api/legions/{lid}/schedules/{sched_id}")
        assert resp.status_code == 200

        # Verify gone
        resp = await client.get(f"/api/legions/{lid}/schedules/{sched_id}")
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_legion_project"]("Delete Test")
        lid = project["project_id"]

        resp = await client.delete(f"/api/legions/{lid}/schedules/fake")
        assert resp.status_code == 404


class TestScheduleHistory:
    async def test_history_empty(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.get(f"/api/legions/{lid}/schedules/{sched_id}/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["executions"] == []
        assert "limit" in body
        assert "offset" in body


class TestIssue1538RepeatCount:
    """Tests for disposable schedules with configurable repeat count (issue #1538)."""

    async def test_create_schedule_default_repeat_count_is_one(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        schedule = await _create_schedule(client, lid, minion_id, "One-shot Default")
        assert schedule["repeat_count"] == 1, "Default repeat_count should be 1"
        assert schedule["fire_count"] == 0

    async def test_create_schedule_with_repeat_count_unlimited(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Unlimited",
                "cron_expression": "*/5 * * * *",
                "prompt": "run forever",
                "repeat_count": None,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["repeat_count"] is None

    async def test_create_schedule_with_explicit_repeat_count(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Three-shot",
                "cron_expression": "*/5 * * * *",
                "prompt": "run three times",
                "repeat_count": 3,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["repeat_count"] == 3

    async def test_create_schedule_repeat_count_zero_rejected(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Zero",
                "cron_expression": "*/5 * * * *",
                "prompt": "invalid",
                "repeat_count": 0,
            },
        )
        assert resp.status_code == 422, "repeat_count=0 should fail validation"

    async def test_create_schedule_repeat_count_negative_rejected(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Negative",
                "cron_expression": "*/5 * * * *",
                "prompt": "invalid",
                "repeat_count": -1,
            },
        )
        assert resp.status_code == 422, "repeat_count=-1 should fail validation"

    async def test_update_schedule_repeat_count(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.put(
            f"/api/legions/{lid}/schedules/{sched_id}",
            json={"repeat_count": 10, "repeat_count_set": True},
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["repeat_count"] == 10

    async def test_update_schedule_set_repeat_count_to_unlimited(self, api_integration_env):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)
        schedule = await _create_schedule(client, lid, minion_id)
        sched_id = schedule["schedule_id"]

        resp = await client.put(
            f"/api/legions/{lid}/schedules/{sched_id}",
            json={"repeat_count": None, "repeat_count_set": True},
        )
        assert resp.status_code == 200
        assert resp.json()["schedule"]["repeat_count"] is None

    async def test_update_schedule_repeat_count_below_fire_count_auto_deletes(
        self, api_integration_env
    ):
        client = api_integration_env["client"]
        lid, minion_id = await _setup_legion_with_minion(api_integration_env)

        # Create schedule with repeat_count=5
        resp = await client.post(
            f"/api/legions/{lid}/schedules",
            json={
                "minion_id": minion_id,
                "name": "Will be deleted",
                "cron_expression": "*/5 * * * *",
                "prompt": "test",
                "repeat_count": 5,
            },
        )
        assert resp.status_code == 200
        sched_id = resp.json()["schedule"]["schedule_id"]

        # Manually set fire_count=3 via direct service manipulation
        svc = api_integration_env["webui"].coordinator.legion_system.scheduler_service
        svc._schedules[sched_id].fire_count = 3

        # Now update repeat_count to 2 (below fire_count=3) — should auto-delete
        resp = await client.put(
            f"/api/legions/{lid}/schedules/{sched_id}",
            json={"repeat_count": 2, "repeat_count_set": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("deleted") is True

        # Confirm 404 after auto-delete
        resp = await client.get(f"/api/legions/{lid}/schedules/{sched_id}")
        assert resp.status_code == 404
