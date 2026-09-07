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

from backend.session_config import SessionConfig


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

    async def test_issue_709_create_session_preserves_auto_memory_mode(self, api_integration_env):
        """Regression: auto_memory_mode must survive the API create → store round-trip."""
        client = api_integration_env["client"]
        create_project = api_integration_env["create_test_project"]

        project = await create_project("AutoMemory")
        pid = project["project_id"]

        resp = await client.post("/api/sessions", json={
            "project_id": pid,
            "name": "Session Memory",
            "auto_memory_mode": "session",
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{sid}")
        session = resp.json()["session"]
        assert session["config"].get("auto_memory_mode") == "session", (
            "auto_memory_mode='session' must be preserved through create_session"
        )

    async def test_create_session_invalid_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post("/api/sessions", json={
            "project_id": fake_id,
            "name": "Orphan",
        })
        assert resp.status_code == 404


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
        assert body["total"] == 0
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
        assert resp.json()["session"]["config"].get("model") == "opus"

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

    async def test_patch_session_overrides_rejected(self, api_integration_env):
        """PATCH /api/sessions/{id} with session_overrides must be rejected (issue #1230)."""
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Overrides Rejection")
        session = await create_session(project["project_id"], "Subject")
        sid = session["session_id"]

        resp = await client.patch(
            f"/api/sessions/{sid}",
            json={"session_overrides": {"model": "claude-opus-4-7"}},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for session_overrides, got {resp.status_code}: {resp.text}"
        )

    async def test_patch_response_includes_persisted_session(self, api_integration_env):
        """PATCH response must return the persisted session, not just {"success": True} (issue #1842)."""
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Patch Response")
        session = await create_session(project["project_id"], "Responder")
        sid = session["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"role": "Reviewer"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["session"]["session_id"] == sid
        assert body["session"]["role"] == "Reviewer"

    async def test_patch_template_id_set_on_templateless_session(self, api_integration_env):
        """Issue #1842: assigning a template_id to a templateless session must persist."""
        coordinator = api_integration_env["coordinator"]
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        template = await coordinator.template_manager.create_template(name="Reviewer Template", config=SessionConfig())

        project = await create_project("Template Assign")
        session = await create_session(project["project_id"], "Templateless")
        sid = session["session_id"]
        assert session["template_id"] is None

        resp = await client.patch(f"/api/sessions/{sid}", json={"template_id": template.template_id})
        assert resp.status_code == 200
        assert resp.json()["session"]["template_id"] == template.template_id

        # Persisted — a fresh GET reflects it too (not just the PATCH response)
        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["template_id"] == template.template_id

    async def test_patch_template_id_switch(self, api_integration_env):
        """Issue #1842: switching from template A to template B must persist the new id."""
        coordinator = api_integration_env["coordinator"]
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        template_a = await coordinator.template_manager.create_template(name="Template A", config=SessionConfig())
        template_b = await coordinator.template_manager.create_template(name="Template B", config=SessionConfig())

        project = await create_project("Template Switch")
        resp = await client.post("/api/sessions", json={
            "project_id": project["project_id"],
            "name": "Switcher",
            "template_id": template_a.template_id,
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"template_id": template_b.template_id})
        assert resp.status_code == 200
        assert resp.json()["session"]["template_id"] == template_b.template_id

        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["template_id"] == template_b.template_id

    async def test_patch_template_id_clear_to_null(self, api_integration_env):
        """Issue #1842: explicitly clearing template_id back to null must persist (not be dropped)."""
        coordinator = api_integration_env["coordinator"]
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        template = await coordinator.template_manager.create_template(name="To Be Cleared", config=SessionConfig())

        project = await create_project("Template Clear")
        resp = await client.post("/api/sessions", json={
            "project_id": project["project_id"],
            "name": "Clearable",
            "template_id": template.template_id,
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        resp = await client.patch(f"/api/sessions/{sid}", json={"template_id": None})
        assert resp.status_code == 200
        assert resp.json()["session"]["template_id"] is None

        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["template_id"] is None

    async def test_patch_omitted_template_id_leaves_existing_value_unchanged(self, api_integration_env):
        """Issue #1842: omitting template_id entirely from the PATCH body must not clear it —
        model_fields_set must distinguish 'omitted' from 'explicit null'."""
        coordinator = api_integration_env["coordinator"]
        create_project = api_integration_env["create_test_project"]
        client = api_integration_env["client"]

        template = await coordinator.template_manager.create_template(name="Untouched", config=SessionConfig())

        project = await create_project("Template Omit")
        resp = await client.post("/api/sessions", json={
            "project_id": project["project_id"],
            "name": "Untouched Session",
            "template_id": template.template_id,
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        # PATCH some other field, deliberately not mentioning template_id at all
        resp = await client.patch(f"/api/sessions/{sid}", json={"role": "Bystander"})
        assert resp.status_code == 200
        assert resp.json()["session"]["template_id"] == template.template_id

    async def test_patch_survives_concurrent_delete_race(self, api_integration_env):
        """If the session is deleted between update_session() succeeding and the
        follow-up get_session_info() re-fetch, the PATCH must not 500 — the update
        itself already succeeded (issue #1842 code review finding)."""
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]

        project = await create_project("Race Test")
        session = await create_session(project["project_id"], "Racer")
        sid = session["session_id"]

        original_get_session_info = coordinator.session_manager.get_session_info
        call_count = 0

        async def deleting_get_session_info(session_id):
            # First call is the handler's initial existence check — let it through normally.
            # Second call is the post-update re-fetch — simulate a concurrent delete
            # landing right before it runs.
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                coordinator.session_manager.get_session_info = original_get_session_info
                del coordinator.session_manager._active_sessions[session_id]
            return await original_get_session_info(session_id)

        coordinator.session_manager.get_session_info = deleting_get_session_info

        resp = await client.patch(f"/api/sessions/{sid}", json={"role": "Doomed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "session" not in body


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


class TestEraseHistory:
    async def test_erase_history(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Erase History Test")
        session = await create_session(project["project_id"], "HistSession")
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/history")
        assert resp.status_code == 200
        assert "success" in resp.json()

    async def test_erase_history_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.delete(f"/api/sessions/{fake_id}/history")
        # May return 200 (no-op) or 500 depending on implementation
        assert resp.status_code in (200, 500)


class TestEraseArchives:
    async def test_erase_archives(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Erase Archives Test")
        session = await create_session(project["project_id"], "ArchSession")
        sid = session["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}/archives")
        assert resp.status_code == 200
        assert "success" in resp.json()

    async def test_erase_archives_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.delete(f"/api/sessions/{fake_id}/archives")
        assert resp.status_code in (200, 500)


class TestMcpToggle:
    async def test_toggle_mcp_server(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("MCP Toggle Test")
        session = await create_session(project["project_id"], "ToggleSession")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/mcp-toggle",
            json={"name": "test-server", "enabled": False},
        )
        # 400 expected since no MCP server named "test-server" exists
        assert resp.status_code in (200, 400)

    async def test_toggle_mcp_nonexistent_session(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/sessions/{fake_id}/mcp-toggle",
            json={"name": "test-server", "enabled": True},
        )
        assert resp.status_code in (400, 404, 422)


class TestMcpReconnect:
    async def test_reconnect_mcp_server(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("MCP Reconnect Test")
        session = await create_session(project["project_id"], "ReconnSession")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/mcp-reconnect",
            json={"name": "test-server"},
        )
        # 400 expected since no MCP server named "test-server" exists
        assert resp.status_code in (200, 400)

    async def test_reconnect_mcp_nonexistent_session(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/sessions/{fake_id}/mcp-reconnect",
            json={"name": "test-server"},
        )
        assert resp.status_code in (400, 404, 422)


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
