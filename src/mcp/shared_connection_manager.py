"""
SharedMcpConnectionManager — Issue #1484

Owns one upstream mcp.client.session.ClientSession per shared-enabled
McpServerConfig.id. Lazily opened on first session attach, kept alive for the
process lifetime (or until config delete with refcount==0). OAuth tokens are
read from oauth_manager and refreshed via oauth_refresh_manager. On token
refresh the upstream connection is torn down and re-opened with the new token.

Issue #1505 fix (Approach B): a per-connection owner asyncio.Task holds the
AsyncExitStack for the connection's entire lifetime.  Foreign tasks signal
close via asyncio.Event and await a closed_future — never calling
exit_stack.aclose() directly.  This avoids the anyio cancel-scope task-affinity
violation that previously caused RuntimeError on OAuth token refresh.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from mcp import ClientSession

from ..logging_config import get_logger
from ..task_utils import task_done_log_exception

_logger = get_logger("mcp_shared", category="MCP_SHARED")


@dataclass
class _SharedConn:
    cfg_id: str
    session: ClientSession | None = None
    # Stale tools are retained across reconnect; refreshed on initialize().
    cached_tools: list[Tool] = field(default_factory=list)
    reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refcount: int = 0
    draining: bool = False
    # Owner-task fields (Approach B, issue #1505)
    owner_task: asyncio.Task | None = None
    ready_future: asyncio.Future | None = None
    close_event: asyncio.Event | None = None
    closed_future: asyncio.Future | None = None
    generation: int = 0
    last_close_error: Exception | None = None


class SharedMcpConnectionManager:
    # Max time a tool call waits for reconnect_lock before failing
    RECONNECT_WAIT_SECONDS = 5.0
    # Max time to wait for the owner task to finish closing
    CLOSE_TIMEOUT_SECONDS = 10.0

    def __init__(self, oauth_manager, oauth_refresh_manager, credential_vault):
        self._oauth_manager = oauth_manager
        self._oauth_refresh_manager = oauth_refresh_manager
        self._vault = credential_vault
        self._conns: dict[str, _SharedConn] = {}
        self._open_lock = asyncio.Lock()
        oauth_refresh_manager.add_broadcast_subscriber(self._on_token_refreshed)
        self._cfg_lookup = None

    # ---- public API --------------------------------------------------------

    async def get_or_open(self, cfg) -> _SharedConn:
        """Return the live shared connection for cfg.id, opening it on first call.

        Raises RuntimeError if the connection has been marked draining.
        """
        conn = self._conns.get(cfg.id)
        if conn and conn.draining:
            raise RuntimeError(
                f"MCP config '{cfg.name}' is being drained; cannot attach new sessions"
            )
        async with self._open_lock:
            conn = self._conns.get(cfg.id)
            if conn is None:
                conn = _SharedConn(cfg_id=cfg.id)
                self._conns[cfg.id] = conn
                await self._open_locked(cfg, conn)
            elif conn.session is None:
                await self._open_locked(cfg, conn)
            conn.refcount += 1
        _logger.info("shared MCP attach cfg=%s refcount=%d", cfg.id, conn.refcount)
        return conn

    async def release(self, cfg_id: str) -> None:
        """Decrement refcount; close upstream when refcount==0 AND draining."""
        conn = self._conns.get(cfg_id)
        if conn is None:
            return
        conn.refcount -= 1
        _logger.info(
            "shared MCP release cfg=%s refcount=%d draining=%s",
            cfg_id,
            conn.refcount,
            conn.draining,
        )
        if conn.refcount <= 0 and conn.draining:
            await self._close(cfg_id)

    async def mark_draining(self, cfg_id: str) -> None:
        """Called from config delete/disable. Close immediately if no refs."""
        conn = self._conns.get(cfg_id)
        if conn is None:
            return
        conn.draining = True
        if conn.refcount <= 0:
            await self._close(cfg_id)

    async def list_tools(self, cfg) -> list[Tool]:
        """Return cached tools, opening the connection if needed."""
        conn = await self._ensure_open(cfg)
        return list(conn.cached_tools)

    async def call_tool(self, cfg, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Forward a tool call to the upstream session.

        If a reconnect is in progress, wait up to RECONNECT_WAIT_SECONDS on
        reconnect_lock, then either retry or return a structured error.
        """
        conn = await self._ensure_open(cfg)
        try:
            return await asyncio.wait_for(
                self._call_tool_locked(conn, name, arguments),
                timeout=self.RECONNECT_WAIT_SECONDS + 30.0,
            )
        except TimeoutError:
            return _disconnect_error_result_named(cfg.id)
        except Exception as exc:
            _logger.exception("shared MCP call_tool failed cfg=%s tool=%s", cfg.id, name)
            return _internal_error_result(cfg, exc)

    async def shutdown(self) -> None:
        """Close every upstream connection. Called from SessionCoordinator.cleanup()."""
        for cfg_id in list(self._conns):
            try:
                await self._close(cfg_id)
            except Exception:
                _logger.exception("Error closing shared MCP cfg=%s", cfg_id)

    def set_cfg_lookup(self, fn) -> None:
        """Inject a coroutine that resolves a server_id to McpServerConfig."""
        self._cfg_lookup = fn

    # ---- private -----------------------------------------------------------

    async def _ensure_open(self, cfg) -> _SharedConn:
        conn = self._conns.get(cfg.id)
        if conn is None or conn.session is None:
            conn = await self.get_or_open(cfg)
            # get_or_open() incremented refcount; _ensure_open is not a lifecycle
            # call — compensate so caller's refcount stays correct.
            conn.refcount -= 1
        return conn

    async def _call_tool_locked(
        self, conn: _SharedConn, name: str, arguments: dict
    ) -> CallToolResult:
        try:
            await asyncio.wait_for(
                conn.reconnect_lock.acquire(),
                timeout=self.RECONNECT_WAIT_SECONDS,
            )
        except TimeoutError:
            return _disconnect_error_result_named(conn.cfg_id)
        try:
            session = conn.session
            if session is None:
                reason = str(conn.last_close_error) if conn.last_close_error else None
                return _disconnect_error_result_named(conn.cfg_id, reason)
            try:
                return await session.call_tool(name, arguments)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                _logger.warning(
                    "shared MCP upstream closed during call cfg=%s tool=%s — marking disconnected",
                    conn.cfg_id,
                    name,
                )
                await self._mark_disconnected_locked(conn)
                return _disconnect_error_result_named(conn.cfg_id)
        finally:
            conn.reconnect_lock.release()

    async def _run_connection(
        self,
        cfg,
        conn: _SharedConn,
        generation: int,
        ready_fut: asyncio.Future,
        close_event: asyncio.Event,
        closed_fut: asyncio.Future,
    ) -> None:
        """Owner coroutine: holds AsyncExitStack for the connection lifetime.

        Parks on close_event after the session is initialised, then exits the
        stack from inside this task — avoiding any cross-task cancel-scope violation.
        """
        stack = AsyncExitStack()
        try:
            try:
                async with stack:
                    read, write = await self._enter_transport(cfg, stack)
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    list_result = await session.list_tools()
                    if generation == conn.generation:
                        conn.session = session
                        conn.cached_tools = list(list_result.tools)
                        _logger.info(
                            "shared MCP opened cfg=%s tools=%d",
                            cfg.id,
                            len(conn.cached_tools),
                        )
                    if not ready_fut.done():
                        ready_fut.set_result(None)
                    # Park here; the stack exits cleanly when we return.
                    await close_event.wait()
                # Stack closed cleanly inside this task.
                closed_fut.set_result(None)
            except BaseException as exc:
                if not ready_fut.done():
                    ready_fut.set_exception(exc)
                closed_fut.set_result(exc if isinstance(exc, Exception) else None)
                if not isinstance(exc, Exception):
                    raise
        finally:
            if generation == conn.generation:
                conn.session = None
                conn.owner_task = None
                conn.close_event = None
                conn.ready_future = None
                conn.closed_future = None

    async def _open_locked(self, cfg, conn: _SharedConn) -> None:
        """Spawn an owner task that opens the transport and parks until close_event."""
        loop = asyncio.get_running_loop()
        ready_fut: asyncio.Future = loop.create_future()
        close_event = asyncio.Event()
        closed_fut: asyncio.Future = loop.create_future()

        conn.generation += 1
        generation = conn.generation
        conn.close_event = close_event
        conn.closed_future = closed_fut
        conn.ready_future = ready_fut

        task = asyncio.create_task(
            self._run_connection(cfg, conn, generation, ready_fut, close_event, closed_fut),
            name=f"shared-mcp-owner-{cfg.id}-{generation}",
        )
        task.add_done_callback(task_done_log_exception)
        conn.owner_task = task

        try:
            await asyncio.wait_for(
                asyncio.shield(ready_fut),
                timeout=self.RECONNECT_WAIT_SECONDS + 30.0,
            )
        except (TimeoutError, Exception):
            # Wait for the owner to finish cleanup before re-raising.
            try:
                await asyncio.wait_for(asyncio.shield(closed_fut), timeout=5.0)
            except Exception:
                pass
            raise

    async def _close_owner_locked(self, conn: _SharedConn) -> Exception | None:
        """Signal the owner task to close and await its completion.

        Safe to call from any task — never touches the AsyncExitStack directly.
        Returns the cleanup exception (if any), or None on clean close.
        """
        if conn.owner_task is None:
            return None
        close_event = conn.close_event
        closed_fut = conn.closed_future
        owner_task = conn.owner_task
        if close_event is not None:
            close_event.set()
        if closed_fut is None:
            return None
        try:
            result = await asyncio.wait_for(
                asyncio.shield(closed_fut), timeout=self.CLOSE_TIMEOUT_SECONDS
            )
        except TimeoutError:
            _logger.error(
                "shared MCP owner close timed out cfg=%s — cancelling owner task",
                conn.cfg_id,
            )
            owner_task.cancel()
            try:
                await owner_task
            except BaseException:
                pass
            err = TimeoutError("shared MCP owner close timed out")
            conn.last_close_error = err
            return err
        try:
            await owner_task
        except BaseException:
            pass
        if result is not None:
            conn.last_close_error = result
        return result

    async def _mark_disconnected_locked(self, conn: _SharedConn) -> None:
        """Tear down the dead session via the owner task. Caller must hold conn.reconnect_lock."""
        err = await self._close_owner_locked(conn)
        if err is not None:
            _logger.exception(
                "Error closing owner on disconnect cfg=%s: %s", conn.cfg_id, err
            )
        conn.session = None

    async def _close(self, cfg_id: str) -> None:
        """Close the upstream connection and remove from _conns."""
        conn = self._conns.get(cfg_id)
        if conn is None:
            return
        err = await self._close_owner_locked(conn)
        if err is not None:
            _logger.error("Error during shared MCP close cfg=%s: %s", cfg_id, err)
        self._conns.pop(cfg_id, None)
        _logger.info("shared MCP closed cfg=%s", cfg_id)

    async def _on_token_refreshed(self, server_id: str) -> None:
        """Reconnect upstream with the freshly refreshed token.

        Called from OAuthRefreshManager._refresh_loop after a successful refresh.
        """
        conn = self._conns.get(server_id)
        if conn is None or conn.session is None:
            return
        _logger.info("shared MCP token refresh — reconnecting cfg=%s", server_id)
        async with conn.reconnect_lock:
            cfg = await self._lookup_cfg(server_id)
            if cfg is None:
                _logger.warning(
                    "shared MCP refresh: cfg %s not found, leaving conn closed", server_id
                )
                return
            await self._mark_disconnected_locked(conn)
            try:
                await self._open_locked(cfg, conn)
            except Exception:
                _logger.exception("Reconnect after refresh failed cfg=%s", server_id)

    async def _enter_transport(self, cfg, stack: AsyncExitStack):
        """Open the right client transport based on cfg.type.

        All user-visible config fields (url, headers, command, args, env) are
        passed through secret_resolver first so any ${secret:NAME} references
        are replaced with the plaintext value from the vault.
        """
        from ..mcp_config_manager import McpServerType
        from .secret_resolver import (
            resolve_secret_refs_in_list,
            resolve_secret_refs_in_mapping,
            resolve_secret_refs_in_str,
        )

        if cfg.type == McpServerType.HTTP:
            url = await resolve_secret_refs_in_str(cfg.url or "", self._vault)
            headers = await self._build_headers(cfg)
            ctx = streamablehttp_client(url=url, headers=headers, terminate_on_close=True)
            read, write, _get_id = await stack.enter_async_context(ctx)
            return read, write
        elif cfg.type == McpServerType.SSE:
            url = await resolve_secret_refs_in_str(cfg.url or "", self._vault)
            headers = await self._build_headers(cfg)
            ctx = sse_client(url=url, headers=headers)
            read, write = await stack.enter_async_context(ctx)
            return read, write
        elif cfg.type == McpServerType.STDIO:
            command = await resolve_secret_refs_in_str(cfg.command or "", self._vault)
            args = await resolve_secret_refs_in_list(list(cfg.args or []), self._vault)
            env = await resolve_secret_refs_in_mapping(dict(cfg.env or {}), self._vault)
            params = StdioServerParameters(
                command=command, args=args or [], env=env or None
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            return read, write
        raise ValueError(f"Unsupported MCP type for shared connection: {cfg.type}")

    async def _build_headers(self, cfg) -> dict[str, str] | None:
        """Build headers dict with secret-refs resolved and OAuth Bearer token if applicable."""
        from ..mcp_config_manager import McpServerType
        from .secret_resolver import resolve_secret_refs_in_mapping

        if cfg.type not in (McpServerType.HTTP, McpServerType.SSE):
            return None
        headers = await resolve_secret_refs_in_mapping(dict(cfg.headers or {}), self._vault)
        headers = headers or {}
        if cfg.oauth_enabled:
            token = await self._oauth_manager.get_stored_token(cfg.id)
            if token:
                headers["Authorization"] = f"Bearer {token.access_token}"
        return headers or None

    async def _lookup_cfg(self, server_id: str):
        if self._cfg_lookup is None:
            return None
        return await self._cfg_lookup(server_id)


def _disconnect_error_result_named(cfg_id: str, reason: str | None = None) -> CallToolResult:
    detail = f" ({reason})" if reason else ""
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Upstream MCP server (config={cfg_id}) is disconnected{detail}; "
                    "try again in a few seconds."
                ),
            )
        ],
        isError=True,
    )


def _internal_error_result(cfg, exc: Exception) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=f"Shared MCP proxy error for {cfg.id}: {exc!r}",
            )
        ],
        isError=True,
    )
