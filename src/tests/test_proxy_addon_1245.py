"""
Regression tests for issue #1245 — proxy addon crashes on flow.request.query_string.

mitmproxy's Request class does not expose query_string; the correct API is
flow.request.query (MultiDictView). The old code raised AttributeError on every
request, silently discarding all credential injection.

We use _FakeRequest with __slots__ (no query_string slot) so that accessing the
absent attribute raises AttributeError provably. The previous MagicMock()-based
fixture in test_proxy_addon_1216.py auto-creates any attribute, which masks this
class of bug.
"""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal MultiDictView-compatible query stub
# ---------------------------------------------------------------------------

class _FakeQuery:
    """List-of-tuples store with the MultiDictView interface used by the fix."""

    def __init__(self, pairs: list[tuple[str, str]] | None = None):
        self._pairs: list[tuple[str, str]] = list(pairs or [])

    def items(self):
        return list(self._pairs)

    def __setitem__(self, key: str, value: str) -> None:
        for i, (k, _) in enumerate(self._pairs):
            if k == key:
                self._pairs[i] = (key, value)
                return
        self._pairs.append((key, value))

    def __getitem__(self, key: str) -> str:
        for k, v in self._pairs:
            if k == key:
                return v
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return any(k == key for k, _ in self._pairs)


# ---------------------------------------------------------------------------
# Typed request stub — __slots__ provably forbids query_string
# ---------------------------------------------------------------------------

class _FakeRequest:
    __slots__ = (
        "pretty_host", "path", "method", "scheme", "port",
        "headers", "query", "url", "content",
    )
    # no query_string — accessing it raises AttributeError

    def __init__(
        self,
        host: str = "api.anthropic.com",
        headers: dict | None = None,
        query: list[tuple[str, str]] | None = None,
        content: bytes | None = None,
    ):
        self.pretty_host = host
        self.path = "/v1/messages"
        self.method = "POST"
        self.scheme = "https"
        self.port = 443
        self.url = f"https://{host}/v1/messages"
        self.headers = dict(headers or {})
        self.query = _FakeQuery(query or [])
        self.content = content


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
                self.request = _FakeRequest(host=host)
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
    """Build a ProxyAddon with pre-populated _records."""
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)

    if allowed is None:
        allowed = {"api.anthropic.com", "api.example.com"}
    f.allowed_domains = allowed

    f._records = {
        r["placeholder"]: {
            "name": r["name"],
            "type": r.get("type", "generic"),
            "value": r["value"],
            "target_hosts": r.get("target_hosts", []),
            "injection": r.get("injection", {}),
            "placeholder": r["placeholder"],
        }
        for r in records
    }
    f._refresh_locks = {ph: asyncio.Lock() for ph in f._records}
    f._session_id = "test-1245"
    f._session_token = "test-token"
    f._log_file = None
    f._routing = {"hostname_rewrites": {}, "virtual_key": None, "model_map": {}, "default_model": None}
    f.logger = MagicMock()
    return f


def _flow(
    host: str = "api.anthropic.com",
    headers: dict | None = None,
    query: list[tuple[str, str]] | None = None,
    content: bytes | None = None,
):
    _install_mitmproxy_stubs()
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    flow = flow_cls(host)
    flow.request.headers = dict(headers or {})
    flow.request.query = _FakeQuery(query or [])
    if content is not None:
        flow.request.content = content
    return flow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

PH = "CC_SECRET_oauth_token_abcd1234"
VALUE = "sk-ant-oat01-real-token"


def test_1245_inject_generic_substitutes_query_value():
    """Placeholder in a query value is replaced; no AttributeError."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_generic

    record = {"name": "oauth_token", "type": "generic", "value": VALUE}
    flow = _flow(query=[("api_key", PH), ("other", "unchanged")])

    result = _inject_generic(flow, record, PH)

    assert result is True
    assert flow.request.query["api_key"] == VALUE
    assert flow.request.query["other"] == "unchanged"


def test_1245_inject_generic_substitutes_authorization_header():
    """Placeholder in Authorization header is replaced (regression: header path)."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_generic

    record = {"name": "oauth_token", "type": "generic", "value": VALUE}
    flow = _flow(headers={"Authorization": f"Bearer {PH}"})

    result = _inject_generic(flow, record, PH)

    assert result is True
    assert flow.request.headers["Authorization"] == f"Bearer {VALUE}"


def test_1245_inject_generic_substitutes_body():
    """Placeholder in request body bytes is replaced (regression: body path)."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_generic

    record = {"name": "oauth_token", "type": "generic", "value": VALUE}
    body = f'{{"token": "{PH}"}}'.encode()
    flow = _flow(content=body)

    result = _inject_generic(flow, record, PH)

    assert result is True
    assert PH.encode() not in flow.request.content
    assert VALUE.encode() in flow.request.content


def test_1245_inject_api_key_query_param_location():
    """api_key record with injection.location=query_param substitutes query value."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_api_key

    record = {
        "name": "api_key",
        "type": "api_key",
        "value": "real-api-key-123",
        "injection": {"location": "query_param"},
    }
    ph = "CC_SECRET_api_key_efgh5678"
    flow = _flow(query=[("key", ph), ("format", "json")])

    result = _inject_api_key(flow, record, ph)

    assert result is True
    assert flow.request.query["key"] == "real-api-key-123"
    assert flow.request.query["format"] == "json"


def test_1245_inject_api_key_header_location_default():
    """api_key record with default location injects into header (regression)."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_api_key

    record = {
        "name": "api_key",
        "type": "api_key",
        "value": "real-api-key-123",
        "injection": {"location": "header", "prefix": "ApiKey"},
    }
    ph = "CC_SECRET_api_key_efgh5678"
    flow = _flow(headers={"X-API-Key": ph})

    result = _inject_api_key(flow, record, ph)

    assert result is True
    assert flow.request.headers["X-API-Key"] == "ApiKey real-api-key-123"


@pytest.mark.asyncio
async def test_1245_request_end_to_end_sets_credential_used():
    """Full ProxyAddon.requestheaders() with generic secret + placeholder in Authorization.

    Pre-fix: _inject_generic raised AttributeError on query_string, the exception
    propagated to request(), and credential_used was never set (remained None).
    Post-fix: injection succeeds and credential_used equals the record name.
    """
    ph = "CC_SECRET_oauth_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "oauth_token",
        "type": "generic",
        "value": VALUE,
        "target_hosts": [],
    }])
    flow = _flow(
        host="api.anthropic.com",
        headers={"Authorization": f"Bearer {ph}"},
    )
    await addon.requestheaders(flow)

    assert flow.request.headers["Authorization"] == f"Bearer {VALUE}"
    assert flow.metadata["credential_used"] == "oauth_token"


@pytest.mark.asyncio
async def test_1245_request_body_substitution_via_request_hook():
    """Body placeholder is replaced by request() (body phase) after requestheaders() sets metadata."""
    ph = "CC_SECRET_oauth_abcd1234"
    addon = _make_addon([{
        "placeholder": ph,
        "name": "oauth_token",
        "type": "generic",
        "value": VALUE,
        "target_hosts": [],
    }])
    body = f'{{"token": "{ph}"}}'.encode()
    flow = _flow(
        host="api.anthropic.com",
        headers={"Authorization": "Bearer already-replaced"},
        content=body,
    )
    # Simulate requestheaders() already having run (sets metadata)
    await addon.requestheaders(flow)
    # request() should replace the body placeholder
    await addon.request(flow)

    assert ph.encode() not in flow.request.content
    assert VALUE.encode() in flow.request.content


def test_1245_inject_generic_empty_query_no_crash():
    """Flow with no query parameters: _inject_generic returns False, no crash."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_generic

    record = {"name": "oauth_token", "type": "generic", "value": VALUE}
    flow = _flow()  # no query, no headers, no content

    result = _inject_generic(flow, record, PH)

    assert result is False


def test_1245_inject_generic_placeholder_in_header_and_body_both_substituted():
    """Placeholder in both header and body: both paths modify and function returns True."""
    _install_mitmproxy_stubs()
    from src.docker.proxy.addon import _inject_generic

    record = {"name": "oauth_token", "type": "generic", "value": VALUE}
    flow = _flow(
        headers={"Authorization": f"Bearer {PH}"},
        content=f'{{"token": "{PH}"}}'.encode(),
    )

    result = _inject_generic(flow, record, PH)

    assert result is True
    assert flow.request.headers["Authorization"] == f"Bearer {VALUE}"
    assert VALUE.encode() in flow.request.content
    assert PH.encode() not in flow.request.content
