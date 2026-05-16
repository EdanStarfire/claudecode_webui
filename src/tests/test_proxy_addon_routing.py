"""
Unit tests for Phase 3 LiteLLM routing in the proxy addon (issue #1427).

Covers:
  - Hostname rewrite: host, port, scheme mutated correctly
  - Headers: x-api-key and Authorization set to virtual key
  - Metadata sentinel: flow.metadata["routed_via_litellm"] set
  - Allowlist still passes for rewritten host (cc-webui.internal)
  - Negative: empty routing → flow unchanged
  - Negative: non-Anthropic host with routing enabled → flow unchanged
  - REQUIRED sentinel guard: secret injection skipped when routed_via_litellm set
  - Inverse sentinel guard: routing disabled → secret injected as normal
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# mitmproxy stubs (same pattern as test_proxy_addon_1428.py)
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, pairs=None):
        self._pairs = list(pairs or [])

    def items(self):
        return list(self._pairs)

    def __setitem__(self, key, value):
        for i, (k, _) in enumerate(self._pairs):
            if k == key:
                self._pairs[i] = (key, value)
                return
        self._pairs.append((key, value))

    def __getitem__(self, key):
        for k, v in self._pairs:
            if k == key:
                return v
        raise KeyError(key)

    def __contains__(self, key):
        return any(k == key for k, _ in self._pairs)


class _FakeHeaders(dict):
    def get(self, key, default=""):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeRequest:
    __slots__ = (
        "pretty_host", "host", "path", "method", "scheme", "port",
        "headers", "query", "url", "content", "stream",
    )

    def __init__(self, host="api.anthropic.com", headers=None, content=None):
        self.pretty_host = host
        self.host = host
        self.path = "/v1/messages"
        self.method = "POST"
        self.scheme = "https"
        self.port = 443
        self.url = f"https://{host}/v1/messages"
        self.headers = _FakeHeaders(headers or {"x-api-key": "sk-ant-real"})
        self.query = _FakeQuery()
        self.content = content
        self.stream = None


class _FakeResponse:
    def __init__(self, headers=None, content=b"", status_code=200):
        self.headers = _FakeHeaders(headers or {"content-type": "application/json"})
        self.content = content
        self.status_code = status_code
        self.stream = None

    @staticmethod
    def make(status, body, headers):
        r = _FakeResponse(headers=headers, status_code=status)
        r.content = body.encode() if isinstance(body, str) else body
        return r


def _install_mitmproxy_stubs():
    class _HTTPFlow:
        def __init__(self, host="api.anthropic.com"):
            self.request = _FakeRequest(host=host)
            self.response = None
            self.client_conn = MagicMock()
            self.client_conn.peername = ("127.0.0.1", 12345)
            self.metadata = {}

    if "mitmproxy" not in sys.modules:
        mitmproxy_pkg = types.ModuleType("mitmproxy")
        mitmproxy_http = types.ModuleType("mitmproxy.http")
        mitmproxy_ctx = types.ModuleType("mitmproxy.ctx")
        mitmproxy_tcp = types.ModuleType("mitmproxy.tcp")

        mitmproxy_http.HTTPFlow = _HTTPFlow
        mitmproxy_http.Response = _FakeResponse
        mitmproxy_ctx.log = MagicMock()
        mitmproxy_tcp.TCPFlow = MagicMock

        sys.modules["mitmproxy"] = mitmproxy_pkg
        sys.modules["mitmproxy.http"] = mitmproxy_http
        sys.modules["mitmproxy.ctx"] = mitmproxy_ctx
        sys.modules["mitmproxy.tcp"] = mitmproxy_tcp
    else:
        sys.modules["mitmproxy.http"].HTTPFlow = _HTTPFlow
        sys.modules["mitmproxy.http"].Response = _FakeResponse
        if not hasattr(sys.modules["mitmproxy.ctx"], "log"):
            sys.modules["mitmproxy.ctx"].log = MagicMock()

    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_addon(records=None, routing=None, allowed=None):
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    a = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    a.allowed_domains = allowed if allowed is not None else {
        "api.anthropic.com",
        "cc-webui.internal",
        "other.example.com",
    }
    a._records = {
        r["placeholder"]: {
            "name": r["name"],
            "type": r.get("type", "generic"),
            "value": r.get("value", ""),
            "target_hosts": r.get("target_hosts", []),
            "injection": r.get("injection", {}),
            "placeholder": r["placeholder"],
            "scrub": r.get("scrub", {}),
        }
        for r in (records or [])
    }
    a._refresh_locks = {ph: asyncio.Lock() for ph in a._records}
    a._routing = routing if routing is not None else {"hostname_rewrites": {}, "virtual_key": None}
    a._session_id = "test-1427"
    a._session_token = "test-token"
    a._log_file = None
    a._socks5_log_file = None
    a.logger = MagicMock()
    return a


def _flow(host="api.anthropic.com", request_headers=None):
    _install_mitmproxy_stubs()
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    f = flow_cls(host)
    if request_headers is not None:
        f.request.headers = _FakeHeaders(request_headers)
    return f


ROUTING_ENABLED = {
    "hostname_rewrites": {"api.anthropic.com": "cc-webui.internal:4000"},
    "virtual_key": "lc-test-vkey-abc",
}


# ---------------------------------------------------------------------------
# Positive: rewrite applied for api.anthropic.com
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hostname_rewritten_to_cc_webui_internal():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert f.request.host == "cc-webui.internal"


@pytest.mark.asyncio
async def test_port_set_to_4000():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert f.request.port == 4000


@pytest.mark.asyncio
async def test_scheme_set_to_http():
    """LiteLLM is plain HTTP — upstream leg must not attempt TLS."""
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert f.request.scheme == "http"


@pytest.mark.asyncio
async def test_x_api_key_replaced_with_virtual_key():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com", request_headers={"x-api-key": "sk-ant-original"})
    await addon.requestheaders(f)
    assert f.request.headers.get("x-api-key") == "lc-test-vkey-abc"


@pytest.mark.asyncio
async def test_authorization_header_set_to_bearer_virtual_key():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert f.request.headers.get("Authorization") == "Bearer lc-test-vkey-abc"


@pytest.mark.asyncio
async def test_metadata_sentinel_set():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert f.metadata.get("routed_via_litellm") is True


@pytest.mark.asyncio
async def test_rewritten_flow_passes_allowlist():
    """After rewrite, cc-webui.internal is in allowed_domains — flow not denied."""
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert not f.metadata.get("denied")


# ---------------------------------------------------------------------------
# Negative: no routing → flow unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_routing_leaves_host_unchanged():
    addon = _make_addon(routing={"hostname_rewrites": {}, "virtual_key": None})
    f = _flow(host="api.anthropic.com")
    original_host = f.request.host
    await addon.requestheaders(f)
    assert f.request.host == original_host
    assert f.request.scheme == "https"
    assert f.request.port == 443


@pytest.mark.asyncio
async def test_empty_routing_no_sentinel():
    addon = _make_addon(routing={"hostname_rewrites": {}, "virtual_key": None})
    f = _flow(host="api.anthropic.com")
    await addon.requestheaders(f)
    assert not f.metadata.get("routed_via_litellm")


# ---------------------------------------------------------------------------
# Negative: non-Anthropic host → unchanged even when routing is enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_anthropic_host_unchanged():
    addon = _make_addon(routing=ROUTING_ENABLED)
    f = _flow(host="other.example.com")
    await addon.requestheaders(f)
    assert f.request.host == "other.example.com"
    assert f.request.scheme == "https"
    assert not f.metadata.get("routed_via_litellm")


# ---------------------------------------------------------------------------
# REQUIRED: Sentinel guard correctness tests
#
# When routed_via_litellm is set, secret injection must be skipped for the
# rewritten host — even if a secret's target_hosts includes api.anthropic.com
# or cc-webui.internal (which might match after the rewrite).
# ---------------------------------------------------------------------------


PH_ANTHROPIC = "CC_SECRET_antkey_001122"
ANTHROPIC_SECRET_VALUE = "sk-ant-original-secret"


def _make_secret_record(
    placeholder: str = PH_ANTHROPIC,
    value: str = ANTHROPIC_SECRET_VALUE,
    target_hosts: list | None = None,
) -> dict:
    return {
        "placeholder": placeholder,
        "name": "antkey",
        "type": "generic",
        "value": value,
        "target_hosts": target_hosts or ["api.anthropic.com"],
    }


@pytest.mark.asyncio
async def test_sentinel_guard_blocks_secret_injection_when_routed():
    """REQUIRED: When routing is active, secret injection must be skipped.

    Setup:
    - A secret targets api.anthropic.com (the rewrite source)
    - The request has the secret placeholder in x-api-key header
    - Routing is enabled → header should be the virtual key, not the secret

    After requestheaders(), x-api-key must equal the virtual key, NOT the
    resolved secret value.
    """
    record = _make_secret_record(
        placeholder=PH_ANTHROPIC,
        value=ANTHROPIC_SECRET_VALUE,
        target_hosts=["api.anthropic.com", "cc-webui.internal"],
    )
    addon = _make_addon(records=[record], routing=ROUTING_ENABLED)
    f = _flow(
        host="api.anthropic.com",
        request_headers={"x-api-key": PH_ANTHROPIC},
    )

    await addon.requestheaders(f)

    # Virtual key must be present — not the secret value
    assert f.request.headers.get("x-api-key") == "lc-test-vkey-abc"
    assert ANTHROPIC_SECRET_VALUE not in f.request.headers.get("x-api-key", "")
    assert f.metadata.get("routed_via_litellm") is True


@pytest.mark.asyncio
async def test_sentinel_guard_inverse_routing_disabled_injects_secret():
    """REQUIRED inverse: when routing is disabled, secret IS injected normally.

    Same secret setup but with empty routing — the placeholder in x-api-key
    must be replaced with the real secret value by the injection loop.
    """
    record = _make_secret_record(
        placeholder=PH_ANTHROPIC,
        value=ANTHROPIC_SECRET_VALUE,
        target_hosts=["api.anthropic.com"],
    )
    addon = _make_addon(
        records=[record],
        routing={"hostname_rewrites": {}, "virtual_key": None},
    )
    f = _flow(
        host="api.anthropic.com",
        request_headers={"x-api-key": PH_ANTHROPIC},
    )

    await addon.requestheaders(f)

    # Secret must be injected — no routing to intercept it
    assert f.request.headers.get("x-api-key") == ANTHROPIC_SECRET_VALUE
    assert not f.metadata.get("routed_via_litellm")
