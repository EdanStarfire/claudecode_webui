"""
OAuthRefreshManager — Issue #989

Encapsulates the per-server OAuth token refresh lifecycle that was previously
embedded in SessionCoordinator:
  - Per-server asyncio locks (prevent concurrent refresh calls)
  - Background refresh tasks (one per server_id, shared across sessions)
  - Reference counts (task cancelled when last session releases)
  - Broadcast callback (notifies the UI after a successful background refresh)
"""

import asyncio
import logging
from collections.abc import Callable

from .logging_config import get_logger
from .task_utils import task_done_log_exception

logger = logging.getLogger(__name__)
coord_logger = get_logger("coordinator")


class OAuthRefreshManager:
    """Manages background OAuth token refresh tasks for MCP servers.

    Issue #976: Tokens are application-scoped — one background refresh task per
    server_id, shared across all sessions that use the same MCP server config.
    """

    # Refresh token this many seconds before the stored expiry.
    REFRESH_BUFFER_SECONDS = 300

    def __init__(self, oauth_manager) -> None:
        """
        Args:
            oauth_manager: OAuthFlowManager instance used for token storage/refresh.
        """
        self._oauth_manager = oauth_manager
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._refresh_refcounts: dict[str, int] = {}
        self._broadcast_callback: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_broadcast_callback(self, callback: Callable[[str], None]) -> None:
        """Set the callback for broadcasting mcp_oauth_refreshed events to the UI.

        Issue #976: Called by web_server after server init.
        The callback receives server_id and appends an event to the UI poll queue.
        """
        self._broadcast_callback = callback

    def ensure_refresh(self, server_id: str) -> None:
        """Increment the ref count for server_id and start a refresh task if needed.

        Issue #976: Tokens are application-scoped — one background task per server_id
        shared across all sessions that use the same MCP server config.
        """
        self._refresh_refcounts[server_id] = self._refresh_refcounts.get(server_id, 0) + 1
        if server_id not in self._refresh_tasks:
            task = asyncio.create_task(
                self._refresh_loop(server_id),
                name=f"oauth-refresh-{server_id}",
            )
            task.add_done_callback(task_done_log_exception)
            self._refresh_tasks[server_id] = task
            coord_logger.info("Started background OAuth refresh task for server %s", server_id)

    def release_refresh(self, server_id: str) -> None:
        """Decrement the ref count for server_id and cancel the task when it hits zero.

        Issue #976: Called when a session terminates or resets.
        """
        count = self._refresh_refcounts.get(server_id, 0)
        if count <= 1:
            self._refresh_refcounts.pop(server_id, None)
            task = self._refresh_tasks.pop(server_id, None)
            if task:
                task.cancel()
                coord_logger.info("Cancelled background OAuth refresh task for server %s", server_id)
            if server_id in self._refresh_locks:
                del self._refresh_locks[server_id]
        else:
            self._refresh_refcounts[server_id] = count - 1

    async def refresh_token(self, server_id: str):
        """Refresh the OAuth token for a server with per-server locking.

        Issue #976: Prevents duplicate concurrent refresh requests for the same server.
        Returns the new OAuthToken on success, or None on failure.
        """
        if server_id not in self._refresh_locks:
            self._refresh_locks[server_id] = asyncio.Lock()
        async with self._refresh_locks[server_id]:
            return await self._oauth_manager.refresh_token(server_id)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _refresh_loop(self, server_id: str) -> None:
        """Background task: refresh an OAuth token before it expires.

        Issue #976: App-level task (one per server_id). Wakes up
        REFRESH_BUFFER_SECONDS before the stored expiry and performs a token
        refresh. Broadcasts mcp_oauth_refreshed on success so the UI status
        indicator stays accurate. Repeats until cancelled or refresh token
        is revoked.
        """
        import time as _time

        while True:
            try:
                store = self._oauth_manager.get_token_store(server_id)
                expiry = await store.get_token_expiry()
                if expiry is None:
                    # No expiry recorded — wait a fixed interval and recheck
                    await asyncio.sleep(300)
                    continue

                sleep_seconds = (expiry - _time.time()) - self.REFRESH_BUFFER_SECONDS
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

                # Attempt refresh
                new_token = await self.refresh_token(server_id)
                if new_token:
                    logger.info(
                        "Background OAuth refresh succeeded for MCP server %s", server_id
                    )
                    if self._broadcast_callback:
                        try:
                            self._broadcast_callback(server_id)
                        except Exception:
                            logger.exception(
                                "Error broadcasting mcp_oauth_refreshed for %s", server_id
                            )
                else:
                    logger.warning(
                        "Background OAuth refresh failed for MCP server %s. "
                        "Session restart will re-authenticate if a valid token is available.",
                        server_id,
                    )
                    # Token cleared by refresh_token() on revocation — stop looping
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "Unexpected error in OAuth refresh loop for server %s", server_id
                )
                await asyncio.sleep(60)  # Back off before retrying
