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
- GET /api/system/git-branches — local + origin branches (issue #1760)
- GET /api/system/git-commits — commit list for a branch (issue #1760)
- POST /api/system/restart — default path SKIP (destructive: os.execv); custom-path
  branch/commit logic covered with mocked git commands (issue #1760)
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

import subprocess
from unittest.mock import AsyncMock, patch


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

    async def test_git_branches(self, api_integration_env):
        """Issue #1760: branch listing runs against the real worktree repo, read-only."""
        client = api_integration_env["client"]

        resp = await client.get("/api/system/git-branches")
        assert resp.status_code == 200
        body = resp.json()
        assert "branches" in body
        assert "remote_fetch_failed" in body
        assert len(body["branches"]) >= 1
        assert any(b["is_current"] for b in body["branches"])
        for b in body["branches"]:
            assert {"name", "is_current", "is_local", "is_remote_only"} <= b.keys()

    async def test_git_commits(self, api_integration_env):
        """Issue #1760: commit list for the current branch, capped at 50, read-only."""
        client = api_integration_env["client"]

        branches_resp = await client.get("/api/system/git-branches")
        current = next(b["name"] for b in branches_resp.json()["branches"] if b["is_current"])

        resp = await client.get("/api/system/git-commits", params={"branch": current})
        assert resp.status_code == 200
        body = resp.json()
        assert body["branch"] == current
        assert len(body["commits"]) <= 50
        assert len(body["commits"]) >= 1
        for c in body["commits"]:
            assert {"hash", "short_hash", "subject", "author", "date"} <= c.keys()
            assert "\n" not in c["subject"]

    async def test_git_commits_missing_branch_param(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get("/api/system/git-commits")
        assert resp.status_code == 422  # FastAPI query-param validation

    async def test_git_commits_unknown_branch(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.get(
            "/api/system/git-commits", params={"branch": "definitely-not-a-real-branch-xyz"}
        )
        assert resp.status_code == 404

    async def test_git_commits_exactly_50_is_not_marked_truncated(self, api_integration_env):
        """Issue #1760: a branch with exactly 50 commits must not falsely report truncated=true."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        log_output = "\n".join(
            f"hash{i}\x1fh{i}\x1fsubject {i}\x1fauthor\x1f2026-01-01T00:00:00+00:00"
            for i in range(50)
        )

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/main"]:
                return "abc123"
            if args[:2] == ["git", "log"]:
                return log_output
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        with patch.object(webui, "_run_git_command", AsyncMock(side_effect=run_git_command_side_effect)):
            resp = await client.get("/api/system/git-commits", params={"branch": "main"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["commits"]) == 50
        assert body["truncated"] is False

    async def test_git_commits_over_50_is_marked_truncated(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        log_output = "\n".join(
            f"hash{i}\x1fh{i}\x1fsubject {i}\x1fauthor\x1f2026-01-01T00:00:00+00:00"
            for i in range(51)
        )

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/main"]:
                return "abc123"
            if args[:2] == ["git", "log"]:
                return log_output
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        with patch.object(webui, "_run_git_command", AsyncMock(side_effect=run_git_command_side_effect)):
            resp = await client.get("/api/system/git-commits", params={"branch": "main"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["commits"]) == 50
        assert body["truncated"] is True


def _fake_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_async_proc(returncode=0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    return proc


class TestRestartCustomPath:
    """Issue #1760: unit-level coverage for the branch/commit restart path.

    All git commands are mocked — nothing here mutates the real repo, and
    ``os.execv``/``coordinator.cleanup`` are patched so the actual restart never fires.
    """

    def _patches(self, webui, run_git_command_side_effect, run_side_effect):
        return [
            patch.object(webui, "_run_git_command", AsyncMock(side_effect=run_git_command_side_effect)),
            patch("src.routers.system.subprocess.run", side_effect=run_side_effect),
            patch("src.routers.system.asyncio.create_subprocess_exec",
                  AsyncMock(return_value=_fake_async_proc())),
            patch("src.routers.system.os.execv"),
            patch.object(webui.coordinator, "cleanup", AsyncMock()),
        ]

    async def test_restart_default_body_runs_unchanged_pull_path(self, api_integration_env):
        """A request with no body must never touch the branch/commit switching logic."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(*args, **kwargs):
            raise AssertionError("_run_git_command must not be called on the default restart path")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "pull"]:
                return _fake_completed(stdout="Already up to date.\n")
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call on default path: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart")
            assert resp.status_code == 202
            body = resp.json()
            assert body["pull_output"] == "Already up to date."
        finally:
            for p in patches:
                p.stop()

    async def test_restart_uncommitted_changes_returns_409(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return " M some_file.py"
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            raise AssertionError(f"subprocess.run must not run once 409 is triggered: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart", json={"branch": "other-branch"})
            assert resp.status_code == 409
        finally:
            for p in patches:
                p.stop()

    async def test_restart_toctou_recheck_blocks_dirty_tree_before_reset(self, api_integration_env):
        """Issue #1760: the pre-reset re-check must fire and block, not just the first check.

        Simulates the working tree going dirty *between* the initial check and the reset
        (i.e. during fetch/checkout) — the second ``git status --porcelain`` call returns dirty
        even though the first returned clean.
        """
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        status_call_count = 0

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            nonlocal status_call_count
            if args == ["git", "status", "--porcelain"]:
                status_call_count += 1
                return "" if status_call_count == 1 else " M some_file.py"
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/feature-x"]:
                return "abc123"
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "feature-x"]:
                return _fake_completed()
            raise AssertionError(f"subprocess.run must not run past the pre-reset check: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart", json={"branch": "feature-x"})
            assert resp.status_code == 409
            assert status_call_count == 2
        finally:
            for p in patches:
                p.stop()

    async def test_restart_status_check_failure_blocks_rather_than_proceeds(self, api_integration_env):
        """Issue #1760: a failed (not just clean) status check must not be treated as safe."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return None  # _run_git_command's failure sentinel
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            raise AssertionError(f"subprocess.run must not run once status check fails: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart", json={"branch": "feature-x"})
            assert resp.status_code == 500
        finally:
            for p in patches:
                p.stop()

    async def test_restart_rejects_flag_like_branch(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/system/restart", json={"branch": "--upload-pack=/bin/sh"})
        assert resp.status_code == 400

    async def test_restart_rejects_flag_like_commit(self, api_integration_env):
        client = api_integration_env["client"]

        resp = await client.post("/api/system/restart", json={"commit": "--exec=/bin/sh"})
        assert resp.status_code == 400

    async def test_restart_custom_branch_fetch_checkout_reset_sequence(self, api_integration_env):
        """Existing local branch: fetch -> checkout <branch> -> reset --hard origin/<branch>."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/feature-x"]:
                return "abc123"
            if args == ["git", "rev-parse", "--short", "HEAD"]:
                return "def4567"
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        checkout_calls = []
        reset_calls = []

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "feature-x"]:
                checkout_calls.append(cmd)
                return _fake_completed()
            if cmd == ["git", "reset", "--hard", "origin/feature-x"]:
                reset_calls.append(cmd)
                return _fake_completed()
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart", json={"branch": "feature-x"})
            assert resp.status_code == 202
            body = resp.json()
            assert "Switched to feature-x @ def4567" in body["pull_output"]
            assert checkout_calls == [["git", "checkout", "feature-x"]]
            assert reset_calls == [["git", "reset", "--hard", "origin/feature-x"]]
        finally:
            for p in patches:
                p.stop()

    async def test_restart_custom_remote_only_branch_creates_tracking_branch(self, api_integration_env):
        """Remote-only branch: checkout -b <branch> --track origin/<branch>."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/remote-only"]:
                return ""
            if args == ["git", "rev-parse", "--short", "HEAD"]:
                return "aaa1111"
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "-b", "remote-only", "--track", "origin/remote-only"]:
                return _fake_completed()
            if cmd == ["git", "reset", "--hard", "origin/remote-only"]:
                return _fake_completed()
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post("/api/system/restart", json={"branch": "remote-only"})
            assert resp.status_code == 202
        finally:
            for p in patches:
                p.stop()

    async def test_restart_custom_commit_only_resets_to_exact_commit(self, api_integration_env):
        """Only a commit is given: branch stays current, reset target is the commit hash."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/main"]:
                return "abc123"
            if args == ["git", "rev-parse", "--short", "HEAD"]:
                return "cafe123"
            raise AssertionError(f"Unexpected _run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "main"]:
                return _fake_completed()
            if cmd == ["git", "reset", "--hard", "0123456789abcdef0123456789abcdef01234567"]:
                return _fake_completed()
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        patches = self._patches(webui, run_git_command_side_effect, run_side_effect)
        for p in patches:
            p.start()
        try:
            resp = await client.post(
                "/api/system/restart",
                json={"commit": "0123456789abcdef0123456789abcdef01234567"},
            )
            assert resp.status_code == 202
            assert "Switched to main @ cafe123" in resp.json()["pull_output"]
        finally:
            for p in patches:
                p.stop()


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
        assert "templates" in resp.json()
        assert isinstance(resp.json()["templates"], list)

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
