"""Frontend client-debug ring-buffer submission endpoint (issue #1931)."""

import json

from fastapi import APIRouter

from shared.exception_handlers import handle_exceptions
from shared.logging_config import get_logger

from ._models import ClientDebugBufferRequest

client_debug_logger = get_logger("client_debug", category="CLIENT_DEBUG")


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/debug/client-buffer")
    @handle_exceptions("submit client debug buffer")
    async def submit_client_debug_buffer(request: ClientDebugBufferRequest):
        """Log a submitted frontend debug ring buffer to client_debug.log."""
        # One line for the whole submission (not one log call per event) — up to
        # 1000 events could otherwise blow through the RotatingFileHandler's
        # backupCount and rotate out unrelated prior log history.
        client_debug_logger.info(
            "session_id=%s reason=%s browser=%s submitted_at=%s event_count=%d events=%s",
            request.session_id,
            request.reason,
            request.browser,
            request.submitted_at,
            len(request.events),
            json.dumps(request.events),
        )
        return {"received": len(request.events)}

    return router
