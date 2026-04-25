"""Long-poll endpoints: /api/poll/*"""

from fastapi import APIRouter, HTTPException

from ..event_queue import EventQueue
from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/poll/ui")
    @handle_exceptions("poll ui")
    async def poll_ui(since: int = 0, timeout: int = 30):
        """HTTP long-poll endpoint for global UI events."""
        effective_timeout = min(float(timeout), 30.0)
        await webui.ui_queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor = webui.ui_queue.events_since(since)
        return {"events": events, "next_cursor": next_cursor}

    @router.get("/api/poll/cursor")
    @handle_exceptions("poll cursor")
    async def get_poll_cursor():
        """Return current UI event queue cursor position for client initialization."""
        return {"cursor": webui.ui_queue.current_cursor}

    @router.get("/api/poll/session/{session_id}/cursor")
    @handle_exceptions("poll session cursor")
    async def get_session_poll_cursor(session_id: str):
        """Return current session event queue cursor position for client initialization."""
        if session_id not in webui.session_queues:
            if not await webui.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            return {"cursor": 0}  # session exists but queue not yet initialized
        return {"cursor": webui.session_queues[session_id].current_cursor}

    @router.get("/api/poll/session/{session_id}")
    @handle_exceptions("poll session")
    async def poll_session(session_id: str, since: int = 0, timeout: int = 30):
        """HTTP long-poll endpoint for session-specific events."""
        if session_id not in webui.session_queues:
            if not await webui.service.get_session_exists(session_id):
                raise HTTPException(status_code=404, detail="Session not found")
            webui.session_queues[session_id] = EventQueue()
        queue = webui.session_queues[session_id]
        effective_timeout = min(float(timeout), 30.0)
        await queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor = queue.events_since(since)
        return {"events": events, "next_cursor": next_cursor}

    return router
