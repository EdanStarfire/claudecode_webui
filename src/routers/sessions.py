"""Dedicated Frontend-side route: GET /api/sessions/{id}/messages (epic #1990
stage 5a, AC3). Registered ahead of relay.py's generic catch-all.

Backend's own `event_cursor` is no longer safe to hand straight to the browser —
session queues stopped adopting Backend's cursor numbering in stage 5a (see
src/poll_relay.py), so Backend-space and Frontend-local-space cursors are two
different numberings now. This route substitutes the exact LOCAL cursor
corresponding to Backend's answer, via PollRelay.wait_until_relayed()'s
checkpoint interpolation, so the browser's next poll (POST /api/poll, this
session's cursor in its `sessions` block) resumes against the numbering that
endpoint actually understands.
"""

import httpx
from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions
from shared.logging_config import get_logger

from ..backend_reachability import is_backend_degraded, to_http_exception

_polling_logger = get_logger('polling', category='POLL')

# Generous versus the common case (see src/poll_relay.py's own constant of the
# same name/value) without blocking this user-facing request anywhere near the
# relay's own 30s upstream long-poll ceiling.
_HISTORY_CURSOR_WAIT_TIMEOUT_SECONDS = 10.0


def build_router(webui) -> APIRouter:
    router = APIRouter()

    async def _fetch_once(session_id: str, limit: int | None, offset: int, retry_allowed: bool) -> dict:
        params: dict = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        try:
            result = await webui.backend_client.get_json(
                f"/api/sessions/{session_id}/messages", params=params
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Session not found") from e
            raise
        except httpx.RequestError as e:
            raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e

        backend_cursor = result.get("event_cursor")
        epoch = result.get("epoch")
        if backend_cursor is None or epoch is None:
            # Backend's own session queue isn't initialized yet (no poll has ever
            # happened for it) — nothing to resolve against; leave event_cursor as-is.
            return result

        webui.poll_relay.touch_session(session_id)
        wait_result = await webui.poll_relay.wait_until_relayed(
            session_id, backend_cursor=backend_cursor, epoch=epoch,
            timeout=_HISTORY_CURSOR_WAIT_TIMEOUT_SECONDS,
        )

        if wait_result.outcome == "stale_epoch":
            if retry_allowed:
                _polling_logger.info(
                    "GET /messages for session %s: epoch moved during wait, retrying once",
                    session_id,
                )
                return await _fetch_once(session_id, limit, offset, retry_allowed=False)
            # Capped at one retry — fall back to a best-effort local cursor. Not
            # silently lossy: the independently-emitted stream_reset for this
            # transition still arrives through the normal relay path on the
            # browser's next poll and forces a resync regardless.
            _polling_logger.info(
                "GET /messages for session %s: epoch moved again on retry, "
                "falling back to current local cursor",
                session_id,
            )
            result["event_cursor"] = webui.poll_relay.current_local_cursor(session_id)
            return result

        result["event_cursor"] = wait_result.local_cursor
        return result

    @router.get("/api/sessions/{session_id}/messages")
    @handle_exceptions("get messages")
    async def get_session_messages(session_id: str, limit: int | None = 50, offset: int = 0):
        """Get messages from a session with pagination metadata — mirrors
        Backend's own route (limit/offset/404 handling unchanged), substituting
        the Frontend-local event_cursor the browser's next poll must use."""
        return await _fetch_once(session_id, limit, offset, retry_allowed=True)

    return router
