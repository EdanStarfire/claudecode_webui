"""Tests for shared/gzip_request_middleware.py (issue #2029, AC6).

Plain ASGI middleware decoding gzip-compressed inbound request bodies on both
tiers — asserts route handlers see an identical, transparently-decoded body
regardless of whether the caller compressed it, and that the size-threshold
guard actually routes large decodes through asyncio.to_thread (mirroring
Starlette's own GZipMiddleware thread-offload discipline).
"""

import asyncio
import gzip
import os
from unittest.mock import AsyncMock, patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from shared import gzip_request_middleware
from shared.gzip_request_middleware import GZipRequestMiddleware


async def _echo_body(request):
    body = await request.body()
    return JSONResponse(
        {
            "received_length": len(body),
            "content_encoding": request.headers.get("content-encoding"),
        }
    )


async def _echo_raw(request):
    body = await request.body()
    return JSONResponse({"received": body.decode("latin-1")})


def _make_app(handler=_echo_raw, decompress_paths=None):
    app = Starlette(routes=[Route("/echo", handler, methods=["POST"])])
    app.add_middleware(GZipRequestMiddleware, decompress_paths=decompress_paths)
    return app


def test_issue_2029_decompresses_gzip_request_body():
    client = TestClient(_make_app())
    payload = "hello world " * 100
    compressed = gzip.compress(payload.encode("latin-1"))

    resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    assert resp.json()["received"] == payload


def test_issue_2029_strips_content_encoding_header_after_decoding():
    client = TestClient(_make_app(_echo_body))
    payload = b"hello world " * 100
    compressed = gzip.compress(payload)

    resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["content_encoding"] is None
    assert data["received_length"] == len(payload)


def test_issue_2029_passes_through_uncompressed_body_unchanged():
    client = TestClient(_make_app())
    payload = "plain text, no compression"

    resp = client.post("/echo", content=payload)

    assert resp.status_code == 200
    assert resp.json()["received"] == payload


def test_issue_2029_decode_stays_inline_below_thread_threshold():
    client = TestClient(_make_app())
    payload = b"y" * 100
    compressed = gzip.compress(payload)

    with patch(
        "shared.gzip_request_middleware.asyncio.to_thread", new=AsyncMock()
    ) as mock_to_thread:
        resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    mock_to_thread.assert_not_called()


def test_issue_2029_decode_uses_thread_above_threshold():
    client = TestClient(_make_app(_echo_body))
    # Random (incompressible) bytes so the compressed body sent over the wire also
    # exceeds the threshold, not just the decompressed size.
    payload = os.urandom(gzip_request_middleware.THREAD_MINIMUM_SIZE + 1024)
    compressed = gzip.compress(payload)

    with patch(
        "shared.gzip_request_middleware.asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)
    ) as mock_to_thread:
        resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    mock_to_thread.assert_called_once()
    assert resp.json()["received_length"] == len(payload)


# --- decompress_paths restriction (issue #2029 review finding) ---


def test_issue_2029_decompresses_when_path_is_in_decompress_paths():
    client = TestClient(_make_app(_echo_body, decompress_paths=frozenset({"/echo"})))
    payload = b"hello world " * 100
    compressed = gzip.compress(payload)

    resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["received_length"] == len(payload)
    assert data["content_encoding"] is None


def test_issue_2029_leaves_body_and_header_untouched_when_path_not_in_decompress_paths():
    """The bug this guards against: registering GZipRequestMiddleware unconditionally
    on Frontend would decompress a browser's compressed body before the generic relay
    route ever sees it, silently defeating relay()'s own pass-through logic and
    forcing a wasteful decompress-then-recompress on every large relayed request."""
    client = TestClient(_make_app(_echo_body, decompress_paths=frozenset({"/some-other-path"})))
    payload = b"hello world " * 100
    compressed = gzip.compress(payload)

    resp = client.post("/echo", content=compressed, headers={"content-encoding": "gzip"})

    assert resp.status_code == 200
    data = resp.json()
    # Route handler must see the ORIGINAL compressed bytes and header, untouched.
    assert data["received_length"] == len(compressed)
    assert data["content_encoding"] == "gzip"


# --- malformed gzip body (issue #2029 review finding) ---


def test_issue_2029_malformed_gzip_body_returns_clean_400():
    client = TestClient(_make_app())

    resp = client.post(
        "/echo", content=b"not actually gzip data", headers={"content-encoding": "gzip"}
    )

    assert resp.status_code == 400
