"""
Tests for src/docker_utils.py — shared Docker /tmp helpers (issue #832).
"""

import asyncio
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.docker_utils import (
    build_embedded_sockets,
    cleanup_session_tmp,
    detect_docker_bridge_gateway,
    get_session_tmp_dir,
    resolve_docker_cli_path,
    translate_docker_tmp_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(session_id: str, docker_enabled: bool, data_dir: Path):
    """Build a minimal mock SessionCoordinator for path-translation tests."""
    session_info = MagicMock()
    session_info.config = {"docker_enabled": docker_enabled}

    session_manager = MagicMock()
    session_manager.get_session_info = AsyncMock(return_value=session_info)

    coordinator = MagicMock()
    coordinator.session_manager = session_manager
    coordinator.data_dir = data_dir
    return coordinator


# ---------------------------------------------------------------------------
# translate_docker_tmp_path
# ---------------------------------------------------------------------------


class TestTranslateDockerTmpPath:
    @pytest.mark.asyncio
    async def test_non_tmp_path_returned_unchanged(self, tmp_path):
        coord = _make_coordinator("sess-1", docker_enabled=True, data_dir=tmp_path)
        result = await translate_docker_tmp_path("/home/user/file.txt", "sess-1", coord)
        assert result == "/home/user/file.txt"
        # get_session_info should NOT be called for non-/tmp paths
        coord.session_manager.get_session_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_docker_enabled_translates_path(self, tmp_path):
        coord = _make_coordinator("sess-1", docker_enabled=True, data_dir=tmp_path)
        result = await translate_docker_tmp_path("/tmp/output.json", "sess-1", coord)
        expected = str(tmp_path / "sessions" / "sess-1" / "tmp" / "output.json")
        assert result == expected

    @pytest.mark.asyncio
    async def test_docker_disabled_no_translation(self, tmp_path):
        coord = _make_coordinator("sess-1", docker_enabled=False, data_dir=tmp_path)
        result = await translate_docker_tmp_path("/tmp/output.json", "sess-1", coord)
        assert result == "/tmp/output.json"

    @pytest.mark.asyncio
    async def test_session_info_none_no_translation(self, tmp_path):
        session_manager = MagicMock()
        session_manager.get_session_info = AsyncMock(return_value=None)
        coord = MagicMock()
        coord.session_manager = session_manager
        coord.data_dir = tmp_path
        result = await translate_docker_tmp_path("/tmp/foo.txt", "sess-x", coord)
        assert result == "/tmp/foo.txt"

    @pytest.mark.asyncio
    async def test_exception_in_get_session_info_returns_original(self, tmp_path):
        session_manager = MagicMock()
        session_manager.get_session_info = AsyncMock(side_effect=RuntimeError("boom"))
        coord = MagicMock()
        coord.session_manager = session_manager
        coord.data_dir = tmp_path
        result = await translate_docker_tmp_path("/tmp/bar.txt", "sess-y", coord)
        assert result == "/tmp/bar.txt"

    @pytest.mark.asyncio
    async def test_nested_path_preserved(self, tmp_path):
        coord = _make_coordinator("sess-2", docker_enabled=True, data_dir=tmp_path)
        result = await translate_docker_tmp_path("/tmp/subdir/deep/file.png", "sess-2", coord)
        expected = str(tmp_path / "sessions" / "sess-2" / "tmp" / "subdir" / "deep" / "file.png")
        assert result == expected


# ---------------------------------------------------------------------------
# get_session_tmp_dir
# ---------------------------------------------------------------------------


class TestGetSessionTmpDir:
    def test_returns_tmp_subdir(self, tmp_path):
        result = get_session_tmp_dir(tmp_path / "sessions" / "abc")
        assert result == tmp_path / "sessions" / "abc" / "tmp"

    def test_return_type_is_path(self, tmp_path):
        result = get_session_tmp_dir(tmp_path)
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# cleanup_session_tmp
# ---------------------------------------------------------------------------


class TestCleanupSessionTmp:
    def test_existing_dir_is_removed(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        tmp_dir = sessions_dir / "sess-1" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "file.txt").write_text("hello")

        cleanup_session_tmp("sess-1", sessions_dir)
        assert not tmp_dir.exists()

    def test_missing_dir_is_noop(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Should not raise
        cleanup_session_tmp("sess-missing", sessions_dir)

    def test_exception_is_logged_not_raised(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        tmp_dir = sessions_dir / "sess-err" / "tmp"
        tmp_dir.mkdir(parents=True)

        with patch("backend.docker_utils.shutil.rmtree", side_effect=OSError("disk error")):
            # Should not propagate the exception
            cleanup_session_tmp("sess-err", sessions_dir)


# ---------------------------------------------------------------------------
# resolve_docker_cli_path — proxy mode (issue #1049)
# ---------------------------------------------------------------------------


class TestResolveDockerCliPathProxy:
    def test_proxy_mode_basic(self):
        """CLAUDE_DOCKER_PROXY_IMAGE is set when proxy_image is provided."""
        _, env = resolve_docker_cli_path(proxy_image="claude-proxy:local")
        assert env["CLAUDE_DOCKER_PROXY_IMAGE"] == "claude-proxy:local"

    def test_no_proxy_no_env_vars(self):
        """No proxy env vars are set when proxy_image is None (regression guard)."""
        _, env = resolve_docker_cli_path(docker_image="my-image")
        assert "CLAUDE_DOCKER_PROXY_IMAGE" not in env
        assert env == {"CLAUDE_DOCKER_IMAGE": "my-image"}


# ---------------------------------------------------------------------------
# detect_docker_bridge_gateway / build_embedded_sockets (issue #1850)
# ---------------------------------------------------------------------------


class TestDetectDockerBridgeGateway:
    @pytest.mark.asyncio
    async def test_returns_gateway_on_success(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"172.17.0.1\n", b""))
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await detect_docker_bridge_gateway()
        assert result == "172.17.0.1"

    @pytest.mark.asyncio
    async def test_returns_none_when_docker_cli_missing(self):
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)):
            result = await detect_docker_bridge_gateway()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_nonzero_returncode(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"no such network"))
        proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await detect_docker_bridge_gateway()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_gateway(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"\n", b""))
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await detect_docker_bridge_gateway()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"172.17.0.1\n", b""))
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError)):
            result = await detect_docker_bridge_gateway()
        assert result is None

    @pytest.mark.asyncio
    async def test_kills_leftover_process_when_it_hangs_past_the_timeout(self):
        """Regression test: reproduced live on WSL2 + Docker Desktop, where `docker
        network inspect` itself hung — the deadline must cover the whole operation
        (not just reading output), and the hung process must not be left running."""
        async def _hang(*_args, **_kwargs):
            await asyncio.sleep(100)

        proc = MagicMock()
        proc.returncode = None  # still running when the timeout fires
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        proc.communicate = AsyncMock(side_effect=_hang)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await detect_docker_bridge_gateway(timeout=0.05)

        assert result is None
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


class TestBuildEmbeddedSockets:
    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_binds_loopback_only_when_no_gateway(self):
        port = self._free_port()
        sockets = build_embedded_sockets(port, None)
        try:
            assert len(sockets) == 1
            assert sockets[0].getsockname() == ("127.0.0.1", port)
        finally:
            for s in sockets:
                s.close()

    def test_binds_gateway_address_when_reachable(self):
        # 127.0.0.2 is loopback-range and bindable on Linux without any Docker
        # present — stands in for "the gateway address happens to be a real,
        # bindable interface on this host" without depending on Docker actually
        # being installed in the test environment.
        port = self._free_port()
        sockets = build_embedded_sockets(port, "127.0.0.2")
        try:
            addresses = {s.getsockname()[0] for s in sockets}
            assert addresses == {"127.0.0.1", "127.0.0.2"}
        finally:
            for s in sockets:
                s.close()

    def test_skips_unbindable_gateway_address_without_raising(self):
        # Simulates Docker Desktop/Rancher, where the bridge lives inside a VM, not
        # the actual host — binding the "gateway" address fails there (issue #1850's
        # Desktop-vs-Engine distinction). Force that deterministically (rather than
        # relying on some real IP being unassigned on the test host) by patching
        # socket.socket.bind to raise only for the gateway address under test.
        port = self._free_port()
        original_bind = socket.socket.bind

        def fake_bind(self, address):
            if address[0] == "203.0.113.5":  # TEST-NET-3 (RFC 5737), never a real interface
                raise OSError("Cannot assign requested address")
            return original_bind(self, address)

        with patch.object(socket.socket, "bind", fake_bind):
            sockets = build_embedded_sockets(port, "203.0.113.5")
        try:
            addresses = {s.getsockname()[0] for s in sockets}
            assert addresses == {"127.0.0.1"}
        finally:
            for s in sockets:
                s.close()

    def test_gateway_equal_to_loopback_not_duplicated(self):
        port = self._free_port()
        sockets = build_embedded_sockets(port, "127.0.0.1")
        try:
            assert len(sockets) == 1
        finally:
            for s in sockets:
                s.close()
