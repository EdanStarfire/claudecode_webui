"""
Stage 8: Integration tests for Utility & System endpoints (17 routes).

Tests:
- GET /health — health check
- GET /api/config — get config
- PUT /api/config — update config
- POST /api/skills/sync — sync skills
- GET /api/skills/status — skill status
- GET /api/system/docker-status — Docker availability
- GET /api/system/git-status — git branch/commit info
- POST /api/system/restart — SKIP (destructive: os.execv)
- GET /api/filesystem/browse — directory browsing
- GET /api/templates — list templates
- GET /api/templates/{template_id} — get template
- POST /api/templates — create template
- PUT /api/templates/{template_id} — update template
- DELETE /api/templates/{template_id} — delete template
- POST /api/permissions/preview — permission preview
- GET /api/sessions/{session_id}/diff — git diff summary
- GET /api/sessions/{session_id}/diff/file — file-level diff
"""





class TestHealth:
    async def test_health_check(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "timestamp" in body


class TestConfig:
    async def test_get_config(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/config")
        assert resp.status_code == 200
        assert "config" in resp.json()

    async def test_update_config(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.put("/api/config", json={
            "features": {"skill_sync_enabled": False},
        })
        assert resp.status_code == 200
        assert "config" in resp.json()


class TestSkills:
    async def test_skill_status(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/skills/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "sync_enabled" in body

    async def test_sync_skills(self, api_integration_env):
        client = api_integration_env["client"]

        # Ensure sync is enabled first
        await client.put("/api/config", json={
            "features": {"skill_sync_enabled": True},
        })

        resp = await client.post("/api/skills/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "synced"

    async def test_sync_skills_when_disabled(self, api_integration_env):
        client = api_integration_env["client"]

        # Disable sync
        await client.put("/api/config", json={
            "features": {"skill_sync_enabled": False},
        })

        resp = await client.post("/api/skills/sync")
        assert resp.status_code == 409


class TestSystemStatus:
    async def test_docker_status(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/system/docker-status")
        assert resp.status_code == 200
        # Should return structured response regardless of Docker availability

    async def test_git_status(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/system/git-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "branch" in body
        assert "last_commit_hash" in body


class TestFilesystem:
    async def test_browse_default(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/filesystem/browse")
        assert resp.status_code == 200
        body = resp.json()
        assert "current_path" in body
        assert "directories" in body
        assert "separator" in body

    async def test_browse_tmp(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/filesystem/browse?path=/tmp")
        assert resp.status_code == 200
        assert resp.json()["current_path"] == "/tmp"

    async def test_browse_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/filesystem/browse?path=/nonexistent_path_xyz")
        assert resp.status_code == 404


class TestTemplates:
    async def test_list_templates(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/templates")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_create_template(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/templates", json={
            "name": "Test Template",
            "permission_mode": "acceptEdits",
            "description": "A test template",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Test Template"
        assert "template_id" in body

    async def test_get_template(self, api_integration_env):
        client = api_integration_env["client"]

        # Create first
        create_resp = await client.post("/api/templates", json={
            "name": "Get Test Template",
            "permission_mode": "default",
        })
        tid = create_resp.json()["template_id"]

        resp = await client.get(f"/api/templates/{tid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Test Template"

    async def test_get_nonexistent_template(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/templates/nonexistent")
        assert resp.status_code == 404

    async def test_update_template(self, api_integration_env):
        client = api_integration_env["client"]

        # Create first
        create_resp = await client.post("/api/templates", json={
            "name": "Update Test",
            "permission_mode": "default",
        })
        tid = create_resp.json()["template_id"]

        resp = await client.put(f"/api/templates/{tid}", json={
            "name": "Updated Name",
            "description": "Updated desc",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"

    async def test_delete_template(self, api_integration_env):
        client = api_integration_env["client"]

        # Create first
        create_resp = await client.post("/api/templates", json={
            "name": "Delete Test",
            "permission_mode": "default",
        })
        tid = create_resp.json()["template_id"]

        resp = await client.delete(f"/api/templates/{tid}")
        assert resp.status_code == 200

        # Verify deleted
        resp = await client.get(f"/api/templates/{tid}")
        assert resp.status_code == 404

    async def test_delete_nonexistent_template(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.delete("/api/templates/nonexistent")
        assert resp.status_code == 404


class TestPermissionPreview:
    async def test_preview_permissions(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/permissions/preview", json={
            "working_directory": "/tmp",
        })
        assert resp.status_code == 200
        assert "permissions" in resp.json()

    async def test_preview_with_allowed_tools(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/permissions/preview", json={
            "working_directory": "/tmp",
            "session_allowed_tools": ["bash", "edit", "read"],
        })
        assert resp.status_code == 200
        assert "permissions" in resp.json()


class TestDiff:
    async def test_diff_summary(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Diff Test")
        session = await create_session(project["project_id"], "DiffSession")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/diff")
        assert resp.status_code == 200
        body = resp.json()
        assert "is_git_repo" in body

    async def test_diff_file_no_path(self, api_integration_env):
        create_project = api_integration_env["create_test_project"]
        create_session = api_integration_env["create_test_session"]
        client = api_integration_env["client"]

        project = await create_project("Diff File Test")
        session = await create_session(project["project_id"], "DiffFileSession")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/diff/file")
        assert resp.status_code == 400
