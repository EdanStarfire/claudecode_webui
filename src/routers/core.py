"""Core cross-cutting endpoints: /, /health, /api/auth/check.

Interrupt and permission-response moved to backend/routers/session_runtime.py
(issue #498) — they act on backend-owned state (SessionCoordinator,
PermissionService) and are now plain relayed routes, not special-cased here.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from shared.exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

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
