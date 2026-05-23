"""Tests for LinksMCPTools — issue #1530."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..mcp.links_mcp_tools import MAX_LABEL_LENGTH, MAX_URL_LENGTH, LinksMCPTools, _validate_url

# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------

class TestValidateUrl:
    def test_http_valid(self):
        assert _validate_url("http://example.com") is None

    def test_https_valid(self):
        assert _validate_url("https://github.com/owner/repo/issues/1") is None

    def test_ftp_rejected(self):
        assert _validate_url("ftp://example.com") is not None

    def test_file_rejected(self):
        assert _validate_url("file:///etc/passwd") is not None

    def test_no_scheme_rejected(self):
        assert _validate_url("example.com") is not None

    def test_missing_host_rejected(self):
        assert _validate_url("https://") is not None

    def test_javascript_rejected(self):
        assert _validate_url("javascript:alert(1)") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tools(upsert_return=None, session_links=None):
    """Return a LinksMCPTools with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.session_manager = MagicMock()
    coordinator.session_manager.upsert_link = AsyncMock(
        return_value=upsert_return or {
            "label": "Test",
            "url": "https://example.com",
            "registered_at": datetime.now(UTC).isoformat(),
        }
    )
    session_info = MagicMock()
    session_info.links = session_links or []
    coordinator.session_manager.get_session_info = AsyncMock(return_value=session_info)
    return LinksMCPTools(session_coordinator=coordinator)


# ---------------------------------------------------------------------------
# _handle_register_link
# ---------------------------------------------------------------------------

class TestHandleRegisterLink:
    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_empty_label(self):
        tools = _make_tools()
        result = await tools._handle_register_link("sid", {"label": "", "url": "https://example.com"})
        assert result["is_error"] is True
        assert "label" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_whitespace_label(self):
        tools = _make_tools()
        result = await tools._handle_register_link("sid", {"label": "   ", "url": "https://example.com"})
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_oversize_label(self):
        tools = _make_tools()
        result = await tools._handle_register_link(
            "sid", {"label": "x" * (MAX_LABEL_LENGTH + 1), "url": "https://example.com"}
        )
        assert result["is_error"] is True
        assert str(MAX_LABEL_LENGTH) in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_empty_url(self):
        tools = _make_tools()
        result = await tools._handle_register_link("sid", {"label": "ok", "url": ""})
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_oversize_url(self):
        tools = _make_tools()
        result = await tools._handle_register_link(
            "sid", {"label": "ok", "url": "https://example.com/" + "a" * MAX_URL_LENGTH}
        )
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_non_http_scheme(self):
        tools = _make_tools()
        result = await tools._handle_register_link("sid", {"label": "ok", "url": "ftp://example.com"})
        assert result["is_error"] is True
        assert "http" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_issue_1530_register_rejects_missing_host(self):
        tools = _make_tools()
        result = await tools._handle_register_link("sid", {"label": "ok", "url": "https://"})
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_issue_1530_register_success_returns_entry(self):
        entry = {
            "label": "GitHub Issue #1530",
            "url": "https://github.com/owner/repo/issues/1530",
            "registered_at": datetime.now(UTC).isoformat(),
        }
        tools = _make_tools(upsert_return=entry)
        result = await tools._handle_register_link(
            "sid", {"label": "GitHub Issue #1530", "url": "https://github.com/owner/repo/issues/1530"}
        )
        assert result["is_error"] is False
        assert "GitHub Issue #1530" in result["content"][0]["text"]
        # Verify upsert_link was called with stripped label
        tools.session_coordinator.session_manager.upsert_link.assert_called_once_with(
            "sid", "GitHub Issue #1530", "https://github.com/owner/repo/issues/1530"
        )

    @pytest.mark.asyncio
    async def test_issue_1530_register_broadcasts_event(self):
        entry = {"label": "Test", "url": "https://example.com", "registered_at": "2026-01-01T00:00:00+00:00"}
        broadcast = AsyncMock()
        tools = _make_tools(upsert_return=entry)
        tools.broadcast_callback = broadcast
        await tools._handle_register_link("sid", {"label": "Test", "url": "https://example.com"})
        broadcast.assert_awaited_once_with("sid", entry)

    @pytest.mark.asyncio
    async def test_issue_1530_register_label_at_max_length_is_accepted(self):
        tools = _make_tools()
        result = await tools._handle_register_link(
            "sid", {"label": "x" * MAX_LABEL_LENGTH, "url": "https://example.com"}
        )
        assert result["is_error"] is False


# ---------------------------------------------------------------------------
# _handle_list_links
# ---------------------------------------------------------------------------

class TestHandleListLinks:
    @pytest.mark.asyncio
    async def test_issue_1530_list_returns_persisted_entries(self):
        links = [
            {"label": "A", "url": "https://a.example.com", "registered_at": "2026-01-01T00:00:00+00:00"},
            {"label": "B", "url": "https://b.example.com", "registered_at": "2026-01-02T00:00:00+00:00"},
        ]
        tools = _make_tools(session_links=links)
        result = await tools._handle_list_links("sid", {})
        assert result["is_error"] is False
        text = result["content"][0]["text"]
        assert "2 link" in text
        assert "https://a.example.com" in text
        assert "https://b.example.com" in text

    @pytest.mark.asyncio
    async def test_issue_1530_list_empty_session(self):
        tools = _make_tools(session_links=[])
        result = await tools._handle_list_links("sid", {})
        assert result["is_error"] is False
        assert "0 link" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_issue_1530_list_returns_error_when_session_not_found(self):
        coordinator = MagicMock()
        coordinator.session_manager = MagicMock()
        coordinator.session_manager.get_session_info = AsyncMock(return_value=None)
        tools = LinksMCPTools(session_coordinator=coordinator)
        result = await tools._handle_list_links("missing-sid", {})
        assert result["is_error"] is True
