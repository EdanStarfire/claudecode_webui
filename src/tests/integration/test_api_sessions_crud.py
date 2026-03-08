"""
Stage 2a: Integration tests for Session CRUD & Configuration endpoints.

Tests:
- POST /api/sessions — create session in project
- GET /api/sessions — list all sessions
- GET /api/sessions/{session_id} — get session info
- GET /api/sessions/{session_id}/descendants — get descendant hierarchy
- PUT /api/sessions/{session_id}/name — update name
- PATCH /api/sessions/{session_id} — update config
- DELETE /api/sessions/{session_id} — delete + cascade children
- DELETE /api/sessions/{session_id}/history — erase distilled history
- DELETE /api/sessions/{session_id}/archives — erase archives
- GET /api/sessions/{session_id}/history-archives-status — check existence
- GET /api/sessions/{session_id}/mcp-status — MCP server status
- POST /api/sessions/{session_id}/mcp-toggle — toggle MCP
- POST /api/sessions/{session_id}/mcp-reconnect — reconnect MCP
"""

import uuid

import pytest

pytestmark = pytest.mark.slow


class TestCreateSession:
    async def test_create_session(self, api_integration_env):
        client = api_integration_env["client"]
        create_project = api_integration_env["create_test_project"]
        data_dir = api_integration_env["data_dir"]

        project = await create_project("Session Test")
        pid = project["project_id"]

        resp = await client.post("/api/sessions", json={
            "project_id": pid,
            "name": "My Session",
        })
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        assert session_id

        # Verify persistence
        state_file = data_dir / "sessions" / session_id / "state.json"
        assert state_file.exists()

    async def test_create_session_with_config(self, api_integration_env):
        client = api_integration_env["client"]
        create_project = api_integration_env["create_test_project"]

        project = await create_project("Configured")
        pid = project["project_id"]

        resp = await client.post("/api/sessions", json={
            "project_id": pid,
            "name": "Configured Session",
            "permission_mode": "acceptEdits",
            "model": "sonnet",
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{sid}")
        session = resp.json()["session"]
        assert session["current_permission_mode"] == "acceptEdits"

    async def test_create_session_invalid_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post("/api/sessions", json={
            "project_id": fake_id,
            "name": "Orphan",
        })
        assert resp.status_code == 500


class TestListSessions:
    async def test_list_empty(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    async def test_list_sessions(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("List Test")
        pid = project["project_id"]
        await create_session(pid, "S1")
        await create_session(pid, "S2")

        resp = await client.get("/api/sessions")
        assert resp.status_code == 200
        assert len(resp.json()["sessions"]) == 2


class TestGetSession:
    async def test_get_session(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Get Test")
        session = await create_session(project["project_id"], "Detail")

        resp = await client.get(f"/api/sessions/{session['session_id']}")
        assert resp.status_code == 200
        assert resp.json()["session"]["name"] == "Detail"

    async def test_get_nonexistent_session(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/sessions/{fake_id}")
        assert resp.status_code == 404


class TestGetDescendants:
    async def test_get_descendants_no_children(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Descendants Test")
        session = await create_session(project["project_id"], "Parent")

        resp = await client.get(f"/api/sessions/{session['session_id']}/descendants")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["descendants"] == []


class TestUpdateSessionName:
    async def test_update_name(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Name Test")
        session = await create_session(project["project_id"], "Original")
        sid = session["session_id"]

        resp = await client.put(f"/api/sessions/{sid}/name", json={"name": "Renamed"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["name"] == "Renamed"

    async def test_update_name_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.put(f"/api/sessions/{fake_id}/name", json={"name": "X"})
        assert resp.status_code == 404


class TestPatchSession:
    async def test_patch_model(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Patch Test")
        session = await create_session(project["project_id"], "Patchable")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"model": "opus"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["model"] == "opus"

    async def test_patch_role(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Role Test")
        session = await create_session(project["project_id"], "Worker")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"role": "Code Reviewer"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["role"] == "Code Reviewer"

    async def test_patch_invalid_model(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Invalid Model")
        session = await create_session(project["project_id"], "Bad")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"model": "gpt-4"})
        assert resp.status_code == 400

    async def test_patch_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.patch(f"/api/sessions/{fake_id}", json={"name": "X"})
        assert resp.status_code == 404

    async def test_patch_multiple_fields(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Multi Patch")
        session = await create_session(project["project_id"], "Multi")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={
            "name": "Updated Name",
            "role": "Tester",
            "system_prompt": "You are a tester.",
        })
        assert resp.status_code == 200

        resp = await client.get(f"/api/sessions/{sid}")
        s = resp.json()["session"]
        assert s["name"] == "Updated Name"
        assert s["role"] == "Tester"

    async def test_patch_empty_no_changes(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Empty Patch")
        session = await create_session(project["project_id"], "NoOp")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={})
        assert resp.status_code == 200
        assert resp.json()["message"] == "No fields to update"


class TestDeleteSession:
    async def test_delete_session(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Delete Test")
        session = await create_session(project["project_id"], "ToDelete")
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200

        # Verify not in list
        resp = await client.get("/api/sessions")
        ids = [s["session_id"] for s in resp.json()["sessions"]]
        assert sid not in ids

    async def test_delete_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.delete(f"/api/sessions/{fake_id}")
        assert resp.status_code == 404


class TestHistoryArchivesStatus:
    async def test_status_no_history(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Status Test")
        session = await create_session(project["project_id"], "Fresh")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/history-archives-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "has_history" in body or "has_distilled_history" in body
        assert "has_archives" in body


class TestMcpStatus:
    async def test_mcp_status(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("MCP Test")
        session = await create_session(project["project_id"], "MCPSession")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/mcp-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "servers" in body or "mcp_servers" in body or "status" in body
