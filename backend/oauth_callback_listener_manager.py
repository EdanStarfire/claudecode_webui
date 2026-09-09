"""Manages custom OAuth callback listeners for Shared MCP servers (issue #1789).

Handles the *custom-port* case only: a config with `oauth_custom_callback_port` set to
something other than the main app's own port needs a dedicated listener, since the main
app's uvicorn process only binds its own port. Structurally modeled on
`LiteLLMProxyManager` — each listener is a minimal ASGI app bound to its own
`uvicorn.Server`, with the same start()/stop()/rebuild() lifecycle and bind-failure-to-
RuntimeError conversion.

One listener can serve multiple registered paths, so two configs can share a custom port
via distinct paths (e.g. `:8765/callback-a` and `:8765/callback-b` both routed by the same
listener). The *path-only* case (custom path, no custom port, or port == main app's own
port) is handled separately in `web_server.py` via a dynamic route mutated directly onto
the main FastAPI app — see `ClaudeWebUI._add_dynamic_oauth_route`.

`render_oauth_callback()` is the single shared HTML/parsing helper reused by:
  - the static `/oauth/callback` route (src/routers/mcp.py)
  - the main app's dynamic path-only routes (src/web_server.py)
  - each `OAuthCallbackListener`'s own routes, below
so all three code paths render identical success/error pages and forward
(state, code) into the same completion logic.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import HTMLResponse

    from .oauth_manager import OAuthFlowManager

from shared.logging_config import get_logger

logger = logging.getLogger(__name__)
debug_logger = get_logger('oauth', category='OAUTH_CALLBACK')

CompleteFlowFn = Callable[[str, str], Awaitable[str]]
DeniedFn = Callable[[str | None, str, str], Awaitable[None]]


def _error_html(message: str, title: str = "Authorization Failed") -> str:
    return f"""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; {title}</h2>
<p>{html.escape(message)}</p>
<p>You may close this window.</p>
</body></html>"""


def _success_html() -> str:
    return """<!DOCTYPE html>
<html><head><title>Connected</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x2705; Connected Successfully</h2>
<p>MCP server authorized. You may close this window.</p>
<script>window.close();</script>
</body></html>"""


async def render_oauth_callback(
    request: Request,
    complete_flow: CompleteFlowFn,
    on_denied: DeniedFn | None = None,
) -> HTMLResponse:
    """Parse code/state/error from an OAuth redirect and render the result page.

    `complete_flow` is an async `(state, code) -> server_id` callable that performs the
    token exchange and any post-completion broadcast; it should raise on failure.

    `on_denied` (issue #1871) is an optional async `(state, error, error_description) ->
    None` callable invoked when the provider redirects back with an `error` param
    (user denied consent, or a provider-side error like invalid_client) — before the
    error page is returned. MCP flows pass None (unchanged behavior: no server-side
    trace beyond the log line below). Standalone vault-secret flows use this to
    broadcast the denial to the still-open settings panel, since a "popup closed before
    completing authorization" never reaches this route at all (the browser closes the
    popup while it's still showing the *provider's* page) and so needs no such hook.
    """
    from starlette.responses import HTMLResponse

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        error_desc = request.query_params.get("error_description", error)
        # Always visible: the authorization server's own rejection (e.g. Google's
        # "invalid_client" for a misconfigured confidential client) previously only ever
        # appeared in the ephemeral browser popup — if the user didn't read it before the
        # popup closed, there was zero server-side trace of why the flow failed.
        logger.error("OAuth callback received error=%s description=%s", error, error_desc)
        if on_denied is not None:
            try:
                await on_denied(state, error, error_desc)
            except Exception:
                logger.exception("Error in OAuth on_denied callback")
        return HTMLResponse(content=_error_html(error_desc), status_code=400)

    if not code or not state:
        debug_logger.debug(
            "OAuth callback missing code and/or state (code_present=%s, state_present=%s)",
            bool(code), bool(state),
        )
        return HTMLResponse(
            content=_error_html(
                "Authorization code or state parameter missing.", title="Missing Parameters"
            ),
            status_code=400,
        )

    debug_logger.debug("OAuth callback received: state=%s…", state[:8])
    try:
        # Issue #1871: `result_id` is an MCP server_id for MCP flows but a vault
        # secret base_name for standalone flows — labeled generically since this
        # helper doesn't know which.
        result_id = await complete_flow(state, code)
        debug_logger.info("OAuth callback completed successfully (id=%s)", result_id)
        return HTMLResponse(content=_success_html())
    except Exception as e:
        logger.exception("OAuth callback error")
        return HTMLResponse(content=_error_html(str(e)), status_code=400)


class OAuthCallbackListener:
    """A single uvicorn listener serving one or more OAuth callback paths on one port."""

    def __init__(
        self,
        host: str,
        port: int,
        complete_flow: CompleteFlowFn,
        on_denied: DeniedFn | None = None,
    ):
        self._host = host
        self._port = port
        self._complete_flow = complete_flow
        self._on_denied = on_denied
        self._paths: set[str] = set()
        self._app = None
        self._server = None
        self._server_task: asyncio.Task | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self._paths)

    @property
    def is_running(self) -> bool:
        return self._server_task is not None and not self._server_task.done()

    def add_path(self, path: str) -> None:
        self._paths.add(path)

    def remove_path(self, path: str) -> None:
        self._paths.discard(path)

    async def start(self) -> None:
        """Build the ASGI app from currently registered paths and launch uvicorn."""
        self._app = self._build_app()
        await self._launch_server()

    async def stop(self) -> None:
        """Gracefully stop the uvicorn task (mirrors LiteLLMProxyManager.stop())."""
        if self._server_task and not self._server_task.done():
            if self._server is not None:
                self._server.should_exit = True  # type: ignore[attr-defined]
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError, SystemExit):
                self._server_task.cancel()
                try:
                    await self._server_task
                except (asyncio.CancelledError, SystemExit):
                    pass
        self._server = None
        self._server_task = None

    async def rebuild(self) -> None:
        """Restart with the current path set — call after add_path()/remove_path()."""
        await self.stop()
        await self.start()

    def _build_app(self):
        from starlette.applications import Starlette
        from starlette.routing import Route

        complete_flow = self._complete_flow
        on_denied = self._on_denied

        async def _handle(request):
            return await render_oauth_callback(request, complete_flow, on_denied)

        routes = [Route(path, _handle, methods=["GET"]) for path in sorted(self._paths)]
        return Starlette(routes=routes)

    async def _launch_server(self) -> None:
        import uvicorn

        config = uvicorn.Config(self._app, host=self._host, port=self._port, log_level="warning")
        server = uvicorn.Server(config)

        async def _serve() -> None:
            # uvicorn calls sys.exit(1) on bind failure, raising SystemExit *inside* this
            # task. asyncio's own task machinery re-raises SystemExit/KeyboardInterrupt
            # (rather than just storing them, as it does for every other exception type)
            # straight out of the event loop's own run_once()/run_forever() — which would
            # crash the entire process, not just this listener. Catching it here, inside
            # the coroutine itself, converts it to a plain RuntimeError *before* it ever
            # reaches that special-cased path, so the wait loop below can retrieve it via
            # `self._server_task.exception()` like any other failure.
            try:
                await server.serve()
            except SystemExit as e:
                raise RuntimeError(
                    f"OAuth callback listener failed to start (port {self._port} in use?)"
                ) from e

        self._server = server
        self._server_task = asyncio.create_task(_serve())

        # Wait until uvicorn has actually bound the port before returning (mirrors
        # LiteLLMProxyManager._launch_server()'s bind-failure-to-RuntimeError conversion).
        deadline = asyncio.get_event_loop().time() + 10.0
        while not server.started:
            if self._server_task.done():
                exc = self._server_task.exception()
                raise exc or RuntimeError("OAuth callback listener task exited during startup")
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError(
                    f"OAuth callback listener failed to bind port {self._port} within 10 seconds"
                )
            await asyncio.sleep(0.05)


class OAuthCallbackListenerManager:
    """Owns zero-or-more `OAuthCallbackListener` instances, keyed by port.

    Also owns the composed "complete the OAuth flow, then broadcast" function used by
    every entry point that can receive an OAuth redirect (the static route, the main
    app's dynamic path-only routes, and each managed listener) — mirrors how
    `oauth_refresh_manager`'s broadcast callback is injected post-construction in
    `web_server.py`.
    """

    def __init__(self, oauth_manager: OAuthFlowManager, host: str):
        self._oauth_manager = oauth_manager
        self._host = host
        self._broadcast_complete: Callable[[str], None] | None = None
        # Issue #1871: standalone (vault-secret) flow completion/denial handlers,
        # injected post-construction like _broadcast_complete above. None until
        # web_server.py wires them — MCP-only deployments never set these.
        self._standalone_complete: CompleteFlowFn | None = None
        self._standalone_denied: DeniedFn | None = None
        self._listeners: dict[int, OAuthCallbackListener] = {}
        # config_id (MCP) or flow_id (standalone, issue #1871) -> (port, path). The two
        # id spaces never collide: MCP config ids and OAuth `state` flow ids come from
        # unrelated ID generators, and each registration is independently keyed.
        self._registrations: dict[str, tuple[int, str]] = {}
        self._lock = asyncio.Lock()

    def set_broadcast_callback(self, callback: Callable[[str], None]) -> None:
        self._broadcast_complete = callback

    def set_standalone_callbacks(
        self, complete_fn: CompleteFlowFn, denied_fn: DeniedFn
    ) -> None:
        """Issue #1871: inject the standalone vault-secret flow completion/denial
        handlers. complete_and_broadcast()/denied_and_broadcast() route to these
        instead of the MCP path whenever the pending flow's context.flow_type is a
        standalone one."""
        self._standalone_complete = complete_fn
        self._standalone_denied = denied_fn

    async def _is_standalone_flow(self, state: str) -> bool:
        context = await self._oauth_manager.get_pending_context(state)
        return bool(context) and context.get("flow_type") in (
            "standalone_secret", "standalone_reconnect",
        )

    async def complete_and_broadcast(self, state: str, code: str) -> str:
        """Exchange the auth code for tokens, then notify the UI. Shared by every
        OAuth callback entry point (static route, dynamic route, listener routes).

        Issue #1871: routes to the standalone completion handler instead of the MCP
        path when the pending flow's context marks it as a standalone vault-secret
        flow — peeked before popping so the routing decision doesn't consume the
        pending entry itself.
        """
        if await self._is_standalone_flow(state) and self._standalone_complete is not None:
            return await self._standalone_complete(state, code)
        server_id = await self._oauth_manager.complete_flow(state, code)
        if self._broadcast_complete is not None:
            self._broadcast_complete(server_id)
        return server_id

    async def denied_and_broadcast(self, state: str | None, error: str, error_description: str) -> None:
        """Issue #1871: notify a standalone flow's caller of a provider-side denial
        or error. No-op for MCP flows (and for malformed callbacks with no state) —
        matches existing MCP behavior of only logging server-side."""
        if not state or self._standalone_denied is None or not await self._is_standalone_flow(state):
            return
        await self._standalone_denied(state, error, error_description)

    def is_port_active(self, port: int) -> bool:
        listener = self._listeners.get(port)
        return listener is not None and listener.is_running

    async def apply_config(self, config) -> None:
        """Reconcile listener state for one config's *custom-port* registration.

        No-ops (and tears down any stale registration) unless the config is enabled,
        shared_connection is on, and oauth_custom_callback_port is set. Call this for
        every create/update — it is idempotent and safe to call unconditionally.
        """
        async with self._lock:
            await self._remove_registration_locked(config.id)
            if config.enabled and config.shared_connection and config.oauth_custom_callback_port is not None:
                await self._add_registration_locked(
                    config.id,
                    config.oauth_custom_callback_port,
                    config.oauth_custom_callback_path or "/oauth/callback",
                )

    async def remove_config(self, config_id: str) -> None:
        """Tear down any custom-port registration for a deleted config."""
        async with self._lock:
            await self._remove_registration_locked(config_id)

    async def register_transient(self, flow_id: str, port: int, path: str) -> None:
        """Issue #1871: register a *transient*, flow_id-keyed custom-port callback
        listener for a standalone OAuth flow — the same underlying registration
        mechanism as apply_config(), just keyed by an ephemeral flow_id instead of a
        persisted MCP config_id."""
        async with self._lock:
            await self._add_registration_locked(flow_id, port, path)

    async def unregister_transient(self, flow_id: str) -> None:
        """Tear down a standalone flow's transient listener registration (completion,
        cancellation, or denial) — idempotent no-op if none was registered."""
        async with self._lock:
            await self._remove_registration_locked(flow_id)

    async def shutdown(self) -> None:
        async with self._lock:
            for listener in list(self._listeners.values()):
                await listener.stop()
            self._listeners.clear()
            self._registrations.clear()

    async def _remove_registration_locked(self, config_id: str) -> None:
        old = self._registrations.pop(config_id, None)
        if old is None:
            return
        port, path = old
        listener = self._listeners.get(port)
        if listener is None:
            return
        listener.remove_path(path)
        if listener.paths:
            await listener.rebuild()
        else:
            await listener.stop()
            del self._listeners[port]

    async def _add_registration_locked(self, config_id: str, port: int, path: str) -> None:
        is_new_listener = port not in self._listeners
        listener = self._listeners.get(port)
        if listener is None:
            listener = OAuthCallbackListener(
                self._host, port, self.complete_and_broadcast, self.denied_and_broadcast
            )
        listener.add_path(path)
        try:
            if listener.is_running:
                await listener.rebuild()
            else:
                await listener.start()
        except Exception:
            # Bind (or rebuild) failed — don't leave a dead/half-registered listener behind.
            # A brand-new listener is simply discarded; an existing one just loses the path
            # we were trying to add, so remove_config() on any of its other configs still works.
            if is_new_listener:
                self._listeners.pop(port, None)
            else:
                listener.remove_path(path)
            raise
        self._listeners[port] = listener
        self._registrations[config_id] = (port, path)
