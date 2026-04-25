"""Session CRUD and messaging endpoints: /api/sessions*"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..event_queue import EventQueue
from ..exception_handlers import handle_exceptions
from ..session_manager import SessionState
from ._models import (
    MessageRequest,
    SessionCreateRequest,
    SessionNameUpdateRequest,
    SessionUpdateRequest,
    _validate_additional_directories,
)

logger = logging.getLogger(__name__)


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
        session_id = await webui.coordinator.create_session(
            session_id=session_id,
            project_id=request.project_id,
            config=config,
            name=request.name,
            permission_callback=webui.permission_service.create_permission_callback(session_id),
        )

        # Create event queue for this new session
        webui.session_queues[session_id] = EventQueue()

        # Broadcast session creation to all UI clients
        session_info_dict = await webui.coordinator.get_session_info(session_id)
        if session_info_dict:
            webui._broadcast_state_change(
                session_id,
                session_info_dict.get("session", session_info_dict),
                datetime.now().isoformat()
            )

        # Broadcast project update to all UI clients (session was added to project)
        project_dict = (
            await webui.service.get_project(request.project_id) if request.project_id else None
        )
        if project_dict:
            webui._broadcast_project_updated(
                {k: v for k, v in project_dict.items() if k != "sessions"}
            )

        return {"session_id": session_id}

    @router.get("/api/sessions")
    @handle_exceptions("list sessions")
    async def list_sessions(limit: int = 500, offset: int = 0):
        """List all sessions"""
        return await webui.coordinator.list_sessions(limit=limit, offset=offset)

    @router.get("/api/sessions/{session_id}")
    @handle_exceptions("get session info")
    async def get_session_info(session_id: str):
        """Get session information"""
        info = await webui.coordinator.get_session_info(session_id)
        if not info:
            raise HTTPException(status_code=404, detail="Session not found")

        # Log session state for debugging
        session_state = info.get('session', {}).get('state', 'unknown')
        logger.info(f"API returning session {session_id} with state: {session_state}")

        return info

    @router.get("/api/sessions/{session_id}/descendants")
    @handle_exceptions("get session descendants")
    async def get_session_descendants(session_id: str, limit: int = 50, offset: int = 0):
        """Get all descendant sessions (children, grandchildren, etc.) of a session"""
        result = await webui.coordinator.get_descendants(session_id, limit=limit, offset=offset)
        result["session_id"] = session_id
        return result

    @router.post("/api/sessions/{session_id}/start")
    @handle_exceptions("start session")
    async def start_session(session_id: str):
        """Start a session"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Clear any existing callbacks to prevent duplicates, then register fresh one
        webui.coordinator.clear_message_callbacks(session_id)
        webui.coordinator.add_message_callback(
            session_id,
            webui._create_message_callback(session_id)
        )

        success = await webui.coordinator.start_session(
            session_id,
            permission_callback=webui.permission_service.create_permission_callback(session_id),
        )
        return {"success": success}

    @router.post("/api/sessions/{session_id}/terminate")
    @handle_exceptions("terminate session")
    async def terminate_session(session_id: str):
        """Terminate a session"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

        # Clean up any pending permissions for this session
        webui._cleanup_pending_permissions_for_session(session_id)

        success = await webui.coordinator.terminate_session(session_id)
        return {"success": success}

    @router.put("/api/sessions/{session_id}/name")
    @handle_exceptions("update session name")
    async def update_session_name(session_id: str, request: SessionNameUpdateRequest):
        """Update session name"""
        success = await webui.coordinator.update_session_name(session_id, request.name)
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

        # Handle name update
        if request.name is not None:
            updates["name"] = request.name

        # Handle model update (takes effect on next restart if session is active)
        if request.model is not None:
            valid_models = ["sonnet", "opus", "haiku", "opusplan"]
            if request.model not in valid_models:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid model. Must be one of: {', '.join(valid_models)}"
                )
            updates["model"] = request.model

        # Handle allowed_tools update (takes effect on next restart if session is active)
        if request.allowed_tools is not None:
            updates["allowed_tools"] = request.allowed_tools

        # Handle disallowed_tools update (takes effect on next reset if session is active)
        if request.disallowed_tools is not None:
            updates["disallowed_tools"] = request.disallowed_tools

        # Handle role update
        if request.role is not None:
            updates["role"] = request.role

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

        if request.skill_creating_enabled is not None:
            updates["skill_creating_enabled"] = request.skill_creating_enabled

        # MCP server configuration (issue #676)
        if request.mcp_server_ids is not None:
            updates["mcp_server_ids"] = request.mcp_server_ids
        if request.enable_claudeai_mcp_servers is not None:
            updates["enable_claudeai_mcp_servers"] = request.enable_claudeai_mcp_servers
        if request.strict_mcp_config is not None:
            updates["strict_mcp_config"] = request.strict_mcp_config
        if request.bare_mode is not None:
            updates["bare_mode"] = request.bare_mode

        if not updates:
            return {"success": True, "message": "No fields to update"}

        success = await webui.service.update_session(session_id, **updates)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update session")

        return {"success": success}

    @router.delete("/api/sessions/{session_id}")
    @handle_exceptions("delete session")
    async def delete_session(session_id: str):
        """Delete a session and all its data (including cascaded child sessions)"""
        # Clean up any pending permissions for this session
        webui._cleanup_pending_permissions_for_session(session_id)

        # Delete the session; service handles project state tracking
        result = await webui.service.delete_session(session_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail="Session not found")

        deleted_ids = result.get("deleted_session_ids", [])

        # Clean up event queues and pending permissions for cascaded child sessions
        webui.session_queues.pop(session_id, None)
        for deleted_id in deleted_ids:
            if deleted_id != session_id:
                webui._cleanup_pending_permissions_for_session(deleted_id)
                webui.session_queues.pop(deleted_id, None)

        # Broadcast project state changes
        project_id = result.get("project_id")
        if project_id:
            if result.get("project_deleted"):
                webui._broadcast_project_deleted(project_id)
                logger.info(f"Appended project_deleted for auto-deleted project {project_id}")
            else:
                updated_project = result.get("updated_project")
                if updated_project:
                    webui._broadcast_project_updated(updated_project)
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
        if state != SessionState.ACTIVE:
            raise HTTPException(status_code=409, detail="Session is not active")
        success = await webui.coordinator.send_message(
            session_id, request.message, metadata=request.metadata
        )
        return {"success": success}

    @router.get("/api/sessions/{session_id}/messages")
    @handle_exceptions("get messages")
    async def get_messages(session_id: str, limit: int | None = 50, offset: int = 0):
        """Get messages from a session with pagination metadata"""
        if not await webui.service.get_session_exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        result = await webui.coordinator.get_session_messages(
            session_id, limit=limit, offset=offset
        )
        # Issue #1000: Include event queue cursor so frontend poll starts
        # exactly where REST left off, preventing duplicate message replay.
        queue = webui.session_queues.get(session_id)
        if queue:
            result["event_cursor"] = queue.current_cursor
        return result

    return router
