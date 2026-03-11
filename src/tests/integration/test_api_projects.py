"""
Stage 1: Integration tests for Project REST API endpoints (8 routes).

Tests:
- POST /api/projects — create project
- GET /api/projects — list all projects
- GET /api/projects/{project_id} — get project + sessions
- PUT /api/projects/{project_id} — update metadata
- DELETE /api/projects/{project_id} — delete with cascade
- PUT /api/projects/reorder — reorder projects
- PUT /api/projects/{project_id}/toggle-expansion — toggle expansion
- PUT /api/projects/{project_id}/sessions/reorder — reorder sessions in project
"""

import json
import uuid


class TestCreateProject:
    async def test_create_project(self, api_integration_env):
        client = api_integration_env["client"]
        data_dir = api_integration_env["data_dir"]

        resp = await client.post("/api/projects", json={
            "name": "My Project",
            "working_directory": "/tmp",
        })
        assert resp.status_code == 200
        project = resp.json()["project"]
        assert project["name"] == "My Project"
        assert project["project_id"]

        # Verify file system persistence
        state_file = data_dir / "projects" / project["project_id"] / "state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["name"] == "My Project"

    async def test_create_project_with_defaults(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/projects", json={
            "name": "Default Project",
            "working_directory": "/tmp",
        })
        assert resp.status_code == 200
        project = resp.json()["project"]
        assert project["session_ids"] == []

    async def test_create_multiple_projects_ordered(self, api_integration_env):
        client = api_integration_env["client"]

        for i in range(3):
            resp = await client.post("/api/projects", json={
                "name": f"Project {i}",
                "working_directory": "/tmp",
            })
            assert resp.status_code == 200

        resp = await client.get("/api/projects")
        projects = resp.json()["projects"]
        assert len(projects) == 3


class TestListProjects:
    async def test_list_empty(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json()["projects"] == []

    async def test_list_after_creates(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        await create_project("Alpha")
        await create_project("Beta")

        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        projects = resp.json()["projects"]
        assert len(projects) == 2
        names = {p["name"] for p in projects}
        assert names == {"Alpha", "Beta"}


class TestGetProject:
    async def test_get_project(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        project = await create_project("Detail Project")
        pid = project["project_id"]

        resp = await client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project"]["name"] == "Detail Project"
        assert "sessions" in body

    async def test_get_project_with_sessions(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("With Sessions")
        pid = project["project_id"]
        await create_session(pid, "Session A")
        await create_session(pid, "Session B")

        resp = await client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2

    async def test_get_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/projects/{fake_id}")
        assert resp.status_code == 404


class TestUpdateProject:
    async def test_update_name(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]
        data_dir = api_integration_env["data_dir"]

        project = await create_project("Old Name")
        pid = project["project_id"]

        resp = await client.put(f"/api/projects/{pid}", json={"name": "New Name"})
        assert resp.status_code == 200

        # Verify in-memory
        resp = await client.get(f"/api/projects/{pid}")
        assert resp.json()["project"]["name"] == "New Name"

        # Verify persistence
        state = json.loads(
            (data_dir / "projects" / pid / "state.json").read_text()
        )
        assert state["name"] == "New Name"

    async def test_update_expansion(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        project = await create_project("Expandable")
        pid = project["project_id"]

        resp = await client.put(f"/api/projects/{pid}", json={"is_expanded": False})
        assert resp.status_code == 200

        resp = await client.get(f"/api/projects/{pid}")
        assert resp.json()["project"]["is_expanded"] is False

    async def test_update_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.put(f"/api/projects/{fake_id}", json={"name": "X"})
        assert resp.status_code == 404


class TestDeleteProject:
    async def test_delete_project(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]
        data_dir = api_integration_env["data_dir"]

        project = await create_project("To Delete")
        pid = project["project_id"]

        resp = await client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200

        # Verify removed from list
        resp = await client.get("/api/projects")
        ids = [p["project_id"] for p in resp.json()["projects"]]
        assert pid not in ids

        # Verify state file removed
        assert not (data_dir / "projects" / pid / "state.json").exists()

    async def test_delete_cascade_sessions(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Cascade")
        pid = project["project_id"]
        await create_session(pid, "Session 1")
        await create_session(pid, "Session 2")

        resp = await client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 200

        # Verify project no longer in listings
        resp = await client.get("/api/projects")
        project_ids = [p["project_id"] for p in resp.json()["projects"]]
        assert pid not in project_ids

    async def test_delete_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.delete(f"/api/projects/{fake_id}")
        assert resp.status_code == 404


class TestReorderProjects:
    async def test_reorder_projects(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        p1 = await create_project("First")
        p2 = await create_project("Second")
        p3 = await create_project("Third")

        # Reverse order
        new_order = [p3["project_id"], p2["project_id"], p1["project_id"]]
        resp = await client.put("/api/projects/reorder", json={"project_ids": new_order})
        assert resp.status_code == 200

        resp = await client.get("/api/projects")
        projects = resp.json()["projects"]
        result_ids = [p["project_id"] for p in projects]
        assert result_ids == new_order


class TestToggleExpansion:
    async def test_toggle_expansion(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        project = await create_project("Toggle Me")
        pid = project["project_id"]
        initial = project.get("is_expanded", True)

        resp = await client.put(f"/api/projects/{pid}/toggle-expansion")
        assert resp.status_code == 200

        resp = await client.get(f"/api/projects/{pid}")
        assert resp.json()["project"]["is_expanded"] is not initial

    async def test_toggle_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.put(f"/api/projects/{fake_id}/toggle-expansion")
        assert resp.status_code == 404


class TestReorderSessions:
    async def test_reorder_sessions_in_project(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Reorder Sessions")
        pid = project["project_id"]
        s1 = await create_session(pid, "A")
        s2 = await create_session(pid, "B")
        s3 = await create_session(pid, "C")

        new_order = [s3["session_id"], s1["session_id"], s2["session_id"]]
        resp = await client.put(
            f"/api/projects/{pid}/sessions/reorder",
            json={"session_ids": new_order},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/projects/{pid}")
        result_ids = resp.json()["project"]["session_ids"]
        assert result_ids == new_order

    async def test_reorder_sessions_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.put(
            f"/api/projects/{fake_id}/sessions/reorder",
            json={"session_ids": []},
        )
        assert resp.status_code in (400, 404)
