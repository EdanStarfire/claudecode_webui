"""Tests for src/backend_client.py's Backend reachability wiring (issue #1844).

BackendClient's relay()/get_json()/request_json() must feed the shared
BackendReachabilityTracker on both the failure path (httpx.RequestError —
connection-level, i.e. Backend is actually unreachable) and the success path
(any response received, even a 4xx/5xx body — that's a genuine backend-side
error, not an unreachable-backend condition), while leaving existing
raise/return behavior for callers completely unchanged. Follows the existing
convention of `side_effect=httpx.ConnectError("refused")` seen in
test_oauth_callback_relay.py.
"""

import asyncio
import gzip
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.requests import Request

from src.backend_client import BackendClient


def _make_client() -> BackendClient:
    client = BackendClient.__new__(BackendClient)  # bypass __init__ (no real AsyncClient/socket)
    from src.backend_reachability import BackendReachabilityTracker
    client.base_url = "http://backend.test"
    client._token = "token"
    client._client = MagicMock()
    client.reachability = BackendReachabilityTracker()
    return client


def _make_request(
    method: str = "GET",
    path: str = "/api/sessions",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def _aiter_bytes(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


def _make_streaming_response(
    content: bytes = b"{}", status_code: int = 200, headers: dict | None = None
) -> MagicMock:
    """A MagicMock standing in for httpx's Response under relay()'s stream=True path.

    `aiter_raw()` must be a plain (non-async) callable returning an async iterator —
    real code does `async for chunk in backend_resp.aiter_raw()`, not
    `await backend_resp.aiter_raw()`.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {}
    resp.aiter_raw = MagicMock(return_value=_aiter_bytes([content]))
    return resp


# --- relay() ---


@pytest.mark.asyncio
async def test_issue_1844_relay_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.send = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.relay(_make_request(), "/api/sessions")

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_relay_records_success_even_on_error_status_body():
    """A response body carrying a 4xx/5xx is a genuine backend-side error, not an
    unreachable backend — relay() never calls raise_for_status() at all, so this
    must still clear reachability."""
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    assert client.reachability.is_unreachable is True

    backend_resp = _make_streaming_response(b'{"detail": "boom"}', status_code=500)
    client._client.send = AsyncMock(return_value=backend_resp)

    await client.relay(_make_request(), "/api/sessions")

    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_2029_relay_closes_response_on_mid_stream_failure():
    """Review finding: aiter_raw() only closes the connection itself once its own
    iteration completes normally. A failure partway through (Backend crash/restart
    mid-stream) must still close the response, or the httpx connection leaks out of
    the pool — repeated occurrences during Backend flakiness/restarts would
    eventually exhaust it."""

    async def _raise_mid_stream():
        yield b"partial"
        raise httpx.ReadError("connection reset")

    client = _make_client()
    backend_resp = MagicMock()
    backend_resp.status_code = 200
    backend_resp.headers = {}
    backend_resp.is_closed = False
    backend_resp.aiter_raw = MagicMock(return_value=_raise_mid_stream())
    backend_resp.aclose = AsyncMock()
    client._client.send = AsyncMock(return_value=backend_resp)

    with pytest.raises(httpx.ReadError):
        await client.relay(_make_request(), "/api/sessions")

    backend_resp.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_issue_1933_relay_omits_timeout_kwarg_when_not_given():
    """Existing relay() callers must keep this client's normal per-request default —
    explicitly passing `timeout=None` to httpx disables the timeout entirely rather
    than falling back to the client default, so the kwarg must be omitted outright."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    await client.relay(_make_request(), "/api/sessions")

    _, kwargs = client._client.build_request.call_args
    assert "timeout" not in kwargs


@pytest.mark.asyncio
async def test_issue_1933_relay_forwards_explicit_timeout():
    """Callers relaying to a known long-running Backend operation (e.g. halt-all
    across a large fleet) must be able to give the client-side call matching
    headroom above the default relay timeout."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    await client.relay(_make_request(), "/api/legions/legion-1/halt-all", timeout=120.0)

    _, kwargs = client._client.build_request.call_args
    assert kwargs["timeout"] == 120.0


# --- relay() response pass-through / decode (issue #2029, AC4) ---


@pytest.mark.asyncio
async def test_issue_2029_relay_passes_through_gzip_body_unchanged_when_accept_encoding_matches():
    """AC4's core claim: when the inbound Accept-Encoding already covers Backend's
    Content-Encoding, relay() must not decompress at all — a weaker "the decoded
    output looks right" assertion could pass even if the code secretly decompressed
    and recompressed, hiding the exact CPU work AC4 says to avoid. Only a call-count
    assertion on gzip.decompress actually proves true byte-for-byte pass-through."""
    import gzip as gzip_module

    client = _make_client()
    compressed = gzip_module.compress(b'{"large": "payload"}')
    backend_resp = _make_streaming_response(
        compressed, headers={"content-encoding": "gzip", "content-type": "application/json"}
    )
    client._client.send = AsyncMock(return_value=backend_resp)

    request = _make_request(headers=[(b"accept-encoding", b"gzip, deflate, br")])

    with patch("src.backend_client.gzip.decompress") as mock_decompress:
        response = await client.relay(request, "/api/sessions/1/messages")

    mock_decompress.assert_not_called()
    assert response.headers["content-encoding"] == "gzip"
    assert bytes(response.body) == compressed


@pytest.mark.asyncio
async def test_issue_2029_relay_decodes_on_accept_encoding_mismatch():
    """The rare non-browser-caller path: no matching Accept-Encoding means relay()
    must decode locally and strip content-encoding so Frontend's own outer
    GZipMiddleware can make an independent compression decision for that caller."""
    import gzip as gzip_module

    client = _make_client()
    raw = b'{"large": "payload"}'
    compressed = gzip_module.compress(raw)
    backend_resp = _make_streaming_response(
        compressed, headers={"content-encoding": "gzip", "content-type": "application/json"}
    )
    client._client.send = AsyncMock(return_value=backend_resp)

    # No Accept-Encoding header at all — the mismatch path.
    request = _make_request()

    response = await client.relay(request, "/api/sessions/1/messages")

    assert "content-encoding" not in response.headers
    assert bytes(response.body) == raw


@pytest.mark.asyncio
async def test_issue_2029_relay_decompress_mismatch_uses_thread_above_threshold():
    """Mirrors Starlette's own thread_minimum_size discipline — a large decode-mismatch
    body must go through asyncio.to_thread, not run inline on the event loop."""
    import gzip as gzip_module

    from src import backend_client as backend_client_module

    client = _make_client()
    # Random (incompressible) bytes so the *compressed* size — what the threshold
    # check actually inspects — also exceeds the threshold, unlike highly repetitive
    # data whose gzip'd form would stay tiny regardless of the original size.
    raw = os.urandom(backend_client_module._DECOMPRESS_THREAD_THRESHOLD + 1024)
    compressed = gzip_module.compress(raw)
    backend_resp = _make_streaming_response(compressed, headers={"content-encoding": "gzip"})
    client._client.send = AsyncMock(return_value=backend_resp)

    with patch(
        "src.backend_client.asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)
    ) as mock_to_thread:
        response = await client.relay(_make_request(), "/api/sessions/1/messages")

    mock_to_thread.assert_called_once()
    assert bytes(response.body) == raw


def test_issue_2029_accept_encoding_covers_honors_q_zero():
    """Review finding: a naive membership check would treat `gzip;q=0` (explicit
    RFC 7231 refusal) as accepting gzip, wrongly taking the pass-through branch for a
    caller that opted out of gzip specifically."""
    from src.backend_client import _accept_encoding_covers

    assert _accept_encoding_covers("gzip;q=0, br", "gzip") is False
    assert _accept_encoding_covers("gzip;q=0.5, br", "gzip") is True
    assert _accept_encoding_covers("gzip", "gzip") is True
    assert _accept_encoding_covers("*;q=0", "gzip") is False
    assert _accept_encoding_covers("*", "gzip") is True
    assert _accept_encoding_covers("br", "gzip") is False


# --- relay() request-body compression (issue #2029, AC6) ---


@pytest.mark.asyncio
async def test_issue_2029_relay_compresses_large_uncompressed_request_body():
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    large_body = b"x" * 9000  # above the 8 KiB compression threshold
    request = _make_request(method="POST", body=large_body)

    await client.relay(request, "/api/sessions/1/messages")

    _, kwargs = client._client.build_request.call_args
    assert kwargs["headers"]["content-encoding"] == "gzip"
    assert kwargs["content"] != large_body
    assert gzip.decompress(kwargs["content"]) == large_body


@pytest.mark.asyncio
async def test_issue_2029_relay_leaves_small_request_body_uncompressed():
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    small_body = b"x" * 100
    request = _make_request(method="POST", body=small_body)

    await client.relay(request, "/api/sessions/1/messages")

    _, kwargs = client._client.build_request.call_args
    assert "content-encoding" not in kwargs["headers"]
    assert kwargs["content"] == small_body


@pytest.mark.asyncio
async def test_issue_2029_relay_forwards_already_gzipped_request_body_unchanged():
    """Pass-through symmetry with the response side — if the browser's body already
    arrived gzip-encoded, relay() must not decompress+recompress it."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    compressed_body = gzip.compress(b"x" * 9000)
    request = _make_request(
        method="POST", body=compressed_body, headers=[(b"content-encoding", b"gzip")]
    )

    await client.relay(request, "/api/sessions/1/messages")

    _, kwargs = client._client.build_request.call_args
    assert kwargs["content"] == compressed_body
    assert kwargs["headers"]["content-encoding"] == "gzip"


@pytest.mark.asyncio
async def test_issue_2029_relay_does_not_recompress_non_gzip_encoded_request_body():
    """Review finding: the old check only special-cased 'gzip' specifically, so a
    body already encoded with e.g. brotli/deflate would get gzip-compressed on top —
    producing bytes Backend's GZipRequestMiddleware (gzip-only) can't correctly
    unwrap. Any pre-existing Content-Encoding must be left alone."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response())

    already_encoded_body = b"\x1b\x25\xb1" + b"x" * 9000  # stand-in for e.g. brotli bytes
    request = _make_request(
        method="POST", body=already_encoded_body, headers=[(b"content-encoding", b"br")]
    )

    await client.relay(request, "/api/sessions/1/messages")

    _, kwargs = client._client.build_request.call_args
    assert kwargs["content"] == already_encoded_body
    assert kwargs["headers"]["content-encoding"] == "br"


# --- get_json() ---


@pytest.mark.asyncio
async def test_issue_1844_get_json_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.send = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.get_json("/api/config")

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_get_json_records_success_before_raise_for_status():
    """A successful connection that gets back a 4xx/5xx must still clear
    reachability, even though raise_for_status() subsequently raises."""
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))

    resp = _make_streaming_response(b'{"detail": "boom"}', status_code=500)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    client._client.send = AsyncMock(return_value=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_json("/api/config")

    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_2029_get_json_decompresses_gzip_response():
    client = _make_client()
    raw = b'{"events": [], "next_cursor": 5}'
    resp = _make_streaming_response(gzip.compress(raw), headers={"content-encoding": "gzip"})
    client._client.send = AsyncMock(return_value=resp)

    result = await client.get_json("/api/poll/ui")

    assert result == {"events": [], "next_cursor": 5}


@pytest.mark.asyncio
async def test_issue_2029_get_json_decompress_uses_thread_above_threshold():
    """Review finding: httpx's high-level .get()/.request() (used here previously)
    decompress fully inline inside the awaited call — GZipDecoder.decode() is a
    plain sync method, no thread offload anywhere in httpx. Now that Backend's poll
    responses are gzip'd (Step 1) and poll.py has no cap on a catch-up batch's size,
    a large buffered batch could block Frontend's shared event loop — the same
    regression class this epic exists to prevent. Mirrors relay()'s already-proven
    thread-offload discipline instead."""
    from src import backend_client as backend_client_module

    client = _make_client()
    # Random (incompressible) bytes so the *compressed* size also exceeds the
    # threshold, not just the decompressed size.
    raw = json.dumps({"padding": os.urandom(backend_client_module._DECOMPRESS_THREAD_THRESHOLD + 1024).hex()}).encode()
    compressed = gzip.compress(raw)
    resp = _make_streaming_response(compressed, headers={"content-encoding": "gzip"})
    client._client.send = AsyncMock(return_value=resp)

    with patch(
        "src.backend_client.asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)
    ) as mock_to_thread:
        result = await client.get_json("/api/poll/session/abc")

    mock_to_thread.assert_called_once()
    assert result == json.loads(raw)


@pytest.mark.asyncio
async def test_issue_2029_get_json_decompress_stays_inline_below_threshold():
    client = _make_client()
    raw = b'{"ok": true}'
    resp = _make_streaming_response(gzip.compress(raw), headers={"content-encoding": "gzip"})
    client._client.send = AsyncMock(return_value=resp)

    with patch(
        "src.backend_client.asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)
    ) as mock_to_thread:
        result = await client.get_json("/api/config")

    mock_to_thread.assert_not_called()
    assert result == {"ok": True}


# --- request_json() ---


@pytest.mark.asyncio
async def test_issue_1844_request_json_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.send = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.request_json("PUT", "/api/config", json={"a": 1})

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_request_json_records_success_before_raise_for_status():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))

    resp = _make_streaming_response(b'{"detail": "boom"}', status_code=400)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400", request=MagicMock(), response=MagicMock(status_code=400)
    )
    client._client.send = AsyncMock(return_value=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.request_json("PUT", "/api/config", json={"a": 1})

    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_1847_request_json_omits_timeout_kwarg_when_not_given():
    """Existing callers (e.g. config.py's PUT /api/config) must keep this client's
    normal per-request default — explicitly passing `timeout=None` to httpx disables
    the timeout entirely rather than falling back to the client default, so the
    kwarg must be omitted outright, not passed as None."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response(b'{"ok": true}'))

    await client.request_json("PUT", "/api/config", json={"a": 1})

    _, kwargs = client._client.build_request.call_args
    assert "timeout" not in kwargs


@pytest.mark.asyncio
async def test_issue_1847_request_json_forwards_explicit_timeout():
    """Callers awaiting a Backend operation with its own long synchronous work
    budget (e.g. the restart endpoint's git operations + uv sync) must be able to
    give the client-side call matching headroom."""
    client = _make_client()
    client._client.send = AsyncMock(return_value=_make_streaming_response(b'{"ok": true}'))

    await client.request_json("POST", "/api/system/restart", json={}, timeout=210.0)

    _, kwargs = client._client.build_request.call_args
    assert kwargs["timeout"] == 210.0


# --- health() / ready() ---


@pytest.mark.asyncio
async def test_issue_1844_health_records_failure_and_returns_false():
    client = _make_client()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await client.health() is False
    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_health_records_success_and_returns_true():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    resp = MagicMock()
    resp.status_code = 200
    client._client.get = AsyncMock(return_value=resp)

    assert await client.health() is True
    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_1844_ready_records_failure_and_returns_false():
    client = _make_client()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await client.ready() is False
    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_ready_records_success_and_returns_true():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ready": True}
    client._client.get = AsyncMock(return_value=resp)

    assert await client.ready() is True
    assert client.reachability.is_unreachable is False
