"""
Session Query for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 6): owns session-info/listing response assembly
(SDK status, message counts, effective config) and the task-leg-registry
pair. This is a mechanical extraction — SessionCoordinator keeps every
public method as a one-line delegate, and every per-session shared dict
stays constructed on the coordinator; SessionQuery receives the same dict
objects by reference, never copies.

Unlike SessionLauncher/MessagePipeline, this is a terminal content
extraction: nothing it calls is still stranded elsewhere, so no Callbacks
dataclass is needed here — same reasoning as PR 4/5.
"""

import asyncio
import logging
from typing import Any

from .config_resolution import resolve_effective_config
from .task_registry import TaskLegRegistry

logger = logging.getLogger(__name__)


class SessionQuery:
    """Owns session-info/listing response assembly and the task-leg registry."""

    def __init__(self, *, coordinator: Any):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), not a value
        # snapshotted here at construction time — same discipline as
        # SessionLauncher/MessagePipeline/ToolCallTracker/ArchiveCoordinator/
        # SessionResources (PR 1-5).
        self._coordinator = coordinator

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    template_manager = property(lambda self: self._coordinator.template_manager)
    profile_manager = property(lambda self: self._coordinator.profile_manager)

    # --- Shared per-session state: live passthrough to the coordinator -------
    _active_sdks = property(lambda self: self._coordinator._active_sdks)
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _task_leg_registries = property(lambda self: self._coordinator._task_leg_registries)

    async def _build_effective_config_payload(
        self, session_info
    ) -> tuple[dict | None, dict | None]:
        """Return (effective_config_dict, template_meta) for template-linked sessions.

        Returns (None, None) for standalone sessions or on resolution failure.
        """
        if not session_info.template_id:
            return None, None
        try:
            eff = await resolve_effective_config(
                session_info, self.template_manager, self.profile_manager
            )
            effective_config_dict = eff.model_dump()
            tmpl = await self.template_manager.get_template(session_info.template_id)
            template_meta = None
            if tmpl:
                template_meta = {
                    "template_id": tmpl.template_id,
                    "name": tmpl.name,
                    "profile_ids": tmpl.profile_ids or {},
                }
            return effective_config_dict, template_meta
        except Exception:
            logger.warning(
                f"Failed to build effective_config for session {session_info.session_id}"
            )
            return None, None

    async def get_session_info(self, session_id: str) -> dict[str, Any] | None:
        """Get comprehensive session information"""
        try:
            # Get session info from manager
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                return None

            # Get SDK status
            sdk_info = {}
            sdk = self._active_sdks.get(session_id)
            if sdk:
                sdk_info = sdk.get_info()
                sdk_info.update({
                    "queue_size": sdk.get_queue_size(),
                })

            # Get storage stats
            storage_info = {}
            storage = self._storage_managers.get(session_id)
            if storage:
                storage_info = {
                    "message_count": await storage.get_message_count()
                }

            # Augment with SDK session metadata if claude_code_session_id is available
            sdk_session_info = None
            if session_info.claude_code_session_id:
                try:
                    from claude_agent_sdk import get_session_info as sdk_get_session_info
                    result = await asyncio.to_thread(
                        sdk_get_session_info,
                        session_info.claude_code_session_id,
                        session_info.working_directory,
                    )
                    if result:
                        sdk_session_info = {
                            "summary": result.summary,
                            "custom_title": result.custom_title,
                            "git_branch": result.git_branch,
                            "first_prompt": result.first_prompt,
                            "tag": result.tag,
                            "created_at": str(result.created_at) if result.created_at else None,
                            "last_modified": str(result.last_modified) if result.last_modified else None,
                        }
                except Exception:
                    logger.warning(f"SDK get_session_info failed for {session_id}")

            effective_config_dict, template_meta = await self._build_effective_config_payload(
                session_info
            )

            return {
                "session": session_info.to_dict(),
                "sdk": sdk_info,
                "storage": storage_info,
                "sdk_session_info": sdk_session_info,
                "effective_config": effective_config_dict,
                "template": template_meta,
            }

        except Exception:
            logger.exception(f"Failed to get session info for {session_id}")
            return None

    async def list_sessions(self, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        """List all sessions with their current status, paginated."""
        try:
            sessions = await self.session_manager.list_sessions()
            all_sessions = []
            for session_info in sessions:
                entry = session_info.to_dict()
                if session_info.template_id:
                    eff, _tmpl = await self._build_effective_config_payload(session_info)
                    entry["effective_config"] = eff
                all_sessions.append(entry)
            total = len(all_sessions)
            sliced = all_sessions[offset : offset + limit]
            return {
                "sessions": sliced,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(sliced) < total,
            }
        except Exception:
            logger.exception("Failed to list sessions")
            return {"sessions": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}

    # ==================== TASK LEG REGISTRY (Issue #1746) ====================

    async def _get_task_leg_registry(self, session_id: str) -> TaskLegRegistry:
        """Get (lazily hydrating) the per-session TaskLegRegistry.

        Hydration replays every stored Task lifecycle message once — so a
        page refresh and a live-streamed session converge on identical
        state. Only cached once a storage manager is available; if the
        session hasn't been started yet in this process, an ephemeral empty
        registry is returned so a later call (once storage exists) can
        still hydrate properly.
        """
        registry = self._task_leg_registries.get(session_id)
        if registry is not None:
            return registry

        registry = TaskLegRegistry()
        storage = self._storage_managers.get(session_id)
        if storage:
            raw_messages = await storage.read_messages()
            for raw_message in raw_messages:
                if raw_message.get("type") != "system":
                    continue
                metadata = raw_message.get("metadata") or {}
                subtype = metadata.get("subtype")
                if subtype:
                    registry.apply_frame(subtype, metadata, raw_message.get("timestamp"))
            self._task_leg_registries[session_id] = registry

        return registry

    async def get_background_agents(self, session_id: str) -> dict[str, Any]:
        """Snapshot of background-agent (Task) leg history for reload/reconnect."""
        registry = await self._get_task_leg_registry(session_id)
        return {"session_id": session_id, "agents": registry.snapshot()}
