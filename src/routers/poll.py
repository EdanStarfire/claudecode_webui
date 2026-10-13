"""Long-poll endpoint: POST /api/poll — poll-relay client (issue #498).

Frontend serves this path and reads from LOCAL EventQueues (webui.ui_queue /
webui.session_queues), same as before the #498 split — only the producer side
changed. src/poll_relay.py owns the single consolidated background task that
continuously long-polls Backend's own POST /api/poll (backend/routers/poll.py,
which owns the real EventQueues SessionCoordinator writes to) and re-appends
into these local queues, so multiple browser tabs share one upstream
connection instead of each opening their own.

Epic #1990 stage 5c phase 5: the legacy single-stream GET /api/poll/ui and
GET /api/poll/session/{id} long-poll endpoints are removed — the browser
migrated to this multiplexed endpoint in phase 3. The one-shot
GET /api/poll/cursor and GET /api/poll/session/{id}/cursor REST bootstrap
endpoints are NOT long-polling and were never named for removal — they stay.

Issue #1598's mark-viewed-at-poll-start behavior happens on Backend's side of
the relay (backend/routers/poll.py calls session_manager.mark_viewed()
directly) — the relay task's own poll call triggers it, so this router doesn't
need to call it separately.
"""

from dataclasses import asdict

import httpx
from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions
from shared.logging_config import get_logger
from shared.poll_multiplex import multiplex_poll
from shared.poll_protocol import MultiplexPollRequest, MultiplexStreamResponse

from ..backend_reachability import is_backend_degraded, to_http_exception

_polling_logger = get_logger('polling', category='POLL')


def _backend_status(webui) -> str:
    """Vocabulary shared with `to_http_exception()`: "degraded" (supervisor gave
    up restarting, needs a manual restart) takes precedence over "unreachable"
    (transient — the poll-relay's own retry loop is expected to recover it) when
    both are true. In remote-Backend mode `backend_supervisor` is None, so
    "degraded" can never occur there — only "unreachable" (issue #1989).
    """
    if is_backend_degraded(webui):
        return "degraded"
    if webui.backend_client.reachability.is_unreachable:
        return "unreachable"
    return "ok"


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/poll")
    @handle_exceptions("poll multiplex")
    async def poll_multiplex(request: MultiplexPollRequest):
        """Multiplexed long-poll endpoint (epic #1990 stage 5c): one request carries
        the UI stream plus a subscription set of session streams. Reads from LOCAL
        EventQueues — the consolidated `PollRelay` background task
        (`src/poll_relay.py`) is what populates them from Backend's own
        multiplexed endpoint.
        """
        if request.ui is not None:
            webui.poll_relay.start_ui_relay()

        streams: dict = {}
        if request.ui is not None:
            streams["ui"] = (webui.ui_queue, request.ui.since)

        valid_session_ids: list[str] = []
        for session_id, query in request.sessions.items():
            if session_id not in webui.session_queues:
                try:
                    await webui.backend_client.get_json(f"/api/sessions/{session_id}")
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        # Unknown/deleted session: omit it from this response rather
                        # than failing the whole multiplexed request over one stale
                        # subscription entry — the UI stream's own session_deleted
                        # event is what tells the browser to drop it.
                        continue
                    raise
                except httpx.RequestError as e:
                    raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e
            webui.poll_relay.touch_session(session_id)
            valid_session_ids.append(session_id)
            streams[session_id] = (webui.session_queues[session_id], query.since)

        effective_timeout = min(float(request.timeout), 30.0)
        results = await multiplex_poll(streams, timeout=effective_timeout)
        backend_status = _backend_status(webui)

        response: dict = {}
        if request.ui is not None:
            result = results["ui"]
            evicted = result.evicted or webui.poll_relay.consume_pending_evicted("ui")
            response["ui"] = asdict(MultiplexStreamResponse(
                events=result.events,
                next_cursor=result.next_cursor,
                reset=result.reset,
                evicted=evicted,
                epoch=webui.poll_relay.last_epoch("ui") or "",
            ))
            if result.events:
                _polling_logger.info(
                    "poll multiplex ui returned %d event(s) since=%d next_cursor=%d",
                    len(result.events), request.ui.since, result.next_cursor,
                )

        sessions_response: dict = {}
        for session_id in valid_session_ids:
            result = results[session_id]
            evicted = result.evicted or webui.poll_relay.consume_pending_evicted(session_id)
            sessions_response[session_id] = asdict(MultiplexStreamResponse(
                events=result.events,
                next_cursor=result.next_cursor,
                reset=result.reset,
                evicted=evicted,
                epoch=webui.poll_relay.last_epoch(session_id) or "",
            ))
            if result.events:
                _polling_logger.info(
                    "poll multiplex session %s returned %d event(s) since=%d next_cursor=%d",
                    session_id, len(result.events), request.sessions[session_id].since,
                    result.next_cursor,
                )
        response["sessions"] = sessions_response
        response["backend_status"] = backend_status
        return response

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
            except httpx.RequestError as e:
                raise to_http_exception(e, degraded=is_backend_degraded(webui)) from e
            return {"cursor": 0}  # session exists but queue not yet initialized
        return {"cursor": webui.session_queues[session_id].current_cursor}

    return router
