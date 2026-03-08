"""
Stage 7: Integration tests for Archive endpoints (6 routes).

Tests:
- GET /api/projects/{project_id}/archives/{session_id} — list archives
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/messages — paginated messages
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/state — state + metadata
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/resources — resources
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/resources/{resource_id} — resource file
- GET /api/projects/{project_id}/deleted-agents — deleted agents list
"""

import json
import uuid

import pytest



async def _create_and_delete_session(env):
    """Create a session, add some messages to disk, then delete it to generate archive."""
    client = env["client"]
    data_dir = env["data_dir"]

    project = await env["create_test_project"]("Archive Test")
    pid = project["project_id"]
    session = await env["create_test_session"](pid, "ToArchive")
    sid = session["session_id"]

    # Write some test messages to messages.jsonl so the archive has content
    messages_file = data_dir / "sessions" / sid / "messages.jsonl"
    messages_file.parent.mkdir(parents=True, exist_ok=True)
    with open(messages_file, "a") as f:
        f.write(json.dumps({
            "type": "system", "content": "Test message", "session_id": sid
        }) + "\n")

    # Delete the session (triggers archival)
    await client.delete(f"/api/sessions/{sid}")

    return pid, sid


class TestListArchives:
    async def test_list_archives_after_delete(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert "archives" in body

    async def test_list_archives_no_archives(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("No Archives")
        pid = project["project_id"]
        fake_sid = str(uuid.uuid4())

        resp = await client.get(f"/api/projects/{pid}/archives/{fake_sid}")
        assert resp.status_code == 200
        assert resp.json()["archives"] == []


class TestArchiveMessages:
    async def test_get_archive_messages(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        # Get archives list
        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/messages"
            )
            assert resp.status_code == 200
            assert "messages" in resp.json()


class TestArchiveState:
    async def test_get_archive_state(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/state"
            )
            assert resp.status_code == 200


class TestArchiveResources:
    async def test_get_archive_resources(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources"
            )
            assert resp.status_code == 200

    async def test_get_archive_resource_file(self, api_integration_env):
        """GET /api/projects/{pid}/archives/{sid}/{aid}/resources/{rid} — raw file."""
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            res_resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources"
            )
            resources = res_resp.json().get("resources", [])
            if resources:
                rid = resources[0]["resource_id"]
                resp = await client.get(
                    f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources/{rid}"
                )
                assert resp.status_code == 200

    async def test_get_archive_resource_file_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources/nonexistent"
            )
            assert resp.status_code == 404


class TestDeletedAgents:
    async def test_deleted_agents_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("No Deletes")
        pid = project["project_id"]

        resp = await client.get(f"/api/projects/{pid}/deleted-agents")
        assert resp.status_code == 200
        body = resp.json()
        assert "agents" in body or "deleted_agents" in body

    async def test_deleted_agents_after_delete(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/deleted-agents")
        assert resp.status_code == 200
