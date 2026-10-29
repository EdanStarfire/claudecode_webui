"""Generic reverse-proxy relay: forwards any unmatched /api/* request to Backend.

Registered last (see src/routers/__init__.py) so core.py/poll.py/config.py's own
routes take priority — this catch-all only ever sees paths none of those handle.
Deviates from the "route-mirroring" pattern originally suggested for this issue:
with genuine process separation, a single generic proxy achieves the same
"no drift" property with zero route enumeration, so Frontend can never drift
from Backend's contract by forgetting to mirror a new route.
"""

import asyncio

import httpx
from fastapi import APIRouter, Request, Response

from shared.exception_handlers import handle_exceptions

from ..backend_reachability import is_backend_degraded, to_http_exception

# Mutating an MCP config can add/change/remove its custom OAuth callback path on
# Backend — resync Frontend's dynamic relay routes (webui.resync_oauth_callback_paths,
# src/web_server.py) right after, so a newly-configured custom path works immediately
# rather than waiting for the next Frontend restart.
_MCP_CONFIG_MUTATION_METHODS = {"POST", "PUT", "DELETE"}

# Issue #1933: halt-all can legitimately take longer than the default relay timeout
# across a large fleet of sessions — give it headroom above that ceiling, same
# "explicit margin above a known long-running operation" discipline as the
# restart-trigger timeout in system.py. This is a safety margin, not the primary
# fix — real per-session termination cost was cut at the source (blocking gc.collect()
# removed from DataStorageManager.cleanup()).
_HALT_ALL_TIMEOUT_SECONDS = 120.0

# Issue #2030 stage 3 (PR 3B): whole-history search scans can take seconds, and a plain ASGI
# handler is not cancelled when the browser aborts. Poll for the browser's disconnect while a
# `/search` relay is in flight and cancel it, which closes the upstream connection: Backend then
# sees the disconnect and cancels the scan (backend/search_scan.py).
_DISCONNECT_POLL_SECONDS = 0.2


def _is_search_path(full_path: str) -> bool:
    return full_path.endswith("/search") and (
        full_path.startswith("sessions/") or full_path.startswith("projects/")
    )


async def _relay_until_disconnect(request: Request, relay_coro) -> Response:
    task = asyncio.ensure_future(relay_coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                task.cancel()
                return Response(status_code=499)
    finally:
        if not task.done():
            task.cancel()


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @handle_exceptions("relay to backend")
    async def relay_to_backend(full_path: str, request: Request):
        timeout = _HALT_ALL_TIMEOUT_SECONDS if full_path.endswith("/halt-all") else None
        try:
            relayed = webui.backend_client.relay(request, f"/api/{full_path}", timeout=timeout)
            if request.method == "GET" and _is_search_path(full_path):
                response = await _relay_until_disconnect(request, relayed)
            else:
                response = await relayed
        except httpx.RequestError as e:
            raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e
        if full_path.startswith("mcp-configs") and request.method in _MCP_CONFIG_MUTATION_METHODS:
            await webui.resync_oauth_callback_paths()
        return response

    # Issue #1789: shared MCP servers can register a custom OAuth callback path on
    # Backend. This handles the default path; non-default (explicitly-configured)
    # paths are mirrored dynamically by webui.resync_oauth_callback_paths()
    # (src/web_server.py), called at startup and after every mcp-configs mutation
    # above — Backend is typically 127.0.0.1-only and not independently reachable,
    # so a custom callback path can only ever complete by relaying through Frontend.
    @router.get("/oauth/callback")
    @handle_exceptions("relay oauth callback")
    async def relay_oauth_callback(request: Request):
        try:
            return await webui.backend_client.relay(request, "/oauth/callback")
        except httpx.RequestError as e:
            raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e

    return router
