"""Long-poll endpoints: /api/poll/* — poll-relay client (issue #498).

Frontend still serves these exact paths and still reads from LOCAL EventQueues
(webui.ui_queue / webui.session_queues), same as before the split — only the
producer side changed. src/poll_relay.py owns background tasks that
continuously long-poll Backend's own /api/poll/* (backend/routers/poll.py,
which owns the real EventQueues SessionCoordinator writes to) and re-append
into these local queues, so multiple browser tabs share one upstream
connection per stream instead of each opening their own.

Issue #1598's mark-viewed-at-poll-start behavior now happens on Backend's side
of the relay (backend/routers/poll.py calls session_manager.mark_viewed()
directly) — the relay task's own poll call triggers it, so this router doesn't
need to call it separately.
"""

import httpx
from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions
from shared.logging_config import get_logger

_polling_logger = get_logger('polling', category='POLL')


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/poll/ui")
    @handle_exceptions("poll ui")
    async def poll_ui(since: int = 0, timeout: int = 30):
        """HTTP long-poll endpoint for global UI events."""
        webui.poll_relay.start_ui_relay()
        effective_timeout = min(float(timeout), 30.0)
        await webui.ui_queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor = webui.ui_queue.events_since(since)
        if events:
            _polling_logger.info(
                "poll ui returned %d event(s) since=%d next_cursor=%d",
                len(events), since, next_cursor
            )
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
            try:
                await webui.backend_client.get_json(f"/api/sessions/{session_id}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail="Session not found") from e
                raise
            return {"cursor": 0}  # session exists but queue not yet initialized
        return {"cursor": webui.session_queues[session_id].current_cursor}

    @router.get("/api/poll/session/{session_id}")
    @handle_exceptions("poll session")
    async def poll_session(session_id: str, since: int = 0, timeout: int = 30):
        """HTTP long-poll endpoint for session-specific events."""
        if session_id not in webui.session_queues:
            try:
                await webui.backend_client.get_json(f"/api/sessions/{session_id}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail="Session not found") from e
                raise
        webui.poll_relay.ensure_session_relay(session_id)
        queue = webui.session_queues[session_id]

        effective_timeout = min(float(timeout), 30.0)
        await queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor = queue.events_since(since)

        if events:
            _polling_logger.info(
                "poll session %s returned %d event(s) since=%d next_cursor=%d",
                session_id, len(events), since, next_cursor
            )
        return {"events": events, "next_cursor": next_cursor}

    return router
