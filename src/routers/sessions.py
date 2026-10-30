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

# Issue #2030: modes whose pages carry no event_cursor/epoch (nothing to resolve at the tail).
_NO_EVENT_CURSOR_MODES = ("before", "around", "around_id", "after")


def _error_detail(response: httpx.Response) -> str:
    try:
        return response.json().get("detail", "error")
    except Exception:
        return "error"


def build_router(webui) -> APIRouter:
    router = APIRouter()

    async def _fetch_once(
        session_id: str, limit: int | None, offset: int, retry_allowed: bool,
        window_params: dict | None = None,
    ) -> dict:
        params: dict = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        if window_params:
            params.update(window_params)
        try:
            result = await webui.backend_client.get_json(
                f"/api/sessions/{session_id}/messages", params=params
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Issue #2030 stage 3: an id lookup that finds nothing is a contract answer too.
                detail = (
                    "message_not_found" if _error_detail(e.response) == "message_not_found"
                    else "Session not found"
                )
                raise HTTPException(status_code=404, detail=detail) from e
            if e.response.status_code == 503 and _error_detail(e.response) == "resolve_busy":
                raise HTTPException(
                    status_code=503, detail="resolve_busy",
                    headers={"Retry-After": e.response.headers.get("Retry-After", "2")},
                ) from e
            if e.response.status_code in (409, 422):
                # Issue #2030: cursor_invalid (409) / bad window params (422) are
                # contract errors the browser handles — pass them through verbatim.
                raise HTTPException(
                    status_code=e.response.status_code, detail=_error_detail(e.response)
                ) from e
            raise
        except httpx.RequestError as e:
            raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e

        if window_params and any(k in window_params for k in _NO_EVENT_CURSOR_MODES):
            # Issue #2030: older/around/after pages carry no event_cursor/epoch — nothing to
            # resolve, and waiting on the relay checkpoint would stall every backfill page.
            return result

        backend_cursor = result.get("event_cursor")
        epoch = result.get("epoch")
        if backend_cursor is None or epoch is None:
            # Backend's own session queue isn't initialized yet (no poll has ever
            # happened for it) — nothing to resolve against; leave event_cursor as-is.
            return result

        webui.poll_relay.touch_session(session_id, seed_backend_cursor=backend_cursor)
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
                return await _fetch_once(
                    session_id, limit, offset, retry_allowed=False, window_params=window_params
                )
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
    async def get_session_messages(
        session_id: str,
        limit: int | None = 50,
        offset: int = 0,
        window: str | None = None,
        before: str | None = None,
        min_turns: int | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
        around: str | None = None,
        around_id: str | None = None,
        before_bytes: int | None = None,
        after_bytes: int | None = None,
        after: str | None = None,
        until: str | None = None,
    ):
        """Get messages from a session with pagination metadata — mirrors
        Backend's own route (limit/offset/404 handling unchanged), substituting
        the Frontend-local event_cursor the browser's next poll must use.

        Issue #2030: ``window=tail`` / ``before=<cursor>`` (stage 3: ``around``/``around_id``/
        ``after`` + their modifiers) are forwarded verbatim; only ``window=tail`` carries an event
        cursor to resolve."""
        window_params = {
            key: value
            for key, value in (
                ("window", window), ("before", before), ("min_turns", min_turns),
                ("max_records", max_records), ("max_bytes", max_bytes),
                ("around", around), ("around_id", around_id), ("before_bytes", before_bytes),
                ("after_bytes", after_bytes), ("after", after), ("until", until),
            )
            if value is not None
        }
        return await _fetch_once(
            session_id, limit, offset, retry_allowed=True, window_params=window_params
        )

    return router
