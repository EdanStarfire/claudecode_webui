"""Plain ASGI middleware decoding gzip-compressed inbound request bodies (issue #2029).

Starlette/FastAPI have no built-in request-decompression. Deliberately NOT
`BaseHTTPMiddleware`, which buffers the whole response through an extra layer this
issue avoids. Registered innermost (before AuthMiddleware/GZipMiddleware) on both
tiers since it only concerns request bodies, not response compression ordering.
"""

import asyncio
import gzip
import logging

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Mirrors starlette.middleware.gzip.GZipMiddleware's own thread_minimum_size default
# so this new decode path shares the same inline-vs-thread cutoff rather than
# inventing a new number.
THREAD_MINIMUM_SIZE = 128 * 1024


async def _buffer_request_body(receive: Receive) -> bytes:
    """Drain the ASGI receive channel into a single bytes object.

    Mirrors starlette.requests.Request.stream()'s disconnect handling.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect()
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


class GZipRequestMiddleware:
    """Decodes a `Content-Encoding: gzip` request body before the downstream app sees it.

    `decompress_paths`, when given, restricts decompression to that exact set of paths —
    every other path passes through completely untouched (body and header both). Used on
    the Frontend tier (issue #2029 review finding): Frontend's generic relay forwards most
    /api/* traffic to Backend unparsed, and Backend has its own GZipRequestMiddleware
    (registered with no restriction) to decode it there — decompressing on Frontend first
    and re-compressing in relay() would silently defeat the "browser compresses once, no
    intermediate decompress+recompress" design goal. Only Frontend's own few
    body-consuming local routes (config PUT, restart POST) need pre-decoding here.
    """

    def __init__(self, app: ASGIApp, decompress_paths: frozenset[str] | None = None) -> None:
        self.app = app
        self.decompress_paths = decompress_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self.decompress_paths is not None and scope["path"] not in self.decompress_paths:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get("content-encoding", "").strip().lower() != "gzip":
            await self.app(scope, receive, send)
            return

        raw = await _buffer_request_body(receive)
        if raw:
            try:
                if len(raw) >= THREAD_MINIMUM_SIZE:
                    decompressed = await asyncio.to_thread(gzip.decompress, raw)
                else:
                    decompressed = gzip.decompress(raw)
            except (OSError, EOFError):
                # Malformed/truncated gzip body — a clean 400 instead of an uncaught
                # exception, which would otherwise bypass the app's own exception
                # handlers (this middleware sits outside ExceptionMiddleware).
                response = JSONResponse({"detail": "Invalid gzip request body"}, status_code=400)
                await response(scope, receive, send)
                return
        else:
            decompressed = b""

        # Body is decompressed now — strip content-encoding/content-length so
        # downstream code doesn't act on stale metadata (the decompressed body has a
        # different length and is no longer gzip-encoded).
        new_scope = dict(scope)
        new_scope["headers"] = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower() not in (b"content-encoding", b"content-length")
        ]

        sent = False

        async def receive_decompressed() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": decompressed, "more_body": False}

        await self.app(new_scope, receive_decompressed, send)
