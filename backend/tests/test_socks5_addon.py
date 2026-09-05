"""
Tests for the SOCKS5 tcp_start allowlist hook in src/docker/proxy/addon.py (issue #1052).

Tests cover:
- Allowlisted hostname → flow proceeds (kill() not called)
- Non-allowlisted hostname → flow.kill() called
- IP-literal destination → flow.kill() called
- Missing server_conn.address → flow.kill() called (fail-closed)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# addon.py lives inside the Docker build context, not in src/.
# Add it to sys.path so it can be imported directly.
_ADDON_DIR = Path(__file__).parent.parent / "docker" / "proxy"


def _load_addon():
    """Import addon.py from the proxy build context."""
    if str(_ADDON_DIR) not in sys.path:
        sys.path.insert(0, str(_ADDON_DIR))
    # Stub mitmproxy modules if not installed (test environment may lack them)
    for mod in ("mitmproxy", "mitmproxy.ctx", "mitmproxy.http", "mitmproxy.tcp"):
        if mod not in sys.modules:
            stub = MagicMock()
            sys.modules[mod] = stub
    # Stub httpx
    if "httpx" not in sys.modules:
        sys.modules["httpx"] = MagicMock()
    import importlib
    if "addon" in sys.modules:
        del sys.modules["addon"]
    return importlib.import_module("addon")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tcp_flow(host: str, port: int = 22) -> MagicMock:
    """Build a minimal mock TCPFlow with the given server address."""
    flow = MagicMock()
    flow.server_conn.address = (host, port)
    return flow


def _make_addon_with_domains(allowed_domains: set):
    addon_mod = _load_addon()
    addon = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    addon._session_token = "tok"
    addon._session_id = "sess-1"
    addon._records = {}
    addon._refresh_locks = {}
    addon.allowed_domains = allowed_domains
    addon.logger = MagicMock()
    addon._log_file = None
    addon._socks5_log_file = None
    return addon


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSocks5TcpStart:
    def test_allowed_hostname_proceeds(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = _make_tcp_flow("github.com", 22)
        addon.tcp_start(flow)
        flow.kill.assert_not_called()

    def test_subdomain_of_allowed_domain_proceeds(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = _make_tcp_flow("ssh.github.com", 22)
        addon.tcp_start(flow)
        flow.kill.assert_not_called()

    def test_non_allowlisted_hostname_killed(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = _make_tcp_flow("example.com", 22)
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_ipv4_literal_killed(self):
        addon = _make_addon_with_domains({"github.com", "1.2.3.4"})
        flow = _make_tcp_flow("1.2.3.4", 22)
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_ipv6_literal_killed(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = _make_tcp_flow("::1", 22)
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_missing_address_attr_killed(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = MagicMock()
        del flow.server_conn.address
        # server_conn.address raises AttributeError
        type(flow.server_conn).address = property(lambda self: (_ for _ in ()).throw(AttributeError()))
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_empty_address_killed(self):
        addon = _make_addon_with_domains({"github.com"})
        flow = MagicMock()
        flow.server_conn.address = None
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_empty_allowed_domains_kills_all(self):
        addon = _make_addon_with_domains(set())
        flow = _make_tcp_flow("github.com", 22)
        addon.tcp_start(flow)
        flow.kill.assert_called_once()

    def test_socks5_log_written_on_allow(self):
        log_file = MagicMock()
        addon = _make_addon_with_domains({"github.com"})
        addon._socks5_log_file = log_file
        flow = _make_tcp_flow("github.com", 22)
        addon.tcp_start(flow)
        log_file.write.assert_called_once()
        written = log_file.write.call_args[0][0]
        import json
        entry = json.loads(written.rstrip("\n"))
        assert entry["allowed"] is True
        assert entry["host"] == "github.com"

    def test_socks5_log_written_on_deny(self):
        log_file = MagicMock()
        addon = _make_addon_with_domains({"github.com"})
        addon._socks5_log_file = log_file
        flow = _make_tcp_flow("evil.com", 22)
        addon.tcp_start(flow)
        log_file.write.assert_called_once()
        written = log_file.write.call_args[0][0]
        import json
        entry = json.loads(written.rstrip("\n"))
        assert entry["allowed"] is False
        assert entry["host"] == "evil.com"
