"""
Regression tests for issue #1428.

Bug A: _make_chunk_filter mid-stream "hold" path returned b"" which mitmproxy's
       HTTP/1.1 chunked-transfer layer interprets as end-of-stream. Fix: return [].

Bug B: stream filters installed on Content-Encoding: gzip/zstd/br responses/requests
       cause ZlibError in the consumer because the raw compressed bytes are fed to the
       filter verbatim. Fix: bail out before stream install for any non-identity encoding.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stubs and loader (same pattern as test_proxy_addon_1400.py)
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
        "pretty_host", "path", "method", "scheme", "port",
        "headers", "query", "url", "content", "stream",
    )

    def __init__(self, host="api.anthropic.com", headers=None, content=None):
        self.pretty_host = host
        self.path = "/v1/messages"
        self.method = "POST"
        self.scheme = "https"
        self.port = 443
        self.url = f"https://{host}/v1/messages"
        self.headers = _FakeHeaders(headers or {})
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


def _make_addon(records, allowed=None):
    import asyncio
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    a = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    a.allowed_domains = allowed if allowed is not None else {"api.anthropic.com", "api.example.com"}
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
        for r in records
    }
    a._refresh_locks = {ph: asyncio.Lock() for ph in a._records}
    a._session_id = "test-1428"
    a._session_token = "test-token"
    a._log_file = None
    a._socks5_log_file = None
    a.logger = MagicMock()
    return a


def _flow(host="api.anthropic.com", request_headers=None, response_headers=None, response_content=b""):
    _install_mitmproxy_stubs()
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    f = flow_cls(host)
    if request_headers is not None:
        f.request.headers = _FakeHeaders(request_headers)
    if response_headers is not None or response_content is not None:
        f.response = _FakeResponse(
            headers=response_headers or {"content-type": "application/json"},
            content=response_content or b"",
        )
    return f


PH = "CC_SECRET_mytoken_abcd1234"
VALUE = "sk-ant-real-token-xyz"


# ---------------------------------------------------------------------------
# Bug A regression tests — _make_chunk_filter carry-hold returns []
# ---------------------------------------------------------------------------

class TestBugAChunkFilterHoldPath:
    def setup_method(self):
        _install_mitmproxy_stubs()

    def _filter(self, pairs):
        from src.docker.proxy.addon import _make_chunk_filter
        return _make_chunk_filter(pairs)

    def test_bug_a_carry_only_chunk_returns_empty_iterable(self):
        """Bug A: chunk smaller than overlap window must return an empty iterable, not b"".

        mitmproxy wraps non-bytes returns via `if isinstance(chunks, bytes): chunks = [chunks]`
        so returning b"" (zero-length bytes) is a valid bytes object and triggers the
        HTTP/1.1 chunked-transfer end-of-stream terminator (0\\r\\n\\r\\n).
        Returning [] produces zero SendHttp events — correct hold behaviour.
        """
        needle = b"A" * 32  # 32-byte needle → overlap = 31
        f = self._filter([(needle, b"REPLACED")])
        # Feed a 5-byte chunk — fits entirely in the overlap window (split_at <= 0)
        result = f(b"AAAAA")
        # Must NOT be b"" — that's the bug. Must be an iterable with no bytes content.
        assert result != b"", "Bug A: returning b'' triggers end-of-stream in HTTP/1.1 chunked mode"
        # Specifically, we expect []
        assert result == []

    def test_bug_a_carry_flushes_on_subsequent_chunk(self):
        """Bug A: after holding bytes in carry, subsequent chunks flush correctly — no data loss."""
        needle = b"SECRETTOKEN"  # 11 bytes → overlap = 10
        replacement = b"REDACTED"
        f = self._filter([(needle, replacement)])

        # Feed two small chunks that together contain the needle
        chunk1 = b"prefix_SECR"  # 11 bytes; split_at = 11 - 10 = 1; carry holds last 10 bytes
        chunk2 = b"ETTOKEN_suffix"

        out1 = f(chunk1)
        out2 = f(chunk2)
        flush = f(b"")

        combined = out1 if isinstance(out1, bytes) else b"".join(out1)
        combined += out2 if isinstance(out2, bytes) else b"".join(out2)
        combined += flush if isinstance(flush, bytes) else b"".join(flush)

        # No data loss: all non-needle bytes must be present
        assert b"prefix_" in combined
        assert b"_suffix" in combined
        # Needle must be replaced
        assert needle not in combined
        assert replacement in combined


# ---------------------------------------------------------------------------
# Bug B regression tests — Content-Encoding bail-out
# ---------------------------------------------------------------------------

class TestBugBResponseheadersContentEncoding:
    def setup_method(self):
        _install_mitmproxy_stubs()

    def _addon(self):
        return _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])

    def test_bug_b_responseheaders_skips_gzip_encoding(self):
        """Bug B: gzip-encoded response must not have stream filter installed."""
        addon = self._addon()
        f = _flow(response_headers={"content-type": "application/json", "content-encoding": "gzip"})
        addon.responseheaders(f)
        assert f.response.stream is None, "Stream must not be installed for gzip-encoded response"

    def test_bug_b_responseheaders_skips_zstd_encoding(self):
        """Bug B: zstd-encoded response must not have stream filter installed."""
        addon = self._addon()
        f = _flow(response_headers={"content-type": "application/json", "content-encoding": "zstd"})
        addon.responseheaders(f)
        assert f.response.stream is None

    def test_bug_b_responseheaders_skips_brotli_encoding(self):
        """Bug B: br (brotli) encoded response must not have stream filter installed."""
        addon = self._addon()
        f = _flow(response_headers={"content-type": "application/json", "content-encoding": "br"})
        addon.responseheaders(f)
        assert f.response.stream is None

    def test_bug_b_responseheaders_identity_does_not_skip(self):
        """RFC 9110: Content-Encoding: identity is a no-op — stream filter SHOULD be installed."""
        addon = self._addon()
        f = _flow(response_headers={"content-type": "application/json", "content-encoding": "identity"})
        addon.responseheaders(f)
        assert callable(f.response.stream), "identity encoding must not suppress stream install"

    def test_bug_b_responseheaders_missing_header_does_not_skip(self):
        """No Content-Encoding header → stream filter SHOULD be installed (normal path)."""
        addon = self._addon()
        f = _flow(response_headers={"content-type": "application/json"})
        addon.responseheaders(f)
        assert callable(f.response.stream), "Missing Content-Encoding must not suppress stream install"


@pytest.mark.asyncio
class TestBugBRequestheadersContentEncoding:
    async def test_bug_b_requestheaders_skips_gzip_encoding(self):
        """Bug B: gzip-encoded request body must not have injection stream filter installed."""
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(request_headers={"content-encoding": "gzip", "content-type": "application/json"})
        await addon.requestheaders(f)
        assert f.request.stream is None, "Stream must not be installed for gzip-encoded request body"
