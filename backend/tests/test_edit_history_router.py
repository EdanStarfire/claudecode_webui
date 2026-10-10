"""Tests for the edit-history router (issue #1128)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routers.edit_history import _classify_bash, build_router

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

    def test_dev_null_redirect_is_not_modifying(self):
        assert _classify_bash("cmd >/dev/null") is False
        assert _classify_bash("cmd >> /dev/null") is False
        assert _classify_bash("cmd 2>/dev/null") is False

    def test_dev_null_with_suppression_pattern_is_not_modifying(self):
        assert _classify_bash("npm test >/dev/null 2>&1") is False

    def test_mixed_real_and_dev_null_is_modifying(self):
        assert _classify_bash("cmd > real.txt 2>/dev/null") is True

    def test_dev_null_with_trailing_punctuation(self):
        assert _classify_bash("cmd > /dev/null; ls") is False

    def test_combined_redirect_to_real_file(self):
        assert _classify_bash("cmd &>>file.log") is True

    def test_append_to_dev_null_is_not_modifying(self):
        assert _classify_bash("cmd >> /dev/null") is False

    def test_dev_null_in_subshell_is_not_modifying(self):
        assert _classify_bash("(echo hello >/dev/null)") is False


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
    async def test_legacy_type_tagged_record_raises_500(self):
        """A `_type`-tagged StoredMessage-era record triggers the AC10 guard
        (issue #2109): surfaced as a 500 (logged loudly server-side via
        `handle_exceptions`), never silently misread as canonical."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            _write_messages(path, [
                {"_type": "AssistantMessage", "timestamp": 1000.0, "data": {"content": []}},
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        assert r.status_code == 500


# ---------------------------------------------------------------------------
# Legacy prepare_for_storage() format: lowercase type + metadata.tool_uses
# ---------------------------------------------------------------------------

def _legacy_assistant_msg(tool_uses: list[dict], ts: float = 1000.0) -> dict:
    return {"type": "assistant", "timestamp": ts, "metadata": {"tool_uses": tool_uses}}


def _legacy_user_msg(tool_results: list[dict], ts: float = 1001.0) -> dict:
    return {"type": "user", "timestamp": ts, "metadata": {"tool_results": tool_results}}


def _legacy_tool_use(tool_id: str, name: str, inp: dict) -> dict:
    return {"id": tool_id, "name": name, "input": inp}


def _legacy_tool_result(tool_id: str, is_error: bool = False) -> dict:
    return {"tool_use_id": tool_id, "is_error": is_error}


class TestGetEditHistoryLegacyFormat:
    @pytest.mark.asyncio
    async def test_lowercase_assistant_with_metadata_tool_uses(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("leg1", "Edit", {"file_path": "/x.py", "old_string": "a", "new_string": "b"})
            _write_messages(path, [_legacy_assistant_msg([tu])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        assert r.status_code == 200
        entries = r.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "Edit"
        assert entries[0]["file_path"] == "/x.py"
        assert entries[0]["tool_use_id"] == "leg1"

    @pytest.mark.asyncio
    async def test_lowercase_user_with_metadata_tool_results(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("leg2", "Write", {"file_path": "/y.py", "content": "hello"})
            tr_ok = _legacy_tool_result("leg2", is_error=False)
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _legacy_user_msg([tr_ok], ts=101.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["succeeded"] is True

    @pytest.mark.asyncio
    async def test_bash_classification(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            modifying = _legacy_tool_use("leg3", "Bash", {"command": "sed -i 's/a/b/' f.txt"})
            non_mod = _legacy_tool_use("leg4", "Bash", {"command": "ls -la"})
            _write_messages(path, [_legacy_assistant_msg([modifying, non_mod])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        by_id = {e["tool_use_id"]: e for e in entries}
        assert by_id["leg3"]["likely_modifying"] is True
        assert by_id["leg4"]["likely_modifying"] is False

    @pytest.mark.asyncio
    async def test_issue_1565_dev_null_bash_is_not_modifying(self):
        """A Bash command that redirects only to /dev/null returns likely_modifying=False."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("leg_dnull", "Bash", {"command": "npm test >/dev/null 2>&1"})
            _write_messages(path, [_legacy_assistant_msg([tu])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["likely_modifying"] is False

    @pytest.mark.asyncio
    async def test_chronological_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu1 = _legacy_tool_use("leg5", "Edit", {"file_path": "/a.py", "old_string": "", "new_string": "x"})
            tu2 = _legacy_tool_use("leg6", "Edit", {"file_path": "/b.py", "old_string": "", "new_string": "y"})
            tu3 = _legacy_tool_use("leg7", "Write", {"file_path": "/c.py", "content": "z"})
            _write_messages(path, [
                _legacy_assistant_msg([tu1], ts=100.0),
                _legacy_assistant_msg([tu2], ts=200.0),
                _legacy_assistant_msg([tu3], ts=300.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert [e["tool_use_id"] for e in entries] == ["leg5", "leg6", "leg7"]

    @pytest.mark.asyncio
    async def test_pending_result_when_no_tool_result(self):
        """Entry with no matching tool_result has succeeded=None (pending)."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("leg8", "Bash", {"command": "make build"})
            _write_messages(path, [_legacy_assistant_msg([tu])])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is None
        assert entries[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# Canonical tool_call status (issue #2117)
# ---------------------------------------------------------------------------

def _tool_call_msg(tid: str, name: str, status: str, ts: float = 1001.0) -> dict:
    return {
        "type": "tool_call",
        "tool_use_id": tid,
        "name": name,
        "status": status,
        "timestamp": ts,
    }


class TestGetEditHistoryToolCallStatus:
    @pytest.mark.asyncio
    async def test_completed_tool_call_no_legacy_result(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("tc1", "Bash", {"command": "make build"})
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _tool_call_msg("tc1", "Bash", "completed", ts=101.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is True
        assert entries[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_tool_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("tc2", "Bash", {"command": "make build"})
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _tool_call_msg("tc2", "Bash", "failed", ts=101.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is False
        assert entries[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_denied_tool_call_no_tool_result(self):
        """The exact bug in #2117: a denied tool call never produces a
        tool_result, so it must resolve to succeeded=False/status=denied,
        not succeeded=None/status=pending."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("tc3", "Edit", {"file_path": "/x.py", "old_string": "a", "new_string": "b"})
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _tool_call_msg("tc3", "Edit", "denied", ts=101.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is False
        assert entries[0]["status"] == "denied"

    @pytest.mark.asyncio
    async def test_interrupted_tool_call_no_tool_result(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("tc4", "Bash", {"command": "make build"})
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _tool_call_msg("tc4", "Bash", "interrupted", ts=101.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is False
        assert entries[0]["status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_non_terminal_tool_call_statuses_are_pending_equivalent(self):
        for status in ("pending", "awaiting_permission", "running"):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "messages.jsonl"
                tu = _legacy_tool_use("tc5", "Bash", {"command": "make build"})
                _write_messages(path, [
                    _legacy_assistant_msg([tu], ts=100.0),
                    _tool_call_msg("tc5", "Bash", status, ts=101.0),
                ])
                webui = _make_webui(str(path))
                app = _make_app(webui)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    r = await client.get("/api/sessions/s1/edit-history")
            entries = r.json()["entries"]
            assert entries[0]["succeeded"] is None
            assert entries[0]["status"] == status

    @pytest.mark.asyncio
    async def test_canonical_tool_call_status_overrides_conflicting_legacy_result(self):
        """A legacy tool_result (succeeded=True) and a conflicting canonical
        tool_call status (denied) for the same tool_use_id: canonical wins."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "messages.jsonl"
            tu = _legacy_tool_use("tc6", "Edit", {"file_path": "/x.py", "old_string": "a", "new_string": "b"})
            tr_ok = _legacy_tool_result("tc6", is_error=False)
            _write_messages(path, [
                _legacy_assistant_msg([tu], ts=100.0),
                _legacy_user_msg([tr_ok], ts=101.0),
                _tool_call_msg("tc6", "Edit", "denied", ts=102.0),
            ])
            webui = _make_webui(str(path))
            app = _make_app(webui)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.get("/api/sessions/s1/edit-history")
        entries = r.json()["entries"]
        assert entries[0]["succeeded"] is False
        assert entries[0]["status"] == "denied"
