"""Issue #2333: live Provider Model switching for LiteLLM-catalog-routed sessions.

Extracted from SessionCoordinator (which must stay a thin orchestrator, see
test_issue_2189_coordinator_responsibility.py). Holds a back-reference to the coordinator and
reads its live managers / per-session dicts, the same pattern SessionLauncher uses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from .config_resolution import resolve_effective_config
from .litellm_proxy_manager import make_model_alias
from .restart_required import ROUTING_SINGLE, LaunchSnapshot, compute_restart_reasons, single_alias
from .session_launcher import _resolve_auto_compact_window
from .session_lifecycle import is_deliverable

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

coord_logger = get_logger("coordinator", category="COORDINATOR")
logger = logging.getLogger(__name__)

# Seconds the live model switch may wait on the SDK control request.
PROVIDER_SWITCH_TIMEOUT_SECONDS = 30.0


class ProviderModelSwitch:
    def __init__(self, coordinator: SessionCoordinator):
        self._c = coordinator
        # session_id -> LaunchSnapshot of the CLI process currently serving it
        self.launch_snapshots: dict[str, LaunchSnapshot] = {}
        # Serializes switches / restart-required recomputes per session (apply -> persist spans awaits)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def set_provider_model(
        self, session_id: str, catalog_id: str, model_id: str
    ) -> dict[str, Any]:
        async with self._lock(session_id):
            return await self._set_provider_model(session_id, catalog_id, model_id)

    async def refresh_restart_required(self, session_id: str) -> list[str]:
        async with self._lock(session_id):
            return await self._refresh(session_id)

    async def _set_provider_model(
        self, session_id: str, catalog_id: str, model_id: str
    ) -> dict[str, Any]:
        """Issue #2333: switch a provider-routed session's catalog model, live when possible.

        Stopped session: persist only (applies at next start, as before). Active single-model
        session: apply live FIRST (SDK set_model for non-Docker — the LiteLLM proxy routes the
        alias with the catalog entry's own credentials; routing-registry swap for Docker, which
        the sidecar addon re-reads), then persist. A failure leaves config/registry untouched
        (ValueError). A change that cannot be live (not single-model routed, alias missing from
        the running router) is persisted and reported through ``restart_required``.

        Deliberately not session_manager.update_model(): that is the plain Model field (#1673).
        Mid-turn / permission-paused: set_model is a control request, effective from the next
        API request; the in-flight turn and any pending permission are untouched.
        """
        catalog_id = (catalog_id or "").strip()
        model_id = (model_id or "").strip()
        if not catalog_id or not model_id:
            raise ValueError("Both a provider catalog entry and a model are required")
        proxy = self._c.litellm_proxy_manager
        alias = make_model_alias(catalog_id, model_id)
        if proxy is None or alias not in await proxy.list_model_aliases():
            raise ValueError(f"Model '{alias}' is not in the provider catalog")

        session_info = await self._c.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")

        sdk = self._c._active_sdks.get(session_id)
        snap = self.launch_snapshots.get(session_id)
        live_capable = (
            sdk is not None and snap is not None
            and is_deliverable(session_info.state)  # ACTIVE, or PAUSED on a permission
        )
        if not live_capable:
            await self._persist_provider_model(session_id, catalog_id, model_id, None)
            return {"model": alias, "applied": False, "restart_required": []}

        can_apply_live = snap.routing == ROUTING_SINGLE and proxy.has_live_model(alias)
        if not can_apply_live:
            # Valid but not live-switchable: keep the user's intent, surface the restart notice.
            await self._persist_provider_model(session_id, catalog_id, model_id, None)
            reasons = await self._refresh(session_id)
            return {"model": alias, "applied": False, "restart_required": reasons}

        previous_alias = snap.live_alias
        previous_routing: dict | None = None
        if snap.docker_enabled:
            previous_routing = proxy.update_session_routing_model(session_id, alias)
            if previous_routing is None:
                raise ValueError("Docker routing is not registered for this session")
        else:
            try:
                ok = await asyncio.wait_for(sdk.set_model(alias), PROVIDER_SWITCH_TIMEOUT_SECONDS)
            except TimeoutError as e:
                raise ValueError("Timed out switching the model on the running session") from e
            except Exception as e:
                logger.exception(f"Failed to switch provider model for session {session_id}")
                raise ValueError(str(e)) from e
            if not ok:
                raise ValueError("The running session did not accept the model switch")

        if self.launch_snapshots.get(session_id) is not snap:
            # The session was stopped/restarted while the switch was in flight: the new process
            # was launched from the stored config, so just persist (no live state to track).
            await self._persist_provider_model(session_id, catalog_id, model_id, None)
            return {"model": alias, "applied": False, "restart_required": []}

        if not await self._persist_provider_model(session_id, catalog_id, model_id, alias, raise_on_fail=False):
            # AC6: never leave the config claiming a model that is not in use — undo the live apply.
            if previous_routing is not None:
                proxy.restore_session_routing(session_id, previous_routing)
            elif previous_alias:
                try:
                    await asyncio.wait_for(sdk.set_model(previous_alias), PROVIDER_SWITCH_TIMEOUT_SECONDS)
                except Exception:
                    logger.exception(f"Failed to roll back provider model for session {session_id}")
            raise ValueError("Failed to save the model change; it was not applied")

        snap.live_alias = alias
        coord_logger.info(f"Provider model set to '{alias}' for session {session_id} (live)")
        reasons = await self._refresh(session_id)
        return {"model": alias, "applied": True, "restart_required": reasons}

    async def _persist_provider_model(
        self, session_id: str, catalog_id: str, model_id: str, current_model: str | None,
        *, raise_on_fail: bool = True,
    ) -> bool:
        ok = await self._c.session_manager.update_provider_model(
            session_id, catalog_id, model_id, current_model=current_model
        )
        if not ok and raise_on_fail:
            raise ValueError("Failed to save the model change")
        return ok

    async def _refresh(self, session_id: str) -> list[str]:
        """Issue #2333: diff the session's current effective config against its launch snapshot
        and publish the reasons (broadcast only when they changed)."""
        snap = self.launch_snapshots.get(session_id)
        session_info = await self._c.session_manager.get_session_info(session_id)
        proxy = self._c.litellm_proxy_manager
        if snap is None or session_info is None or proxy is None or session_id not in self._c._active_sdks:
            return []
        cfg = await resolve_effective_config(session_info, self._c.template_manager, self._c.profile_manager)
        alias = single_alias(cfg)
        new_window = None
        live = True
        if alias:
            live = proxy.has_live_model(alias)
            if snap.wants_compact_window:
                new_window = await _resolve_auto_compact_window(proxy, [alias], session_id)
        reasons = compute_restart_reasons(
            snap, cfg, new_compact_window=new_window, alias_in_live_router=live
        )
        await self._c.session_manager.set_restart_required(session_id, reasons)
        return reasons
