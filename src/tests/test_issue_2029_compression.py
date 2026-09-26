"""Tests for issue #2029: HTTP payload compression (both hops, both directions).

Covers the GZipMiddleware registration itself (T1, AC8) against a minimal Starlette
app built with the exact same parameters used in src/web_server.py and
backend/web_server.py — Steps 1/2 of the plan add no new compression logic (Starlette's
GZipMiddleware does the work), so these tests exercise the registration/configuration,
not a reimplementation of Starlette's own behavior.
"""

import gzip
import inspect
import json

from starlette.applications import Starlette
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

_EXCLUDE_CONTENT_TYPES = (*DEFAULT_EXCLUDED_CONTENT_TYPES, "application/octet-stream")


def _large_history_payload(record_count: int = 2000) -> list[dict]:
    """A representative large session-history-style JSON payload (issue #2026's
    synthetic-session tests use a similar SystemMessage-shaped record for the same
    reason: repetitive-but-realistic structure, large enough to exercise both the
    minimum_size and thread_minimum_size thresholds)."""
    return [
        {
            "_type": "AssistantMessage",
            "timestamp": 1700000000.0 + i,
            "session_id": "synthetic-session",
            "message_id": f"synthetic-{i}",
            "data": {
                "content": [
                    {"type": "text", "text": f"This is synthetic message content number {i}."}
                ]
            },
        }
        for i in range(record_count)
    ]


async def _large_history(_request):
    return JSONResponse(_large_history_payload())


async def _small_body(_request):
    return JSONResponse({"ok": True})


async def _range_response(_request):
    # Mirrors backend/http_range.py's 206 behavior — Starlette's GZipMiddleware
    # must skip compression for partial-content responses regardless.
    body = b"x" * 10_000
    return Response(
        content=body[100:200],
        status_code=206,
        headers={"Content-Range": "bytes 100-199/10000"},
        media_type="application/octet-stream",
    )


def _make_app():
    app = Starlette(
        routes=[
            Route("/history", _large_history),
            Route("/small", _small_body),
            Route("/range", _range_response),
        ]
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, exclude_content_types=_EXCLUDE_CONTENT_TYPES)
    return app


# --- T1: response compression negotiation ---


def test_issue_2029_compresses_when_accept_encoding_gzip():
    client = TestClient(_make_app())

    resp = client.get("/history", headers={"Accept-Encoding": "gzip"})

    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["vary"] == "Accept-Encoding"


def test_issue_2029_does_not_compress_without_accept_encoding():
    client = TestClient(_make_app())

    resp = client.get("/history", headers={"Accept-Encoding": ""})

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


def test_issue_2029_decoded_body_identical_regardless_of_compression():
    client = TestClient(_make_app())

    compressed_resp = client.get("/history", headers={"Accept-Encoding": "gzip"})
    plain_resp = client.get("/history", headers={"Accept-Encoding": ""})

    assert compressed_resp.json() == plain_resp.json() == _large_history_payload()


def test_issue_2029_small_body_stays_below_minimum_size_uncompressed():
    client = TestClient(_make_app())

    resp = client.get("/small", headers={"Accept-Encoding": "gzip"})

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


# --- T5: range/206 responses are never compressed ---


def test_issue_2029_206_range_response_never_compressed():
    client = TestClient(_make_app())

    resp = client.get("/range", headers={"Accept-Encoding": "gzip"})

    assert resp.status_code == 206
    assert "content-encoding" not in resp.headers
    assert resp.content == b"x" * 100


# --- AC8: representative large history response compresses at least 5x ---


def test_issue_2029_large_history_compresses_at_least_5x():
    raw_body = json.dumps(_large_history_payload()).encode()
    compressed_body = gzip.compress(raw_body, compresslevel=9)

    assert len(compressed_body) <= len(raw_body) * 0.20


# --- Step 3: dependency-floor regression guard (review finding) ---


def test_issue_2029_installed_starlette_gzip_supports_thread_offload():
    """The actual invariant Step 3's `starlette>=1.4.0` pin protects — a feature
    check, not just a version-number proxy for it. If a future dependency
    resolution ever regresses to a pre-thread-offload GZipMiddleware despite the
    version pin (e.g. a packaging error, or the pin being loosened by mistake),
    this fails loudly instead of silently reintroducing a #2026-class
    event-loop-blocking regression."""
    sig = inspect.signature(GZipMiddleware.__init__)
    assert "thread_minimum_size" in sig.parameters
