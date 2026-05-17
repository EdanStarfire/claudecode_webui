"""
Tests for issue #1216 — enforce target_hosts scoping in ProxyAddon.requestheaders().

Verifies that the per-record host gate added to requestheaders() correctly:
- Injects only when the request host matches target_hosts.
- Skips injection (and OAuth refresh) for non-matching hosts.
- Retains match-all semantics when target_hosts is empty or absent.
- Uses subdomain-aware matching (same as _is_allowed()).
- Handles multiple records with independent scopes simultaneously.

Tests call requestheaders() directly via the mitmproxy stub fixture pattern from
test_proxy_credentials_1051.py. No live proxy or Docker is required.
"""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# mitmproxy stubs + addon loader
# ---------------------------------------------------------------------------

def _install_mitmproxy_stubs():
    """Install minimal mitmproxy stubs if not already present, then (re)load addon."""
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

        class _HTTPFlow:
            def __init__(self, host):
                self.request = MagicMock()
                self.request.pretty_host = host
                self.request.path = "/test"
                self.request.method = "GET"
                self.request.scheme = "https"
                self.request.port = 443
                self.request.headers = {}
                self.response = None
                self.client_conn = MagicMock()
                self.client_conn.peername = ("127.0.0.1", 12345)
                self.metadata = {}

        mitmproxy_http.HTTPFlow = _HTTPFlow
        mitmproxy_http.Response = _Response
        mitmproxy_ctx.log = MagicMock()
        mitmproxy_tcp.TCPFlow = MagicMock

        sys.modules["mitmproxy"] = mitmproxy_pkg
        sys.modules["mitmproxy.http"] = mitmproxy_http
        sys.modules["mitmproxy.ctx"] = mitmproxy_ctx
        sys.modules["mitmproxy.tcp"] = mitmproxy_tcp

    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_addon(records: list[dict], allowed: set[str] | None = None):
    """Build a ProxyAddon with pre-populated _records.

    Each record dict: {placeholder, name, type, value, target_hosts (optional)}.
    allowed defaults to all hosts referenced in records plus api.anthropic.com.
    """
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)

    if allowed is None:
        allowed = {"api.anthropic.com", "api.github.com", "github.com",
                   "notgithub.com", "other.allowed.com"}
    f.allowed_domains = allowed

    f._records = {
        r["placeholder"]: {
            "name": r["name"],
            "type": r.get("type", "bearer"),
            "value": r["value"],
            "target_hosts": r.get("target_hosts", []),
        }
        for r in records
    }
    f._refresh_locks = {ph: asyncio.Lock() for ph in f._records}
    f._session_id = "test-1216"
    f._session_token = "test-token"
    f._log_file = None
    f._routing = {"hostname_rewrites": {}, "virtual_key": None, "model_map": {}, "default_model": None}
    f.logger = MagicMock()
    return f


def _flow(host: str, headers: dict | None = None):
    _install_mitmproxy_stubs()
    flow = sys.modules["mitmproxy.http"].HTTPFlow(host)
    flow.request.headers = dict(headers or {})
    return flow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1216_request_injects_when_host_matches_target():
    """Matching host → placeholder replaced in Authorization header."""
    ph = "CC_SECRET_anthropic_key_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "anthropic_key",
        "type": "bearer",
        "value": "sk-ant-real",
        "target_hosts": ["api.anthropic.com"],
    }])
    flow = _flow("api.anthropic.com", {"Authorization": f"Bearer {ph}"})
    await addon.requestheaders(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-real"
    assert flow.metadata["credential_used"] == "anthropic_key"
    # request() after requestheaders() must not re-mutate the header (no-op guard)
    await addon.request(flow)
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-real"


@pytest.mark.asyncio
async def test_issue_1216_request_skips_injection_when_host_does_not_match():
    """Non-matching allowlisted host → placeholder remains, credential_used is None."""
    ph = "CC_SECRET_anthropic_key_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "anthropic_key",
        "type": "bearer",
        "value": "sk-ant-real",
        "target_hosts": ["api.anthropic.com"],
    }])
    flow = _flow("api.github.com", {"Authorization": f"Bearer {ph}"})
    await addon.requestheaders(flow)
    assert flow.request.headers["Authorization"] == f"Bearer {ph}"
    assert flow.metadata["credential_used"] is None


@pytest.mark.asyncio
async def test_issue_1216_request_injects_when_target_hosts_empty():
    """target_hosts=[] → backward-compat match-all; injects on any allowlisted host."""
    ph = "CC_SECRET_generic_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "generic_secret",
        "type": "bearer",
        "value": "real-value",
        "target_hosts": [],
    }])
    flow = _flow("api.github.com", {"Authorization": f"Bearer {ph}"})
    await addon.requestheaders(flow)
    assert flow.request.headers["Authorization"] == "Bearer real-value"
    assert flow.metadata["credential_used"] == "generic_secret"


@pytest.mark.asyncio
async def test_issue_1216_request_injects_when_target_hosts_missing():
    """Record without target_hosts key → match-all (backward compat for older records)."""
    ph = "CC_SECRET_old_record_abcd1234"
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    f.allowed_domains = {"api.github.com"}
    f._records = {
        ph: {
            "name": "old_record",
            "type": "bearer",
            "value": "real-value",
            # target_hosts key intentionally absent
        }
    }
    f._refresh_locks = {ph: asyncio.Lock()}
    f._session_id = "test-1216"
    f._session_token = "test-token"
    f._log_file = None
    f._routing = {"hostname_rewrites": {}, "virtual_key": None, "model_map": {}, "default_model": None}
    f.logger = MagicMock()

    flow = _flow("api.github.com", {"Authorization": f"Bearer {ph}"})
    await f.requestheaders(flow)
    assert flow.request.headers["Authorization"] == "Bearer real-value"
    assert flow.metadata["credential_used"] == "old_record"


@pytest.mark.asyncio
async def test_issue_1216_request_subdomain_match():
    """target_hosts=["github.com"] → injects on api.github.com (subdomain)."""
    ph = "CC_SECRET_github_token_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "github_token",
        "type": "bearer",
        "value": "ghp-real",
        "target_hosts": ["github.com"],
    }])
    flow = _flow("api.github.com", {"Authorization": f"Bearer {ph}"})
    await addon.requestheaders(flow)
    assert flow.request.headers["Authorization"] == "Bearer ghp-real"
    assert flow.metadata["credential_used"] == "github_token"


@pytest.mark.asyncio
async def test_issue_1216_request_subdomain_non_match_substring():
    """target_hosts=["github.com"] → does NOT inject on notgithub.com (substring, not subdomain)."""
    ph = "CC_SECRET_github_token_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "github_token",
        "type": "bearer",
        "value": "ghp-real",
        "target_hosts": ["github.com"],
    }])
    flow = _flow("notgithub.com", {"Authorization": f"Bearer {ph}"})
    await addon.requestheaders(flow)
    assert flow.request.headers["Authorization"] == f"Bearer {ph}"
    assert flow.metadata["credential_used"] is None


@pytest.mark.asyncio
async def test_issue_1216_request_multiple_records_independent_scoping():
    """Two records with different target_hosts — only the matching one injects per request."""
    ph_a = "CC_SECRET_anthropic_abcd1234"
    ph_b = "CC_SECRET_github_efgh5678"
    addon = _make_addon([
        {
            "placeholder": ph_a,
            "name": "anthropic_key",
            "type": "bearer",
            "value": "sk-ant-real",
            "target_hosts": ["api.anthropic.com"],
        },
        {
            "placeholder": ph_b,
            "name": "github_token",
            "type": "bearer",
            "value": "ghp-real",
            "target_hosts": ["api.github.com"],
        },
    ])

    # Request to api.anthropic.com — only anthropic_key injects
    flow_a = _flow("api.anthropic.com", {
        "Authorization": f"Bearer {ph_a}",
        "X-GitHub-Token": f"Bearer {ph_b}",
    })
    await addon.requestheaders(flow_a)
    assert flow_a.request.headers["Authorization"] == "Bearer sk-ant-real"
    assert flow_a.request.headers["X-GitHub-Token"] == f"Bearer {ph_b}"
    assert flow_a.metadata["credential_used"] == "anthropic_key"

    # Request to api.github.com — only github_token injects
    flow_b = _flow("api.github.com", {
        "Authorization": f"Bearer {ph_b}",
        "X-Anthropic-Key": f"Bearer {ph_a}",
    })
    await addon.requestheaders(flow_b)
    assert flow_b.request.headers["Authorization"] == "Bearer ghp-real"
    assert flow_b.request.headers["X-Anthropic-Key"] == f"Bearer {ph_a}"
    assert flow_b.metadata["credential_used"] == "github_token"
