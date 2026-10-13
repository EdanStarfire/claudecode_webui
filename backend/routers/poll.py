"""Long-poll endpoints: /api/poll/* — Backend-owned (issue #498).

Backend is the process where SessionCoordinator pushes events into ui_queue/
session_queues, so it's the side that must actually serve these long-poll
requests. The Frontend API's src/routers/poll.py becomes a poll-relay client
against these same endpoints (Phase 2) rather than a direct EventQueue consumer.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from shared.event_envelope import PROCESS_EPOCH
from shared.event_queue import EventQueue, reset_occurred
from shared.exception_handlers import handle_exceptions
from shared.logging_config import get_logger
from shared.poll_multiplex import multiplex_poll
from shared.poll_protocol import MultiplexPollRequest, MultiplexStreamResponse, PollResponse

_polling_logger = get_logger('polling', category='POLL')


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/poll")
    @handle_exceptions("poll multiplex")
    async def poll_multiplex(request: MultiplexPollRequest):
        """Multiplexed long-poll endpoint (epic #1990 stage 5c): one request carries
        the UI stream plus a subscription set of session streams, each with its own
        cursor. Fairness across subscribed streams is delegated to
        shared/poll_multiplex.py so one noisy session can't delay UI events or
        another session's updates — see that module's docstring for the
        evicted-vs-reset truncation semantics.
        """
        effective_timeout = min(float(request.timeout), 30.0)

        streams: dict[str, tuple[EventQueue, int]] = {}
        if request.ui is not None:
            streams["ui"] = (webui.ui_queue, request.ui.since)

        valid_session_ids: list[str] = []
        for session_id, query in request.sessions.items():
            if session_id not in webui.session_queues:
                if not await webui.service.get_session_exists(session_id):
                    # Unknown/deleted session: omit it from this response rather
                    # than failing the whole multiplexed request over one stale
                    # subscription entry — the UI stream's own session_deleted
                    # event is what tells the browser to drop it.
                    continue
                webui.session_queues[session_id] = EventQueue(
                    on_append=webui._queue_append_hook(session_id)
                )
            # Issue #1598: mark session viewed at poll START, not poll END —
            # recording the timestamp here ensures any completion event arriving
            # during multiplex_poll() is timestamped AFTER last_viewed_at and
            # correctly surfaces as unread once the user navigates away.
            try:
                await webui.coordinator.session_manager.mark_viewed(session_id)
            except Exception:
                _polling_logger.exception("mark_viewed failed for session %s", session_id)
            valid_session_ids.append(session_id)
            streams[session_id] = (webui.session_queues[session_id], query.since)

        results = await multiplex_poll(streams, timeout=effective_timeout)

        response: dict = {}
        if "ui" in results:
            result = results["ui"]
            response["ui"] = asdict(MultiplexStreamResponse(
                events=result.events,
                next_cursor=result.next_cursor,
                reset=result.reset,
                evicted=result.evicted,
                epoch=PROCESS_EPOCH,
            ))
            if result.events:
                _polling_logger.info(
                    "poll multiplex ui returned %d event(s) since=%d next_cursor=%d",
                    len(result.events), request.ui.since, result.next_cursor,
                )

        sessions_response: dict = {}
        for session_id in valid_session_ids:
            result = results[session_id]
            sessions_response[session_id] = asdict(MultiplexStreamResponse(
                events=result.events,
                next_cursor=result.next_cursor,
                reset=result.reset,
                evicted=result.evicted,
                epoch=PROCESS_EPOCH,
            ))
            if result.events:
                _polling_logger.info(
                    "poll multiplex session %s returned %d event(s) since=%d next_cursor=%d",
                    session_id, len(result.events), request.sessions[session_id].since,
                    result.next_cursor,
                )
        response["sessions"] = sessions_response
        return response

    @router.get("/api/poll/ui")
    @handle_exceptions("poll ui")
    async def poll_ui(since: int = 0, timeout: int = 30):
        """HTTP long-poll endpoint for global UI events."""
        effective_timeout = min(float(timeout), 30.0)
        await webui.ui_queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor, evicted = webui.ui_queue.events_since(since)
        # Epic #1990 stage 5a intentionally left this as-is: it's Backend's own
        # API contract for detecting THIS process's EventQueue instance being
        # replaced beneath a stale caller (issues #1984/#1988) — a narrower,
        # same-process signal that cross-process epoch comparison can't catch by
        # construction. Stage 5a's cursor-regression removal was about the
        # cross-tier coupling (src/poll_relay.py no longer adopts or trusts this
        # field) — not about this endpoint's own internal contract, which stays.
        reset = reset_occurred(next_cursor, since)
        if events:
            _polling_logger.info(
                "poll ui returned %d event(s) since=%d next_cursor=%d",
                len(events), since, next_cursor
            )
        return asdict(PollResponse(
            events=events, next_cursor=next_cursor, reset=reset, evicted=evicted, epoch=PROCESS_EPOCH
        ))

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
            webui.session_queues[session_id] = EventQueue(on_append=webui._queue_append_hook(session_id))
        queue = webui.session_queues[session_id]

        # Issue #1598: Mark session viewed at poll START, not poll END.
        # Recording the timestamp here ensures any completion event arriving
        # during wait_for_events() is timestamped AFTER last_viewed_at and
        # correctly surfaces as unread once the user navigates away.
        try:
            await webui.coordinator.session_manager.mark_viewed(session_id)
        except Exception:
            _polling_logger.exception("mark_viewed failed for session %s", session_id)

        effective_timeout = min(float(timeout), 30.0)
        await queue.wait_for_events(since, timeout=effective_timeout)
        events, next_cursor, evicted = queue.events_since(since)
        # See poll_ui()'s comment above — unchanged post-stage-5a for the same reason.
        reset = reset_occurred(next_cursor, since)

        if events:
            _polling_logger.info(
                "poll session %s returned %d event(s) since=%d next_cursor=%d",
                session_id, len(events), since, next_cursor
            )
        return asdict(PollResponse(
            events=events, next_cursor=next_cursor, reset=reset, evicted=evicted, epoch=PROCESS_EPOCH
        ))

    return router
