"""Manages the in-process LiteLLM proxy server."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.provider_catalog import ProviderCatalogManager

legion_logger = get_logger("legion", category="LITELLM_PROXY")

# Separator between catalog_id and model_id in LiteLLM model_name aliases.
MODEL_ALIAS_SEP = "--"


class LiteLLMProxyManager:
    """
    Lifecycle manager for the embedded LiteLLM ASGI proxy.

    Responsibilities:
    - Start/stop the uvicorn task serving the LiteLLM proxy app
    - Rebuild the proxy model_list from the provider catalog + vault secrets
    - Maintain a per-session virtual key registry for custom_auth
    """

    def __init__(self, catalog_manager: ProviderCatalogManager, vault, port: int = 4000):
        self._catalog_manager = catalog_manager
        self._vault = vault
        self._port = port
        self._key_registry: dict[str, str] = {}  # api_key -> session_id
        self._routing_registry: dict[str, dict] = {}  # session_id -> {virtual_key, base_url}
        self._server_task: asyncio.Task | None = None
        self._rebuild_lock = asyncio.Lock()
        self._running = False
        self._last_restart: datetime | None = None
        self._last_error: str | None = None
        self._model_count: int = 0
        try:
            from litellm.proxy.proxy_server import app as _  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "LiteLLM proxy package not available. Install litellm[proxy]."
            ) from e

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Build model_list from catalog and start the uvicorn task."""
        try:
            model_list = await self._build_model_list()
            await self._launch_server(model_list)
            self._running = True
            self._last_restart = datetime.now(UTC)
            self._last_error = None
            legion_logger.info(
                f"LiteLLM proxy started on port {self._port} with {len(model_list)} model(s), "
                f"bound to 0.0.0.0:{self._port} (auth via virtual key)"
            )
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            raise

    async def stop(self) -> None:
        """Gracefully stop the uvicorn task."""
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        self._server_task = None
        self._running = False
        legion_logger.info("LiteLLM proxy stopped")

    async def rebuild(self) -> None:
        """Re-resolve catalog secrets, regenerate model_list, restart server."""
        async with self._rebuild_lock:
            legion_logger.info("Rebuilding LiteLLM proxy...")
            await self.stop()
            await self.start()

    @property
    def is_running(self) -> bool:
        return self._running and self._server_task is not None and not self._server_task.done()

    @property
    def port(self) -> int:
        return self._port

    @property
    def last_restart(self) -> datetime | None:
        return self._last_restart

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def model_count(self) -> int:
        return self._model_count

    # ── Virtual Key Registry ───────────────────────────────────────────────

    def register_session_key(self, session_id: str) -> str:
        """Mint and register a virtual API key for a session. Returns the key."""
        key = f"lc-{uuid.uuid4().hex}"
        self._key_registry[key] = session_id
        return key

    def unregister_session_key(self, session_id: str) -> None:
        """Remove all keys belonging to session_id."""
        keys_to_remove = [k for k, v in self._key_registry.items() if v == session_id]
        for k in keys_to_remove:
            del self._key_registry[k]

    def lookup_session_for_key(self, api_key: str) -> str | None:
        """Return session_id for api_key, or None if not registered."""
        return self._key_registry.get(api_key)

    # ── Routing Registry ──────────────────────────────────────────────────────

    def register_session_routing(self, session_id: str, virtual_key: str, base_url: str) -> None:
        """Store virtual key + base URL for a session (used by Docker delivery, Phase 3)."""
        self._routing_registry[session_id] = {"virtual_key": virtual_key, "base_url": base_url}

    def unregister_session_routing(self, session_id: str) -> None:
        """Remove routing entry for session_id (no-op if absent)."""
        self._routing_registry.pop(session_id, None)

    def get_session_routing(self, session_id: str) -> dict | None:
        """Return {virtual_key, base_url} for session_id, or None if not registered."""
        return self._routing_registry.get(session_id)

    def build_hostname_rewrites(self) -> dict[str, str]:
        """Return the static hostname-rewrite map for Docker sidecars.

        Keys are source hostnames the CLI contacts; values are the rewrite
        destination (cc-webui.internal:<port>) where LiteLLM is reachable.
        """
        return {"api.anthropic.com": f"cc-webui.internal:{self._port}"}

    # ── LiteLLM Custom Auth ────────────────────────────────────────────────

    async def auth_callback(self, request, api_key: str):
        """LiteLLM custom_auth hook. Validates virtual keys."""
        from fastapi import HTTPException

        session_id = self.lookup_session_for_key(api_key)
        if not session_id:
            raise HTTPException(status_code=401, detail="Invalid virtual key")

        from litellm.proxy._types import UserAPIKeyAuth

        return UserAPIKeyAuth(api_key=api_key, user_id=session_id)

    # ── Internal ───────────────────────────────────────────────────────────

    async def _build_model_list(self) -> list[dict]:
        """Resolve catalog entries + secrets into LiteLLM model_list format."""
        entries = await self._catalog_manager.list_entries()
        model_list = []
        for entry in entries:
            resolved_params = await self._catalog_manager.resolve_params(entry, self._vault)
            for model in entry.get("models", []):
                model_name = f"{entry['id']}{MODEL_ALIAS_SEP}{model['id']}"
                model_list.append({
                    "model_name": model_name,
                    "litellm_params": {
                        "model": model["litellm_model"],
                        **resolved_params,
                    },
                })
        self._model_count = len(model_list)
        return model_list

    async def _launch_server(self, model_list: list[dict]) -> None:
        """Configure LiteLLM globals and start the uvicorn task."""
        import litellm
        from litellm.proxy import proxy_server as _ps

        # Set proxy module globals directly — initialize() in litellm>=1.50
        # does not accept model_list or custom_auth kwargs.
        # The auth module re-imports user_custom_auth per-request so setting
        # the module attribute is sufficient.
        router = litellm.Router(model_list=model_list)
        _ps.llm_router = router
        _ps.llm_model_list = model_list
        _ps.user_custom_auth = self.auth_callback
        litellm.telemetry = False

        import uvicorn

        config = uvicorn.Config(_ps.app, host="0.0.0.0", port=self._port, log_level="warning")
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())
