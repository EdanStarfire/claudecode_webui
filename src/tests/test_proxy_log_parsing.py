"""Tests for proxy log parsing in session_coordinator.

Issue #1102: Verifies HTTP access.log JSON parsing, CoreDNS dns.log parsing,
and the tail-read helper with line limits.
"""

import json
import tempfile
from pathlib import Path

import pytest

from ..session_coordinator import _count_file_lines, _tail_read_lines


# ==================== _tail_read_lines ====================


class TestTailReadLines:
    def test_returns_last_n_lines(self, tmp_path):
        log_file = tmp_path / "test.log"
        lines = [f"line{i}\n" for i in range(500)]
        log_file.write_text("".join(lines))

        result = _tail_read_lines(log_file, 200)
        assert len(result) == 200
        # Last line should be line499
        assert result[-1].strip() == "line499"
        # First returned line should be line300
        assert result[0].strip() == "line300"

    def test_returns_all_lines_when_fewer_than_limit(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("a\nb\nc\n")

        result = _tail_read_lines(log_file, 200)
        assert len(result) == 3

    def test_empty_file_returns_empty_list(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        result = _tail_read_lines(log_file, 200)
        assert result == []

    def test_nonexistent_file_returns_empty_list(self, tmp_path):
        result = _tail_read_lines(tmp_path / "missing.log", 200)
        assert result == []


# ==================== _count_file_lines ====================


class TestCountFileLines:
    def test_counts_lines(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("a\nb\nc\n")
        assert _count_file_lines(log_file) == 3

    def test_empty_file_returns_zero(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        assert _count_file_lines(log_file) == 0

    def test_nonexistent_file_returns_zero(self, tmp_path):
        assert _count_file_lines(tmp_path / "missing.log") == 0


# ==================== HTTP log parsing ====================


class TestHttpLogParsing:
    """Test get_proxy_logs HTTP parsing via SessionCoordinator."""

    @pytest.fixture
    async def coordinator(self, tmp_path):
        """Create a minimal SessionCoordinator pointed at tmp_path."""
        from unittest.mock import AsyncMock, MagicMock

        from ..session_coordinator import SessionCoordinator

        coord = SessionCoordinator.__new__(SessionCoordinator)
        coord.data_dir = tmp_path
        coord.logger = MagicMock()
        return coord

    @pytest.fixture
    def session_id(self):
        return "test-session-001"

    @pytest.fixture
    def proxy_dir(self, tmp_path, session_id):
        d = tmp_path / "sessions" / session_id / "docker_claude_data" / "proxy"
        d.mkdir(parents=True)
        return d

    @pytest.mark.asyncio
    async def test_valid_http_entries_parsed(self, coordinator, proxy_dir, session_id):
        entries = [
            {"ts": 1713600000, "host": "api.github.com", "method": "GET", "path": "/repos",
             "status": 200, "allowed": True, "credential_used": "gh-token",
             "scheme": "https", "port": 443, "bytes": 1234},
            {"ts": 1713600001, "host": "blocked.example.com", "method": "POST", "path": "/data",
             "status": 403, "allowed": False, "credential_used": None,
             "scheme": "https", "port": 443, "bytes": 0},
        ]
        access_log = proxy_dir / "access.log"
        access_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = await coordinator.get_proxy_logs(session_id, log_type="http", limit=200)

        assert result["log_type"] == "http"
        assert result["total_lines"] == 2
        assert len(result["entries"]) == 2
        assert result["entries"][0]["host"] == "api.github.com"
        assert result["entries"][0]["allowed"] is True
        assert result["entries"][1]["allowed"] is False

    @pytest.mark.asyncio
    async def test_malformed_json_lines_skipped(self, coordinator, proxy_dir, session_id):
        access_log = proxy_dir / "access.log"
        access_log.write_text(
            '{"ts": 1, "host": "ok.com", "method": "GET", "path": "/", "status": 200, "allowed": true, "credential_used": null, "scheme": "https", "port": 443, "bytes": 0}\n'
            "not valid json\n"
            '{"ts": 2, "host": "ok2.com", "method": "GET", "path": "/x", "status": 200, "allowed": true, "credential_used": null, "scheme": "https", "port": 443, "bytes": 0}\n'
        )

        result = await coordinator.get_proxy_logs(session_id, log_type="http", limit=200)
        assert len(result["entries"]) == 2

    @pytest.mark.asyncio
    async def test_missing_log_file_returns_empty(self, coordinator, proxy_dir, session_id):
        result = await coordinator.get_proxy_logs(session_id, log_type="http", limit=200)
        assert result == {"entries": [], "total_lines": 0, "log_type": "http"}

    @pytest.mark.asyncio
    async def test_limit_applied(self, coordinator, proxy_dir, session_id):
        access_log = proxy_dir / "access.log"
        lines = []
        for i in range(300):
            lines.append(json.dumps({
                "ts": i, "host": f"host{i}.com", "method": "GET", "path": "/",
                "status": 200, "allowed": True, "credential_used": None,
                "scheme": "https", "port": 443, "bytes": 0
            }))
        access_log.write_text("\n".join(lines) + "\n")

        result = await coordinator.get_proxy_logs(session_id, log_type="http", limit=10)
        assert len(result["entries"]) == 10
        # Should be the last 10 entries (ts 290-299)
        assert result["entries"][-1]["ts"] == 299


# ==================== DNS log parsing ====================


class TestDnsLogParsing:
    @pytest.fixture
    async def coordinator(self, tmp_path):
        from unittest.mock import MagicMock

        from ..session_coordinator import SessionCoordinator

        coord = SessionCoordinator.__new__(SessionCoordinator)
        coord.data_dir = tmp_path
        coord.logger = MagicMock()
        return coord

    @pytest.fixture
    def session_id(self):
        return "test-session-002"

    @pytest.fixture
    def proxy_dir(self, tmp_path, session_id):
        d = tmp_path / "sessions" / session_id / "docker_claude_data" / "proxy"
        d.mkdir(parents=True)
        return d

    @pytest.mark.asyncio
    async def test_noerror_parsed(self, coordinator, proxy_dir, session_id):
        dns_log = proxy_dir / "dns.log"
        dns_log.write_text(
            '[INFO] 10.0.0.2:54312 - 12345 "A IN api.github.com. udp 128 false 4096" NOERROR qr,rd,ra 73 0.023s\n'
        )
        result = await coordinator.get_proxy_logs(session_id, log_type="dns", limit=200)
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["query_type"] == "A"
        assert entry["hostname"] == "api.github.com"
        assert entry["result"] == "NOERROR"

    @pytest.mark.asyncio
    async def test_nxdomain_parsed(self, coordinator, proxy_dir, session_id):
        dns_log = proxy_dir / "dns.log"
        dns_log.write_text(
            '[INFO] 10.0.0.2:54313 - 12346 "AAAA IN nonexistent.example. udp 128 false 4096" NXDOMAIN qr,rd 0 0.001s\n'
        )
        result = await coordinator.get_proxy_logs(session_id, log_type="dns", limit=200)
        assert result["entries"][0]["result"] == "NXDOMAIN"
        assert result["entries"][0]["query_type"] == "AAAA"
        assert result["entries"][0]["hostname"] == "nonexistent.example"

    @pytest.mark.asyncio
    async def test_refused_parsed(self, coordinator, proxy_dir, session_id):
        dns_log = proxy_dir / "dns.log"
        dns_log.write_text(
            '[INFO] 10.0.0.2:54314 - 12347 "A IN blocked.example.com. udp 128 false 4096" REFUSED qr,rd 0 0.001s\n'
        )
        result = await coordinator.get_proxy_logs(session_id, log_type="dns", limit=200)
        assert result["entries"][0]["result"] == "REFUSED"

    @pytest.mark.asyncio
    async def test_malformed_lines_skipped(self, coordinator, proxy_dir, session_id):
        dns_log = proxy_dir / "dns.log"
        dns_log.write_text(
            "this is not a valid coreDNS line\n"
            '[INFO] 10.0.0.2:54312 - 12345 "A IN good.host.com. udp 128 false 4096" NOERROR qr,rd,ra 73 0.023s\n'
            "another garbage line\n"
        )
        result = await coordinator.get_proxy_logs(session_id, log_type="dns", limit=200)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["hostname"] == "good.host.com"

    @pytest.mark.asyncio
    async def test_missing_dns_log_returns_empty(self, coordinator, proxy_dir, session_id):
        result = await coordinator.get_proxy_logs(session_id, log_type="dns", limit=200)
        assert result == {"entries": [], "total_lines": 0, "log_type": "dns"}
