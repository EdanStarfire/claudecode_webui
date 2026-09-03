"""
McpOneshotConnector — Issue #1800

Provides a genuinely independent one-shot open -> handshake -> list_tools -> close
connection lifecycle for non-shared (shared_connection=False) MCP server configs, used
by the "Test connection" / "Show tools" affordance in Library settings.

Deliberately does NOT reuse or modify SharedMcpConnectionManager. That manager's
owner-task/close_event machinery (see shared_connection_manager.py's module docstring,
issue #1505) exists to dodge an anyio cross-task cancel-scope bug that only arises
because a shared connection can be opened by one task and later closed by another (on
drain or OAuth token refresh). A one-shot connection here is opened and closed by the
same task within a single call, so that apparatus is unnecessary — wrapping the entire
`AsyncExitStack` block in one `asyncio.wait_for(...)` is sufficient for both the timeout
and the cleanup guarantee (stdio_client's own SIGTERM->SIGKILL escalation on cancellation
only holds when cancellation propagates through its own context-manager exit, in the
same task that entered it).

The per-type transport dispatch (`_enter_transport`/`_build_headers`) mirrors
SharedMcpConnectionManager's private methods of the same name rather than importing
them, per the issue's requirement that SharedMcpConnectionManager itself not change.
"""

import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from shared.logging_config import get_logger

_logger = get_logger("mcp_oneshot", category="MCP_ONESHOT")


class McpOneshotConnector:
    """Opens a throwaway connection to a non-shared MCP config, lists its tools, and
    closes it immediately. Holds no state across calls beyond a per-config asyncio.Lock.
    """

    TIMEOUT_SECONDS = 30.0

    def __init__(self, oauth_manager, credential_vault):
        self._oauth_manager = oauth_manager
        self._vault = credential_vault
        self._locks: dict[str, asyncio.Lock] = {}

    async def test_connect(self, cfg) -> dict:
        """Open a throwaway connection to cfg, list tools, close it. Never persists state.

        Returns {"status": "connected"|"failed", "stage": "transport"|"handshake"|
        "list_tools"|None, "tools": [...], "error": str|None}.

        Serialized per cfg.id via an internal lock so rapid repeated calls for the same
        config never overlap two live connections/processes; different configs use
        different locks and run fully in parallel.
        """
        lock = self._get_lock(cfg.id)
        async with lock:
            # Local to this call — never shared across concurrent test_connect() calls
            # for different configs, unlike an instance attribute would be.
            stage_tracker = {"stage": None}
            try:
                return await asyncio.wait_for(
                    self._run(cfg, stage_tracker), timeout=self.TIMEOUT_SECONDS
                )
            except TimeoutError:
                _logger.warning("mcp oneshot test_connect timed out cfg=%s", cfg.id)
                return {
                    "status": "failed",
                    "stage": stage_tracker["stage"],
                    "tools": [],
                    "error": f"Timed out after {self.TIMEOUT_SECONDS:.0f}s",
                }
            except Exception as exc:
                # Catches failures that escape the per-stage try/excepts in _run() —
                # e.g. AsyncExitStack.__aexit__ raising while unwinding a transport/
                # session that was already flagged failed and returned early.
                _logger.exception("mcp oneshot test_connect failed cfg=%s", cfg.id)
                return {
                    "status": "failed",
                    "stage": stage_tracker["stage"],
                    "tools": [],
                    "error": str(exc),
                }

    def _get_lock(self, cfg_id: str) -> asyncio.Lock:
        lock = self._locks.get(cfg_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cfg_id] = lock
        return lock

    async def _run(self, cfg, stage_tracker: dict) -> dict:
        """Open -> handshake -> list_tools -> close, tagging a failure with the stage
        it happened in. The whole AsyncExitStack lifetime is covered by the single
        asyncio.wait_for() in test_connect(), so a timeout at any stage cancels this
        coroutine and unwinds the stack from within this same task.
        """
        async with AsyncExitStack() as stack:
            stage_tracker["stage"] = "transport"
            try:
                read, write = await self._enter_transport(cfg, stack)
            except Exception as exc:
                return {"status": "failed", "stage": "transport", "tools": [], "error": str(exc)}

            stage_tracker["stage"] = "handshake"
            try:
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            except Exception as exc:
                return {"status": "failed", "stage": "handshake", "tools": [], "error": str(exc)}

            stage_tracker["stage"] = "list_tools"
            try:
                list_result = await session.list_tools()
            except Exception as exc:
                return {"status": "failed", "stage": "list_tools", "tools": [], "error": str(exc)}

            return {
                "status": "connected",
                "stage": None,
                "tools": list(list_result.tools),
                "error": None,
            }

    # ---- transport dispatch (mirrors SharedMcpConnectionManager — not imported, see
    # module docstring) -----------------------------------------------------------

    async def _enter_transport(self, cfg, stack: AsyncExitStack):
        """Open the right client transport based on cfg.type.

        All user-visible config fields (url, headers, command, args, env) are passed
        through secret_resolver first so any ${secret:NAME} references are replaced
        with the plaintext value from the vault.
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
            params = StdioServerParameters(command=command, args=args or [], env=env or None)
            read, write = await stack.enter_async_context(stdio_client(params))
            return read, write
        raise ValueError(f"Unsupported MCP type for one-shot connection: {cfg.type}")

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
