"""BackendClient: httpx wrapper for relaying requests to the Backend process (issue #498).

Used by the generic reverse-proxy router and the poll-relay background tasks.
Never forwards the browser's own auth token — injects the separate,
backend-scoped credential instead (two trust boundaries, never bridged).
"""

import asyncio
import gzip
import logging

import httpx
from fastapi import Request
from fastapi.responses import Response

from shared.gzip_request_middleware import THREAD_MINIMUM_SIZE as _DECOMPRESS_THREAD_THRESHOLD

from .backend_reachability import BackendReachabilityTracker

logger = logging.getLogger(__name__)

# Headers that must not be forwarded verbatim between the two hops. Note
# content-encoding is deliberately NOT here (issue #2029) — relay() decides on a
# per-response basis whether to pass it through unchanged or strip it after decoding.
_STRIPPED_REQUEST_HEADERS = {"host", "authorization", "content-length"}
_STRIPPED_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection"}

# Threshold above which an uncompressed outbound request body gets gzip-compressed
# before being forwarded to Backend (issue #2029, AC6).
_COMPRESS_REQUEST_THRESHOLD = 8 * 1024


def _accept_encoding_covers(accept_encoding: str, encoding: str) -> bool:
    """Whether an Accept-Encoding header value covers a given encoding token.

    Honors an explicit `q=0` ("not acceptable" per RFC 7231 §5.3.4/§5.3.1) rather than
    treating every mention of the token as acceptance — a bare substring/membership
    check would wrongly treat `gzip;q=0` (explicit refusal) as accepting gzip.
    """
    if not accept_encoding:
        return False
    token_q: dict[str, float] = {}
    for part in accept_encoding.split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip().lower()
        if not token:
            continue
        q = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 1.0
        token_q[token] = q
    if encoding in token_q:
        return token_q[encoding] > 0
    if "*" in token_q:
        return token_q["*"] > 0
    return False


class BackendClient:
    """Thin HTTP client for talking to the Backend control-plane process."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self.reachability = BackendReachabilityTracker()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        headers["authorization"] = f"Bearer {self._token}"
        return headers

    async def relay(self, request: Request, path: str, timeout: float | None = None) -> Response:
        """Forward an incoming FastAPI Request to Backend and return its response verbatim.

        Forwards method/path/query/body/headers (minus the browser's own auth,
        replaced with the backend-scoped credential) — Frontend never re-declares
        Backend's route contract, so it can't drift from it.

        Pass `timeout` explicitly for callers relaying to a known long-running Backend
        operation (e.g. halt-all across a large fleet) — same margin-above-the-operation
        discipline as get_json()/request_json() (issue #1933).

        Issue #2029: true byte-for-byte pass-through of Backend's (possibly gzip'd)
        response body when the original caller's Accept-Encoding already covers it —
        avoids the decompress-then-recompress waste of httpx's auto-decoding high-level
        API. Also gzip-compresses a large uncompressed inbound request body before
        forwarding it to Backend (AC6), symmetric with the response-side pass-through.
        """
        inbound_accept_encoding = request.headers.get("accept-encoding", "")
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _STRIPPED_REQUEST_HEADERS
        }
        body = await request.body()

        # If the browser's body already arrived with ANY Content-Encoding, forward it
        # unchanged — for "gzip" specifically, Backend's own GZipRequestMiddleware
        # decodes it (no decompress+recompress here); for anything else, we have no
        # business gzip-compressing an already-encoded body on top (that would produce
        # a body Backend's GZipRequestMiddleware can't correctly unwrap). Otherwise,
        # compress large uncompressed bodies ourselves before forwarding (small bodies
        # stay uncompressed — no behavior change). Checked via the case-insensitive
        # Headers object, not the rebuilt plain dict below (HTTP header casing isn't
        # guaranteed).
        inbound_content_encoding = request.headers.get("content-encoding", "").strip().lower()
        if not inbound_content_encoding and len(body) > _COMPRESS_REQUEST_THRESHOLD:
            if len(body) >= _DECOMPRESS_THREAD_THRESHOLD:
                body = await asyncio.to_thread(gzip.compress, body)
            else:
                body = gzip.compress(body)
            headers["content-encoding"] = "gzip"

        headers = self._auth_headers(headers)

        kwargs = {}
        if timeout is not None:
            kwargs["timeout"] = timeout

        req = self._client.build_request(
            request.method,
            path,
            params=request.query_params,
            content=body,
            headers=headers,
            **kwargs,
        )
        try:
            backend_resp = await self._client.send(req, stream=True)
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            raise

        try:
            raw_body = b"".join([chunk async for chunk in backend_resp.aiter_raw()])
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            raise
        finally:
            # aiter_raw() only closes the connection itself once its own iteration
            # completes normally — a failure partway through (Backend crash/restart
            # mid-stream) would otherwise leak the httpx connection out of the pool.
            # is_closed makes this a no-op on the normal-completion path.
            if not backend_resp.is_closed:
                await backend_resp.aclose()
        self.reachability.record_success()

        response_headers = {
            k: v for k, v in backend_resp.headers.items()
            if k.lower() not in _STRIPPED_RESPONSE_HEADERS
        }

        content_encoding = backend_resp.headers.get("content-encoding", "")
        if content_encoding and not _accept_encoding_covers(inbound_accept_encoding, content_encoding):
            # Rare mismatch (non-browser caller without a matching Accept-Encoding):
            # decode locally, then strip content-encoding so Frontend's own outer
            # GZipMiddleware makes an independent, correct compression decision.
            if content_encoding.strip().lower() == "gzip":
                if len(raw_body) >= _DECOMPRESS_THREAD_THRESHOLD:
                    raw_body = await asyncio.to_thread(gzip.decompress, raw_body)
                else:
                    raw_body = gzip.decompress(raw_body)
            # Case-insensitive removal — httpx's Headers preserves Backend's original
            # casing, which a plain dict .pop("content-encoding") could miss.
            response_headers = {
                k: v for k, v in response_headers.items() if k.lower() != "content-encoding"
            }

        return Response(
            content=raw_body,
            status_code=backend_resp.status_code,
            headers=response_headers,
            media_type=backend_resp.headers.get("content-type"),
        )

    async def get_json(self, path: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """GET a JSON endpoint on Backend and return the decoded body. Raises on non-2xx.

        Pass `timeout` explicitly for long-poll callers — Backend's poll endpoints can
        legitimately take up to their own `timeout` query param to respond, so the
        client-side HTTP timeout must have margin above that value, not equal it, or
        network latency/scheduling jitter on top of an at-the-ceiling server response
        turns a normal idle poll into a client-side ReadTimeout (issue #498 review finding).
        """
        try:
            resp = await self._client.get(path, params=params, headers=self._auth_headers(), timeout=timeout)
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            raise
        self.reachability.record_success()
        resp.raise_for_status()
        return resp.json()

    async def request_json(
        self, method: str, path: str, json: dict | None = None, timeout: float | None = None
    ) -> dict:
        """Call a JSON endpoint on Backend and return the decoded body. Raises on non-2xx.

        Pass `timeout` explicitly for callers awaiting a Backend operation with its own
        long synchronous work budget (e.g. the restart endpoint's git operations + uv
        sync, up to ~180s) — the client-side timeout must have margin above that budget,
        same discipline as get_json() (issue #498 review finding). Omitted entirely
        (not just falsy) when not given, so existing callers keep this client's normal
        per-request default instead of an explicit `timeout=None` disabling it outright.
        """
        kwargs = {"json": json, "headers": self._auth_headers()}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            raise
        self.reachability.record_success()
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> bool:
        """Best-effort liveness check — never raises."""
        try:
            resp = await self._client.get("/health", timeout=2.0)
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            return False
        self.reachability.record_success()
        return resp.status_code == 200

    async def ready(self) -> bool:
        """Best-effort readiness check — never raises."""
        try:
            resp = await self._client.get("/ready", timeout=2.0)
        except httpx.RequestError as e:
            self.reachability.record_failure(e)
            return False
        self.reachability.record_success()
        return resp.status_code == 200 and resp.json().get("ready") is True
