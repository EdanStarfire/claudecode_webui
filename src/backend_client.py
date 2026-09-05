"""BackendClient: httpx wrapper for relaying requests to the Backend process (issue #498).

Used by the generic reverse-proxy router and the poll-relay background tasks.
Never forwards the browser's own auth token — injects the separate,
backend-scoped credential instead (two trust boundaries, never bridged).
"""

import logging

import httpx
from fastapi import Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)

# Headers that must not be forwarded verbatim between the two hops.
_STRIPPED_REQUEST_HEADERS = {"host", "authorization", "content-length"}
_STRIPPED_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}


class BackendClient:
    """Thin HTTP client for talking to the Backend control-plane process."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        headers["authorization"] = f"Bearer {self._token}"
        return headers

    async def relay(self, request: Request, path: str) -> Response:
        """Forward an incoming FastAPI Request to Backend and return its response verbatim.

        Forwards method/path/query/body/headers (minus the browser's own auth,
        replaced with the backend-scoped credential) — Frontend never re-declares
        Backend's route contract, so it can't drift from it.
        """
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _STRIPPED_REQUEST_HEADERS
        }
        headers = self._auth_headers(headers)
        body = await request.body()

        backend_resp = await self._client.request(
            request.method,
            path,
            params=request.query_params,
            content=body,
            headers=headers,
        )

        response_headers = {
            k: v for k, v in backend_resp.headers.items()
            if k.lower() not in _STRIPPED_RESPONSE_HEADERS
        }
        return Response(
            content=backend_resp.content,
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
        resp = await self._client.get(path, params=params, headers=self._auth_headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    async def request_json(self, method: str, path: str, json: dict | None = None) -> dict:
        """Call a JSON endpoint on Backend and return the decoded body. Raises on non-2xx."""
        resp = await self._client.request(method, path, json=json, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> bool:
        """Best-effort liveness check — never raises."""
        try:
            resp = await self._client.get("/health", timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def ready(self) -> bool:
        """Best-effort readiness check — never raises."""
        try:
            resp = await self._client.get("/ready", timeout=2.0)
            return resp.status_code == 200 and resp.json().get("ready") is True
        except httpx.HTTPError:
            return False
