"""
Tests for src/docker_utils.py — shared Docker /tmp helpers (issue #832).
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.docker_utils import (
    cleanup_session_tmp,
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
    session_info.docker_enabled = docker_enabled

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

        with patch("src.docker_utils.shutil.rmtree", side_effect=OSError("disk error")):
            # Should not propagate the exception
            cleanup_session_tmp("sess-err", sessions_dir)


# ---------------------------------------------------------------------------
# resolve_docker_cli_path — proxy mode (issue #1049)
# ---------------------------------------------------------------------------


class TestResolveDockerCliPathProxy:
    def test_proxy_mode_basic(self):
        """CLAUDE_DOCKER_PROXY_CONTAINER is set when proxy_container is provided."""
        _, env = resolve_docker_cli_path(proxy_container="claude-proxy")
        assert env["CLAUDE_DOCKER_PROXY_CONTAINER"] == "claude-proxy"
        assert "CLAUDE_DOCKER_PROXY_CA_CERT" not in env

    def test_proxy_mode_all_params(self):
        """Both proxy env vars are set when proxy_container and proxy_ca_cert provided."""
        _, env = resolve_docker_cli_path(
            proxy_container="claude-proxy",
            proxy_ca_cert="/certs/ca.pem",
        )
        assert env["CLAUDE_DOCKER_PROXY_CONTAINER"] == "claude-proxy"
        assert env["CLAUDE_DOCKER_PROXY_CA_CERT"] == "/certs/ca.pem"

    def test_no_proxy_no_env_vars(self):
        """No proxy env vars are set when proxy_container is None (regression guard)."""
        _, env = resolve_docker_cli_path(docker_image="my-image")
        assert "CLAUDE_DOCKER_PROXY_CONTAINER" not in env
        assert "CLAUDE_DOCKER_PROXY_CA_CERT" not in env
        assert env == {"CLAUDE_DOCKER_IMAGE": "my-image"}
