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

# Mutating an MCP config can add/change/remove its custom OAuth callback path on
# Backend — resync Frontend's dynamic relay routes (webui.resync_oauth_callback_paths,
# src/web_server.py) right after, so a newly-configured custom path works immediately
# rather than waiting for the next Frontend restart.
_MCP_CONFIG_MUTATION_METHODS = {"POST", "PUT", "DELETE"}


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @handle_exceptions("relay to backend")
    async def relay_to_backend(full_path: str, request: Request):
        response = await webui.backend_client.relay(request, f"/api/{full_path}")
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
        return await webui.backend_client.relay(request, "/oauth/callback")

    return router
