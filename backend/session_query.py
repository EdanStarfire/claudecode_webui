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
import hashlib
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from .background_tasks import (
    BACKGROUND_TASKS_SUBTYPE,
    PROCESS_BOUNDARY_SUBTYPES,
    latest_background_tasks,
)
from .cold_scan import ColdScanManager
from .config_manager import PerformanceConfig
from .config_resolution import resolve_effective_config
from .history_index import HistoryIndexBuildingError, HistoryIndexState, scan_file_into
from .history_search import DEFAULT_MAX_RESULTS, SearchCaps
from .message_history_reader import (
    ArchiveNotFoundError,
    FileMessageHistoryReader,
    MessageNotFoundError,
    ResolveBusyError,
    ResolvedId,
    build_after_budget,
    build_around_budget,
    build_budget,
    read_after_payload,
    read_around_payload,
    read_history_payload,
)
from .search_scan import SearchScanManager
from .session_lifecycle import is_live, is_paused
from .task_list import TASK_TOOL_NAMES, TaskListState
from .task_registry import TASK_LIFECYCLE_SUBTYPES, TaskLegRegistry

logger = logging.getLogger(__name__)

# Issue #2030 stage 3 PR 3A: id lookups (``around_id=``). The only O(file) step of the windowed
# reads; runs on the cold-scan pool (never the default executor), single-flight per (session, id).
RESOLVE_WAIT_S = 5.0
RESOLVE_MAX_FLIGHTS_PER_KEY = 4
_RESOLVE_KIND = "resolve:"

# Issue #2030 (§3.4): substring prefilters for the streamed snapshot hydrators.
_TASK_LEG_PREFILTERS = tuple(f'"{s}"' for s in sorted(TASK_LIFECYCLE_SUBTYPES))
_TASK_LIST_PREFILTERS = tuple(f'"{n}"' for n in sorted(TASK_TOOL_NAMES))
_BACKGROUND_TASK_PREFILTERS = tuple(
    f'"{s}"' for s in (BACKGROUND_TASKS_SUBTYPE, *sorted(PROCESS_BOUNDARY_SUBTYPES))
)


class SessionQuery:
    """Owns session-info/listing response assembly and the task-leg registry."""

    def __init__(self, *, coordinator: Any, performance: PerformanceConfig | None = None):
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
        # Issue #2030 stage 2 (§3.3): every cold O(file) scan (agents/background/tasks
        # snapshots + the history index) runs on one bounded dedicated pool with
        # per-(session, kind) single-flight; see backend/cold_scan.py.
        performance = performance or self._load_performance_config(coordinator)
        self._cold_scans = ColdScanManager(performance.cold_scan_workers)
        # Issue #2030 stage 3 (PR 3B): whole-history search runs on its own small pool, never on
        # the cold-scan pool or the default executor; see backend/search_scan.py.
        self._searches = SearchScanManager(
            performance.search_workers, performance.search_max_flights_per_session
        )
        self._history_index_cache_max = max(1, int(performance.history_index_cache_max or 1))
        self._history_index_wait_s = max(0.1, float(performance.history_index_wait_s or 20.0))
        # key (session_id, or "<sid>/archive/<aid>") -> state; LRU, most recent last.
        self._history_indexes: OrderedDict[str, HistoryIndexState] = OrderedDict()
        self._history_index_fingerprints: dict[str, tuple[int, int]] = {}  # archive keys only
        self._history_index_pending: dict[str, list[tuple[int, int, dict]]] = {}
        # session_id -> (task, cancel event) of the live-callback registry hydration
        self._live_registry_inflight: dict[str, tuple[asyncio.Task, threading.Event]] = {}

    @staticmethod
    def _load_performance_config(coordinator: Any) -> PerformanceConfig:
        try:
            return coordinator.tenant_context.load_config().performance
        except Exception:
            logger.debug("performance config unavailable; using defaults", exc_info=True)
            return PerformanceConfig()

    def shutdown(self) -> None:
        self._cold_scans.shutdown()
        self._searches.shutdown()

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

    async def _get_task_leg_registry(
        self, session_id: str, *, live: bool = False
    ) -> TaskLegRegistry:
        """Get (lazily hydrating) the per-session TaskLegRegistry.

        Hydration replays every stored Task lifecycle message once — so a
        page refresh and a live-streamed session converge on identical
        state. Only cached once a storage manager is available; if the
        session hasn't been started yet in this process, an ephemeral empty
        registry is returned so a later call (once storage exists) can
        still hydrate properly.

        ``live=True`` is the LIVE-message critical path (message_pipeline's task-frame feed): it
        blocks that session's message processing, so it never waits on the cold-scan pool — it
        runs its own scan on the default executor (single-flight among live callers only).
        """
        registry = self._task_leg_registries.get(session_id)
        if registry is not None:
            return registry

        storage = self._storage_managers.get(session_id)
        if not storage:
            return TaskLegRegistry()
        # Issue #2030 (§3.4): stream only the lifecycle records, off the event loop — no
        # whole-file parse. Non-matching lines are never JSON-decoded.
        reader = storage.reader

        def _scan(cancel) -> TaskLegRegistry:
            registry = TaskLegRegistry()
            for raw_message in reader.iter_matching(_TASK_LEG_PREFILTERS, cancel=cancel):
                if raw_message.get("type") != "system":
                    continue
                metadata = raw_message.get("metadata") or {}
                subtype = metadata.get("subtype")
                if subtype:
                    registry.apply_frame(subtype, metadata, raw_message.get("timestamp"))
            return registry

        if live:
            return await self._live_registry_hydration(session_id, _scan)

        async def _flight(handle) -> TaskLegRegistry:
            registry = await handle.run(_scan)
            if handle.cancelled:
                return registry
            # A live-path hydration may have warmed (and live-fed) the registry while this job
            # was queued: that one is at least as current, keep it.
            return self._task_leg_registries.setdefault(session_id, registry)

        return await self._cold_scans.single_flight(
            session_id, "agents", _flight, empty=TaskLegRegistry
        )

    async def _live_registry_hydration(self, session_id: str, scan) -> TaskLegRegistry:
        """Default-executor hydration for the live callback (see ``live`` above). Concurrent live
        callers share one scan; reset/delete (``clear_task_list``) cancels it; the result is
        cached unless a pool flight (browser-requested) cached one first."""
        entry = self._live_registry_inflight.get(session_id)
        if entry is None:
            cancel = threading.Event()

            async def _run() -> TaskLegRegistry:
                try:
                    registry = await asyncio.to_thread(scan, cancel)
                finally:
                    if self._live_registry_inflight.get(session_id, (None,))[0] is task:
                        del self._live_registry_inflight[session_id]
                if cancel.is_set():
                    return TaskLegRegistry()
                return self._task_leg_registries.setdefault(session_id, registry)

            task = asyncio.ensure_future(_run())
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            entry = (task, cancel)
            self._live_registry_inflight[session_id] = entry
        return await asyncio.shield(entry[0])

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

        def _scan(cancel) -> list[dict[str, Any]]:
            return latest_background_tasks(
                reader.iter_matching(_BACKGROUND_TASK_PREFILTERS, cancel=cancel)
            )

        async def _flight(handle) -> list[dict[str, Any]]:
            tasks = await handle.run(_scan)
            if not handle.cancelled:
                self._background_tasks[session_id] = tasks
            return tasks

        return await self._cold_scans.single_flight(session_id, "background", _flight, empty=list)

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

    async def _live_reader(self, session_id: str) -> FileMessageHistoryReader | None:
        storage = self._storage_managers.get(session_id)
        if storage:
            return storage.reader
        session_dir = await self.session_manager.get_session_directory(session_id)
        if session_dir is None:
            return None
        return FileMessageHistoryReader(session_dir / "messages.jsonl", label=session_id)

    async def get_window_messages(
        self, session_id: str, *, window: str | None = None, before: str | None = None,
        min_turns: int | None = None, max_records: int | None = None,
        max_bytes: int | None = None, around: str | None = None, around_id: str | None = None,
        before_bytes: int | None = None, after_bytes: int | None = None,
        after: str | None = None, until: str | None = None, **_unused: Any,
    ) -> dict[str, Any]:
        """Issue #2030: ``window=tail`` / ``before=<cursor>`` history read (byte-cursor reader);
        stage 3: ``around=<cursor>`` / ``around_id=<id>`` windows and ``after=<cursor>`` forward
        pages.

        Raises ``CursorInvalidError`` for an unusable cursor (route maps it to 409),
        ``MessageNotFoundError`` (404), ``ResolveBusyError`` (503).
        """
        if around is not None or around_id is not None or after is not None:
            reader = await self._live_reader(session_id)
            if reader is None:
                raise MessageNotFoundError(around_id or "")
            return await self._windowed_read(
                session_id, reader, around, around_id, before_bytes, after_bytes, after, until,
                max_bytes,
            )
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

    async def get_archive_window_messages(
        self, session_id: str, archive_id: str, archive_manager: Any, **params: Any
    ) -> dict[str, Any]:
        """``around`` / ``around_id`` / ``after`` of an archive. Raises ``ArchiveNotFoundError``
        for an unsafe path component or a missing archive (never a cursor error)."""
        try:
            messages_file = archive_manager.archive_messages_path(session_id, archive_id)
        except ValueError as e:
            raise ArchiveNotFoundError(archive_id) from e
        if not await asyncio.to_thread(os.path.exists, messages_file):
            raise ArchiveNotFoundError(archive_id)
        key = f"{session_id}/archive/{archive_id}"
        reader = FileMessageHistoryReader(messages_file, label=key, strict_legacy=False)
        return await self._windowed_read(
            key, reader, params.get("around"), params.get("around_id"),
            params.get("before_bytes"), params.get("after_bytes"), params.get("after"),
            params.get("until"), params.get("max_bytes"),
        )

    async def _windowed_read(
        self, key: str, reader: Any, around: str | None, around_id: str | None,
        before_bytes: int | None, after_bytes: int | None, after: str | None,
        until: str | None, max_bytes: int | None,
    ) -> dict[str, Any]:
        """``around`` / ``around_id`` / ``after`` read of one history file. The reads themselves are
        cheap (bounded by the window) and stay on the default executor; only an id lookup, which is
        O(file), goes through the cold-scan pool."""
        if after is not None:
            return await read_after_payload(reader, after, build_after_budget(max_bytes), until)
        unresolved = False
        if around is None:
            found = await self.resolve_message_id(key, reader, around_id or "")
            around, unresolved = found.cursor, found.parent_unresolved
        payload = await read_around_payload(
            reader, around, build_around_budget(before_bytes, after_bytes)
        )
        if unresolved:  # a tool_use_id whose launching message was not found: anchored on its tool_call
            payload["parent_unresolved"] = True
        return payload

    async def resolve_message_id(self, key: str, reader: Any, ident: str) -> ResolvedId:
        """Location (``ResolvedId``) of ``ident`` (message_id or tool_use_id) in ``reader``'s file, via the cold-scan
        pool: single-flight per (key, id), at most ``RESOLVE_MAX_FLIGHTS_PER_KEY`` distinct lookups
        per key, waiter cap ``RESOLVE_WAIT_S``. Raises ``MessageNotFoundError`` (also when the
        session was deleted/reset mid-lookup) and ``ResolveBusyError``."""
        kind = _RESOLVE_KIND + hashlib.sha1(ident.encode()).hexdigest()[:12]
        active = self._cold_scans.inflight_kinds(key)
        if kind not in active and sum(k.startswith(_RESOLVE_KIND) for k in active) >= (
            RESOLVE_MAX_FLIGHTS_PER_KEY
        ):
            raise ResolveBusyError(key)

        async def _flight(handle) -> ResolvedId | None:
            return await handle.run(lambda cancel: reader.lookup_id(ident, cancel))

        try:
            found = await self._cold_scans.single_flight(
                key, kind, _flight, empty=lambda: None, timeout=RESOLVE_WAIT_S
            )
        except TimeoutError as e:
            raise ResolveBusyError(key) from e
        if found is None:
            raise MessageNotFoundError(ident)
        return found

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

        def _replay(cancel) -> TaskListState:
            state = TaskListState()
            for record in reader.iter_matching(_TASK_LIST_PREFILTERS, cancel=cancel):
                state.apply_tool_call(record)
            return state

        async def _flight(handle) -> TaskListState:
            pending: list[dict[str, Any]] = []
            self._task_list_pending[session_id] = pending
            try:
                state = await handle.run(_replay)
            finally:
                if self._task_list_pending.get(session_id) is pending:
                    del self._task_list_pending[session_id]
            # Live records appended while the replay was running may or may not have been seen
            # by it; TaskListState de-duplicates by tool_use_id, so replaying them is safe.
            for record in pending:
                state.apply_tool_call(record)
            if not handle.cancelled:
                self._task_lists[session_id] = state
            return state

        return await self._cold_scans.single_flight(
            session_id, "tasks", _flight, empty=TaskListState
        )

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
        # A reset/delete must not serve or cache pre-reset state: abandon every in-flight cold
        # scan of the session (queued ones never start, running ones stop at the next batch)
        # and drop its history index.
        self._cold_scans.invalidate(session_id)
        self._searches.invalidate(session_id)
        live = self._live_registry_inflight.pop(session_id, None)
        if live is not None:
            live[1].set()
        self.drop_history_index(session_id)
        # The agent/background snapshot caches are history-derived too: the reset paths pop
        # them already; a delete never did (a per-deleted-session leak found by the load test).
        self._task_leg_registries.pop(session_id, None)
        self._background_tasks.pop(session_id, None)

    async def get_session_tasks(self, session_id: str) -> dict[str, Any]:
        """SDK task-list snapshot (browser order: numeric id ascending, deleted excluded)."""
        state = await self._get_task_list(session_id)
        return {"session_id": session_id, "tasks": state.snapshot()}

    # ==================== HISTORY INDEX (Issue #2030, stage 2) ====================

    # Cold scans the index chains behind (per-session ordering, §3.3 item 2).
    _INDEX_AFTER = ("agents", "background", "tasks")

    def _index_cache_get(self, key: str) -> HistoryIndexState | None:
        state = self._history_indexes.get(key)
        if state is not None:
            self._history_indexes.move_to_end(key)
        return state

    def _index_cache_put(self, key: str, state: HistoryIndexState) -> None:
        self._history_indexes[key] = state
        self._history_indexes.move_to_end(key)
        while len(self._history_indexes) > self._history_index_cache_max:
            evicted, _ = self._history_indexes.popitem(last=False)
            self._history_index_fingerprints.pop(evicted, None)

    def drop_history_index(self, session_id: str, *, abandon_scans: bool = False) -> None:
        """Drop a session's cached index (stop/terminate/delete/reset). ``abandon_scans`` also
        abandons an in-flight index scan (its waiters get 503 ``index_building`` and retry); it
        leaves in-flight searches alone (a stop does not change the history file)."""
        self._history_indexes.pop(session_id, None)
        self._history_index_pending.pop(session_id, None)
        if abandon_scans:
            self._cold_scans.invalidate(session_id, kinds=("index",))
            # (searches deliberately survive a stop: it does not change the history file and
            # nothing is cached; delete / reset abandon them via clear_task_list)

    def attach_history_index_feed(self, storage_manager: Any, session_id: str) -> bool:
        """Register the live feed on a storage manager's ``on_append`` (idempotent). The hook runs
        inside the writer's lock: O(1), no awaits, no locks, never raises into the writer."""
        if getattr(storage_manager, "_history_index_fed", False):
            return False
        storage_manager._history_index_fed = True

        async def _feed(_session_id, _project_id, record) -> None:
            try:
                start, end = storage_manager.last_append_range
                self.feed_history_index(session_id, record, start, end)
            except Exception:
                logger.exception("history index feed failed for session %s", session_id)

        storage_manager.on_append.append(_feed)
        return True

    def feed_history_index(
        self, session_id: str, record: dict[str, Any], start: int, end: int
    ) -> None:
        """Live feed of a just-persisted record (``start``/``end`` = its byte range). Applied
        only when the index is warm; buffered while a scan is in flight (the scan may or may
        not have read it — the buffer is de-duplicated by byte offset on commit)."""
        state = self._history_indexes.get(session_id)
        if state is not None:
            if start < state.end:
                return  # already covered by the scan that built this state
            if start > state.end:
                # A write this index never saw (unhooked writer, truncation): it can no longer
                # be trusted — drop it, the next request rescans.
                self._history_indexes.pop(session_id, None)
                return
            state.apply(start, end, record)
            return
        pending = self._history_index_pending.get(session_id)
        if pending is not None:
            rtype = record.get("type")
            # Only user/system records carry what the reducer reads; anything else is kept as
            # a type-only stub so the pending buffer stays small while a long scan runs.
            pending.append((start, end, record if rtype in ("user", "system") else {"type": rtype}))

    async def get_history_index(self, session_id: str) -> dict[str, Any]:
        """Turn + compaction index of a live session's history (``/history_index``).

        Raises ``HistoryIndexBuildingError`` when the cold scan does not finish within the
        waiter cap or was abandoned (session stopped/reset mid-scan)."""
        storage = self._storage_managers.get(session_id)
        if storage:
            # Idempotent: covers storage managers created outside the session-start paths.
            if self.attach_history_index_feed(storage, session_id):
                # A manager this index was never fed by (replaced / created unhooked): anything
                # it appended is unseen, so a previously warm state cannot be trusted.
                self.drop_history_index(session_id)
            reader = storage.reader
        else:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir is None:
                return HistoryIndexState().to_payload()
            reader = FileMessageHistoryReader(session_dir / "messages.jsonl", label=session_id)

        state = self._index_cache_get(session_id)
        if state is not None:
            history_id, size = await asyncio.to_thread(reader.probe)
            if history_id == state.history_id and size >= state.end:
                return state.to_payload()
            self.drop_history_index(session_id)  # file replaced/truncated underneath us

        def _scan(cancel) -> HistoryIndexState:
            history_id, _ = reader.probe()
            state = HistoryIndexState(history_id)
            scan_file_into(reader, state, cancel)
            return state

        async def _flight(handle) -> HistoryIndexState:
            pending: list[tuple[int, int, dict]] = []
            self._history_index_pending[session_id] = pending
            try:
                state = await handle.run(_scan)
            finally:
                if self._history_index_pending.get(session_id) is pending:
                    del self._history_index_pending[session_id]
            if handle.cancelled:
                return state
            for start, end, record in pending:
                if start < state.end:
                    continue  # the scan already read this record
                if start > state.end:
                    return state  # gap: serve this scan's view uncached, rescan next time
                state.apply(start, end, record)
            self._index_cache_put(session_id, state)
            return state

        return await self._await_index(
            session_id, "index", _flight, after=self._INDEX_AFTER
        )

    async def _await_index(self, key: str, kind: str, flight, *, after: tuple[str, ...]):
        try:
            state = await self._cold_scans.single_flight(
                key, kind, flight, empty=lambda: None, after=after,
                timeout=self._history_index_wait_s,
            )
        except TimeoutError as e:
            raise HistoryIndexBuildingError(key) from e
        if state is None:
            raise HistoryIndexBuildingError(key)
        return state.to_payload()

    async def get_archive_history_index(
        self, session_id: str, archive_id: str, messages_file: Any
    ) -> dict[str, Any]:
        """Index of an archived session's history; cached by (path, size, mtime)."""
        key = f"{session_id}/archive/{archive_id}"
        reader = FileMessageHistoryReader(messages_file, label=key, strict_legacy=False)

        def _fingerprint() -> tuple[int, int]:
            try:
                st = os.stat(messages_file)
            except FileNotFoundError:
                return (0, 0)
            return (st.st_size, st.st_mtime_ns)

        fingerprint = await asyncio.to_thread(_fingerprint)
        state = self._index_cache_get(key)
        if state is not None:
            if self._history_index_fingerprints.get(key) == fingerprint:
                return state.to_payload()
            self._history_indexes.pop(key, None)

        def _scan(cancel) -> HistoryIndexState:
            history_id, _ = reader.probe()
            state = HistoryIndexState(history_id, archive=True)
            scan_file_into(reader, state, cancel)
            return state

        async def _flight(handle) -> HistoryIndexState:
            state = await handle.run(_scan)
            if not handle.cancelled:
                self._index_cache_put(key, state)
                self._history_index_fingerprints[key] = fingerprint
            return state

        return await self._await_index(key, "index", _flight, after=())

    # ==================== WHOLE-HISTORY SEARCH (Issue #2030, stage 3) ====================

    async def search_history(
        self, session_id: str, query: str, cursor: str | None, limit: int | None, client_id,
        is_disconnected,
    ) -> dict[str, Any] | None:
        """One newest-first page of the whole-history search of a live session (``None`` when the
        search was superseded / abandoned). Raises ``SearchQueryError``, ``CursorInvalidError``,
        ``SearchBusyError``."""
        storage = self._storage_managers.get(session_id)
        if storage:
            reader = storage.reader
        else:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir is None:
                return None
            reader = FileMessageHistoryReader(session_dir / "messages.jsonl", label=session_id)
        return await self._search(
            session_id, reader, query, cursor, limit, client_id, is_disconnected
        )

    async def search_archive_history(
        self, session_id: str, archive_id: str, messages_file: Any, query: str,
        cursor: str | None, limit: int | None, client_id, is_disconnected,
    ) -> dict[str, Any] | None:
        key = f"{session_id}/archive/{archive_id}"
        reader = FileMessageHistoryReader(messages_file, label=key, strict_legacy=False)
        return await self._search(key, reader, query, cursor, limit, client_id, is_disconnected)

    async def _search(self, key, reader, query, cursor, limit, client_id, is_disconnected):
        caps = SearchCaps(max_results=min(max(1, limit or DEFAULT_MAX_RESULTS), DEFAULT_MAX_RESULTS))
        page = await self._searches.search(
            key, reader, query, cursor, caps, client_id=client_id, is_disconnected=is_disconnected,
        )
        return None if page is None else page.to_payload()
