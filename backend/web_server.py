"""
FastAPI application for the Backend control plane (issue #498).

Adapted from the pre-split ClaudeWebUI (src/web_server.py): owns SessionCoordinator,
Legion, permissions, watchdog, analytics, OAuth, and all other session-execution
state. Unlike the Frontend API, this process serves no static assets and has its
own bearer-token AuthMiddleware validating the backend-scoped credential (never the
browser's own token — two trust boundaries, never bridged).
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.event_queue import EventQueue

from .analytics.audit_writer import AuditWriter
from .analytics.database import AnalyticsDB
from .analytics_store import AnalyticsStore
from .application_service import ApplicationService
from .message_parser import MessageParser, MessageProcessor
from .permission_service import PermissionService
from .session_coordinator import SessionCoordinator
from .skill_manager import SkillManager
from .task_utils import task_done_log_exception

logger = logging.getLogger(__name__)


def _read_litellm_port_sync(data_dir: Path, default: int = 4000) -> int:
    """Read litellm_port from providers.json or legacy config.json without async I/O."""
    import json as _json
    try:
        providers = data_dir / "providers.json"
        if providers.exists():
            return _json.loads(providers.read_text(encoding="utf-8")).get("litellm_port", default)
        legacy = Path.home() / ".config" / "cc_webui" / "config.json"
        if legacy.exists():
            data = _json.loads(legacy.read_text(encoding="utf-8"))
            return data.get("provider_catalog", {}).get("litellm_port", default)
    except Exception:
        pass
    return default


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token auth for the Backend-scoped credential (issue #498).

    Frontend never forwards the browser's own auth token here — this validates a
    separate, backend-scoped credential (generated at Frontend startup for
    auto-start, or supplied via --remote-backend-token for a manual remote Backend).
    Exempts liveness/readiness (both must be reachable before/without a valid token
    so Frontend's supervisor can poll them) and the per-session Bearer-token
    endpoints consumed directly by Docker/LiteLLM sidecars, which use their own auth.
    """

    EXEMPT_PATHS = {'/health', '/ready'}
    # Issue #827: the per-session secrets resolve endpoint uses its own Bearer token
    # auth, not the backend-scoped operator token. Exempt it from global AuthMiddleware.
    EXEMPT_SUFFIXES = ('/secrets/resolve', '/routing')
    # Issue #1134: session-scoped proxy write-back endpoints (per-session Bearer token auth).
    _PROXY_WRITE_BACK_PATTERN = re.compile(r'^/api/sessions/[^/]+/(secrets/[^/]+|events)$')

    def __init__(self, app, auth_token: str):
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in self.EXEMPT_PATHS:
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


class BackendApp:
    """Backend control-plane application: sessions, projects, Legion, MCP tooling."""

    def __init__(self, data_dir: Path = None, experimental: bool = False,
                 mock_sdk: bool = False, fixtures_dir: Path | None = None,
                 available_fixtures: list[str] | None = None,
                 config_file: Path | None = None,
                 auth_token: str | None = None,
                 host: str = "127.0.0.1", port: int = 8000):
        self.app = FastAPI(title="Claude Code WebUI Backend", version="1.0.0")
        self.host = host
        self.port = port
        self.coordinator = SessionCoordinator(data_dir, experimental=experimental, host=host, port=port)
        self.service = ApplicationService(self.coordinator)
        self.config_file = config_file
        # Issue #1789: track config_id -> path for dynamic (path-only, no custom port) OAuth
        # callback routes registered directly on the main app's router.
        self._dynamic_oauth_routes: dict[str, str] = {}

        self.auth_token = auth_token

        # Wire mock SDK factory if mock mode active (issue #561)
        if mock_sdk and fixtures_dir:
            from .mock_sdk import MockClaudeSDK
            self.coordinator.set_sdk_factory(
                _mock_factory_for_fixtures(
                    MockClaudeSDK, fixtures_dir, set(available_fixtures or [])
                )
            )
        self.skill_manager = SkillManager()
        self.ui_queue = EventQueue()
        self.session_queues: dict[str, EventQueue] = {}

        # Inject ui_queue into LegionSystem so legion components can append events directly
        self.coordinator.legion_system.ui_queue = self.ui_queue

        # Issue #1130/#1131: Session watchdog service (created here, started in initialize())
        from .config_manager import load_config as _load_cfg
        from .session_watchdog import SessionWatchdogService
        _cfg = _load_cfg(config_file) if config_file else _load_cfg()
        self._watchdog = SessionWatchdogService(
            session_manager=self.coordinator.session_manager,
            template_manager=self.coordinator.template_manager,
            app_config=_cfg,
            ui_queue=self.ui_queue,
        )
        self.coordinator._watchdog = self._watchdog

        from .config_manager import AppConfigManager
        from .litellm_proxy_manager import LiteLLMProxyManager
        from .provider_catalog import ProviderCatalogManager
        self.app_config_manager = AppConfigManager(config_file=config_file) if config_file else AppConfigManager()
        self.provider_catalog_manager = ProviderCatalogManager(self.coordinator.provider_catalog_store)
        # Read litellm_port synchronously at init time — providers.json may not exist yet
        # (store.load() runs in coordinator.initialize()); fall back to legacy config then default 4000.
        _litellm_port = _read_litellm_port_sync(data_dir or Path("data"))
        self.litellm_proxy_manager = LiteLLMProxyManager(
            self.provider_catalog_manager,
            self.coordinator.credential_vault,
            port=_litellm_port,
            config_file=self.config_file,
        )
        self.coordinator.litellm_proxy_manager = self.litellm_proxy_manager

        # Issue #1127: Audit subsystem — analytics DB + AuditWriter
        _analytics_db_path = (data_dir or Path("data")) / "analytics.db"
        self._analytics_db = AnalyticsDB(_analytics_db_path)
        self._audit_writer = AuditWriter(self._analytics_db)
        # Issue #1125: Per-session token usage store (shares AnalyticsDB connection)
        self.analytics_store = AnalyticsStore(self._analytics_db)
        # Expose for router access
        self.analytics_db = self._analytics_db
        self.audit_writer = self._audit_writer
        # EventQueue to wake long-poll on new audit events
        self.audit_queue = EventQueue()

        # Initialize MessageProcessor for unified message formatting
        self._message_parser = MessageParser()
        self._message_processor = MessageProcessor(self._message_parser)

        # Permission lifecycle management (session_queues must be initialized first)
        self.permission_service = PermissionService(self.coordinator, self.session_queues)

        # Rate limiting for restart endpoint (issue #434)
        self._last_restart_time: float = 0

        # Setup routes
        self._setup_routes()

        # Backend AuthMiddleware always validates the backend-scoped token — unlike the
        # Frontend's browser-facing auth, there's no "auth disabled" mode here: the token
        # is generated (or supplied) at process start regardless of deployment shape.
        if self.auth_token:
            self.app.add_middleware(AuthMiddleware, auth_token=self.auth_token)
            logger.info("Backend authentication middleware enabled")

        @self.app.get("/health")
        async def health_check():
            """Liveness — always true once the process is up."""
            return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

        self._ready = False

        @self.app.get("/ready")
        async def ready_check():
            """Readiness — true once SessionCoordinator and its managers finish constructing."""
            return {"ready": self._ready, "timestamp": datetime.now(UTC).isoformat()}

        # Issue #699: Wire UI notification callback for comm sounds
        self.coordinator.legion_system.comm_router.set_ui_notification_callback(
            self._broadcast_comm_notification_to_ui
        )
        self.coordinator.legion_system.scheduler_service.set_schedule_broadcast_callback(
            self._broadcast_schedule_event
        )

        # Inject permission callback factory into SessionCoordinator
        # This allows legion components (overseer_controller, comm_router) to create
        # permission callbacks for spawned minions without direct access to web_server
        self.coordinator.set_permission_callback_factory(self._get_permission_callback_factory())
        logger.info("Permission callback factory injected into SessionCoordinator")

        # Inject message callback registrar into SessionCoordinator
        # This allows legion components to register message callbacks
        self.coordinator.set_message_callback_registrar(self._get_message_callback_registrar())
        logger.info("Message callback registrar injected into SessionCoordinator")

        # Issue #404: Inject resource broadcast callback into SessionCoordinator
        self.coordinator.set_resource_broadcast_callback(self._broadcast_resource_registered)
        logger.info("Resource broadcast callback injected into SessionCoordinator")

        # Issue #1530: Inject link broadcast callback into SessionCoordinator
        self.coordinator.set_link_broadcast_callback(self._broadcast_link_registered)
        logger.info("Link broadcast callback injected into SessionCoordinator")

        # Issue #976/#989: Inject OAuth refresh broadcast callback into OAuthRefreshManager
        self.coordinator.oauth_refresh_manager.set_broadcast_callback(self._broadcast_mcp_oauth_refreshed)
        logger.info("OAuth refresh broadcast callback injected into OAuthRefreshManager")

        # Issue #1789: Inject OAuth completion broadcast callback into OAuthCallbackListenerManager
        self.coordinator.oauth_callback_listener_manager.set_broadcast_callback(
            self._broadcast_mcp_oauth_complete
        )
        logger.info("OAuth completion broadcast callback injected into OAuthCallbackListenerManager")

        # Issue #1387: Wire vault refresh manager service + broadcast callback
        self.coordinator.vault_refresh_manager.set_service(self.service)
        self.coordinator.vault_refresh_manager.set_broadcast_callback(self._broadcast_vault_secret_event)
        logger.info("VaultRefreshManager wired")

        # Issue #1789: snapshot every literal path this app already owns, taken once here
        # (before any dynamic OAuth callback route is ever registered).
        self._reserved_route_paths: frozenset[str] = frozenset(
            r.path for r in self.app.router.routes if getattr(r, "path", None)
        )

    def _get_permission_callback_factory(self):
        def factory(session_id: str):
            return self.permission_service.create_permission_callback(session_id)
        return factory

    def _get_message_callback_registrar(self):
        def registrar(session_id: str):
            # Clear any existing callbacks to prevent duplicates
            self.coordinator.clear_message_callbacks(session_id)

            self.coordinator.add_message_callback(
                session_id,
                self._create_message_callback(session_id)
            )
            logger.info(f"Registered message callback for session {session_id}")

            if session_id not in self.session_queues:
                self.session_queues[session_id] = EventQueue()

            async def _broadcast_session_added():
                try:
                    session = await self.service._get_session_object(session_id)
                    if session and session.project_id:
                        project_dict = await self.service.get_project(session.project_id)
                        if project_dict:
                            self._broadcast_project_updated(
                                {k: v for k, v in project_dict.items() if k != "sessions"}
                            )
                            logger.debug(f"Appended project_updated for internally spawned session {session_id}")
                except Exception:
                    logger.exception(f"Error broadcasting project_updated for session {session_id}")

            task = asyncio.create_task(_broadcast_session_added(), name="broadcast_session_added")
            task.add_done_callback(task_done_log_exception)

        return registrar

    async def _on_watchdog_alert_audit(self, alert: dict) -> None:
        """Forward watchdog alerts to AuditWriter and wake audit long-poll."""
        try:
            await self._audit_writer.on_watchdog_alert(alert)
            self.audit_queue.append({"type": "audit_event", "data": alert})
        except Exception:
            logger.exception("_on_watchdog_alert_audit error (non-fatal)")

    async def _wake_audit_queue(self) -> None:
        """Signal audit long-poll that new rows are available after a flush."""
        try:
            self.audit_queue.append({"type": "audit_event_flush"})
        except Exception:
            logger.exception("_wake_audit_queue error (non-fatal)")

    async def _broadcast_comm_notification_to_ui(self, comm):
        """Issue #699: Push comm notification event to UI poll queue for audio alerts."""
        try:
            self.ui_queue.append({
                "type": "notification",
                "data": {
                    "event_type": "minion_comm",
                    "comm_type": comm.comm_type.value if hasattr(comm.comm_type, 'value') else str(comm.comm_type),
                    "from_minion_name": comm.from_minion_name or "Minion",
                    "comm_id": comm.comm_id,
                    "session_id": comm.from_minion_id,
                }
            })
            logger.debug(f"Appended UI notification for comm {comm.comm_id}")
        except Exception:
            logger.exception("Error appending comm notification to UI queue")

    async def _broadcast_schedule_event(self, legion_id: str, event: dict):
        """Broadcast schedule event to UI poll queue."""
        try:
            event["legion_id"] = legion_id
            self.ui_queue.append(event)
        except Exception:
            logger.exception("Error appending schedule event")

    def _broadcast_project_updated(self, project: dict) -> None:
        """Emit project_updated to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "project_updated", "data": {"project": project}})
            logger.debug("Appended project_updated for project %s", project.get("project_id"))
        except Exception:
            logger.exception("Error appending project_updated")

    def _broadcast_project_deleted(self, project_id: str) -> None:
        """Emit project_deleted to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "project_deleted", "data": {"project_id": project_id}})
            logger.debug("Appended project_deleted for project %s", project_id)
        except Exception:
            logger.exception("Error appending project_deleted")

    def _broadcast_state_change(self, session_id: str, session_dict: dict, timestamp: str | None = None) -> None:
        """Emit state_change to the global UI poll queue."""
        try:
            self.ui_queue.append({
                "type": "state_change",
                "data": {"session_id": session_id, "session": session_dict, "timestamp": timestamp}
            })
            logger.info("Appended state_change for session %s", session_id)
        except Exception:
            logger.exception("Error appending state_change")

    def _broadcast_server_restarting(self, pull_output: str, sync_output: str) -> None:
        """Emit server_restarting to the global UI poll queue."""
        try:
            self.ui_queue.append({
                "type": "server_restarting",
                "message": "Server is restarting...",
                "pull_output": pull_output,
                "sync_output": sync_output,
                "timestamp": datetime.now(UTC).isoformat(),
            })
        except Exception:
            logger.warning("Failed to append restart notice")

    def _broadcast_mcp_oauth_complete(self, server_id: str) -> None:
        """Emit mcp_oauth_complete to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "mcp_oauth_complete", "server_id": server_id})
        except Exception:
            logger.exception("Error appending mcp_oauth_complete")

    # ── Issue #1789: custom OAuth callback path/port for Shared MCP servers ────────────

    def oauth_callback_path_conflicts_with_app_route(self, path: str) -> bool:
        """True if `path` collides with a real, pre-existing application route."""
        return path in self._reserved_route_paths

    def _add_dynamic_oauth_route(self, path: str) -> None:
        """Register a dynamic OAuth callback route directly on the main app (path-only
        custom callback case — no dedicated listener/port involved).

        See src/web_server.py's pre-split version for the full race-safety argument
        (verified against fastapi==0.124.4/starlette==0.50.0, single event loop only).
        """
        from starlette.routing import Route

        complete_flow = self.coordinator.oauth_callback_listener_manager.complete_and_broadcast

        async def _handler(request):
            from .oauth_callback_listener_manager import render_oauth_callback
            return await render_oauth_callback(request, complete_flow)

        self.app.router.routes.insert(0, Route(path, _handler, methods=["GET"]))
        AuthMiddleware.EXEMPT_PATHS.add(path)
        logger.info(f"Registered dynamic OAuth callback route: {path}")

    def _remove_dynamic_oauth_route(self, path: str) -> None:
        """Remove a dynamic OAuth callback route + its EXEMPT_PATHS entry (paired teardown)."""
        self.app.router.routes[:] = [
            r for r in self.app.router.routes if getattr(r, "path", None) != path
        ]
        AuthMiddleware.EXEMPT_PATHS.discard(path)
        logger.info(f"Removed dynamic OAuth callback route: {path}")

    async def _sync_oauth_callback_for_config(self, config) -> None:
        """Reconcile custom OAuth callback routing/listener state for one MCP config."""
        config_id = config.id
        wants_custom = bool(
            config.enabled
            and config.shared_connection
            and (config.oauth_custom_callback_path or config.oauth_custom_callback_port)
        )
        wants_listener = wants_custom and (
            config.oauth_custom_callback_port is not None
            and config.oauth_custom_callback_port != self.port
        )
        wants_dynamic_route = wants_custom and not wants_listener
        desired_path = (config.oauth_custom_callback_path or "/oauth/callback") if wants_custom else None

        current_path = self._dynamic_oauth_routes.get(config_id)
        if wants_dynamic_route:
            if current_path != desired_path:
                if current_path is not None:
                    self._remove_dynamic_oauth_route(current_path)
                self._add_dynamic_oauth_route(desired_path)
                self._dynamic_oauth_routes[config_id] = desired_path
        elif current_path is not None:
            self._remove_dynamic_oauth_route(current_path)
            del self._dynamic_oauth_routes[config_id]

        if wants_listener:
            await self.coordinator.oauth_callback_listener_manager.apply_config(config)
        else:
            await self.coordinator.oauth_callback_listener_manager.remove_config(config_id)

    async def _remove_oauth_callback_for_config(self, config_id: str) -> None:
        """Tear down any dynamic route / listener registration for a deleted config."""
        path = self._dynamic_oauth_routes.pop(config_id, None)
        if path is not None:
            self._remove_dynamic_oauth_route(path)
        await self.coordinator.oauth_callback_listener_manager.remove_config(config_id)

    def _broadcast_vault_secret_event(self, secret_name: str, error: str | None) -> None:
        """Issue #1387: Emit secret_refreshed or secret_refresh_failed to the UI poll queue."""
        try:
            if error is None:
                self.ui_queue.append({"type": "secret_refreshed", "secret_name": secret_name})
            else:
                self.ui_queue.append({
                    "type": "secret_refresh_failed",
                    "secret_name": secret_name,
                    "error": error,
                })
        except Exception:
            logger.exception("Error appending vault secret event for %s", secret_name)

    def _broadcast_mcp_oauth_refreshed(self, server_id: str) -> None:
        """Issue #976: Emit mcp_oauth_refreshed to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "mcp_oauth_refreshed", "server_id": server_id})
        except Exception:
            logger.exception("Error appending mcp_oauth_refreshed")

    def _broadcast_rate_limits_update(self, data: dict) -> None:
        """Issue #899: Emit rate_limits_update to the global UI poll queue."""
        try:
            self.ui_queue.append({"type": "rate_limits_update", "data": data})
        except Exception:
            logger.exception("Error appending rate_limits_update")

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict):
        """Issue #404: Called by ResourceMCPTools when a resource is registered."""
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "resource_registered",
                    "resource": resource_metadata,
                    "timestamp": datetime.now(UTC).isoformat()
                })
                logger.debug(f"Appended resource_registered for {resource_metadata.get('resource_id')} to session {session_id}")
        except Exception:
            logger.exception("Error appending resource_registered")

    async def _broadcast_link_registered(self, session_id: str, link: dict):
        """Issue #1530: Called by LinksMCPTools when a link is registered or updated."""
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "link_registered",
                    "link": link,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
                logger.debug(f"Appended link_registered for '{link.get('label')}' to session {session_id}")
        except Exception:
            logger.exception("Error appending link_registered")

    async def _broadcast_queue_update(self, session_id: str, action: str, item: dict):
        """Issue #500: Real-time queue status updates."""
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "queue_update",
                    "action": action,
                    "item": item,
                    "pending_count": self.coordinator.queue_manager.get_pending_count(session_id),
                    "timestamp": datetime.now(UTC).isoformat()
                })
        except Exception:
            logger.exception("Error appending queue_update")

    async def _broadcast_usage_update(self, session_id: str, usage: dict):
        """Append usage_updated event to session poll queue (issue #1125)."""
        try:
            if session_id in self.session_queues:
                self.session_queues[session_id].append({
                    "type": "usage_updated",
                    "session_id": session_id,
                    "usage": usage,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
        except Exception:
            logger.exception("Error appending usage_updated")

    def _cleanup_pending_permissions_for_session(self, session_id: str):
        """Clean up pending permissions for a specific session by auto-denying them"""
        self.permission_service.cleanup_pending_for_session(session_id)

    async def initialize(self):
        """Initialize the Backend application."""
        from .config_manager import load_config
        await self.coordinator.initialize()

        try:
            await self.litellm_proxy_manager.start()
        except Exception:
            logger.exception(
                "LiteLLM proxy failed to start — catalog-selected sessions will be unavailable; "
                "native sessions continue normally"
            )

        # Issue #1789: start custom OAuth callback routes/listeners for already-configured
        # MCP servers (path-only dynamic routes + dedicated custom-port listeners).
        existing_mcp_configs = await self.coordinator.mcp_config_manager.list_configs()
        for mcp_cfg in existing_mcp_configs:
            try:
                await self._sync_oauth_callback_for_config(mcp_cfg)
            except Exception:
                logger.exception(
                    f"Failed to start OAuth callback routing for MCP config {mcp_cfg.id} — "
                    "custom-callback OAuth will be unavailable for it until fixed"
                )

        config = load_config(self.config_file) if self.config_file else load_config()
        if config.features.skill_sync_enabled:
            await self.skill_manager.sync()
        else:
            logger.info("Skill syncing disabled by config")

        # Create event queues for all existing sessions
        sessions_result = await self.coordinator.list_sessions()
        for s in sessions_result.get("sessions", []):
            sid = s.get('session_id') or (s.get('session') or {}).get('session_id')
            if sid:
                self.session_queues[sid] = EventQueue()

        # Register callbacks
        self.coordinator.add_state_change_callback(self._on_state_change)
        self.coordinator.add_session_reset_callback(self._on_session_reset)
        self.coordinator.add_tool_call_broadcast_callback(self._on_tool_call_broadcast)
        self.coordinator.set_rate_limit_broadcast_callback(self._broadcast_rate_limits_update)

        # Issue #500: Wire queue processor broadcast callback
        self.coordinator.queue_processor.set_broadcast_callback(self._broadcast_queue_update)
        self.coordinator.set_enqueue_broadcast_callback(self._broadcast_queue_update)

        # Issue #1125: Wire analytics usage broadcast callback
        self.coordinator._usage_broadcast_callback = self._broadcast_usage_update

        # Issue #1050: Best-effort proxy image check on startup (informational only)
        from shared.logging_config import get_logger as _get_logger
        _startup_logger = _get_logger('coordinator', category='PROXY')
        startup_config = load_config(self.config_file) if self.config_file else load_config()
        if startup_config.proxy.proxy_image:
            from .docker_utils import check_proxy_image_available
            image_ok = await check_proxy_image_available(startup_config.proxy.proxy_image)
            if image_ok:
                _startup_logger.info(f"Default proxy image '{startup_config.proxy.proxy_image}' available.")
            else:
                _startup_logger.info(
                    f"Default proxy image '{startup_config.proxy.proxy_image}' not found locally. "
                    f"It will be auto-built on first proxy-enabled session start."
                )

        # Issue #1387: Start vault OAuth2 background refresh manager
        await self.coordinator.vault_refresh_manager.start()
        logger.info("VaultRefreshManager started")

        # Issue #1130: Start session watchdog service
        await self._watchdog.start()

        # Issue #1127: Initialize audit subsystem
        try:
            await self._analytics_db.initialize()
            self.coordinator.set_audit_writer(self._audit_writer)
            self.coordinator.set_analytics_store(self.analytics_store)
            self.coordinator.session_manager.add_state_change_callback(
                self._audit_writer.on_session_state_change
            )
            self._watchdog.on_alert.append(self._on_watchdog_alert_audit)
            self.coordinator.legion_system.comm_router.audit_writer = self._audit_writer
            self._audit_writer.start()
            self._audit_writer.on_flush = self._wake_audit_queue
            logger.info("Audit subsystem initialized")
        except Exception:
            logger.exception("Audit subsystem failed to initialize — audit will be unavailable")
            self._audit_writer = AuditWriter(None)
            self.audit_writer = self._audit_writer

        self._ready = True
        logger.info("Backend initialized")

    def _setup_routes(self):
        """Setup FastAPI routes"""
        from .routers import register_all
        register_all(self.app, self)

    def _create_message_callback(self, session_id: str):
        """Create message callback for poll queue broadcasting using unified MessageProcessor"""
        async def callback(session_id: str, message_data: Any):
            logger.info(f"Message callback triggered for session {session_id}, message type: {getattr(message_data, 'type', 'unknown')}")
            try:
                # Issue #1486: assistant_delta — lightweight envelope, no MessageProcessor
                if isinstance(message_data, dict) and message_data.get("type") == "assistant_delta":
                    if message_data.get("parent_tool_use_id") is not None:
                        # Out of scope for v1: subagent streaming deltas are dropped
                        logger.debug(
                            f"Dropped subagent assistant_delta for session {session_id} "
                            f"(parent_tool_use_id={message_data['parent_tool_use_id']})"
                        )
                        return
                    if session_id in self.session_queues:
                        self.session_queues[session_id].append({
                            "type": "assistant_delta",
                            "session_id": session_id,
                            "data": {
                                "uuid": message_data["uuid"],
                                "event": message_data["event"],
                                "message_id": message_data.get("message_id"),
                                "tool_use_id": message_data.get("tool_use_id"),
                            },
                            "timestamp": datetime.now(UTC).isoformat(),
                        })
                    return

                # Process message and prepare for poll queue using MessageProcessor
                if hasattr(message_data, '__dict__'):
                    # Handle ParsedMessage objects (from MessageProcessor)
                    websocket_data = self._message_processor.prepare_for_websocket(message_data)
                    parsed_message = message_data
                else:
                    # Handle raw dict messages - process them first
                    parsed_message = self._message_processor.process_message(message_data, source="websocket")
                    websocket_data = self._message_processor.prepare_for_websocket(parsed_message)

                # Issue #1000/#1486: Propagate message_id for frontend streaming dedup.
                if isinstance(message_data, dict) and 'message_id' in message_data:
                    websocket_data['message_id'] = message_data['message_id']
                elif isinstance((meta := getattr(message_data, 'metadata', None)), dict) and meta.get('message_id'):
                    websocket_data['message_id'] = meta['message_id']
                elif parsed_message.metadata and parsed_message.metadata.get('message_id'):
                    websocket_data['message_id'] = parsed_message.metadata['message_id']

                # Wrap in standard poll queue envelope
                serialized = {
                    "type": "message",
                    "session_id": session_id,
                    "data": websocket_data,
                    "timestamp": datetime.now(UTC).isoformat()
                }

                # Issue #1694: Append the assistant envelope — and mark it on the message-
                # emitted barrier — BEFORE emitting tool_call updates below.
                if session_id in self.session_queues:
                    self.session_queues[session_id].append(serialized)
                    logger.info(f"Appended message to session queue for {session_id}")

                message_id_for_barrier = websocket_data.get('message_id')
                if message_id_for_barrier:
                    self.coordinator.mark_assistant_message_emitted(session_id, message_id_for_barrier)

                # Issue #324: Emit tool_call messages for tool lifecycle events
                await self._emit_tool_call_updates(session_id, parsed_message)

                # Issue #952: Emit context_update after result messages using SDK API
                msg_type = getattr(parsed_message, 'type', None)
                if msg_type is not None:
                    msg_type_str = msg_type.value if hasattr(msg_type, 'value') else str(msg_type)
                else:
                    msg_type_str = websocket_data.get("type", "")
                if msg_type_str == "result" and session_id in self.session_queues:
                    ctx = await self.coordinator.get_context_usage(session_id)
                    if ctx and ctx.get("totalTokens"):
                        self.session_queues[session_id].append({
                            "type": "context_update",
                            "session_id": session_id,
                            "input_tokens": ctx["totalTokens"],
                            "context_window": ctx["maxTokens"],
                            "context_pct": round(ctx["percentage"], 1),
                            "timestamp": datetime.now(UTC).isoformat(),
                        })

            except Exception:
                logger.exception("Error in message callback")

        return callback

    async def _emit_tool_call_updates(self, session_id: str, parsed_message: Any):
        """Issue #324: Emit unified tool_call messages for tool lifecycle events."""
        try:
            msg_type = getattr(parsed_message, 'type', None)
            if msg_type:
                msg_type = msg_type.value if hasattr(msg_type, 'value') else str(msg_type)

            metadata = getattr(parsed_message, 'metadata', {}) or {}

            # Handle tool_use in assistant messages
            if msg_type == 'assistant':
                tool_uses = metadata.get('tool_uses', [])
                # Issue #195: Propagate parent_tool_use_id from message to child tool_calls
                parent_tool_use_id = metadata.get('parent_tool_use_id')
                for tool_use in tool_uses:
                    tool_id = tool_use.get('id')
                    tool_name = tool_use.get('name')
                    input_params = tool_use.get('input', {})

                    if tool_id and tool_name:
                        tool_call = self.coordinator.create_tool_call(
                            session_id=session_id,
                            tool_use_id=tool_id,
                            name=tool_name,
                            input_params=input_params,
                            requires_permission=False,  # Will be updated if permission is requested
                            parent_tool_use_id=parent_tool_use_id,
                            message_id=metadata.get('message_id'),
                        )

                        tool_call_data = tool_call.to_dict()
                        tool_call_data["type"] = "tool_call"

                        websocket_message = {
                            "type": "message",
                            "session_id": session_id,
                            "data": tool_call_data,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                        if session_id in self.session_queues:
                            self.session_queues[session_id].append(websocket_message)
                        logger.debug(f"Emitted tool_call pending for {tool_name} ({tool_id}) in session {session_id}")

            # Handle tool_results in user messages
            elif msg_type == 'user':
                tool_results = metadata.get('tool_results', [])
                for tool_result in tool_results:
                    tool_use_id = tool_result.get('tool_use_id')
                    result_content = tool_result.get('content')
                    is_error = tool_result.get('is_error', False)

                    if tool_use_id:
                        # Issue #1593/#1730: resolve sender attachment resource IDs for send_comm
                        sender_attachments = None
                        if not is_error:
                            active_tc = self.coordinator._get_active_tool_call(session_id, tool_use_id)
                            if active_tc and active_tc.name == "mcp__legion__send_comm":
                                sender_attachments = self.coordinator._parse_send_comm_sender_attachments(
                                    result_content
                                )

                        updated_tool_call = self.coordinator.update_tool_call_result(
                            session_id=session_id,
                            tool_use_id=tool_use_id,
                            result=result_content,
                            is_error=is_error,
                            triggering_message=tool_result,  # Issue #494: embed ToolResultBlock
                            sender_attachments=sender_attachments,
                        )

                        if updated_tool_call:
                            tool_call_data = updated_tool_call.to_dict()
                            tool_call_data["type"] = "tool_call"

                            websocket_message = {
                                "type": "message",
                                "session_id": session_id,
                                "data": tool_call_data,
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            if session_id in self.session_queues:
                                self.session_queues[session_id].append(websocket_message)
                            logger.debug(
                                f"Emitted tool_call {'failed' if is_error else 'completed'} "
                                f"for {tool_use_id} in session {session_id}"
                            )

        except Exception:
            logger.exception("Error emitting tool_call updates")

    async def _on_state_change(self, state_data: dict):
        """Handle session state changes"""
        try:
            session_id = state_data.get("session_id")
            if session_id:
                session_info_dict = await self.coordinator.get_session_info(session_id)
                if session_info_dict:
                    session_dict = session_info_dict.get("session", {})
                    # Issue #500: Include queue status in state changes
                    session_dict["queue_pending_count"] = (
                        self.coordinator.queue_manager.get_pending_count(session_id)
                    )
                    self._broadcast_state_change(session_id, session_dict, state_data.get("timestamp"))
        except Exception:
            logger.exception("Error handling state change")

    def _on_tool_call_broadcast(self, session_id: str, tool_call_data: dict):
        """Issue #520: Append tool_call message to session poll queue. Called synchronously from coordinator."""
        if session_id in self.session_queues:
            self.session_queues[session_id].append({
                "type": "tool_call",
                "session_id": session_id,
                "data": tool_call_data,
                "timestamp": datetime.now(UTC).isoformat(),
            })

    async def _on_session_reset(self, session_id: str):
        """Issue #500: Append session_reset to UI queue so frontend clears stale messages."""
        try:
            message = {
                "type": "session_reset",
                "data": {"session_id": session_id},
            }
            self.ui_queue.append(message)
            logger.info(f"Appended session_reset for {session_id} to UI queue")
        except Exception:
            logger.exception("Error appending session_reset")

    async def _run_git_command(
        self, args: list[str], cwd: str, allow_nonzero: bool = False
    ) -> str | None:
        """Run a git command via asyncio.create_subprocess_exec and return stdout, or None on error."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0 and not allow_nonzero:
                return None
            return stdout.decode().strip()
        except (TimeoutError, FileNotFoundError, OSError) as e:
            logger.debug(f"Git command failed: {args} - {e}")
            return None

    async def cleanup(self):
        """Cleanup resources"""
        # Issue #1387: Stop vault refresh manager
        await self.coordinator.vault_refresh_manager.stop()
        # Issue #1130: Stop session watchdog service
        if hasattr(self, '_watchdog') and self._watchdog is not None:
            await self._watchdog.stop()
        try:
            await self.litellm_proxy_manager.stop()
        except Exception:
            logger.exception("Error stopping LiteLLM proxy during cleanup")
        try:
            await self.coordinator.oauth_callback_listener_manager.shutdown()
        except Exception:
            logger.exception("Error stopping OAuth callback listeners during cleanup")
        # Issue #1789: tear down any remaining dynamic OAuth callback routes + their
        # AuthMiddleware.EXEMPT_PATHS entries.
        for path in list(self._dynamic_oauth_routes.values()):
            try:
                self._remove_dynamic_oauth_route(path)
            except Exception:
                logger.exception(f"Error removing dynamic OAuth callback route {path} during cleanup")
        self._dynamic_oauth_routes.clear()
        await self.coordinator.cleanup()
        logger.info("Backend cleanup completed")


def _mock_factory_for_fixtures(mock_cls, fixtures_dir: Path, available_fixtures: set[str]):
    """Create a factory that maps session names to fixture directories (issue #561)."""
    def factory(session_id, working_directory, **kwargs):
        session_name = kwargs.pop("session_name", None)
        if session_name:
            candidate = fixtures_dir / session_name
            if candidate.is_dir():
                kwargs["session_dir"] = str(candidate)
            else:
                raise ValueError(
                    f"No fixture found for session name '{session_name}'. "
                    f"Available fixtures: {', '.join(sorted(available_fixtures))}"
                )
        return mock_cls(session_id=session_id, working_directory=working_directory, **kwargs)
    return factory


def create_app(
    data_dir: Path = None,
    experimental: bool = False,
    mock_sdk: bool = False,
    fixtures_dir: Path | None = None,
    available_fixtures: list[str] | None = None,
    auth_token: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastAPI:
    """Create and configure the Backend FastAPI application"""
    app_instance = BackendApp(
        data_dir, experimental=experimental,
        mock_sdk=mock_sdk, fixtures_dir=fixtures_dir,
        available_fixtures=available_fixtures,
        auth_token=auth_token,
        host=host, port=port,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await app_instance.initialize()
        yield
        await app_instance.cleanup()

    app_instance.app.router.lifespan_context = _lifespan
    return app_instance.app
