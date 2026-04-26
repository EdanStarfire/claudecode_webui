"""Tests for the edit-history router (issue #1128)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ..routers.edit_history import _classify_bash, build_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_webui(messages_path: str | None, session_exists: bool = True):
    """Return a minimal mock webui object that satisfies the router's needs."""
    webui = MagicMock()
    webui.service.get_session_diff_context = AsyncMock(
        return_value={"exists": session_exists, "working_directory": "/tmp"}
    )
    webui.service.get_session_messages_path = AsyncMock(return_value=messages_path)
    return webui


def _make_app(webui):
    app = FastAPI()
    app.include_router(build_router(webui))
    return app


def _write_messages(path: Path, messages: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def _tool_use_block(tool_id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result_block(tool_id: str, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error}


def _assistant_msg(blocks: list[dict], ts: float = 1000.0) -> dict:
    return {"_type": "AssistantMessage", "timestamp": ts, "data": {"content": blocks}}


def _user_msg(blocks: list[dict], ts: float = 1001.0) -> dict:
    return {"_type": "UserMessage", "timestamp": ts, "data": {"content": blocks}}


# ---------------------------------------------------------------------------
# _classify_bash unit tests
# ---------------------------------------------------------------------------

class TestClassifyBash:
    def test_empty_command_returns_false(self):
        assert _classify_bash("") is False

    def test_sed_i_is_modifying(self):
        assert _classify_bash("sed -i 's/foo/bar/' file.txt") is True

    def test_ls_is_not_modifying(self):
        assert _classify_bash("ls -la /tmp") is False

    def test_cat_is_not_modifying(self):
        assert _classify_bash("cat README.md") is False

    def test_output_redirection_is_modifying(self):
        assert _classify_bash("echo hello > output.txt") is True

    def test_append_redirection_is_modifying(self):
        assert _classify_bash("echo hello >> log.txt") is True

    def test_git_commit_is_modifying(self):
        assert _classify_bash("git commit -m 'fix'") is True

    def test_git_status_is_not_modifying(self):
        assert _classify_bash("git status") is False

    def test_npm_install_is_modifying(self):
        assert _classify_bash("npm install") is True

    def test_uv_add_is_modifying(self):
        assert _classify_bash("uv add requests") is True

    def test_mv_is_modifying(self):
        assert _classify_bash("mv old.txt new.txt") is True

    def test_grep_is_not_modifying(self):
        assert _classify_bash("grep -r 'pattern' .") is False


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestGetEditHistory:
    @pytest.mark.asyncio
    async def test_unknown_session_404(self):
        webui = _make_webui(None, session_exists=False)
        app = _make_app(webui)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/sessions/missing/edit-history")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_session_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            path.touch()
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        assert r.status_code == 200
        data = r.json()
        assert data["entries"] == []
        assert data["tool_count"] == 0

    @pytest.mark.asyncio
    async def test_edit_tool_use_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            block = _tool_use_block(
                "tu1", "Edit",
                {"file_path": "/src/main.py", "old_string": "foo", "new_string": "bar"}
            )
            _write_messages(path, [_assistant_msg([block])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) == 1
        e = entries[0]
        assert e["tool_name"] == "Edit"
        assert e["file_path"] == "/src/main.py"
        assert e["input"]["old_string"] == "foo"
        assert e["input"]["new_string"] == "bar"
        assert e["tool_use_id"] == "tu1"

    @pytest.mark.asyncio
    async def test_write_includes_line_count(self):
        content = "line1\nline2\nline3"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            block = _tool_use_block(
                "tu2", "Write",
                {"file_path": "/out.txt", "content": content}
            )
            _write_messages(path, [_assistant_msg([block])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert len(entries) == 1
        e = entries[0]
        assert e["tool_name"] == "Write"
        assert e["line_count"] == 3
        assert e["file_path"] == "/out.txt"

    @pytest.mark.asyncio
    async def test_bash_classification(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            modifying = _tool_use_block("tu3", "Bash", {"command": "sed -i 's/a/b/' f.txt"})
            non_mod = _tool_use_block("tu4", "Bash", {"command": "ls -la"})
            _write_messages(path, [_assistant_msg([modifying, non_mod])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert len(entries) == 2
        by_id = {e["tool_use_id"]: e for e in entries}
        assert by_id["tu3"]["likely_modifying"] is True
        assert by_id["tu4"]["likely_modifying"] is False

    @pytest.mark.asyncio
    async def test_tool_result_succeeded_flag(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            block = _tool_use_block("tu5", "Edit", {"file_path": "/f.py", "old_string": "a", "new_string": "b"})
            result_ok = _tool_result_block("tu5", is_error=False)
            block2 = _tool_use_block("tu6", "Edit", {"file_path": "/g.py", "old_string": "x", "new_string": "y"})
            result_err = _tool_result_block("tu6", is_error=True)
            _write_messages(path, [
                _assistant_msg([block, block2], ts=1000.0),
                _user_msg([result_ok, result_err], ts=1001.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        by_id = {e["tool_use_id"]: e for e in entries}
        assert by_id["tu5"]["succeeded"] is True
        assert by_id["tu6"]["succeeded"] is False

    @pytest.mark.asyncio
    async def test_chronological_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            b1 = _tool_use_block("tu7", "Edit", {"file_path": "/a.py", "old_string": "", "new_string": "x"})
            b2 = _tool_use_block("tu8", "Edit", {"file_path": "/b.py", "old_string": "", "new_string": "y"})
            b3 = _tool_use_block("tu9", "Write", {"file_path": "/c.py", "content": "z"})
            _write_messages(path, [
                _assistant_msg([b1], ts=100.0),
                _assistant_msg([b2], ts=200.0),
                _assistant_msg([b3], ts=300.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert [e["tool_use_id"] for e in entries] == ["tu7", "tu8", "tu9"]

    @pytest.mark.asyncio
    async def test_pending_result_when_no_tool_result(self):
        """Entry with no matching tool_result has succeeded=None (pending)."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            block = _tool_use_block("tu10", "Bash", {"command": "make build"})
            _write_messages(path, [_assistant_msg([block])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is None
