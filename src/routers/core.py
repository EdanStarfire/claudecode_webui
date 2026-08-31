"""Core cross-cutting endpoints: /, /health, /api/auth/check, /oauth/callback.

Issue #498: interrupt/permission-response moved to session_runtime.py so they mirror
under /api/backend like the rest of session-domain routes — this file's remaining
routes are frontend-only concepts with no REMOTE mirror (/oauth/callback handles a
browser redirect back to the Hub's own OAuth flow — moved here from mcp.py so that
file can convert to mount-relative paths without accidentally prefixing this
non-/api, non-relay-eligible route).
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..exception_handlers import handle_exceptions
from ..oauth_callback_listener_manager import render_oauth_callback


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/oauth/callback", response_class=HTMLResponse)
    @handle_exceptions("handle oauth callback")
    async def oauth_callback(request: Request):
        """Handle OAuth 2.1 authorization code callback.

        Exempt from auth middleware — this route is reached before any token exists.
        On success broadcasts mcp_oauth_complete to all UI WebSocket clients.

        Shares its parsing/rendering logic with per-config custom callback routes/listeners
        (issue #1789) via render_oauth_callback().
        """
        return await render_oauth_callback(
            request, webui.coordinator.oauth_callback_listener_manager.complete_and_broadcast
        )

    @router.get("/", response_class=HTMLResponse)
    @handle_exceptions("serve root")
    async def read_root():
        """Serve the main HTML page"""
        html_file = Path(__file__).parent.parent.parent / "frontend" / "dist" / "index.html"
        if html_file.exists():
            return HTMLResponse(
                content=html_file.read_text(encoding='utf-8'),
                status_code=200,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
        return HTMLResponse(content=webui._default_html(), status_code=200)

    @router.get("/health")
    @handle_exceptions("health check")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

    @router.get("/api/auth/check")
    @handle_exceptions("check auth")
    async def auth_check(request: Request):
        """Check authentication status (issue #728). Exempt from auth middleware."""
        authenticated = False
        if webui.auth_enabled and webui.auth_token:
            # Check Authorization header
            auth_header = request.headers.get('authorization', '')
            if auth_header.startswith('Bearer ') and auth_header[7:] == webui.auth_token:
                authenticated = True
            # Check query param
            if not authenticated and request.query_params.get('token') == webui.auth_token:
                authenticated = True
        elif not webui.auth_enabled:
            authenticated = True
        return {"auth_required": webui.auth_enabled, "authenticated": authenticated}

    return router
