"""Manages the in-process LiteLLM proxy server."""

from __future__ import annotations

import asyncio
import inspect
import uuid
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
        try:
            from litellm.proxy.proxy_server import app as _  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "LiteLLM proxy package not available. Install litellm[proxy]."
            ) from e

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Build model_list from catalog and start the uvicorn task."""
        model_list = await self._build_model_list()
        await self._launch_server(model_list)
        self._running = True
        legion_logger.info(
            f"LiteLLM proxy started on port {self._port} with {len(model_list)} model(s)"
        )

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
        return model_list

    async def _launch_server(self, model_list: list[dict]) -> None:
        """Configure LiteLLM and start the uvicorn task."""
        import litellm
        from litellm.proxy.proxy_server import app, initialize

        litellm.model_list = model_list
        litellm.telemetry = False

        # initialize() signature varies across LiteLLM versions; probe it
        sig = inspect.signature(initialize)
        init_kwargs: dict = {"model_list": model_list, "custom_auth": self.auth_callback}
        # Older versions required positional-style keyword args
        for param_name in ("model", "alias", "api_base", "api_version"):
            if param_name in sig.parameters:
                init_kwargs[param_name] = None

        result = initialize(**init_kwargs)
        if inspect.isawaitable(result):
            await result

        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=self._port, log_level="warning")
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())
