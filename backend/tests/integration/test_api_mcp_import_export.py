"""
Integration tests for MCP server import/export endpoints (issue #788).

Export format: named dict keyed by server name, e.g. {"server-name": {type, command, ...}}
Import format: same named dict structure

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
        assert resp.json() == {}

    async def test_export_all(self, api_integration_env):
        client = api_integration_env["client"]

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
        assert isinstance(data, dict)
        assert "export-test-server" in data

        exported = data["export-test-server"]
        assert exported["type"] == "stdio"
        assert exported["command"] == "npx"
        assert exported["args"] == ["-y", "@test/mcp"]
        # name must NOT appear inside the server object
        assert "name" not in exported
        # System fields should not be exported
        assert "id" not in exported
        assert "slug" not in exported
        assert "created_at" not in exported

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_export_by_ids(self, api_integration_env):
        client = api_integration_env["client"]

        r1 = await client.post("/api/mcp-configs", json={"name": "export-id-a", "type": "stdio", "command": "cmd-a"})
        r2 = await client.post("/api/mcp-configs", json={"name": "export-id-b", "type": "stdio", "command": "cmd-b"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        id_a = r1.json()["id"]
        id_b = r2.json()["id"]

        resp = await client.post("/api/mcp-configs/export", json={"ids": [id_a]})
        assert resp.status_code == 200
        data = resp.json()
        assert "export-id-a" in data
        assert "export-id-b" not in data

        await client.delete(f"/api/mcp-configs/{id_a}")
        await client.delete(f"/api/mcp-configs/{id_b}")


class TestMcpImportDryRun:
    async def test_import_dry_run_create(self, api_integration_env):
        client = api_integration_env["client"]

        servers = {"import-dry-new": {"type": "stdio", "command": "node", "args": ["server.js"]}}
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["summary"]["create"] == 1
        assert data["summary"]["update"] == 0
        assert data["imported"] == []

        # Server should NOT actually exist after dry run
        list_resp = await client.get("/api/mcp-configs")
        names = [c["name"] for c in list_resp.json()["configs"]]
        assert "import-dry-new" not in names

    async def test_import_dry_run_update(self, api_integration_env):
        client = api_integration_env["client"]

        create_resp = await client.post("/api/mcp-configs", json={
            "name": "import-dry-exist",
            "type": "stdio",
            "command": "old-cmd",
        })
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        servers = {"import-dry-exist": {"type": "stdio", "command": "new-cmd"}}
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["update"] == 1
        assert data["summary"]["create"] == 0

        # Original should be unchanged
        get_resp = await client.get(f"/api/mcp-configs/{config_id}")
        assert get_resp.json()["command"] == "old-cmd"

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_dry_run_skip_invalid(self, api_integration_env):
        client = api_integration_env["client"]

        servers = {
            "bad-type": {"type": "invalid", "command": "cmd"},
            "no-cmd": {"type": "stdio"},
            "no-url": {"type": "sse"},
        }
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["skip"] == 3
        assert data["summary"]["create"] == 0


class TestMcpImportCommit:
    async def test_import_commit_creates_server(self, api_integration_env):
        client = api_integration_env["client"]

        servers = {"import-commit-new": {"type": "stdio", "command": "npx", "args": ["-y", "@new/mcp"]}}
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["summary"]["create"] == 1
        assert len(data["imported"]) == 1
        assert data["imported"][0]["name"] == "import-commit-new"

        # Server should now exist
        list_resp = await client.get("/api/mcp-configs")
        names = [c["name"] for c in list_resp.json()["configs"]]
        assert "import-commit-new" in names

        config_id = data["imported"][0]["id"]
        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_commit_updates_server(self, api_integration_env):
        client = api_integration_env["client"]

        create_resp = await client.post("/api/mcp-configs", json={
            "name": "import-commit-exist",
            "type": "stdio",
            "command": "old-cmd",
        })
        assert create_resp.status_code == 200
        config_id = create_resp.json()["id"]

        servers = {"import-commit-exist": {"type": "stdio", "command": "updated-cmd"}}
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["update"] == 1
        assert len(data["imported"]) == 1
        assert data["imported"][0]["command"] == "updated-cmd"

        get_resp = await client.get(f"/api/mcp-configs/{config_id}")
        assert get_resp.json()["command"] == "updated-cmd"

        await client.delete(f"/api/mcp-configs/{config_id}")

    async def test_import_commit_multiple_servers(self, api_integration_env):
        """Multiple servers in one dict import."""
        client = api_integration_env["client"]

        servers = {
            "import-multi-a": {"type": "stdio", "command": "cmd-a"},
            "import-multi-b": {"type": "stdio", "command": "cmd-b"},
        }
        resp = await client.post("/api/mcp-configs/import", json={"servers": servers, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["create"] == 2
        assert len(data["imported"]) == 2

        for cfg in data["imported"]:
            await client.delete(f"/api/mcp-configs/{cfg['id']}")
