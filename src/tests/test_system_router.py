"""Tests for src/routers/system.py's /api/system/restart (issue #498).

Regression coverage for the bug found in manual testing: POST /api/system/restart
lived only in backend/routers/system.py after the split (a straight relocation of
the pre-#498 unified router), so the browser's "Restart Server" button restarted
Backend only via os.execv — Frontend kept running its old process image forever,
silently never applying pulled changes to src/, main.py, or shared/. Frontend now
intercepts this one route itself instead of falling through to the generic relay.
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

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
