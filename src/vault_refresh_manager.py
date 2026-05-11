"""
VaultRefreshManager — proactive background OAuth2 refresh for vault secrets (issue #1387).

Mirrors OAuthRefreshManager's shape but operates on vault oauth2 secrets rather than
MCP server OAuth sessions. One asyncio task per secret, keyed by secret name.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from .logging_config import get_logger
from .task_utils import task_done_log_exception

logger = logging.getLogger(__name__)
coord_logger = get_logger("coordinator")


class VaultRefreshManager:
    """Background OAuth2 refresh tasks for vault oauth2 secrets.

    Issue #1387: Proactive background refresh with one-retry-60s-backoff semantics.
    Per-secret asyncio.Lock deduplicates concurrent refresh calls from the manager,
    manual refresh endpoint, and proxy write-back.
    """

    def __init__(self, credential_vault) -> None:
        self._vault = credential_vault
        self._service = None  # ApplicationService; set via set_service()
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._broadcast_callback: Callable[[str, str | None], None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_service(self, service) -> None:
        """Inject ApplicationService (called from web_server after both are created)."""
        self._service = service

    def set_broadcast_callback(self, callback: Callable[[str, str | None], None]) -> None:
        """Set broadcast callback: callback(secret_name, error_or_None)."""
        self._broadcast_callback = callback

    def get_lock(self, name: str) -> asyncio.Lock:
        """Return (creating if needed) the per-secret asyncio.Lock."""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    async def start(self) -> None:
        """Schedule refresh for all existing OAUTH2 vault secrets on startup."""
        from .models.secret_record import SecretType
        secrets = await self._vault.list_secrets()
        for s in secrets:
            if s.get("type") == SecretType.OAUTH2.value and s.get("refresh"):
                self.schedule_secret(s["name"])
        coord_logger.info(
            "VaultRefreshManager started — scheduled %d secret(s)", len(self._tasks)
        )

    async def stop(self) -> None:
        """Cancel all background refresh tasks gracefully."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._locks.clear()
        coord_logger.info("VaultRefreshManager stopped")

    def schedule_secret(self, name: str) -> None:
        """Start or restart background refresh for a vault oauth2 secret."""
        existing = self._tasks.get(name)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(
            self._refresh_loop(name),
            name=f"vault-refresh-{name}",
        )
        task.add_done_callback(task_done_log_exception)
        self._tasks[name] = task

    def unschedule_secret(self, name: str) -> None:
        """Cancel and remove background refresh for a deleted/superseded secret."""
        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
        self._locks.pop(name, None)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _sleep_until_refresh(self, name: str) -> bool:
        """Sleep until the next refresh window. Returns False if task should exit."""
        import time as _time

        meta = await self._vault.get_secret(name)
        if meta is None:
            return False  # Secret deleted

        refresh = meta.get("refresh") or {}

        # Failed-state secrets re-attempt immediately on startup
        if refresh.get("last_refresh_status") == "failed":
            return True

        expires_at_str = refresh.get("expires_at")
        if not expires_at_str:
            await asyncio.sleep(300)
            return True

        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            buffer = int(refresh.get("buffer_seconds", 300))
            sleep_secs = (expires_at.timestamp() - _time.time()) - buffer
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)
        except Exception:
            await asyncio.sleep(300)

        return True

    async def _do_refresh(self, name: str) -> None:
        """Perform one refresh attempt under per-secret lock. Raises on failure."""
        from .models.secret_record import SecretType
        from .secret_types.oauth2 import OAuth2Handler
        from .secrets_keyring import get_secret_value, set_secret_value

        async with self.get_lock(name):
            meta = await self._vault.get_secret(name)
            if meta is None or meta.get("type") != SecretType.OAUTH2.value:
                raise RuntimeError(f"Secret '{name}' not found or not oauth2 type")

            record = dict(meta)
            record["value"] = get_secret_value(name) or ""

            async def _get_sibling(sibling_name: str) -> str:
                return get_secret_value(sibling_name) or ""

            handler = OAuth2Handler()
            updates = await handler.refresh(record, _get_sibling)

            new_value = updates.get("value")
            if new_value:
                set_secret_value(name, new_value)

            refresh_info = dict(updates.get("refresh") or {})
            new_refresh_token = refresh_info.pop("_new_refresh_token", None)
            refresh_token_name = (meta.get("refresh") or {}).get("refresh_token_secret_name")
            if new_refresh_token and refresh_token_name:
                set_secret_value(refresh_token_name, new_refresh_token)

            if self._service:
                await self._service.update_secret(name=name, refresh=refresh_info or None)

    async def _record_failure(self, name: str, error: str) -> None:
        """Persist last_refresh_status=failed and error message to vault metadata."""
        try:
            meta = await self._vault.get_secret(name)
            if meta and self._service:
                refresh = dict(meta.get("refresh") or {})
                refresh["last_refresh_at"] = datetime.now(UTC).isoformat()
                refresh["last_refresh_status"] = "failed"
                refresh["last_refresh_error"] = error
                await self._service.update_secret(name=name, refresh=refresh)
        except Exception:
            logger.exception("Failed to record refresh failure for secret %s", name)

    def _broadcast(self, name: str, error: str | None) -> None:
        if self._broadcast_callback:
            try:
                self._broadcast_callback(name, error)
            except Exception:
                logger.exception("Error in vault refresh broadcast callback for %s", name)

    async def _refresh_loop(self, name: str) -> None:
        """Background task: proactive refresh with one-retry-then-stop semantics."""
        while True:
            try:
                should_continue = await self._sleep_until_refresh(name)
                if not should_continue:
                    break

                # First attempt
                try:
                    await self._do_refresh(name)
                    logger.info("Background vault OAuth2 refresh succeeded for secret %s", name)
                    self._broadcast(name, None)
                    continue  # Loop back to sleep until next expiry
                except asyncio.CancelledError:
                    raise
                except Exception as e1:
                    logger.warning(
                        "Vault OAuth2 refresh attempt 1 failed for secret %s: %s", name, e1
                    )

                # One retry after 60s backoff
                await asyncio.sleep(60)
                try:
                    await self._do_refresh(name)
                    logger.info(
                        "Vault OAuth2 refresh retry succeeded for secret %s", name
                    )
                    self._broadcast(name, None)
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e2:
                    error_str = str(e2)
                    logger.error(
                        "Vault OAuth2 refresh retry also failed for secret %s: %s", name, e2
                    )
                    await self._record_failure(name, error_str)
                    self._broadcast(name, error_str)
                    break  # User must Reconnect to recover

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "Unexpected error in vault refresh loop for secret %s", name
                )
                await asyncio.sleep(60)
