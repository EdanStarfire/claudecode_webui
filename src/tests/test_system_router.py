"""Tests for src/routers/system.py's /api/system/restart (issue #498) and the
Frontend-side git-status/branches/commits endpoints (issue #1847).

Regression coverage for the bug found in manual testing: POST /api/system/restart
lived only in backend/routers/system.py after the split (a straight relocation of
the pre-#498 unified router), so the browser's "Restart Server" button restarted
Backend only via os.execv — Frontend kept running its old process image forever,
silently never applying pulled changes to src/, main.py, or shared/. Frontend now
intercepts this one route itself instead of falling through to the generic relay.

The git-status/branches/commits endpoints (issue #1847) close a related gap: those
routes had no Frontend-side handler at all and fell through to the generic relay,
so they always described Backend's repo, never Frontend's own — in embedded mode
this happened to look correct only because it's the same checkout.
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.routers.system import _finish_restart, build_router


def _make_webui(backend_supervisor=None):
    webui = MagicMock()
    webui.app = FastAPI()
    webui.app.include_router(build_router(webui))
    webui._last_restart_time = 0
    webui._oauth_resync_task = None
    webui.backend_supervisor = backend_supervisor
    webui.poll_relay.stop = AsyncMock()
    webui.backend_client.aclose = AsyncMock()
    # Remote-mode Backend orchestration defaults (#1847) — sane "everything's fine"
    # mocks so pre-existing tests exercising remote mode (backend_supervisor=None)
    # without caring about Backend orchestration don't need to know about it.
    webui.backend_client.health = AsyncMock(return_value=True)
    webui.backend_client.ready = AsyncMock(return_value=True)
    webui.backend_client.request_json = AsyncMock(return_value={"status": "restarting"})
    webui.ui_queue.append = MagicMock()
    return webui


def _fake_completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRestartDefaultPath:
    @pytest.mark.asyncio
    async def test_default_pull_succeeds_and_broadcasts(self):
        webui = _make_webui()

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "pull"]:
                return _fake_completed(stdout="Already up to date.\n")
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with patch("src.routers.system.subprocess.run", side_effect=run_side_effect):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 202
        body = resp.json()
        assert body["pull_output"] == "Already up to date."
        assert body["sync_output"] == "Synced"
        webui.ui_queue.append.assert_called_once()
        assert webui.ui_queue.append.call_args[0][0]["type"] == "server_restarting"

    @pytest.mark.asyncio
    async def test_git_pull_failure_returns_500(self):
        webui = _make_webui()

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "pull"]:
                return _fake_completed(returncode=1, stderr="fatal: conflict")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with patch("src.routers.system.subprocess.run", side_effect=run_side_effect):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_rate_limited_on_rapid_repeat_call(self):
        webui = _make_webui()

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "pull"]:
                return _fake_completed(stdout="Already up to date.\n")
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with patch("src.routers.system.subprocess.run", side_effect=run_side_effect):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                first = await client.post("/api/system/restart")
                second = await client.post("/api/system/restart")

        assert first.status_code == 202
        assert second.status_code == 429


class TestRestartCustomTarget:
    @pytest.mark.asyncio
    async def test_rejects_flag_like_branch(self):
        webui = _make_webui()

        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            resp = await client.post("/api/system/restart", json={"branch": "--upload-pack=/bin/sh"})

        assert resp.status_code == 400
        # Malformed Frontend target must be rejected before Backend is touched at
        # all (issue #1847 review finding) — not just before Frontend's own git ops.
        webui.backend_client.health.assert_not_awaited()
        webui.backend_client.request_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uncommitted_changes_returns_409(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return " M some_file.py"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        with patch("src.routers.system.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart", json={"branch": "other-branch"})

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_switch_branch_succeeds(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/feature-x"]:
                return "abc123"
            if args == ["git", "rev-parse", "--short", "HEAD"]:
                return "def4567"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "feature-x"]:
                return _fake_completed()
            if cmd == ["git", "reset", "--hard", "origin/feature-x"]:
                return _fake_completed()
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with (
            patch("src.routers.system.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)),
            patch("src.routers.system.subprocess.run", side_effect=run_side_effect),
            patch("src.routers.system.asyncio.create_subprocess_exec", AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart", json={"branch": "feature-x"})

        assert resp.status_code == 202
        assert "Switched to feature-x @ def4567" in resp.json()["pull_output"]


class TestFinishRestart:
    """Regression coverage for the actual bug: Frontend must restart itself, and
    must only touch Backend when it owns Backend's lifecycle (embedded mode)."""

    @pytest.mark.asyncio
    async def test_embedded_mode_stops_backend_before_reexec(self):
        webui = _make_webui(backend_supervisor=MagicMock(stop=AsyncMock()))

        with (
            patch("src.routers.system.asyncio.sleep", AsyncMock()),
            patch("src.routers.system.os.execv") as mock_execv,
        ):
            await _finish_restart(webui)

        webui.backend_supervisor.stop.assert_awaited_once()
        webui.poll_relay.stop.assert_awaited_once()
        webui.backend_client.aclose.assert_awaited_once()
        mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_remote_mode_never_touches_backend(self):
        """Remote mode (no backend_supervisor): Frontend restarts itself only —
        a remote Backend is a separate deployment this action must not manage."""
        webui = _make_webui(backend_supervisor=None)

        with (
            patch("src.routers.system.asyncio.sleep", AsyncMock()),
            patch("src.routers.system.os.execv") as mock_execv,
        ):
            await _finish_restart(webui)

        webui.poll_relay.stop.assert_awaited_once()
        webui.backend_client.aclose.assert_awaited_once()
        mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_backend_stop_failure_does_not_block_frontend_reexec(self):
        """A failure stopping the old Backend must not prevent Frontend's own
        restart — an orphaned old Backend is a lesser problem than Frontend
        refusing to apply newly-pulled code at all."""
        supervisor = MagicMock(stop=AsyncMock(side_effect=RuntimeError("boom")))
        webui = _make_webui(backend_supervisor=supervisor)

        with (
            patch("src.routers.system.asyncio.sleep", AsyncMock()),
            patch("src.routers.system.os.execv") as mock_execv,
        ):
            await _finish_restart(webui)

        mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_reexec_preserves_original_argv(self):
        webui = _make_webui()

        with (
            patch("src.routers.system.asyncio.sleep", AsyncMock()),
            patch("src.routers.system.os.execv") as mock_execv,
            patch("src.routers.system.sys.argv", ["main.py", "--port", "8000"]),
            patch("src.routers.system.sys.executable", "/usr/bin/python3"),
        ):
            await _finish_restart(webui)

        mock_execv.assert_called_once_with(
            "/usr/bin/python3", ["/usr/bin/python3", "main.py", "--port", "8000"]
        )


def _http_status_error(status_code, detail):
    request = httpx.Request("POST", "http://backend/api/system/restart")
    response = httpx.Response(status_code, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(str(status_code), request=request, response=response)


def _default_run_side_effect(cmd, **kwargs):
    if cmd == ["git", "pull"]:
        return _fake_completed(stdout="Already up to date.\n")
    if cmd == ["uv", "sync"]:
        return _fake_completed(stdout="Synced\n")
    raise AssertionError(f"Unexpected subprocess.run call: {cmd}")


def _no_subprocess_expected(cmd, **kwargs):
    raise AssertionError(f"Frontend must not touch its own git state: {cmd}")


class TestRestartRemoteBackendOrchestration:
    """Remote mode (webui.backend_supervisor is None) Backend-orchestration step (#1847)."""

    @pytest.mark.asyncio
    async def test_no_explicit_targets_both_tiers_restart(self):
        webui = _make_webui(backend_supervisor=None)
        # health/ready/request_json defaults ("everything's fine") come from _make_webui.

        with (
            patch("src.routers.system._BACKEND_RESTART_GRACE_SECONDS", 0),
            patch("src.routers.system.subprocess.run", side_effect=_default_run_side_effect),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 202
        body = resp.json()
        assert body["backend"] == {"status": "restarted", "detail": "Backend restarted and is ready."}
        assert body["pull_output"] == "Already up to date."
        webui.backend_client.request_json.assert_awaited_once_with(
            "POST", "/api/system/restart", json={"branch": None, "commit": None}, timeout=210.0,
        )

    @pytest.mark.asyncio
    async def test_independent_per_tier_targets(self):
        webui = _make_webui(backend_supervisor=None)
        # health/ready/request_json defaults ("everything's fine") come from _make_webui.

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/frontend-branch"]:
                return "abc123"
            if args == ["git", "rev-parse", "--short", "HEAD"]:
                return "def4567"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        def run_side_effect(cmd, **kwargs):
            if cmd == ["git", "checkout", "frontend-branch"]:
                return _fake_completed()
            if cmd == ["git", "reset", "--hard", "origin/frontend-branch"]:
                return _fake_completed()
            if cmd == ["uv", "sync"]:
                return _fake_completed(stdout="Synced\n")
            raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

        with (
            patch("src.routers.system.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)),
            patch("src.routers.system.subprocess.run", side_effect=run_side_effect),
            patch("src.routers.system.asyncio.create_subprocess_exec", AsyncMock()),
            patch("src.routers.system._BACKEND_RESTART_GRACE_SECONDS", 0),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/system/restart",
                    json={"branch": "frontend-branch", "backend_branch": "backend-branch"},
                )

        assert resp.status_code == 202
        assert "Switched to frontend-branch" in resp.json()["pull_output"]
        webui.backend_client.request_json.assert_awaited_once_with(
            "POST", "/api/system/restart", json={"branch": "backend-branch", "commit": None}, timeout=210.0,
        )

    @pytest.mark.asyncio
    async def test_backend_rejects_target_synchronously(self):
        webui = _make_webui(backend_supervisor=None)
        webui.backend_client.health = AsyncMock(return_value=True)
        webui.backend_client.request_json = AsyncMock(
            side_effect=_http_status_error(409, "Uncommitted changes present.")
        )

        with patch("src.routers.system.subprocess.run", side_effect=_no_subprocess_expected):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 502
        assert "Uncommitted changes present." in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_backend_already_unreachable_fails_fast(self):
        webui = _make_webui(backend_supervisor=None)
        webui.backend_client.health = AsyncMock(return_value=False)
        webui.backend_client.request_json = AsyncMock()

        with patch("src.routers.system.subprocess.run", side_effect=_no_subprocess_expected):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 502
        assert "already unreachable" in resp.json()["detail"]
        webui.backend_client.request_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_backend_never_becomes_healthy_times_out(self):
        webui = _make_webui(backend_supervisor=None)

        # health() is called once for the initial "already unreachable?" fail-fast
        # check (must be True/reachable here) and then repeatedly inside the poll
        # loop (must stay False forever to exercise the timeout path).
        calls = {"n": 0}

        async def health_side_effect():
            calls["n"] += 1
            return calls["n"] == 1

        webui.backend_client.health = AsyncMock(side_effect=health_side_effect)
        webui.backend_client.request_json = AsyncMock(return_value={"status": "restarting"})

        with (
            patch("src.routers.system._BACKEND_RESTART_TIMEOUT", 0.2),
            patch("src.routers.system._BACKEND_RESTART_POLL_INTERVAL", 0.05),
            patch("src.routers.system._BACKEND_RESTART_GRACE_SECONDS", 0),
            patch("src.routers.system.subprocess.run", side_effect=_no_subprocess_expected),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 502
        assert "did not become healthy" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_health_and_ready_each_get_independent_timeout_budget(self):
        """Regression guard (#1847 review finding): health and ready must each get
        their OWN full timeout budget, not one shared deadline split between them —
        mirrors the bug BackendSupervisor.wait_ready() already fixed for startup."""
        webui = _make_webui(backend_supervisor=None)
        # First health() call is the initial "already unreachable?" fail-fast check
        # (True/reachable); the next 4 are the health-poll phase, taking ~4 polls
        # (~0.2s) to succeed — enough to exhaust a *shared* 0.3s deadline before
        # ready() ever got a turn under the old (buggy) design.
        webui.backend_client.health = AsyncMock(side_effect=[True, False, False, False, True])
        webui.backend_client.ready = AsyncMock(side_effect=[False, False, True])
        webui.backend_client.request_json = AsyncMock(return_value={"status": "restarting"})

        with (
            patch("src.routers.system._BACKEND_RESTART_TIMEOUT", 0.3),
            patch("src.routers.system._BACKEND_RESTART_POLL_INTERVAL", 0.05),
            patch("src.routers.system._BACKEND_RESTART_GRACE_SECONDS", 0),
            patch("src.routers.system.subprocess.run", side_effect=_default_run_side_effect),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 202
        assert resp.json()["backend"] == {"status": "restarted", "detail": "Backend restarted and is ready."}

    @pytest.mark.asyncio
    async def test_embedded_mode_regression_unaffected(self):
        """Regression: embedded mode must not execute any of the new remote-mode
        orchestration code — no Backend health/ready/request_json calls at all."""
        webui = _make_webui(backend_supervisor=MagicMock(stop=AsyncMock()))
        webui.backend_client.health = AsyncMock()
        webui.backend_client.ready = AsyncMock()
        webui.backend_client.request_json = AsyncMock()

        with patch("src.routers.system.subprocess.run", side_effect=_default_run_side_effect):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.post("/api/system/restart")

        assert resp.status_code == 202
        assert resp.json()["backend"] is None
        webui.backend_client.health.assert_not_awaited()
        webui.backend_client.ready.assert_not_awaited()
        webui.backend_client.request_json.assert_not_awaited()


def _fake_async_proc(returncode=0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    return proc


class TestFrontendGitStatusEndpoints:
    """Frontend's own git-status/branches/commits (issue #1847) — distinct from the
    relayed /api/system/git-status etc., which describe Backend's repo instead."""

    @pytest.mark.asyncio
    async def test_frontend_git_status_shape(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "log", "-1", "--format=%H"]:
                return "abc123fullhash"
            if args == ["git", "log", "-1", "--format=%s"]:
                return "Local commit subject"
            if args == ["git", "status", "--porcelain"]:
                return ""
            if args == ["git", "rev-parse", "--verify", "origin/main"]:
                return "def456"
            if args == ["git", "log", "-1", "--format=%H", "origin/main"]:
                return "def456fullhash"
            if args == ["git", "log", "-1", "--format=%s", "origin/main"]:
                return "Remote commit subject"
            if args == ["git", "rev-list", "--count", "HEAD..origin/main"]:
                return "2"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        with (
            patch("shared.git_restart.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)),
            patch(
                "shared.git_restart.asyncio.create_subprocess_exec",
                AsyncMock(return_value=_fake_async_proc()),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.get("/api/system/frontend-git-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["branch"] == "main"
        assert body["last_commit_hash"] == "abc123fullhash"
        assert body["has_uncommitted_changes"] is False
        assert body["remote_commit_hash"] == "def456fullhash"
        assert body["commits_behind"] == 2
        assert body["remote_fetch_failed"] is False

    @pytest.mark.asyncio
    async def test_frontend_git_branches_shape(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return "main"
            if args == ["git", "for-each-ref", "refs/heads", "--format=%(refname:short)"]:
                return "main\nfeature-x"
            if args == ["git", "for-each-ref", "refs/remotes/origin", "--format=%(refname:short)"]:
                return "origin/HEAD\norigin/main\norigin/remote-only"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        with (
            patch("shared.git_restart.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)),
            patch("shared.git_restart.asyncio.create_subprocess_exec", AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.get("/api/system/frontend-git-branches")

        assert resp.status_code == 200
        body = resp.json()
        names = {b["name"] for b in body["branches"]}
        assert names == {"main", "feature-x", "remote-only"}
        current = next(b for b in body["branches"] if b["name"] == "main")
        assert current["is_current"] is True
        assert current["is_local"] is True
        remote_only = next(b for b in body["branches"] if b["name"] == "remote-only")
        assert remote_only["is_remote_only"] is True
        assert remote_only["is_local"] is False

    @pytest.mark.asyncio
    async def test_frontend_git_commits_shape(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args == ["git", "rev-parse", "--verify", "--quiet", "refs/heads/main"]:
                return "abc123"
            if args[:2] == ["git", "log"]:
                return "hash1\x1fh1\x1fSubject one\x1fauthor\x1f2026-01-01T00:00:00+00:00"
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        with patch("shared.git_restart.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.get("/api/system/frontend-git-commits", params={"branch": "main"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["branch"] == "main"
        assert len(body["commits"]) == 1
        assert body["commits"][0]["short_hash"] == "h1"
        assert body["truncated"] is False

    @pytest.mark.asyncio
    async def test_frontend_git_commits_missing_branch_param(self):
        webui = _make_webui()

        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            resp = await client.get("/api/system/frontend-git-commits")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_frontend_git_commits_unknown_branch(self):
        webui = _make_webui()

        async def run_git_command_side_effect(args, cwd, allow_nonzero=False):
            if args[0:4] == ["git", "rev-parse", "--verify", "--quiet"]:
                return None
            raise AssertionError(f"Unexpected run_git_command call: {args}")

        with patch("shared.git_restart.run_git_command", AsyncMock(side_effect=run_git_command_side_effect)):
            async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
                resp = await client.get(
                    "/api/system/frontend-git-commits", params={"branch": "definitely-not-a-real-branch"}
                )

        assert resp.status_code == 404


class TestFrontendModeEndpoint:
    """Authoritative embedded-vs-remote signal (issue #1847 review finding): reflects live
    wiring (webui.backend_supervisor), not config.json alone — CLI-only --remote-backend-url
    never persists to config.json, so a config-based check would misreport such a deployment."""

    @pytest.mark.asyncio
    async def test_embedded_mode_reports_false(self):
        webui = _make_webui(backend_supervisor=MagicMock())

        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            resp = await client.get("/api/system/frontend-mode")

        assert resp.status_code == 200
        assert resp.json() == {"remote_mode": False}

    @pytest.mark.asyncio
    async def test_remote_mode_reports_true(self):
        webui = _make_webui(backend_supervisor=None)

        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            resp = await client.get("/api/system/frontend-mode")

        assert resp.status_code == 200
        assert resp.json() == {"remote_mode": True}
