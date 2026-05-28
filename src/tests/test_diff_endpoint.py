"""Regression tests for issue #1595 — untracked directory handling in the diff endpoint."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

SESSION_ID = "test-session-1595"


def _git(path: Path, *cmd: str) -> None:
    subprocess.run(cmd, cwd=path, check=True, capture_output=True)


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with an initial commit so HEAD exists."""
    _git(path, "git", "init")
    _git(path, "git", "config", "user.email", "test@test.com")
    _git(path, "git", "config", "user.name", "Test")
    (path / "README.md").write_text("# Test\n")
    _git(path, "git", "add", ".")
    _git(path, "git", "commit", "-m", "Initial commit")


def _session_ctx(working_directory: str) -> dict:
    return {"exists": True, "working_directory": working_directory}


@pytest.mark.asyncio
async def test_untracked_directory_expanded_to_files(tmp_path):
    """Untracked dir with 2 files — response files contains both paths, not the dir."""
    from ..web_server import ClaudeWebUI

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "newmodule").mkdir()
    (repo / "newmodule" / "a.py").write_text("x = 1\n")
    (repo / "newmodule" / "b.py").write_text("y = 2\n")

    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    ctx = _session_ctx(str(repo))

    with patch.object(webui.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            response = await client.get(f"/api/sessions/{SESSION_ID}/diff")

    assert response.status_code == 200
    data = response.json()
    assert data["is_git_repo"] is True

    files = data["files"]
    assert "newmodule/" not in files, "Directory path must not appear in files"
    assert "newmodule/a.py" in files
    assert "newmodule/b.py" in files
    assert files["newmodule/a.py"]["insertions"] == 1
    assert files["newmodule/b.py"]["insertions"] == 1


@pytest.mark.asyncio
async def test_untracked_nested_directories(tmp_path):
    """3-level untracked dir — response contains full file path with correct stats."""
    from ..web_server import ClaudeWebUI

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "a" / "b" / "c").mkdir(parents=True)
    (repo / "a" / "b" / "c" / "file.txt").write_text("hello\nworld\n")

    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    ctx = _session_ctx(str(repo))

    with patch.object(webui.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            response = await client.get(f"/api/sessions/{SESSION_ID}/diff")

    assert response.status_code == 200
    files = response.json()["files"]
    assert "a/b/c/file.txt" in files
    assert files["a/b/c/file.txt"]["insertions"] == 2


@pytest.mark.asyncio
async def test_untracked_dir_in_wip_commit_files(tmp_path):
    """WIP commit files list contains individual file paths, not the directory path."""
    from ..web_server import ClaudeWebUI

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "module").mkdir()
    (repo / "module" / "x.py").write_text("a = 1\n")
    (repo / "module" / "y.py").write_text("b = 2\n")

    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    ctx = _session_ctx(str(repo))

    with patch.object(webui.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            response = await client.get(f"/api/sessions/{SESSION_ID}/diff")

    assert response.status_code == 200
    commits = response.json()["commits"]
    wip = next((c for c in commits if c.get("is_uncommitted")), None)
    assert wip is not None, "WIP commit must be present"
    assert "module/" not in wip["files"], "Directory path must not appear in WIP commit files"
    assert "module/x.py" in wip["files"]
    assert "module/y.py" in wip["files"]


@pytest.mark.asyncio
async def test_tracked_changes_unaffected(tmp_path):
    """Tracked-file diff stats are correct even when an untracked directory is also present."""
    from ..web_server import ClaudeWebUI

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "README.md").write_text("# Test\nNew line added\n")
    (repo / "extras").mkdir()
    (repo / "extras" / "data.txt").write_text("extra content\n")

    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    ctx = _session_ctx(str(repo))

    with patch.object(webui.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            response = await client.get(f"/api/sessions/{SESSION_ID}/diff")

    assert response.status_code == 200
    data = response.json()
    files = data["files"]
    assert "extras/" not in files
    assert "extras/data.txt" in files
    assert files["extras/data.txt"]["status"] == "added"


@pytest.mark.asyncio
async def test_directory_path_rejected_by_file_endpoint(tmp_path):
    """File diff endpoint returns 400 when path resolves to a directory."""
    from ..web_server import ClaudeWebUI

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "somedir").mkdir()
    (repo / "somedir" / "file.txt").write_text("content\n")

    webui = ClaudeWebUI(data_dir=tmp_path / "data")
    ctx = _session_ctx(str(repo))

    with patch.object(webui.service, "get_session_diff_context", new_callable=AsyncMock, return_value=ctx):
        async with AsyncClient(transport=ASGITransport(app=webui.app), base_url="http://test") as client:
            response = await client.get(
                f"/api/sessions/{SESSION_ID}/diff/file",
                params={"path": "somedir"}
            )

    assert response.status_code == 400
