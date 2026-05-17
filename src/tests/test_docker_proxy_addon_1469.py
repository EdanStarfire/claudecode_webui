"""
Unit tests for _rewrite_model_in_body() — issue #1469 per-tier model routing.

Matrix:
- tier match: body {"model": "claude-opus-4-7"} → opus alias
- body {"model": "haiku"} → haiku alias
- body {"model": "opusplan"} → default alias (word-boundary regex prevents opus match)
- body {"model": "claude-sonnet-4-6"} → sonnet alias
- body with no "model" field → no-op
- body absent (None content) → no-op
- body gzip-encoded → no-op
- model_map={} + default_model=<alias> → unconditional rewrite to default_model
- default_model=None → no-op (no catalog routing)
- routed_via_litellm not set → no-op (request() short-circuits)
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# mitmproxy stubs (shared pattern with other proxy addon tests)
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

    def __init__(self, host="api.anthropic.com", content=None, headers=None):
        self.pretty_host = host
        self.host = host
        self.path = "/v1/messages"
        self.method = "POST"
        self.scheme = "https"
        self.port = 443
        self.url = f"https://{host}/v1/messages"
        self.headers = _FakeHeaders(headers or {
            "content-type": "application/json",
            "x-api-key": "lc-vkey",
        })
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
        def __init__(self, host="api.anthropic.com", content=None, headers=None):
            self.request = _FakeRequest(host=host, content=content, headers=headers)
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


TIER_MODEL_MAP = {
    "haiku": "bedrock--haiku-fast",
    "sonnet": "bedrock--sonnet-balanced",
    "opus": "bedrock--opus-power",
    "default": "bedrock--sonnet-balanced",
}

ROUTING_WITH_TIERS = {
    "hostname_rewrites": {"api.anthropic.com": "cc-webui.internal:4000"},
    "virtual_key": "lc-vkey",
    "model_map": TIER_MODEL_MAP,
    "default_model": TIER_MODEL_MAP["default"],
}

ROUTING_SINGLE_MODEL = {
    "hostname_rewrites": {"api.anthropic.com": "cc-webui.internal:4000"},
    "virtual_key": "lc-vkey",
    "model_map": {},
    "default_model": "bedrock--sonnet-balanced",
}

ROUTING_NO_CATALOG = {
    "hostname_rewrites": {},
    "virtual_key": None,
    "model_map": {},
    "default_model": None,
}


def _make_addon(routing=None):
    _install_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    a = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    a.allowed_domains = {"api.anthropic.com", "cc-webui.internal"}
    a._records = {}
    a._refresh_locks = {}
    a._routing = routing if routing is not None else ROUTING_WITH_TIERS
    a._session_id = "test-1469"
    a._session_token = "test-token"
    a._log_file = None
    a._socks5_log_file = None
    a.logger = MagicMock()
    return a


def _flow(body_dict=None, metadata=None, headers=None, content_override=None):
    _install_mitmproxy_stubs()
    flow_cls = sys.modules["mitmproxy.http"].HTTPFlow
    if content_override is not None:
        content = content_override
    elif body_dict is not None:
        content = json.dumps(body_dict).encode()
    else:
        content = None
    f = flow_cls(content=content, headers=headers)
    if metadata:
        f.metadata.update(metadata)
    return f


# ---------------------------------------------------------------------------
# Tier routing: model_map populated
# ---------------------------------------------------------------------------


def test_issue_1469_opus_body_rewrites_to_opus_alias():
    """{'model': 'claude-opus-4-7'} → opus tier alias."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "claude-opus-4-7", "messages": []}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--opus-power"
    assert f.metadata["model_rewrite"] == ("claude-opus-4-7", "bedrock--opus-power")


def test_issue_1469_haiku_body_rewrites_to_haiku_alias():
    """{'model': 'haiku'} → haiku tier alias."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "haiku"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--haiku-fast"


def test_issue_1469_sonnet_canonical_id_rewrites_to_sonnet_alias():
    """{'model': 'claude-sonnet-4-6'} → sonnet tier alias."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "claude-sonnet-4-6"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--sonnet-balanced"


def test_issue_1469_opusplan_does_not_match_opus_tier():
    """{'model': 'opusplan'} → default alias (word-boundary prevents opus match)."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "opusplan"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    # opusplan has no \b boundary after "opus" since 'p' is a word char → falls to default
    assert body["model"] == "bedrock--sonnet-balanced"


def test_issue_1469_unrecognised_model_falls_back_to_default_tier():
    """{'model': 'claude-unknown-99'} → default alias."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "claude-unknown-99"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--sonnet-balanced"


def test_issue_1469_same_model_no_rewrite():
    """When original already equals rewritten alias, content is unchanged."""
    routing = {**ROUTING_SINGLE_MODEL}
    routing["default_model"] = "bedrock--sonnet-balanced"
    addon = _make_addon(routing=routing)
    original_bytes = json.dumps({"model": "bedrock--sonnet-balanced"}).encode()
    f = _flow(content_override=original_bytes, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    assert f.request.content == original_bytes
    assert "model_rewrite" not in f.metadata


# ---------------------------------------------------------------------------
# Single-model (no tier map): unconditional rewrite
# ---------------------------------------------------------------------------


def test_issue_1469_single_model_rewrites_unconditionally():
    """model_map={}, default_model=<alias> → rewrite any model string."""
    addon = _make_addon(routing=ROUTING_SINGLE_MODEL)
    f = _flow(body_dict={"model": "claude-opus-4-7"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--sonnet-balanced"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_issue_1469_no_default_model_noop():
    """default_model=None → no rewrite (no catalog routing configured)."""
    addon = _make_addon(routing=ROUTING_NO_CATALOG)
    f = _flow(body_dict={"model": "claude-opus-4-7"}, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    body = json.loads(f.request.content)
    assert body["model"] == "claude-opus-4-7"
    assert "model_rewrite" not in f.metadata


def test_issue_1469_none_content_noop():
    """flow.request.content is None (streaming body) → no-op."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(content_override=None, metadata={"routed_via_litellm": True})
    # Should not raise, content stays None
    addon._rewrite_model_in_body(f)
    assert f.request.content is None


def test_issue_1469_gzip_encoded_noop():
    """Gzip-encoded body → no-op (accepted limitation)."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(
        content_override=b"\x1f\x8b...",
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        metadata={"routed_via_litellm": True},
    )
    addon._rewrite_model_in_body(f)
    # Content unchanged, no model_rewrite metadata
    assert f.request.content == b"\x1f\x8b..."
    assert "model_rewrite" not in f.metadata


def test_issue_1469_body_no_model_field_noop():
    """Body without 'model' key → no-op."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    original = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    f = _flow(content_override=original, metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    assert f.request.content == original
    assert "model_rewrite" not in f.metadata


def test_issue_1469_invalid_json_body_noop():
    """Malformed JSON body → no-op."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(content_override=b"not-json{", metadata={"routed_via_litellm": True})
    addon._rewrite_model_in_body(f)
    assert f.request.content == b"not-json{"


# ---------------------------------------------------------------------------
# Integration: request() hook calls _rewrite_model_in_body only when routed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1469_request_hook_rewrites_when_routed():
    """request() calls _rewrite_model_in_body when routed_via_litellm is True."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(body_dict={"model": "claude-opus-4-7"}, metadata={"routed_via_litellm": True})
    await addon.request(f)
    body = json.loads(f.request.content)
    assert body["model"] == "bedrock--opus-power"


@pytest.mark.asyncio
async def test_issue_1469_request_hook_skips_rewrite_when_not_routed():
    """request() does NOT call _rewrite_model_in_body when routed_via_litellm is absent."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    original = json.dumps({"model": "claude-opus-4-7"}).encode()
    f = _flow(content_override=original)  # no routed_via_litellm in metadata
    await addon.request(f)
    body = json.loads(f.request.content)
    assert body["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_issue_1469_request_hook_skips_denied_flow():
    """request() returns early for denied flows without rewriting."""
    addon = _make_addon(routing=ROUTING_WITH_TIERS)
    f = _flow(
        body_dict={"model": "claude-opus-4-7"},
        metadata={"routed_via_litellm": True, "denied": True},
    )
    await addon.request(f)
    body = json.loads(f.request.content)
    assert body["model"] == "claude-opus-4-7"
