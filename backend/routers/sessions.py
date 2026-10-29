"""Session CRUD and messaging endpoints: /api/sessions*"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from shared.event_envelope import PROCESS_EPOCH, session_stream_epoch
from shared.exception_handlers import handle_exceptions

from ..fixture_export import FixtureExportError
from ..history_index import HistoryIndexBuildingError
from ..message_history_reader import CursorInvalidError
from ..session_lifecycle import is_deliverable
from ._models import (
    FixtureExportRequest,
    MessageRequest,
    SessionCreateRequest,
    SessionNameUpdateRequest,
    SessionUpdateRequest,
    _validate_additional_directories,
)
from ._search import run_search

logger = logging.getLogger(__name__)


async def _validate_recording_config(webui, session_id: str, config: dict) -> None:
    """Reject a config-dict PATCH that turns recording_enabled on when it isn't
    actually allowed (issue #1998). Server-side is the source of truth for both
    rejections here — the manage-modal toggle/disabledWhen are client-side courtesy
    only, never the only enforcement (mirrors the Docker-rejection banner's own
    posture).
    """
    if not config.get("recording_enabled"):
        return
    if not webui.service.get_session_recording_enabled():
        raise HTTPException(
            status_code=400,
            detail="Session recording is disabled on this instance. Restart the "
                   "Backend with --enable-session-recording to enable this "
                   "developer feature.",
        )
    effective_docker_enabled = config.get("docker_enabled")
    if effective_docker_enabled is None:
        current_info = await webui.service.get_session_object(session_id)
        effective_docker_enabled = bool(
            current_info.config.get("docker_enabled")
        ) if current_info else False
    if effective_docker_enabled:
        raise HTTPException(
            status_code=400,
            detail="Recording is not supported on Docker-isolated sessions and "
                   "cannot be enabled while Docker Isolation is on. Disable Docker "
                   "Isolation on this session, or create a separate non-Docker "
                   "session for recording.",
        )


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ==================== SESSION ENDPOINTS ====================

    @router.post("/api/sessions")
    @handle_exceptions("create session")
    async def create_session(request: SessionCreateRequest):
        """Create a new Claude Code session within a project"""
        # Pre-generate session ID so we can pass it to permission callback
        session_id = str(uuid.uuid4())

        if request.project_id:
            if not await webui.service.validate_project_exists(request.project_id):
                raise HTTPException(status_code=404, detail="Project not found")

        # Issue #630: Validate additional directories
        validated_dirs = _validate_additional_directories(
            request.additional_directories, None
        )

        config = request.model_copy(update={"additional_directories": validated_dirs})
        session_id = await webui.service.create_session(
            session_id=session_id,
            project_id=request.project_id,
            config=config,
            name=request.name,
            role=request.role,
            capabilities=request.capabilities,
            permission_callback=webui.service.create_permission_callback(session_id),
        )

        # Create event queue for this new session
        webui.service.get_or_create_session_queue(session_id)

        # Broadcast session creation to all UI clients
        session_info_dict = await webui.service.get_session_info(session_id)
        if session_info_dict:
            webui.service.broadcast_state_change(
                session_id,
                session_info_dict.get("session", session_info_dict),
                datetime.now().isoformat()
            )

        # Broadcast project update to all UI clients (session was added to project)
        project_dict = (
            await webui.service.get_project(request.project_id) if request.project_id else None
        )
        if project_dict:
            webui.service.broadcast_project_updated(
                {k: v for k, v in project_dict.items() if k != "sessions"}
            )

        return {"session_id": session_id}

    @router.get("/api/sessions")
    @handle_exceptions("list sessions")
    async def list_sessions(limit: int = 500, offset: int = 0):
        """List all sessions"""
        return await webui.service.list_sessions(limit=limit, offset=offset)

    @router.get("/api/sessions/{session_id}")
    @handle_exceptions("get session info")
    async def get_session_info(session_id: str):
        """Get session information"""
        info = await webui.service.get_session_info(session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")

        # Log session state for debugging
        session_state = info.get('session', {}).get('state', 'unknown')
        logger.info(f"API returning session {session_id} with state: {session_state}")

        return info

    @router.get("/api/sessions/{session_id}/usage")
    @handle_exceptions("get session usage")
    async def get_session_usage(session_id: str):
        """Return aggregated token usage and estimated cost for a session (issue #1125)."""
        from ..config_manager import PricingConfig, compute_cost
        aggregate = await webui.service.get_session_usage_aggregate(session_id)
        if aggregate is None:
            raise HTTPException(status_code=404, detail="No usage data for this session")

        config = webui.tenant_context.load_config()
        pricing = config.pricing if config.pricing else PricingConfig()

        model = aggregate.get("model")
        rates, rates_known = pricing.get_rates(model)

        result: dict = {
            "session_id": session_id,
            "model": model,
            "turn_count": aggregate.get("turn_count", 0),
            "input_tokens": aggregate.get("input_tokens", 0),
            "output_tokens": aggregate.get("output_tokens", 0),
            "cache_write_tokens": aggregate.get("cache_write_tokens", 0),
            "cache_read_tokens": aggregate.get("cache_read_tokens", 0),
            "cache_write_tokens_5m": aggregate.get("cache_write_tokens_5m", 0),
            "cache_write_tokens_1h": aggregate.get("cache_write_tokens_1h", 0),
            "sdk_reported_cost_usd": aggregate.get("sdk_total_cost_usd"),
            "rates_known": rates_known,
        }

        if rates is not None:
            result["estimated_cost_usd"] = compute_cost(rates, aggregate)
            result["rates_used"] = rates.to_dict()
        else:
            result["estimated_cost_usd"] = None
            result["rates_used"] = None

        return result

    @router.get("/api/sessions/{session_id}/descendants")
    @handle_exceptions("get session descendants")
    async def get_session_descendants(session_id: str, limit: int = 50, offset: int = 0):
        """Get all descendant sessions (children, grandchildren, etc.) of a session"""
        result = await webui.service.get_descendants(session_id, limit=limit, offset=offset)
        result["session_id"] = session_id
        return result

    @router.post("/api/sessions/{session_id}/start")
    @handle_exceptions("start session")
    async def start_session(session_id: str):
        """Start a session"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        success = await webui.service.start_session_with_fresh_callback(session_id)
        return {"success": success}

    @router.post("/api/sessions/{session_id}/mark-unread")
    @handle_exceptions("mark session unread")
    async def mark_session_unread(session_id: str):
        """Restore the unread indicator by clearing last_viewed_at (issue #1597)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        success = await webui.service.mark_session_unread(session_id)
        return {"success": success}

    @router.post("/api/sessions/{session_id}/mark-read")
    @handle_exceptions("mark session read")
    async def mark_session_read(session_id: str):
        """Clear the unread indicator by aligning last_viewed_at with last_completion_at (issue #1646)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        success = await webui.service.mark_session_read(session_id)
        return {"success": success}

    @router.post("/api/sessions/{session_id}/terminate")
    @handle_exceptions("terminate session")
    async def terminate_session(session_id: str):
        """Terminate a session"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Clean up any pending permissions for this session
        webui.service.cleanup_pending_permissions_for_session(session_id)

        success = await webui.service.terminate_session_only(session_id)
        return {"success": success}

    @router.post("/api/sessions/{session_id}/export-fixture")
    @handle_exceptions("export fixture", value_error_status=404)
    async def export_fixture_route(session_id: str, request: FixtureExportRequest):
        """Export a recording session's raw log as a named test fixture (issue #1998).

        403s independent of session state when the instance wasn't started with
        --enable-session-recording — this developer capability is structurally
        unavailable, not just defaulted off. Coverage-check failure returns HTTP 200
        with {success: false, missing_markers: [...]} — the UI renders that inline
        as the failure panel rather than treating it as a request error.
        """
        if not webui.service.get_session_recording_enabled():
            raise HTTPException(
                status_code=403,
                detail="Session recording is disabled on this instance. Restart the Backend "
                       "with --enable-session-recording to enable this developer feature.",
            )
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        name = request.name or session_id
        try:
            result = await webui.service.export_session_fixture(session_id, name)
        except FixtureExportError as e:
            return {"success": False, "missing_markers": e.missing_markers}
        return result.to_dict()

    @router.put("/api/sessions/{session_id}/name")
    @handle_exceptions("update session name")
    async def update_session_name(session_id: str, request: SessionNameUpdateRequest):
        """Update session name"""
        success = await webui.service.update_session_name(session_id, request.name)
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"success": success}

    @router.patch("/api/sessions/{session_id}")
    @handle_exceptions("update session")
    async def update_session(session_id: str, request: SessionUpdateRequest):
        """Update session fields (generic endpoint)"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        updates = {}

        # Handle full config dict replacement (issue #1230)
        if request.config is not None:
            await _validate_recording_config(webui, session_id, request.config)
            updates["_replace_config"] = request.config

        # Handle name update
        if request.name is not None:
            updates["name"] = request.name

        # Handle model update (persisted-config-only; takes effect on next restart if
        # session is active). Live sessions should use POST /api/sessions/{id}/model
        # instead, which switches the model immediately without a restart (issue #1673).
        if request.model is not None:
            model = request.model.strip()
            if not model:
                raise HTTPException(status_code=400, detail="Model cannot be empty")
            updates["model"] = model

        # Handle allowed_tools update (takes effect on next restart if session is active)
        if request.allowed_tools is not None:
            updates["allowed_tools"] = request.allowed_tools

        # Handle disallowed_tools update (takes effect on next reset if session is active)
        if request.disallowed_tools is not None:
            updates["disallowed_tools"] = request.disallowed_tools

        # Handle role update
        if request.role is not None:
            updates["role"] = request.role

        # Handle template_id update (issue #1842). Uses model_fields_set (not `is not None`)
        # since explicit null (clear template) and omitted field both parse to None.
        if "template_id" in request.model_fields_set:
            updates["template_id"] = request.template_id

        # Handle system_prompt update
        if request.system_prompt is not None:
            updates["system_prompt"] = request.system_prompt

        # Handle override_system_prompt update
        if request.override_system_prompt is not None:
            updates["override_system_prompt"] = request.override_system_prompt

        # Handle capabilities update
        if request.capabilities is not None:
            updates["capabilities"] = request.capabilities

        # Handle sandbox_enabled update
        if request.sandbox_enabled is not None:
            updates["sandbox_enabled"] = request.sandbox_enabled

        # Handle sandbox_config update (issue #458)
        if request.sandbox_config is not None:
            updates["sandbox_config"] = request.sandbox_config

        # Handle setting_sources update (issue #36)
        if request.setting_sources is not None:
            updates["setting_sources"] = request.setting_sources

        # Handle cli_path update (issue #489)
        # Empty string means clear the custom CLI path
        if request.cli_path is not None:
            updates["cli_path"] = request.cli_path if request.cli_path.strip() else None

        # Handle process_wrapper update (issue #1672)
        # Empty string means clear the process wrapper
        if request.process_wrapper is not None:
            updates["process_wrapper"] = (
                request.process_wrapper if request.process_wrapper.strip() else None
            )

        # Handle additional_directories update (issue #630)
        if request.additional_directories is not None:
            session_wd = await webui.service.get_session_working_directory(session_id)
            validated_dirs = _validate_additional_directories(
                request.additional_directories, session_wd
            )
            updates["additional_directories"] = validated_dirs

        # Handle Docker sub-field updates (docker_enabled is immutable; image/mounts/home are editable)
        # Takes effect on next restart if session is active.
        if request.docker_image is not None:
            updates["docker_image"] = request.docker_image if request.docker_image.strip() else None
        if request.docker_extra_mounts is not None:
            updates["docker_extra_mounts"] = request.docker_extra_mounts
        if request.docker_home_directory is not None:
            updates["docker_home_directory"] = (
                request.docker_home_directory if request.docker_home_directory.strip() else None
            )

        # Handle thinking and effort configuration (issue #540)
        if request.thinking_mode is not None:
            updates["thinking_mode"] = request.thinking_mode if request.thinking_mode else None
        if request.thinking_budget_tokens is not None:
            updates["thinking_budget_tokens"] = request.thinking_budget_tokens
        if request.effort is not None:
            eff = request.effort if request.effort else None
            if eff == 'max':
                eff = 'high'
            updates["effort"] = eff

        if request.history_distillation_enabled is not None:
            updates["history_distillation_enabled"] = request.history_distillation_enabled

        if request.auto_memory_mode is not None:
            updates["auto_memory_mode"] = request.auto_memory_mode

        if request.auto_memory_directory is not None:
            updates["auto_memory_directory"] = request.auto_memory_directory

        if request.task_tools_mode is not None:
            updates["task_tools_mode"] = request.task_tools_mode

        if request.skill_creating_enabled is not None:
            updates["skill_creating_enabled"] = request.skill_creating_enabled

        # MCP server configuration (issue #676)
        if request.mcp_server_ids is not None:
            updates["mcp_server_ids"] = request.mcp_server_ids
        if request.enable_claudeai_mcp_servers is not None:
            updates["enable_claudeai_mcp_servers"] = request.enable_claudeai_mcp_servers
        if request.strict_mcp_config is not None:
            updates["strict_mcp_config"] = request.strict_mcp_config
        # Hook configuration (issue #1629)
        if request.hook_ids is not None:
            updates["hook_ids"] = request.hook_ids
        if request.bare_mode is not None:
            updates["bare_mode"] = request.bare_mode
        if request.enable_streaming_text is not None:
            updates["enable_streaming_text"] = request.enable_streaming_text

        if request.working_directory is not None:
            updates["working_directory"] = request.working_directory.strip() or None

        if not updates:
            return {"success": True, "message": "No fields to update"}

        success = await webui.service.update_session(session_id, **updates)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update session")

        session_info = await webui.service.get_session_object(session_id)
        if session_info is None:
            # Session was concurrently deleted between the update above and this lookup;
            # the update itself already succeeded against the (now-gone) session.
            return {"success": True}
        return {"success": True, "session": session_info.to_dict()}

    @router.delete("/api/sessions/{session_id}")
    @handle_exceptions("delete session")
    async def delete_session(session_id: str):
        """Delete a session and all its data (including cascaded child sessions)"""
        # Clean up any pending permissions for this session
        webui.service.cleanup_pending_permissions_for_session(session_id)

        # Delete the session; service handles project state tracking
        result = await webui.service.delete_session(session_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail="Session not found")

        deleted_ids = result.get("deleted_session_ids", [])

        # Clean up event queues and pending permissions for cascaded child sessions
        webui.service.drop_session_queue(session_id)
        for deleted_id in deleted_ids:
            if deleted_id != session_id:
                webui.service.cleanup_pending_permissions_for_session(deleted_id)
                webui.service.drop_session_queue(deleted_id)

        # Broadcast project state changes
        project_id = result.get("project_id")
        if project_id:
            if result.get("project_deleted"):
                webui.service.broadcast_project_deleted(project_id)
                logger.info(f"Appended project_deleted for auto-deleted project {project_id}")
            else:
                updated_project = result.get("updated_project")
                if updated_project:
                    webui.service.broadcast_project_updated(updated_project)
                    logger.debug(
                        f"Appended project_updated for project {project_id} after session deletion"
                    )

        return {
            "success": result.get("success"),
            "deleted_session_ids": deleted_ids
        }

    @router.post("/api/sessions/{session_id}/messages")
    @handle_exceptions("send message")
    async def send_message(session_id: str, request: MessageRequest):
        """Send a message to a session"""
        state = await webui.service.get_session_state(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # Issue #1746 (stage: permissions): PAUSED (a permission decision pending on the
        # current turn) is not a reason to reject a new message — the SDK's own message queue
        # already accepts input regardless of an in-flight permission callback, and it will
        # simply wait its turn to be delivered once the current turn resolves, the same way an
        # ordinary busy/is_processing session already queues a message today.
        if not is_deliverable(state):
            raise HTTPException(status_code=409, detail="Session is not active")
        success = await webui.service.send_message(
            session_id, request.message, metadata=request.metadata
        )
        # Issue #1572: a manual user send means the user has already taken over —
        # cancel any pending auto-resume so it doesn't deliver a duplicate "continue"
        # nudge later. Deliberately scoped to this REST-driven send path only: this
        # same session_coordinator.send_message() is also the delivery primitive for
        # queue_processor/comm_router/MCP-tool sends, which must NOT cancel it (a
        # queued/cron/comm delivery isn't the user taking over).
        if success:
            cancelled = await webui.service.cancel_auto_resume_schedule(session_id)
            # Issue #2190: only unpause when a pending auto-resume actually existed and
            # was cancelled — never unconditionally, or this would silently clear a
            # queue pause the user set manually for unrelated reasons (which has no
            # pending auto_resume schedule to cancel here).
            if cancelled:
                await webui.service.pause_queue(session_id, False)
        return {"success": success}

    @router.get("/api/sessions/{session_id}/messages")
    @handle_exceptions("get messages")
    async def get_messages(
        session_id: str,
        limit: int | None = 50,
        offset: int = 0,
        window: str | None = None,
        before: str | None = None,
        min_turns: int | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
    ):
        """Get messages from a session with pagination metadata.

        Issue #2030: ``window=tail`` returns the newest turn-snapped window (+ ``latest_init``,
        ``event_cursor``/``epoch``); ``before=<opaque cursor>`` returns an older page (no
        event cursor). Without either, legacy ``limit``/``offset`` paging is unchanged.
        """
        if window is not None and window != "tail":
            raise HTTPException(status_code=422, detail="window must be 'tail'")
        if window is not None and before is not None:
            raise HTTPException(status_code=422, detail="window and before are mutually exclusive")
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            result = await webui.service.get_session_messages(
                session_id, limit=limit, offset=offset, window=window, before=before,
                min_turns=min_turns, max_records=max_records, max_bytes=max_bytes,
            )
        except CursorInvalidError as e:
            raise HTTPException(status_code=409, detail="cursor_invalid") from e
        if before is not None:
            # Older pages carry no event cursor: it only has meaning at the tail.
            return result
        # Issue #1000: Include event queue cursor so frontend poll starts
        # exactly where REST left off, preventing duplicate message replay.
        queue = webui.service.get_session_queue(session_id)
        if queue:
            result["event_cursor"] = queue.current_cursor
        # Epic #1990 stage 5a: which Backend-process generation produced event_cursor —
        # needed by the Frontend-issued history cursor mechanism (src/routers/sessions.py)
        # to detect a restart racing this very read.
        result["epoch"] = session_stream_epoch(queue) if queue else PROCESS_EPOCH
        return result

    @router.get("/api/sessions/{session_id}/background_agents")
    @handle_exceptions("get background agents")
    async def get_background_agents(session_id: str):
        """Snapshot of background-agent (Task) leg history, for reload/reconnect (issue #1746)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await webui.service.get_background_agents(session_id)

    @router.get("/api/sessions/{session_id}/background_tasks")
    @handle_exceptions("get background tasks")
    async def get_background_tasks(session_id: str):
        """Currently running background tasks (bash/agent/workflow) snapshot (issue #2145)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await webui.service.get_background_tasks(session_id)

    @router.get("/api/sessions/{session_id}/tasks")
    @handle_exceptions("get session tasks")
    async def get_session_tasks(session_id: str):
        """SDK task-list snapshot (TaskCreate/TaskUpdate reduced server-side, issue #2030)."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await webui.service.get_session_tasks(session_id)

    @router.get("/api/sessions/{session_id}/history_index")
    @handle_exceptions("get history index")
    async def get_history_index(session_id: str):
        """Server-owned history index (issue #2030 stage 2): the display-user turns
        (``[cursor, message_id, size_hint]``) and compaction-group keys of the WHOLE history,
        independent of how much of it the browser has loaded. Cold scans are bounded; a build
        that does not finish within the waiter cap answers 503 ``index_building``."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            return await webui.service.get_history_index(session_id)
        except HistoryIndexBuildingError as e:
            raise HTTPException(
                status_code=503, detail="index_building", headers={"Retry-After": "5"}
            ) from e

    @router.get("/api/sessions/{session_id}/search")
    @handle_exceptions("search session history")
    async def search_history(
        session_id: str, request: Request, q: str, cursor: str | None = None,
        limit: int | None = None, client_id: str | None = None,
    ):
        """Whole-history case-insensitive substring search (issue #2030 stage 3), newest-first
        pages: ``results`` (<= 50), ``next_cursor``/``exhausted``, ``scanned_fraction``.
        A newer query from the same ``client_id`` (per-tab, optional) supersedes this client's older one
        (``superseded: true``); other clients are never affected; a disconnect cancels the scan. 400 bad query, 409 ``cursor_invalid``, 503 ``search_busy``."""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return await run_search(
            request,
            lambda gone: webui.service.search_history(
                session_id, q, cursor, limit, client_id, gone
            ),
        )

    return router
