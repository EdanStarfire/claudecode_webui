"""
FastAPI Frontend API shell for Claude Code WebUI (issue #498).

Serves the Vue SPA, authenticates the browser, and relays every domain request
to the Backend control-plane process. Holds zero session-execution code and
zero domain state — every session/project/legion read or write is a live
relay (generic reverse-proxy for most routes, poll-relay for the two
long-poll streams, split-ownership merge for /api/config).
"""

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from shared.event_queue import EventQueue

from .backend_client import BackendClient
from .poll_relay import PollRelay

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for the browser-facing token (issue #728).

    Exempts static assets, root HTML, health check, and auth check endpoint.
    Never validates the backend-scoped token — that's a separate trust boundary
    Backend's own AuthMiddleware enforces (backend/web_server.py).
    """

    EXEMPT_PATHS = {
        '/', '/health', '/ready', '/api/auth/check', '/oauth/callback',
        # Public static assets from frontend/public/ — served at root without hashing,
        # must be accessible without a token (browsers fetch favicons unauthenticated).
        '/favicon.ico', '/favicon-16x16.png', '/favicon-32x32.png',
        '/apple-touch-icon.png', '/android-chrome-192x192.png', '/android-chrome-512x512.png',
        '/site.webmanifest', '/robots.txt',
    }
    EXEMPT_PREFIXES = ('/assets/',)
    # Issue #827: The per-session secrets resolve endpoint uses its own Bearer token
    # auth, not the global operator token. Exempt it from global AuthMiddleware.
    # Issue #498: these endpoints are consumed directly by Docker/LiteLLM sidecars
    # against Backend's own bind address — never actually relayed through Frontend
    # in practice, but exempted here too for defense in depth.
    EXEMPT_SUFFIXES = ('/secrets/resolve', '/routing')
    # Issue #1134: Session-scoped proxy write-back endpoints (per-session Bearer token auth).
    _PROXY_WRITE_BACK_PATTERN = re.compile(r'^/api/sessions/[^/]+/(secrets/[^/]+|events)$')

    def __init__(self, app, auth_token: str):
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in self.EXEMPT_PATHS:
            return await call_next(request)
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)
        for suffix in self.EXEMPT_SUFFIXES:
            if path.endswith(suffix):
                return await call_next(request)
        if self._PROXY_WRITE_BACK_PATTERN.match(path):
            return await call_next(request)

        token = None
        auth_header = request.headers.get('authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        if not token:
            token = request.query_params.get('token')

        if token != self.auth_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required", "auth_required": True}
            )

        return await call_next(request)


class ClaudeWebUI:
    """Frontend API application: static serving, browser auth, Backend relay."""

    def __init__(
        self,
        backend_url: str,
        backend_token: str,
        config_file: Path | None = None,
        auth_token: str | None = None,
        auth_enabled: bool = False,
        host: str = "127.0.0.1",
        port: int = 8000,
    ):
        self.app = FastAPI(title="Claude Code WebUI", version="1.0.0")
        self.host = host
        self.port = port
        self.config_file = config_file

        # Authentication (issue #728) — the browser-facing token. Never forwarded
        # to Backend; a separate backend-scoped credential is used for that hop.
        self.auth_token = auth_token
        self.auth_enabled = auth_enabled

        self.backend_client = BackendClient(backend_url, backend_token)

        # Local fan-out queues for the poll-relay — multiple browser tabs share
        # these instead of each opening their own upstream connection to Backend.
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {}
        self.poll_relay = PollRelay(self.backend_client, self.ui_queue, self.session_queues)

        self._ready = False

        # Setup routes
        self._setup_routes()

        # Register auth middleware if enabled (issue #728)
        if self.auth_enabled and self.auth_token:
            self.app.add_middleware(AuthMiddleware, auth_token=self.auth_token)
            logger.info("Authentication middleware enabled")

        # Setup static files (Vue 3 production build)
        static_dir = Path(__file__).parent.parent / "frontend" / "dist"
        if not static_dir.exists():
            raise RuntimeError(
                f"Frontend build not found at {static_dir}. "
                "Run 'cd frontend && npm run build' to create production build."
            )
        self.app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")
        # Serve root-level public files (favicons, robots.txt, etc.) from dist/ root.
        # Registered after all API routes so it only handles paths that don't match any route.
        self.app.mount("/", StaticFiles(directory=str(static_dir)), name="static-root")

    def _setup_routes(self):
        """Setup FastAPI routes"""
        from .routers import register_all
        register_all(self.app, self)

    async def initialize(self):
        """Initialize the Frontend application."""
        self.poll_relay.start_ui_relay()
        # Best-effort — Backend may still be starting; readiness gating against
        # this (blocking Frontend's own /ready until Backend reports ready) is
        # Phase 3's backend_supervisor.py job. For now just log connectivity.
        if await self.backend_client.health():
            logger.info("Backend reachable at %s", self.backend_client.base_url)
        else:
            logger.warning(
                "Backend not reachable at %s during Frontend startup — "
                "requests will fail until it's available",
                self.backend_client.base_url,
            )
        self._ready = True
        logger.info("Claude Code WebUI (Frontend) initialized")

    def _default_html(self) -> str:
        """Default HTML content when no index.html exists"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Claude Code WebUI</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body>
            <h1>Claude Code WebUI</h1>
            <p>Welcome to Claude Code WebUI. The frontend interface is being loaded.</p>
            <p>Please check that the static files are properly configured.</p>
        </body>
        </html>
        """

    async def cleanup(self):
        """Cleanup resources"""
        await self.poll_relay.stop()
        await self.backend_client.aclose()
        logger.info("Frontend cleanup completed")


def create_app(
    backend_url: str,
    backend_token: str,
    config_file: Path | None = None,
    auth_token: str | None = None,
    auth_enabled: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastAPI:
    """Create and configure the Frontend FastAPI application"""
    app_instance = ClaudeWebUI(
        backend_url=backend_url,
        backend_token=backend_token,
        config_file=config_file,
        auth_token=auth_token,
        auth_enabled=auth_enabled,
        host=host,
        port=port,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await app_instance.initialize()
        yield
        await app_instance.cleanup()

    app_instance.app.router.lifespan_context = _lifespan
    return app_instance.app
