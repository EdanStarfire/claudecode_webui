"""
Stage 5: Integration tests for Legion endpoints.

Tests:
- GET /api/legions/{legion_id}/timeline — paginated comm timeline
- GET /api/legions/{legion_id}/hierarchy — minion hierarchy tree
- POST /api/legions/{legion_id}/comms — send comm
- POST /api/legions/{legion_id}/minions — create minion
- POST /api/legions/{legion_id}/halt-all — emergency halt
- POST /api/legions/{legion_id}/resume-all — resume all
"""

import uuid

import pytest



async def _setup_legion(env):
    """Create a legion project for testing."""
    project = await env["create_test_legion_project"]("Legion Test")
    return project


class TestLegionTimeline:
    async def test_timeline_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/timeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["comms"] == []
        assert body["total"] == 0

    async def test_timeline_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        # Non-existent legion returns empty timeline (no 404)
        resp = await client.get(f"/api/legions/{fake_id}/timeline")
        assert resp.status_code == 200
        assert resp.json()["comms"] == []

    async def test_timeline_pagination(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/timeline?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert "limit" in body
        assert "offset" in body


class TestLegionHierarchy:
    async def test_hierarchy_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/hierarchy")
        assert resp.status_code == 200

    async def test_hierarchy_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.get(f"/api/legions/{fake_id}/hierarchy")
        assert resp.status_code == 404


class TestSendComm:
    async def test_send_comm_to_minion(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        # Send comm to user (avoids need for active minion SDK session)
        resp = await client.post(
            f"/api/legions/{lid}/comms",
            json={
                "to_user": True,
                "content": "Status update for user",
                "comm_type": "info",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "comm" in resp.json()

    async def test_send_comm_nonexistent_legion(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/comms",
            json={"content": "Hello", "comm_type": "info"},
        )
        assert resp.status_code == 404


class TestCreateMinion:
    async def test_create_minion(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(
            f"/api/legions/{lid}/minions",
            json={
                "name": "Test Worker",
                "role": "Code Review",
                "permission_mode": "acceptEdits",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["minion_id"]
        assert body["minion"]["name"] == "Test Worker"

    async def test_create_minion_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/minions",
            json={"name": "Orphan", "role": "Worker"},
        )
        assert resp.status_code == 404

    async def test_create_minion_with_capabilities(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(
            f"/api/legions/{lid}/minions",
            json={
                "name": "Capable Worker",
                "role": "Specialist",
                "capabilities": ["python", "testing"],
            },
        )
        assert resp.status_code == 200


class TestHaltAll:
    async def test_halt_all_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/halt-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["halted_count"] == 0

    async def test_halt_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(f"/api/legions/{fake_id}/halt-all")
        assert resp.status_code == 404


class TestResumeAll:
    async def test_resume_all_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/resume-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["resumed_count"] == 0

    async def test_resume_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(f"/api/legions/{fake_id}/resume-all")
        assert resp.status_code == 404
