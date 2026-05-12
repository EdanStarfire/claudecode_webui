"""
Regression tests for issue #1399 — move header substitution + OAuth refresh
to requestheaders() so streamed requests get the substituted Authorization header.

Five invariants established by the fix:

1. OAuth refresh fires in requestheaders(), not request().
2. request() does NOT retrigger OAuth refresh.
3. Streamed-body simulation: requestheaders() injects headers; request() no-ops cleanly.
4. Buffered-body case: both header and body placeholders are replaced across the two hooks.
5. Denied host short-circuits in requestheaders() before any injection runs.
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal MultiDictView-compatible query stub (reused from test_1245 pattern)
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


class _FakeRequest:
    __slots__ = (
        "pretty_host", "path", "method", "scheme", "port",
        "headers", "query", "url", "content",
    )

    def __init__(self, host="api.anthropic.com", headers=None, content=None):
        self.pretty_host = host
        self.path = "/v1/messages"
        self.method = "POST"
        self.scheme = "https"
        self.port = 443
        self.url = f"https://{host}/v1/messages"
        self.headers = dict(headers or {})
        self.query = _FakeQuery()
        self.content = content


# ---------------------------------------------------------------------------
# mitmproxy stubs + addon loader
# ---------------------------------------------------------------------------

def _install_mitmproxy_stubs():
    """Install minimal mitmproxy stubs if not already present, then (re)load addon.

    Installs a concrete _Response class (not a MagicMock) so status_code assertions
    work even when running after test_proxy_addon_1175.py which installs a MagicMock
    Response. We force-set mitmproxy.http.Response so any module loaded afterward
    gets the real stub.
    """
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
        mitmproxy_http.Response = _Response
        mitmproxy_ctx.log = MagicMock()
        mitmproxy_tcp.TCPFlow = MagicMock

        sys.modules["mitmproxy"] = mitmproxy_pkg
        sys.modules["mitmproxy.http"] = mitmproxy_http
        sys.modules["mitmproxy.ctx"] = mitmproxy_ctx
        sys.modules["mitmproxy.tcp"] = mitmproxy_tcp
    else:
        # mitmproxy already installed — ensure HTTPFlow and Response are our stubs
        # (test_proxy_addon_1175.py installs Response = MagicMock(); override it).
        sys.modules["mitmproxy.http"].HTTPFlow = _HTTPFlow
        sys.modules["mitmproxy.http"].Response = _Response
        if not hasattr(sys.modules["mitmproxy.ctx"], "log"):
            sys.modules["mitmproxy.ctx"].log = MagicMock()

    # Always reload the addon module so it picks up the updated stubs.
    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_addon(records, allowed=None):
    """Build a ProxyAddon with pre-populated _records. Returns (addon, addon_mod)."""
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod  # noqa: PLC0415

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    if allowed is None:
        allowed = {"api.anthropic.com"}
    f.allowed_domains = allowed
    f._records = {
        r["placeholder"]: {
            "name": r["name"],
            "type": r.get("type", "bearer"),
            "value": r["value"],
            "target_hosts": r.get("target_hosts", []),
            "refresh": r.get("refresh", {}),
        }
        for r in records
    }
    f._refresh_locks = {ph: asyncio.Lock() for ph in f._records}
    f._session_id = "test-1399"
    f._session_token = "test-token"
    f._log_file = None
    f.logger = MagicMock()
    return f, addon_mod


def _flow(host="api.anthropic.com", headers=None, content=None):
    """Build a flow WITHOUT calling _install_mitmproxy_stubs (avoids module invalidation)."""
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    flow = flow_cls(host)
    flow.request.headers = dict(headers or {})
    flow.request.content = content
    return flow


# ---------------------------------------------------------------------------
# Test 1 — OAuth refresh fires in requestheaders(), not request()
# ---------------------------------------------------------------------------

PH = "CC_SECRET_oauth_1399_abcd1234"
EXPIRED_REFRESH = {
    "token_url": "https://auth.example.com/token",
    "expires_at": "2000-01-01T00:00:00Z",   # always in the past
    "client_id": "client-id",
    "refresh_token_secret_name": "refresh_tok",
    "buffer_seconds": 60,
}


@pytest.mark.asyncio
async def test_issue_1399_oauth_refresh_fires_in_requestheaders():
    """OAuth refresh must occur in requestheaders(), before any upstream forwarding."""
    addon, addon_mod = _make_addon([{
        "placeholder": PH,
        "name": "oauth_secret",
        "type": "oauth2",
        "value": "old-access-token",
        "refresh": EXPIRED_REFRESH,
    }])
    # Inject refresh_tok partner record
    addon._records["CC_SECRET_refresh_tok"] = {
        "name": "refresh_tok", "type": "bearer", "value": "my-refresh-token",
    }
    addon._refresh_locks["CC_SECRET_refresh_tok"] = asyncio.Lock()

    flow = _flow("api.anthropic.com", {"Authorization": f"Bearer {PH}"})

    new_token = "new-access-token-from-refresh"
    fake_updates = {"value": new_token}

    with patch.object(addon_mod, "_do_refresh", new=AsyncMock(return_value=fake_updates)) as mock_refresh:
        with patch.object(addon, "_patch_secret", new=AsyncMock()):
            await addon.requestheaders(flow)

    mock_refresh.assert_called_once()
    assert flow.request.headers["Authorization"] == f"Bearer {new_token}"
    assert flow.metadata.get("credential_used") == "oauth_secret"


# ---------------------------------------------------------------------------
# Test 2 — request() does NOT retrigger OAuth refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1399_request_does_not_retrigger_oauth_refresh():
    """After requestheaders(), calling request() must not call _do_refresh again."""
    addon, addon_mod = _make_addon([{
        "placeholder": PH,
        "name": "oauth_secret",
        "type": "oauth2",
        "value": "old-access-token",
        "refresh": EXPIRED_REFRESH,
    }])
    addon._records["CC_SECRET_refresh_tok"] = {
        "name": "refresh_tok", "type": "bearer", "value": "my-refresh-token",
    }
    addon._refresh_locks["CC_SECRET_refresh_tok"] = asyncio.Lock()

    flow = _flow("api.anthropic.com", {"Authorization": f"Bearer {PH}"})

    fake_updates = {"value": "new-access-token"}

    with patch.object(addon_mod, "_do_refresh", new=AsyncMock(return_value=fake_updates)) as mock_refresh:
        with patch.object(addon, "_patch_secret", new=AsyncMock()):
            await addon.requestheaders(flow)
            await addon.request(flow)

    assert mock_refresh.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — Streamed-body simulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1399_streamed_body_header_injected_request_noop():
    """Simulates mitmproxy stream_large_bodies: content is None.

    requestheaders() must inject the Authorization header.
    request() must not raise and must not re-mutate the header.
    """
    ph = "CC_SECRET_bearer_1399_abcd1234"
    addon, _ = _make_addon([{
        "placeholder": ph,
        "name": "anthropic_key",
        "type": "bearer",
        "value": "sk-ant-real",
        "target_hosts": ["api.anthropic.com"],
    }])

    # content=None simulates streaming (mitmproxy hasn't buffered the body yet)
    flow = _flow("api.anthropic.com", headers={"Authorization": f"Bearer {ph}"}, content=None)

    await addon.requestheaders(flow)

    assert flow.request.headers["Authorization"] == "Bearer sk-ant-real"
    assert flow.metadata.get("credential_used") == "anthropic_key"

    # request() should complete without error even with content=None
    await addon.request(flow)

    # Header must not have been re-mutated
    assert flow.request.headers["Authorization"] == "Bearer sk-ant-real"


# ---------------------------------------------------------------------------
# Test 4 — Buffered-body case: both header and body placeholders replaced
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1399_buffered_body_header_and_body_both_replaced():
    """Both header and body placeholders are replaced across the two hooks."""
    ph = "CC_SECRET_generic_1399_abcd1234"
    value = "real-generic-value"
    addon, _ = _make_addon([{
        "placeholder": ph,
        "name": "generic_secret",
        "type": "generic",
        "value": value,
        "target_hosts": [],
    }])

    body = f'{{"key": "{ph}"}}'.encode()
    flow = _flow(
        "api.anthropic.com",
        headers={"Authorization": f"Bearer {ph}"},
        content=body,
    )

    await addon.requestheaders(flow)
    # Header replaced in requestheaders()
    assert flow.request.headers["Authorization"] == f"Bearer {value}"

    await addon.request(flow)
    # Body replaced in request()
    assert ph.encode() not in flow.request.content
    assert value.encode() in flow.request.content


# ---------------------------------------------------------------------------
# Test 5 — Denied host short-circuits in requestheaders() before injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issue_1399_denied_host_short_circuits_in_requestheaders():
    """Non-allowlisted host must be blocked in requestheaders(); injection must not run."""
    ph = "CC_SECRET_bearer_1399_abcd1234"
    addon, addon_mod = _make_addon(
        records=[{
            "placeholder": ph,
            "name": "anthropic_key",
            "type": "bearer",
            "value": "sk-ant-real",
            "target_hosts": [],
        }],
        allowed={"api.anthropic.com"},   # evil.example.com is NOT in allowlist
    )

    flow = _flow("evil.example.com", headers={"Authorization": f"Bearer {ph}"})

    header_dispatch_mock = {k: MagicMock(return_value=False) for k in addon_mod._INJECT_HEADERS_DISPATCH}

    with patch.object(addon_mod, "_INJECT_HEADERS_DISPATCH", header_dispatch_mock):
        await addon.requestheaders(flow)

    assert flow.metadata.get("denied") is True
    # flow.response is synthesised as a 403 by the addon
    assert flow.response is not None
    assert flow.response.status_code == 403
    # No injection helpers should have been called
    for mock_fn in header_dispatch_mock.values():
        mock_fn.assert_not_called()
    # Header must remain as the raw placeholder
    assert flow.request.headers["Authorization"] == f"Bearer {ph}"
