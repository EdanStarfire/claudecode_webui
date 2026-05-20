"""
SharedMcpConnectionManager — Issue #1484

Owns one upstream mcp.client.session.ClientSession per shared-enabled
McpServerConfig.id. Lazily opened on first session attach, kept alive for the
process lifetime (or until config delete with refcount==0). OAuth tokens are
read from oauth_manager and refreshed via oauth_refresh_manager. On token
refresh the upstream connection is torn down and re-opened with the new token.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

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

_logger = get_logger("mcp_shared", category="MCP_SHARED")


@dataclass
class _SharedConn:
    cfg_id: str
    session: ClientSession | None = None
    exit_stack: AsyncExitStack | None = None
    # Stale tools are retained across reconnect; refreshed on initialize().
    # Running sessions won't see changes from upstream notifications/tools/list_changed
    # until their next reconnect.
    cached_tools: list[Tool] = field(default_factory=list)
    reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refcount: int = 0
    draining: bool = False


class SharedMcpConnectionManager:
    # Q2: max time a tool call waits for reconnect_lock before failing
    RECONNECT_WAIT_SECONDS = 5.0

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
        """Q4: called from config delete/disable. Close immediately if no refs."""
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

        Q2: if a reconnect is in progress, wait up to RECONNECT_WAIT_SECONDS
        on reconnect_lock, then either retry or return a structured error.
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
                return _disconnect_error_result_named(conn.cfg_id)
            return await session.call_tool(name, arguments)
        finally:
            conn.reconnect_lock.release()

    async def _open_locked(self, cfg, conn: _SharedConn) -> None:
        """Open the transport, wrap in ClientSession, run initialize, cache tools."""
        stack = AsyncExitStack()
        try:
            read, write = await self._enter_transport(cfg, stack)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            list_result = await session.list_tools()
            conn.session = session
            conn.exit_stack = stack
            conn.cached_tools = list(list_result.tools)
            _logger.info(
                "shared MCP opened cfg=%s tools=%d", cfg.id, len(conn.cached_tools)
            )
        except Exception:
            await stack.aclose()
            raise

    async def _enter_transport(self, cfg, stack: AsyncExitStack):
        """Open the right client transport based on cfg.type.

        All user-visible config fields (url, headers, command, args, env) are
        passed through secret_resolver first so any ${secret:NAME} references
        are replaced with the plaintext value from the vault. The agent never
        sees the real value because the upstream connection lives in this
        WebUI process — agent containers receive only proxy tools/list and
        tools/call traffic.
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
        """Build headers dict with secret-refs resolved and OAuth Bearer token if applicable.

        For HTTP/SSE configs only. Returns None if neither headers nor OAuth apply.
        """
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

    async def _close(self, cfg_id: str) -> None:
        conn = self._conns.pop(cfg_id, None)
        if conn and conn.exit_stack is not None:
            try:
                await conn.exit_stack.aclose()
            except Exception:
                _logger.exception("Error during shared MCP close cfg=%s", cfg_id)
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
            if conn.exit_stack is not None:
                try:
                    await conn.exit_stack.aclose()
                except Exception:
                    _logger.exception("Error closing for refresh cfg=%s", server_id)
            conn.session = None
            conn.exit_stack = None
            try:
                await self._open_locked(cfg, conn)
            except Exception:
                _logger.exception("Reconnect after refresh failed cfg=%s", server_id)

    async def _lookup_cfg(self, server_id: str):
        if self._cfg_lookup is None:
            return None
        return await self._cfg_lookup(server_id)


def _disconnect_error_result_named(cfg_id: str) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Upstream MCP server (config={cfg_id}) is disconnected; "
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
