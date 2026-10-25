"""Issue #2279 AC1 (T1): API payloads must not leak the session working directory.

Scans every string value of the diff and edit-history responses for the host
working-directory prefix. Fields that legitimately stay absolute are the documented
display fields in `backend.path_display.DISPLAY_PATH_FIELDS`.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.application_service import _run_git_command_impl
from backend.path_display import DISPLAY_PATH_FIELDS, relativize_to
from backend.routers import diff, edit_history


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


@pytest.fixture
def env(tmp_path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    _git(workdir, "init", "-q")
    _git(workdir, "config", "user.email", "t@example.com")
    _git(workdir, "config", "user.name", "t")
    (workdir / "a.py").write_text("one\n")
    _git(workdir, "add", ".")
    _git(workdir, "commit", "-qm", "init")
    (workdir / "a.py").write_text("two\n")
    (workdir / "new.txt").write_text("x\n")
    outside = tmp_path / "elsewhere" / "z.py"

    messages = tmp_path / "messages.jsonl"
    tool_uses = [
        {"id": "e1", "name": "Edit",
         "input": {"file_path": str(workdir / "a.py"), "old_string": "one", "new_string": "two"}},
        {"id": "w1", "name": "Write",
         "input": {"file_path": str(workdir / "sub" / "n.py"), "content": "hi\n"}},
        {"id": "e2", "name": "Edit",
         "input": {"file_path": str(outside), "old_string": "a", "new_string": "b"}},
    ]
    messages.write_text(
        json.dumps({"type": "assistant", "timestamp": 1.0, "metadata": {"tool_uses": tool_uses}})
        + "\n"
    )

    webui = MagicMock()
    webui.service.get_session_diff_context = AsyncMock(
        return_value={"exists": True, "working_directory": str(workdir)}
    )
    webui.service.get_session_messages_path = AsyncMock(return_value=str(messages))
    webui.service.run_git_command = _run_git_command_impl
    app = FastAPI()
    app.include_router(diff.build_router(webui))
    app.include_router(edit_history.build_router(webui))
    return app, workdir, outside


async def _get(app, url, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(url, params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_edit_history_has_no_workdir_prefix_for_inside_paths(env):
    app, workdir, outside = env
    data = await _get(app, "/api/sessions/s1/edit-history")
    by_id = {e["tool_use_id"]: e for e in data["entries"]}

    assert by_id["e1"]["file_path"] == "a.py"
    assert by_id["e1"]["input"]["file_path"] == "a.py"
    assert by_id["e1"]["input"]["old_string"] == "one"
    assert by_id["e1"]["input"]["new_string"] == "two"
    assert "file_path_outside_workdir" not in by_id["e1"]
    assert by_id["w1"]["file_path"] == "sub/n.py"
    assert by_id["w1"]["input"]["content"] == "hi\n"

    assert by_id["e2"]["file_path"] == str(outside)
    assert by_id["e2"]["input"]["file_path"] == str(outside)
    assert by_id["e2"]["file_path_outside_workdir"] is True

    leaked = [s for s in _strings(data) if str(workdir) in s]
    assert not leaked, leaked


@pytest.mark.asyncio
async def test_diff_payloads_have_no_workdir_prefix(env):
    app, workdir, _ = env
    summary = await _get(app, "/api/sessions/s1/diff")
    assert summary["is_git_repo"] is True
    file_diff = await _get(app, "/api/sessions/s1/diff/file", path="a.py")
    untracked = await _get(app, "/api/sessions/s1/diff/file", path="new.txt")
    for payload in (summary, file_diff, untracked):
        leaked = [s for s in _strings(payload) if str(workdir) in s]
        assert not leaked, leaked


def test_display_fields_registry_documents_reasons():
    assert {"session.working_directory", "filesystem.browse"} <= set(DISPLAY_PATH_FIELDS)
    assert all(r.strip() for r in DISPLAY_PATH_FIELDS.values())


class TestRelativizeTo:
    def test_inside_and_workdir_itself(self, tmp_path):
        assert relativize_to(tmp_path, tmp_path / "a" / "b.py") == ("a/b.py", False)
        assert relativize_to(tmp_path, tmp_path) == (".", False)

    def test_trailing_slash_workdir(self, tmp_path):
        assert relativize_to(str(tmp_path) + "/", tmp_path / "a.py") == ("a.py", False)

    def test_outside_and_sibling_prefix(self, tmp_path):
        wd = tmp_path / "repo"
        wd.mkdir()
        sibling = str(tmp_path / "repo2" / "x.py")
        assert relativize_to(wd, sibling) == (sibling, True)

    def test_dotdot_escape_is_outside(self, tmp_path):
        wd = tmp_path / "repo"
        wd.mkdir()
        p = str(wd / ".." / "x.py")
        assert relativize_to(wd, p) == (p, True)

    def test_dotdot_staying_inside_is_normalized(self, tmp_path):
        assert relativize_to(tmp_path, str(tmp_path / "a" / ".." / "b.py")) == ("b.py", False)

    @pytest.mark.skipif(os.name == "nt", reason="symlinks")
    def test_symlinked_workdir_and_symlink_escape(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert relativize_to(link, real / "a.py") == ("a.py", False)
        assert relativize_to(real, link / "a.py") == ("a.py", False)
        other = tmp_path / "other"
        other.mkdir()
        (real / "esc").symlink_to(other)
        p = str(real / "esc" / "f.py")
        assert relativize_to(real, p) == (p, True)

    def test_relative_input_is_against_workdir(self, tmp_path):
        assert relativize_to(tmp_path, "src/a.py") == ("src/a.py", False)
