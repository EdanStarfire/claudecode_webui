"""
Integration tests for MCP server import/export endpoints (issue #788).

Tests:
- POST /api/mcp-configs/export — export all or selected configs
- POST /api/mcp-configs/import (dry_run=true) — preview without committing
- POST /api/mcp-configs/import (dry_run=false) — commit import
"""


class TestMcpExport:
    async def test_export_empty(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post("/api/mcp-configs/export", json={})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_export_all(self, api_integration_env):
        client = api_integration_env["client"]

        # Create a config first
        create_resp = await client.post("/api/mcp-configs", json={
            "name": "export-test-server",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@test/mcp"],
        })
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        resp = await client.post("/api/mcp-configs/export", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        exported = next((s for s in data if s["name"] == "export-test-server"), None)
        assert exported is not None
        assert exported["type"] == "stdio"
        assert exported["command"] == "npx"
        assert exported["args"] == ["-y", "@test/mcp"]
        # System fields should NOT be exported
        assert "id" not in exported
        assert "slug" not in exported
        assert "created_at" not in exported

        # Cleanup
        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_export_by_ids(self, api_integration_env):
        client = api_integration_env["client"]

        r1 = await client.post("/api/mcp-configs", json={"name": "export-id-a", "type": "stdio", "command": "cmd-a"})
        r2 = await client.post("/api/mcp-configs", json={"name": "export-id-b", "type": "stdio", "command": "cmd-b"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        id_a = r1.json()["id"]
        id_b = r2.json()["id"]

        # Export only id_a
        resp = await client.post("/api/mcp-configs/export", json={"ids": [id_a]})
        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data]
        assert "export-id-a" in names
        assert "export-id-b" not in names

        # Cleanup
        await client.delete(f"/api/mcp-configs/{id_a}")
        await client.delete(f"/api/mcp-configs/{id_b}")


class TestMcpImportDryRun:
    async def test_import_dry_run_create(self, api_integration_env):
        client = api_integration_env["client"]

        servers = [{"name": "import-dry-new", "type": "stdio", "command": "node", "args": ["server.js"]}]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["summary"]["create"] == 1
        assert data["summary"]["update"] == 0
        assert data["imported"] == []

        # Server should NOT actually exist after dry run
        list_resp = await client.get("/api/mcp-configs")
        names = [c["name"] for c in list_resp.json()]
        assert "import-dry-new" not in names

    async def test_import_dry_run_update(self, api_integration_env):
        client = api_integration_env["client"]

        # Create existing server
        create_resp = await client.post("/api/mcp-configs", json={
            "name": "import-dry-exist",
            "type": "stdio",
            "command": "old-cmd",
        })
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        # Dry run with same name → should show "update"
        servers = [{"name": "import-dry-exist", "type": "stdio", "command": "new-cmd"}]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["update"] == 1
        assert data["summary"]["create"] == 0

        # Original should be unchanged
        get_resp = await client.get(f"/api/mcp-configs/{config_id}")
        assert get_resp.json()["command"] == "old-cmd"

        # Cleanup
        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_dry_run_skip_invalid(self, api_integration_env):
        client = api_integration_env["client"]

        servers = [
            {"name": "", "type": "stdio", "command": "cmd"},   # missing name
            {"name": "bad-type", "type": "invalid", "command": "cmd"},  # bad type
            {"name": "no-cmd", "type": "stdio"},               # stdio without command
        ]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["skip"] == 3
        assert data["summary"]["create"] == 0


class TestMcpImportCommit:
    async def test_import_commit_creates_server(self, api_integration_env):
        client = api_integration_env["client"]

        servers = [{"name": "import-commit-new", "type": "stdio", "command": "npx", "args": ["-y", "@new/mcp"]}]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["summary"]["create"] == 1
        assert len(data["imported"]) == 1
        assert data["imported"][0]["name"] == "import-commit-new"

        # Server should now exist
        list_resp = await client.get("/api/mcp-configs")
        names = [c["name"] for c in list_resp.json()]
        assert "import-commit-new" in names

        # Cleanup
        config_id = data["imported"][0]["id"]
        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_commit_updates_server(self, api_integration_env):
        client = api_integration_env["client"]

        # Create existing server
        create_resp = await client.post("/api/mcp-configs", json={
            "name": "import-commit-exist",
            "type": "stdio",
            "command": "old-cmd",
        })
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        # Import with same name → should update
        servers = [{"name": "import-commit-exist", "type": "stdio", "command": "updated-cmd"}]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["update"] == 1
        assert len(data["imported"]) == 1
        assert data["imported"][0]["command"] == "updated-cmd"

        # Verify the update
        get_resp = await client.get(f"/api/mcp-configs/{config_id}")
        assert get_resp.json()["command"] == "updated-cmd"

        # Cleanup
        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_commit_single_object(self, api_integration_env):
        """Single server object (not array) should be handled on the client side."""
        client = api_integration_env["client"]

        # The backend expects an array; single-object wrapping is done in the frontend
        servers = [{"name": "import-single-obj", "type": "stdio", "command": "single-cmd"}]
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        assert resp.json()["summary"]["create"] == 1

        config_id = resp.json()["imported"][0]["id"]
        await client.delete(f"/api/mcp-configs/{config_id}")
