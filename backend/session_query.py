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

from .background_tasks import (
    BACKGROUND_TASKS_SUBTYPE,
    PROCESS_BOUNDARY_SUBTYPES,
    latest_background_tasks,
)
from .config_resolution import resolve_effective_config
from .message_history_reader import FileMessageHistoryReader, build_budget, read_history_payload
from .session_lifecycle import is_live, is_paused
from .task_list import TASK_TOOL_NAMES, TaskListState
from .task_registry import TASK_LIFECYCLE_SUBTYPES, TaskLegRegistry

logger = logging.getLogger(__name__)

# Issue #2030 (§3.4): substring prefilters for the streamed snapshot hydrators.
_TASK_LEG_PREFILTERS = tuple(f'"{s}"' for s in sorted(TASK_LIFECYCLE_SUBTYPES))
_TASK_LIST_PREFILTERS = tuple(f'"{n}"' for n in sorted(TASK_TOOL_NAMES))
_BACKGROUND_TASK_PREFILTERS = tuple(
    f'"{s}"' for s in (BACKGROUND_TASKS_SUBTYPE, *sorted(PROCESS_BOUNDARY_SUBTYPES))
)


class SessionQuery:
    """Owns session-info/listing response assembly and the task-leg registry."""

    def __init__(self, *, coordinator: Any):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), not a value
        # snapshotted here at construction time — same discipline as
        # SessionLauncher/MessagePipeline/ToolCallTracker/ArchiveCoordinator/
        # SessionResources (PR 1-5).
        self._coordinator = coordinator
        # Issue #2030 (§3.6): cold-hydration bookkeeping for the task-list snapshot.
        # session_id -> live Task* records that arrived while hydration was in flight
        self._task_lists: dict[str, TaskListState] = {}
        self._task_list_pending: dict[str, list[dict[str, Any]]] = {}
        self._task_list_inflight: dict[str, asyncio.Task] = {}
        self._task_list_generation: dict[str, int] = {}

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    template_manager = property(lambda self: self._coordinator.template_manager)
    profile_manager = property(lambda self: self._coordinator.profile_manager)

    # --- Shared per-session state: live passthrough to the coordinator -------
    _active_sdks = property(lambda self: self._coordinator._active_sdks)
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _task_leg_registries = property(lambda self: self._coordinator._task_leg_registries)
    _background_tasks = property(lambda self: self._coordinator._background_tasks)

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
                    # Deferred import: NOT a circular-import break (confirmed during
                    # issue #2189 PR 11's AC6 cleanup) — it's an optional/version-
                    # guarded SDK API. Older claude_agent_sdk versions may lack
                    # get_session_info entirely; the surrounding except clause relies
                    # on the lookup happening here so a missing symbol degrades this
                    # one augmentation instead of failing module import.
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
            # Issue #2030 (§3.4): stream only the lifecycle records, off the event loop —
            # no whole-file parse. Non-matching lines are never JSON-decoded.
            reader = storage.reader

            def _hydrate() -> None:
                for raw_message in reader.iter_matching(_TASK_LEG_PREFILTERS):
                    if raw_message.get("type") != "system":
                        continue
                    metadata = raw_message.get("metadata") or {}
                    subtype = metadata.get("subtype")
                    if subtype:
                        registry.apply_frame(subtype, metadata, raw_message.get("timestamp"))

            await asyncio.to_thread(_hydrate)
            self._task_leg_registries[session_id] = registry

        return registry

    async def get_background_agents(self, session_id: str) -> dict[str, Any]:
        """Snapshot of background-agent (Task) leg history for reload/reconnect."""
        registry = await self._get_task_leg_registry(session_id)
        return {"session_id": session_id, "agents": registry.snapshot()}

    # ==================== BACKGROUND TASKS (Issue #2145) ====================

    async def _get_background_tasks(self, session_id: str) -> list[dict[str, Any]]:
        """Latest background-task snapshot, lazily hydrated from messages.jsonl.

        Only cached once storage exists (same rule as the task leg registry), so a
        not-yet-started session returns an ephemeral empty list.
        """
        cached = self._background_tasks.get(session_id)
        if cached is not None:
            return cached
        storage = self._storage_managers.get(session_id)
        if not storage:
            return []
        reader = storage.reader
        tasks = await asyncio.to_thread(
            lambda: latest_background_tasks(reader.iter_matching(_BACKGROUND_TASK_PREFILTERS))
        )
        self._background_tasks[session_id] = tasks
        return tasks

    async def get_background_tasks(self, session_id: str) -> dict[str, Any]:
        """Currently running background tasks, for reload/reconnect.

        Empty unless the session's SDK process is alive — keyed on process liveness,
        not state == ACTIVE, so a PAUSED session (awaiting permission) keeps its list.
        """
        info = await self.session_manager.get_session_info(session_id)
        state = getattr(info, "state", None)
        if (
            session_id not in self._active_sdks
            or state is None
            or not (is_live(state) or is_paused(state))
        ):
            return {"session_id": session_id, "tasks": []}
        return {"session_id": session_id, "tasks": await self._get_background_tasks(session_id)}

    async def get_window_messages(
        self, session_id: str, *, window: str | None = None, before: str | None = None,
        min_turns: int | None = None, max_records: int | None = None,
        max_bytes: int | None = None, **_unused: Any,
    ) -> dict[str, Any]:
        """Issue #2030: ``window=tail`` / ``before=<cursor>`` history read (byte-cursor reader).

        Raises ``CursorInvalidError`` for an unusable cursor (route maps it to 409).
        """
        storage = self._storage_managers.get(session_id)
        if not storage:
            logger.error(f"No storage manager found for session {session_id}")
            return {
                "messages": [], "start_cursor": "b:0", "has_more_before": False,
                "history_id": "", "snapped": True, "total_bytes": 0, "latest_init": None,
            }
        return await read_history_payload(
            storage.reader, before=before,
            budget=build_budget(
                before=before is not None, min_turns=min_turns,
                max_records=max_records, max_bytes=max_bytes,
            ),
        )

    # ==================== SDK TASK LIST SNAPSHOT (Issue #2030) ====================

    async def _get_task_list(self, session_id: str) -> TaskListState:
        """Lazily hydrated per-session task list (cached only once storage exists)."""
        state = self._task_lists.get(session_id)
        if state is not None:
            return state
        # The task list is conversation state: it must stay readable after terminate
        # (which drops the storage manager), so fall back to the session's directory.
        storage = self._storage_managers.get(session_id)
        if storage:
            reader = storage.reader
        else:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir is None:
                return TaskListState()
            reader = FileMessageHistoryReader(session_dir / "messages.jsonl", label=session_id)
        inflight = self._task_list_inflight.get(session_id)
        if inflight is None:
            inflight = asyncio.ensure_future(self._hydrate_task_list(session_id, reader))
            self._task_list_inflight[session_id] = inflight
        return await asyncio.shield(inflight)

    async def _hydrate_task_list(self, session_id: str, reader) -> TaskListState:
        generation = self._task_list_generation.get(session_id, 0)
        state = TaskListState()
        pending: list[dict[str, Any]] = []
        self._task_list_pending[session_id] = pending

        def _replay() -> None:
            for record in reader.iter_matching(_TASK_LIST_PREFILTERS):
                state.apply_tool_call(record)

        try:
            await asyncio.to_thread(_replay)
        finally:
            if self._task_list_pending.get(session_id) is pending:
                del self._task_list_pending[session_id]
            if self._task_list_inflight.get(session_id) is asyncio.current_task():
                del self._task_list_inflight[session_id]
        # Live records appended while the replay was running may or may not have been seen
        # by it; TaskListState de-duplicates by tool_use_id, so replaying them is safe.
        for record in pending:
            state.apply_tool_call(record)
        if self._task_list_generation.get(session_id, 0) == generation:
            self._task_lists[session_id] = state
        return state

    def feed_task_tool_call(self, session_id: str, record: dict[str, Any]) -> None:
        """Live feed of a just-persisted tool_call record. Applied only when the cache is
        warm (a cold hydration replays the stored record itself — feeding it as well would
        double-count, the same trap as the leg registry), or buffered while a hydration is
        in flight."""
        if record.get("type") != "tool_call" or record.get("name") not in TASK_TOOL_NAMES:
            return
        state = self._task_lists.get(session_id)
        if state is not None:
            state.apply_tool_call(record)
            return
        pending = self._task_list_pending.get(session_id)
        if pending is not None:
            pending.append(record)

    def clear_task_list(self, session_id: str) -> None:
        self._task_lists.pop(session_id, None)
        self._task_list_pending.pop(session_id, None)
        self._task_list_inflight.pop(session_id, None)  # a reset must not serve pre-reset state
        self._task_list_generation[session_id] = self._task_list_generation.get(session_id, 0) + 1

    async def get_session_tasks(self, session_id: str) -> dict[str, Any]:
        """SDK task-list snapshot (browser order: numeric id ascending, deleted excluded)."""
        state = await self._get_task_list(session_id)
        return {"session_id": session_id, "tasks": state.snapshot()}
