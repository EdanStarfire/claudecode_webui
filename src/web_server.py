"""
FastAPI Frontend API shell for Claude Code WebUI (issue #498).

Serves the Vue SPA, authenticates the browser, and relays every domain request
to the Backend control-plane process. Holds zero session-execution code and
zero domain state — every session/project/legion read or write is a live
relay (generic reverse-proxy for most routes, poll-relay for the two
long-poll streams, split-ownership merge for /api/config).
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

from shared.event_queue import EventQueue

from .backend_client import BackendClient
from .backend_supervisor import BackendSupervisor
from .poll_relay import PollRelay

logger = logging.getLogger(__name__)

# Self-healing periodic resync of dynamic OAuth callback paths (issue #498) —
# catches the case where startup's retry window and every subsequent
# mcp-configs-mutation trigger all missed (e.g. Backend was down the whole time).
_OAUTH_RESYNC_INTERVAL_SECONDS = 60


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
        backend_url: str | None = None,
        backend_token: str | None = None,
        backend_supervisor: BackendSupervisor | None = None,
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

        # Either a supervisor that owns an auto-started local Backend (single-user
        # self-hosted default, issue #498 Phase 3), or a manually-configured
        # remote Backend URL/token — exactly one of the two is provided.
        self.backend_supervisor = backend_supervisor
        if backend_supervisor is not None:
            backend_url = backend_supervisor.base_url
            backend_token = backend_supervisor.token
        if not backend_url or not backend_token:
            raise ValueError("Either backend_supervisor or backend_url+backend_token is required")
        self.backend_client = BackendClient(backend_url, backend_token)

        # Local fan-out queues for the poll-relay — multiple browser tabs share
        # these instead of each opening their own upstream connection to Backend.
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {}
        self.poll_relay = PollRelay(self.backend_client, self.ui_queue, self.session_queues)

        # Issue #1789 (via #498): custom OAuth callback paths for shared MCP servers
        # are registered dynamically on Backend, but only Frontend is typically
        # publicly reachable — mirror Backend's registry as dynamic relay routes here.
        self._oauth_callback_paths: set[str] = set()
        self._oauth_resync_task: asyncio.Task | None = None

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

    # ── Issue #1789 (via #498): dynamic OAuth callback path relay ──────────────

    def _add_oauth_callback_relay_route(self, path: str) -> None:
        """Register a relay route for a Backend-side custom OAuth callback path.

        Mirrors Backend's own _add_dynamic_oauth_route (backend/web_server.py) —
        same race-safety argument applies (single event loop, single uvicorn worker).
        Inserted at the front of the route list so it's checked before the catch-all
        static mount at "/" (registered last, in __init__).
        """
        async def _handler(request: Request):
            return await self.backend_client.relay(request, path)

        self.app.router.routes.insert(0, Route(path, _handler, methods=["GET"]))
        AuthMiddleware.EXEMPT_PATHS.add(path)
        self._oauth_callback_paths.add(path)
        logger.info("Registered OAuth callback relay route: %s", path)

    def _remove_oauth_callback_relay_route(self, path: str) -> None:
        self.app.router.routes[:] = [
            r for r in self.app.router.routes if getattr(r, "path", None) != path
        ]
        AuthMiddleware.EXEMPT_PATHS.discard(path)
        self._oauth_callback_paths.discard(path)
        logger.info("Removed OAuth callback relay route: %s", path)

    async def resync_oauth_callback_paths(self) -> None:
        """Mirror Backend's currently-registered custom OAuth callback paths as
        dynamic relay routes here. Backend is typically 127.0.0.1-only and not
        independently reachable by an external OAuth provider — only Frontend has
        a public bind address in the common case, so a custom callback path can
        only ever complete by being relayed through Frontend. The default
        /oauth/callback path is always relayed regardless (src/routers/relay.py);
        this only handles non-default, explicitly-configured paths.
        """
        try:
            body = await self.backend_client.get_json("/api/internal/oauth-callback-paths")
        except httpx.HTTPError:
            logger.exception("Failed to resync OAuth callback paths from Backend")
            return
        desired = set(body.get("paths", [])) - {"/oauth/callback"}
        current = set(self._oauth_callback_paths)
        for path in desired - current:
            self._add_oauth_callback_relay_route(path)
        for path in current - desired:
            self._remove_oauth_callback_relay_route(path)

    async def _resync_oauth_callback_paths_with_retry(self, attempts: int = 3, delay: float = 1.0) -> None:
        """Startup helper: a transient failure right as Backend becomes ready
        shouldn't leave pre-existing custom OAuth callback configs unmirrored
        until the next unrelated MCP-config mutation happens to trigger a resync.
        Retries a few times before falling back to the periodic background task.
        """
        for attempt in range(1, attempts + 1):
            before = set(self._oauth_callback_paths)
            await self.resync_oauth_callback_paths()
            # resync_oauth_callback_paths() swallows httpx errors internally (logs
            # and returns) rather than raising, so "no visible change and Backend
            # was unreachable" is inferred, not caught — best-effort, not exact.
            try:
                reachable = await self.backend_client.health()
            except httpx.HTTPError:
                reachable = False
            if reachable:
                return
            logger.warning(
                "OAuth callback path resync attempt %d/%d: Backend unreachable, retrying",
                attempt, attempts,
            )
            if before != self._oauth_callback_paths:
                return
            await asyncio.sleep(delay)

    async def _periodic_oauth_resync_loop(self) -> None:
        """Self-healing background resync — catches the case where every retry at
        startup failed (Backend was down/unreachable for the whole retry window)
        and no MCP-config mutation happens afterward to trigger a resync otherwise.
        """
        while True:
            await asyncio.sleep(_OAUTH_RESYNC_INTERVAL_SECONDS)
            await self.resync_oauth_callback_paths()

    async def initialize(self):
        """Initialize the Frontend application."""
        if self.backend_supervisor is not None:
            await self.backend_supervisor.start()
            ready = await self.backend_supervisor.wait_ready(self.backend_client)
            if not ready:
                logger.error(
                    "Backend did not become ready during startup — "
                    "Frontend will report /ready=false until it recovers"
                )
                self._ready = False
                return
        else:
            # Manually-configured remote Backend — best-effort connectivity log,
            # no local process to wait on.
            if await self.backend_client.health():
                logger.info("Backend reachable at %s", self.backend_client.base_url)
            else:
                logger.warning(
                    "Backend not reachable at %s during Frontend startup — "
                    "requests will fail until it's available",
                    self.backend_client.base_url,
                )

        self.poll_relay.start_ui_relay()
        await self._resync_oauth_callback_paths_with_retry()
        self._oauth_resync_task = asyncio.create_task(
            self._periodic_oauth_resync_loop(), name="oauth_callback_resync"
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
        if self._oauth_resync_task is not None:
            self._oauth_resync_task.cancel()
            try:
                await self._oauth_resync_task
            except asyncio.CancelledError:
                pass
        await self.poll_relay.stop()
        await self.backend_client.aclose()
        # Frontend does not exit before Backend has had a chance to shut down
        # cleanly — SIGTERM, wait with timeout, then SIGKILL (backend_supervisor.stop).
        if self.backend_supervisor is not None:
            await self.backend_supervisor.stop()
        logger.info("Frontend cleanup completed")


def create_app(
    backend_url: str | None = None,
    backend_token: str | None = None,
    backend_supervisor: BackendSupervisor | None = None,
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
        backend_supervisor=backend_supervisor,
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
