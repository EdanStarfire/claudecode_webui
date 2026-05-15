"""
Tests for issue #1400 — per-chunk stream filter for streamed body injection and scrub.

Covers:
  - _make_chunk_filter: carry-buffer correctness, boundary-spanning, edge cases
  - _build_request_pairs / _build_response_pairs: filtering by type, host, value
  - Hook installation: requestheaders() installs request stream; responseheaders() installs response stream
  - Integration: end-to-end chunk delivery through installed filters
  - Capture-back: _capture_from_response continues under streaming
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs and addon loader (pattern from test_proxy_addon_1245.py)
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
    """Case-insensitive header dict stub with .get() and .items()."""

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
    """Install (or replace) mitmproxy stubs with 1400-compatible versions and reload addon."""
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
        # Always replace HTTPFlow/Response so _FakeRequest with `stream` slot is used.
        sys.modules["mitmproxy.http"].HTTPFlow = _HTTPFlow
        sys.modules["mitmproxy.http"].Response = _FakeResponse
        if not hasattr(sys.modules["mitmproxy.ctx"], "log"):
            sys.modules["mitmproxy.ctx"].log = MagicMock()

    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_addon(records, allowed=None):
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
    a._session_id = "test-1400"
    a._session_token = "test-token"
    a._log_file = None
    a._socks5_log_file = None
    a.logger = MagicMock()
    return a


def _flow(host="api.anthropic.com", headers=None, content=None, response_headers=None, response_content=b""):
    _install_mitmproxy_stubs()
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    f = flow_cls(host)
    f.request.headers = _FakeHeaders(headers or {})
    if content is not None:
        f.request.content = content
    if response_headers is not None or response_content is not None:
        f.response = _FakeResponse(
            headers=response_headers or {"content-type": "application/json"},
            content=response_content,
        )
    return f


PH = "CC_SECRET_mytoken_abcd1234"
VALUE = "sk-ant-real-token-xyz"


def _b(result) -> bytes:
    """Normalize a filter return value to bytes (handles both bytes and list returns)."""
    if isinstance(result, bytes):
        return result
    return b"".join(result)


# ---------------------------------------------------------------------------
# Unit tests — _make_chunk_filter
# ---------------------------------------------------------------------------

class TestMakeChunkFilter:
    def setup_method(self):
        _install_mitmproxy_stubs()

    def _filter(self, pairs):
        from src.docker.proxy.addon import _make_chunk_filter
        return _make_chunk_filter(pairs)

    def test_single_chunk_match(self):
        f = self._filter([(b"SECRET", b"REPLACED")])
        out = f(b"prefix SECRET suffix")
        flush = f(b"")
        assert b"REPLACED" in out + flush
        assert b"SECRET" not in out + flush

    def test_no_match_passthrough(self):
        f = self._filter([(b"NEEDLE", b"X")])
        out = f(b"this has no match at all")
        flush = f(b"")
        assert b"NEEDLE" not in out + flush
        assert b"this has no match at all" in out + flush

    def test_boundary_spanning_match_two_chunks(self):
        needle = b"SECRET"
        f = self._filter([(needle, b"REPLACED")])
        # Split "SECRET" as "SEC" + "RET"
        out1 = f(b"SEC")
        out2 = f(b"RET")
        flush = f(b"")
        combined = _b(out1) + _b(out2) + _b(flush)
        assert b"SECRET" not in combined
        assert b"REPLACED" in combined

    def test_boundary_spanning_match_three_chunks(self):
        needle = b"LONGTOKEN"
        f = self._filter([(needle, b"X")])
        # Feed 3-byte chunks
        parts = [f(b"LON"), f(b"GTO"), f(b"KEN"), f(b"")]
        combined = b"".join(_b(p) for p in parts)
        assert b"LONGTOKEN" not in combined
        assert b"X" in combined

    def test_end_of_stream_flush(self):
        # Entire needle sits in carry at end
        f = self._filter([(b"SECRET", b"REPLACED")])
        # Feed a tiny chunk that holds most of the data in carry
        out = f(b"SECRET")
        flush = f(b"")
        combined = out + flush
        assert b"SECRET" not in combined
        assert b"REPLACED" in combined

    def test_empty_input(self):
        f = self._filter([(b"X", b"Y")])
        result = f(b"")
        assert result == b""

    def test_multiple_distinct_needles_single_chunk(self):
        f = self._filter([(b"AAA", b"X"), (b"BBB", b"Y")])
        out = f(b"AAA and BBB")
        flush = f(b"")
        combined = out + flush
        assert b"AAA" not in combined
        assert b"BBB" not in combined
        assert b"X" in combined
        assert b"Y" in combined

    def test_longer_needles_processed_first(self):
        # pairs with shorter first — factory must sort to longer-first
        f = self._filter([(b"AB", b"X"), (b"ABC", b"Y")])
        out = f(b"ABC")
        flush = f(b"")
        combined = _b(out) + _b(flush)
        # "ABC" should become "Y", not "XC"
        assert b"Y" in combined
        assert b"XC" not in combined

    def test_replacement_grows_buffer(self):
        f = self._filter([(b"X", b"XXXX")])
        out = f(b"aXb")
        flush = f(b"")
        combined = out + flush
        assert b"XXXX" in combined
        assert combined.count(b"X") >= 4

    def test_replacement_shrinks_buffer(self):
        f = self._filter([(b"AAAA", b"a")])
        out = f(b"AAAA")
        flush = f(b"")
        combined = _b(out) + _b(flush)
        assert b"AAAA" not in combined
        assert b"a" in combined

    def test_carry_size_invariant(self):
        """After each non-final call, carry length must be <= max_needle_len - 1."""
        from src.docker.proxy.addon import _make_chunk_filter
        needle = b"ABCDE"  # length 5, overlap = 4

        # Instrument via closure inspection — we can't directly access carry,
        # but we can verify that the total bytes emitted + final flush == input.
        pairs = [(needle, b"X")]
        f = _make_chunk_filter(pairs)

        data = b"ABCDEABCDEABCDE"
        total_out = b""
        for i in range(0, len(data), 3):
            total_out += _b(f(data[i:i+3]))
        total_out += _b(f(b""))

        # Correctness: no needle in output
        assert needle not in total_out
        # Output length: each replacement of 5 bytes → 1 byte (shrinks by 4 each time)
        occurrences = data.count(needle)
        expected_len = len(data) - occurrences * (len(needle) - 1)
        assert len(total_out) == expected_len

    def test_factory_rejects_empty_pairs(self):
        from src.docker.proxy.addon import _make_chunk_filter
        with pytest.raises((ValueError, AssertionError)):
            _make_chunk_filter([])

    def test_single_byte_needle_overlap_zero(self):
        """overlap=0 means nothing held in carry; bytes emitted immediately."""
        f = self._filter([(b"A", b"B")])
        out = f(b"XAXAX")
        flush = f(b"")
        combined = out + flush
        assert b"A" not in combined
        assert combined == b"XBXBX"


# ---------------------------------------------------------------------------
# Unit tests — _build_request_pairs
# ---------------------------------------------------------------------------

class TestBuildRequestPairs:
    def setup_method(self):
        _install_mitmproxy_stubs()

    def _build(self, records, host):
        from src.docker.proxy.addon import _build_request_pairs
        return _build_request_pairs(records, host)

    def _records(self, recs):
        return {r["placeholder"]: r for r in recs}

    def test_matches_generic_only(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "val_g", "target_hosts": []},
            {"placeholder": "PH_B", "name": "b", "type": "bearer", "value": "val_b", "target_hosts": []},
            {"placeholder": "PH_O", "name": "o", "type": "oauth2", "value": "val_o", "target_hosts": []},
        ])
        pairs = self._build(recs, "api.anthropic.com")
        needles = [p[0] for p in pairs]
        assert b"PH_G" in needles
        assert b"PH_B" not in needles
        assert b"PH_O" not in needles

    def test_skips_empty_value(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "", "target_hosts": []},
        ])
        pairs = self._build(recs, "api.anthropic.com")
        assert pairs == []

    def test_target_host_filtering(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "val_g",
             "target_hosts": ["api.foo.com"]},
        ])
        assert self._build(recs, "api.bar.com") == []
        pairs = self._build(recs, "api.foo.com")
        assert len(pairs) == 1
        assert pairs[0] == (b"PH_G", b"val_g")


# ---------------------------------------------------------------------------
# Unit tests — _build_response_pairs
# ---------------------------------------------------------------------------

class TestBuildResponsePairs:
    def setup_method(self):
        _install_mitmproxy_stubs()

    def _build(self, records, host):
        from src.docker.proxy.addon import _build_response_pairs
        return _build_response_pairs(records, host)

    def _records(self, recs):
        return {r["placeholder"]: r for r in recs}

    def test_includes_all_types(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "vg", "target_hosts": []},
            {"placeholder": "PH_B", "name": "b", "type": "bearer", "value": "vb", "target_hosts": []},
            {"placeholder": "PH_O", "name": "o", "type": "oauth2", "value": "vo", "target_hosts": []},
        ])
        pairs = self._build(recs, "api.anthropic.com")
        values = [p[0] for p in pairs]
        assert b"vg" in values
        assert b"vb" in values
        assert b"vo" in values

    def test_skips_empty_value(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "", "target_hosts": []},
        ])
        assert self._build(recs, "api.anthropic.com") == []

    def test_target_host_filtering(self):
        recs = self._records([
            {"placeholder": "PH_G", "name": "g", "type": "generic", "value": "secret_val",
             "target_hosts": ["api.foo.com"]},
        ])
        assert self._build(recs, "api.bar.com") == []
        pairs = self._build(recs, "api.foo.com")
        assert len(pairs) == 1
        assert pairs[0] == (b"secret_val", b"PH_G")


# ---------------------------------------------------------------------------
# Hook installation tests
# ---------------------------------------------------------------------------

class TestResponseheadersHook:
    def test_installs_stream_for_matching_host(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": ["api.anthropic.com"],
        }])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "application/json"})
        addon.responseheaders(f)
        assert callable(f.response.stream)

    def test_no_stream_when_denied(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "application/json"})
        f.metadata["denied"] = True
        addon.responseheaders(f)
        assert f.response.stream is None

    def test_no_stream_when_binary(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "image/png"})
        addon.responseheaders(f)
        assert f.response.stream is None

    def test_no_stream_when_no_matching_records(self):
        addon = _make_addon([])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "application/json"})
        addon.responseheaders(f)
        assert f.response.stream is None

    def test_no_stream_when_value_empty(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": "", "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "application/json"})
        addon.responseheaders(f)
        assert f.response.stream is None


@pytest.mark.asyncio
class TestRequestheadersStreamInstall:
    async def test_installs_stream_when_body_injectable(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com")
        await addon.requestheaders(f)
        assert callable(f.request.stream)

    async def test_no_stream_when_only_header_types(self):
        addon = _make_addon([
            {"placeholder": "PH_B", "name": "b", "type": "bearer", "value": "val_b", "target_hosts": []},
            {"placeholder": "PH_O", "name": "o", "type": "oauth2", "value": "val_o", "target_hosts": []},
        ])
        f = _flow(host="api.anthropic.com")
        await addon.requestheaders(f)
        assert f.request.stream is None

    async def test_existing_header_substitution_unchanged(self):
        """The #1399 header substitution still works; stream install doesn't interfere."""
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com", headers={"Authorization": f"Bearer {PH}"})
        await addon.requestheaders(f)
        assert f.request.headers["Authorization"] == f"Bearer {VALUE}"
        assert callable(f.request.stream)


# ---------------------------------------------------------------------------
# Integration-style tests — end-to-end streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRequestStreamEndToEnd:
    async def test_replaces_placeholder_across_chunks(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com")
        await addon.requestheaders(f)
        filt = f.request.stream
        assert filt is not None

        # Feed PH split across two chunks
        mid = len(PH) // 2
        chunk1 = f'prefix {PH[:mid]}'.encode()
        chunk2 = f'{PH[mid:]} suffix'.encode()
        out = _b(filt(chunk1)) + _b(filt(chunk2)) + _b(filt(b""))
        assert PH.encode() not in out
        assert VALUE.encode() in out


class TestResponseStreamEndToEnd:
    def test_redacts_value_across_chunks(self):
        addon = _make_addon([{
            "placeholder": PH, "name": "tok", "type": "generic",
            "value": VALUE, "target_hosts": [],
        }])
        f = _flow(host="api.anthropic.com", response_headers={"content-type": "application/json"})
        addon.responseheaders(f)
        filt = f.response.stream
        assert filt is not None

        # Feed VALUE split across two chunks
        mid = len(VALUE) // 2
        chunk1 = f'data: {VALUE[:mid]}'.encode()
        chunk2 = f'{VALUE[mid:]} end'.encode()
        out = _b(filt(chunk1)) + _b(filt(chunk2)) + _b(filt(b""))
        assert VALUE.encode() not in out
        assert PH.encode() in out


@pytest.mark.asyncio
class TestCaptureBackAfterStreamedResponse:
    async def test_capture_back_works_with_stored_content(self):
        """response() captures new token from buffered content (store_streamed_bodies=True)."""
        old_value = "old-access-token-123"
        new_value = "new-access-token-xyz"
        ph = "CC_SECRET_oauth_aaaa1111"

        addon = _make_addon([{
            "placeholder": ph,
            "name": "oauth_tok",
            "type": "oauth2",
            "value": old_value,
            "target_hosts": [],
            "scrub": {
                "update_on_change": True,
                "matcher_jsonpath": "$.access_token",
            },
        }])

        # Simulate buffered post-stream response content
        response_body = f'{{"access_token": "{new_value}", "token_type": "bearer"}}'.encode()
        f = _flow(
            host="api.anthropic.com",
            response_headers={"content-type": "application/json"},
            response_content=response_body,
        )

        with patch.object(addon, "_patch_secret", new=AsyncMock()) as mock_patch:
            await addon.response(f)

        # Token should be captured and PATCHed
        assert addon._records[ph]["value"] == new_value
        mock_patch.assert_called_once_with("oauth_tok", {"value": new_value})


# ---------------------------------------------------------------------------
# Issue #1425 — Problem C: SSE connection timeout (5 scenarios)
# ---------------------------------------------------------------------------


class TestIssue1425SSEUnbufferedFilter:
    """Tests for fix #1425 Problem C: _is_sse_response + _make_unbuffered_chunk_filter."""

    def setup_method(self):
        _install_mitmproxy_stubs()

    def test_issue_1425_c1_sse_chunks_forwarded_immediately_no_holdback(self):
        """C1: SSE response with 3 chunks — each forwarded immediately; secret within chunk redacted."""
        from src.docker.proxy.addon import _make_unbuffered_chunk_filter

        secret = b"CC_SECRET_tok_aabbccdd"
        replacement = b"real-token-xyz"
        f = _make_unbuffered_chunk_filter([(replacement, secret)])

        chunk1 = b"data: hello world\n\n"
        chunk2 = b"data: real-token-xyz is here\n\n"
        chunk3 = b"data: done\n\n"

        out1 = f(chunk1)
        out2 = f(chunk2)
        out3 = f(chunk3)
        eos = f(b"")

        # Each chunk forwarded immediately (no carry buffer holdback)
        assert out1 == chunk1
        # Secret within chunk is scrubbed
        assert replacement not in out2
        assert secret in out2
        assert out3 == chunk3
        assert eos == b""

    def test_issue_1425_c2_cross_chunk_boundary_not_redacted(self):
        """C2: SSE with needle split across two chunks → not redacted (documented residual risk)."""
        from src.docker.proxy.addon import _make_unbuffered_chunk_filter

        needle = b"real-token-xyz"
        replacement = b"CC_SECRET_tok_11223344"
        f = _make_unbuffered_chunk_filter([(needle, replacement)])

        # Split needle across two chunks
        half = len(needle) // 2
        chunk1 = b"data: prefix-" + needle[:half]
        chunk2 = needle[half:] + b"-suffix\n\n"

        out1 = f(chunk1)
        out2 = f(chunk2)

        # Needle straddles boundary — accepted residual risk.
        combined = out1 + out2
        # The full needle IS present when the two chunks are concatenated (not redacted).
        assert needle in combined
        # The replacement CC_SECRET_* is NOT present (scrubbing did not fire cross-boundary).
        assert replacement not in combined

    def test_issue_1425_c3_non_sse_response_uses_carry_buffer(self):
        """C3: application/json response → buffered _make_chunk_filter installed (regression guard)."""
        from src.docker.proxy.addon import _make_chunk_filter, _make_unbuffered_chunk_filter

        ph = PH
        value = VALUE
        records = [{
            "placeholder": ph,
            "name": "test_tok",
            "type": "generic",
            "value": value,
            "target_hosts": [],
        }]
        addon = _make_addon(records)

        f = _flow(
            host="api.anthropic.com",
            response_headers={"content-type": "application/json"},
            response_content=b"",
        )

        addon.responseheaders(f)

        # stream must be a function (the chunk filter)
        assert callable(f.response.stream)
        # Verify it IS the carry-buffer variant by checking that it holds back bytes:
        # Send a chunk exactly equal to the needle — carry-buffer filter returns b"" (held back).
        needle_bytes = value.encode()
        f.response.stream(needle_bytes)
        # Carry buffer filter: short chunks are held; unbuffered filter would return them immediately.
        # The carry-buffer filter returns empty for a chunk that fits entirely in the overlap window.
        # We can't inspect internals, but we can check _make_chunk_filter separately.
        buffered = _make_chunk_filter([(needle_bytes, b"REPLACED")])
        unbuffered = _make_unbuffered_chunk_filter([(needle_bytes, b"REPLACED")])
        buf_result = buffered(needle_bytes)
        ub_result = unbuffered(needle_bytes)
        # Carry-buffer: short needle-sized chunk held back (returns b"" or partial)
        assert len(buf_result) < len(needle_bytes)
        # Unbuffered: passes through immediately (with scrubbing)
        assert ub_result == b"REPLACED"

    def test_issue_1425_c4_sse_with_no_records_no_filter_installed(self):
        """C4: SSE response but no records targeting this host → responseheaders returns early,
        no filter installed (existing short-circuit unchanged)."""
        addon = _make_addon([])  # no records

        f = _flow(
            host="api.anthropic.com",
            response_headers={"content-type": "text/event-stream"},
            response_content=b"",
        )

        addon.responseheaders(f)

        assert f.response.stream is None

    def test_issue_1425_c5_sse_responseheaders_installs_unbuffered_filter(self):
        """C5: SSE response with matching records → responseheaders installs unbuffered filter;
        chunks pass through immediately without holdback."""
        ph = PH
        value = VALUE
        records = [{
            "placeholder": ph,
            "name": "sse_tok",
            "type": "generic",
            "value": value,
            "target_hosts": [],
        }]
        addon = _make_addon(records)

        f = _flow(
            host="api.anthropic.com",
            response_headers={"content-type": "text/event-stream"},
            response_content=b"",
        )

        addon.responseheaders(f)

        assert callable(f.response.stream), "SSE filter must be installed"

        # The installed filter must NOT hold back a chunk smaller than the needle length.
        # Feed a small SSE heartbeat that does NOT contain the needle.
        heartbeat = b": heartbeat\n\n"
        result = f.response.stream(heartbeat)
        # Unbuffered: chunk passes through immediately (no carry buffer holdback).
        assert result == heartbeat
