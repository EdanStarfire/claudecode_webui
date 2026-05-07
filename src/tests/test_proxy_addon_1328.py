"""
Unit tests for issue #1328 — allow-all sentinel ("*") in docker_proxy_allowlist_domains.

Tests cover:
  a. _is_allowed short-circuits to True when "*" in allowed_domains
  b. _is_allowed without sentinel behaves as before (suffix-match)
  c. tcp_start allows arbitrary hostname when sentinel set (flow.kill not called)
  d. tcp_start still kills IP-literal even when sentinel set
  e. _host_matches_targets is unchanged regardless of allowed_domains content

No mitmproxy stack is required — all tests run against pure Python logic.
"""

import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# mitmproxy stubs + addon loader
# ---------------------------------------------------------------------------

def _install_mitmproxy_stubs():
    """Install minimal mitmproxy stubs if not already present, then reload addon."""
    if "mitmproxy" not in sys.modules:
        mitmproxy_pkg = types.ModuleType("mitmproxy")
        mitmproxy_http = types.ModuleType("mitmproxy.http")
        mitmproxy_ctx = types.ModuleType("mitmproxy.ctx")
        mitmproxy_tcp = types.ModuleType("mitmproxy.tcp")

        class _Response:
            status_code = 200
            content = b""

            @staticmethod
            def make(status, body, headers):
                r = _Response()
                r.status_code = status
                r.content = body.encode() if isinstance(body, str) else body
                return r

        mitmproxy_http.Response = _Response
        mitmproxy_ctx.log = MagicMock()
        mitmproxy_tcp.TCPFlow = MagicMock

        sys.modules["mitmproxy"] = mitmproxy_pkg
        sys.modules["mitmproxy.http"] = mitmproxy_http
        sys.modules["mitmproxy.ctx"] = mitmproxy_ctx
        sys.modules["mitmproxy.tcp"] = mitmproxy_tcp

    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_addon(allowed_domains: set):
    """Build a minimal ProxyAddon instance with the given allowed_domains set."""
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    a = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    a.allowed_domains = allowed_domains
    a._records = {}
    a._refresh_locks = {}
    a._session_id = "test-1328"
    a._session_token = "test-token"
    a._log_file = None
    a._socks5_log_file = None
    a.logger = MagicMock()
    return a


def _make_tcp_flow(address):
    """Build a minimal TCPFlow-like mock with the given server_conn.address."""
    flow = MagicMock()
    flow.server_conn.address = address
    return flow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_is_allowed_sentinel_short_circuits():
    """_is_allowed returns True for any host when "*" is in allowed_domains."""
    _install_mitmproxy_stubs()
    addon = _make_addon({"*"})
    assert addon._is_allowed("anything.example") is True
    assert addon._is_allowed("totally.unrelated.domain.org") is True
    assert addon._is_allowed("127.0.0.1") is True  # even odd values pass


def test_is_allowed_without_sentinel_unchanged():
    """_is_allowed without sentinel falls back to suffix-match (existing behaviour)."""
    _install_mitmproxy_stubs()
    addon = _make_addon({"foo.com"})
    assert addon._is_allowed("foo.com") is True
    assert addon._is_allowed("sub.foo.com") is True
    assert addon._is_allowed("bar.com") is False
    assert addon._is_allowed("notfoo.com") is False


def test_tcp_start_allows_arbitrary_hostname_under_sentinel():
    """tcp_start does not kill the flow for a named host when sentinel is set."""
    _install_mitmproxy_stubs()
    addon = _make_addon({"*"})
    flow = _make_tcp_flow(("arbitrary.example.com", 443))

    with patch.object(addon, "_write_socks5_log") as mock_log:
        addon.tcp_start(flow)

    flow.kill.assert_not_called()
    mock_log.assert_called_once_with(
        host="arbitrary.example.com", port=443, allowed=True, reason="ok"
    )


def test_tcp_start_still_kills_ip_literal_under_sentinel():
    """IP-literal rejection runs before the allowlist check — sentinel does not bypass it."""
    _install_mitmproxy_stubs()
    addon = _make_addon({"*"})
    flow = _make_tcp_flow(("8.8.8.8", 53))

    with patch.object(addon, "_write_socks5_log") as mock_log:
        addon.tcp_start(flow)

    flow.kill.assert_called_once()
    mock_log.assert_called_once_with(
        host="8.8.8.8", port=53, allowed=False, reason="ip_literal"
    )


def test_host_matches_targets_unchanged_under_sentinel():
    """_host_matches_targets is purely about secrets — allowed_domains is irrelevant.

    Verifies the contract: sentinel in allowed_domains does NOT relax per-secret
    target_hosts scoping. A secret restricted to anthropic.com is never injected
    on wikipedia.org regardless of allowlist content.
    """
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _host_matches_targets

    # Scoped secret: only anthropic.com
    assert _host_matches_targets("wikipedia.org", ["anthropic.com"]) is False
    assert _host_matches_targets("api.anthropic.com", ["anthropic.com"]) is True

    # Unconstrained secret (empty target_hosts) always matches — independent of allowlist
    assert _host_matches_targets("wikipedia.org", []) is True
    assert _host_matches_targets("api.anthropic.com", []) is True
