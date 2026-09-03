"""Generic reverse-proxy relay: forwards any unmatched /api/* request to Backend.

Registered last (see src/routers/__init__.py) so core.py/poll.py/config.py's own
routes take priority — this catch-all only ever sees paths none of those handle.
Deviates from the "route-mirroring" pattern originally suggested for this issue:
with genuine process separation, a single generic proxy achieves the same
"no drift" property with zero route enumeration, so Frontend can never drift
from Backend's contract by forgetting to mirror a new route.
"""

from fastapi import APIRouter, Request

from shared.exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @handle_exceptions("relay to backend")
    async def relay_to_backend(full_path: str, request: Request):
        return await webui.backend_client.relay(request, f"/api/{full_path}")

    # Issue #1789: shared MCP servers can register a custom OAuth callback path on
    # Backend (defaults to /oauth/callback, dynamically registered per-config —
    # backend/web_server.py._add_dynamic_oauth_route). Known gap: a fully custom
    # (non-default) callback path isn't relayed here since Frontend has no visibility
    # into Backend's currently-registered dynamic paths — only the default is covered.
    @router.get("/oauth/callback")
    @handle_exceptions("relay oauth callback")
    async def relay_oauth_callback(request: Request):
        return await webui.backend_client.relay(request, "/oauth/callback")

    return router
