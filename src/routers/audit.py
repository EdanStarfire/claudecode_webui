"""Audit endpoints: /api/audit/* and /api/poll/audit"""

import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..analytics.audit_query import AuditQueryService
from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    def _query_service() -> AuditQueryService:
        db = getattr(webui, "analytics_db", None)
        if db is None or not db._initialized:
            return None
        sm = webui.coordinator.session_manager
        return AuditQueryService(db, sm)

    def _unavailable():
        return JSONResponse(
            status_code=503,
            content={"detail": "Audit subsystem not available"},
        )

    @router.get("/api/audit/events")
    @handle_exceptions("audit events")
    async def get_audit_events(
        since: float | None = Query(default=None),
        until: float | None = Query(default=None),
        session_ids: str | None = Query(default=None, description="Comma-separated session IDs"),
        project_id: str | None = Query(default=None),
        event_types: str | None = Query(default=None, description="Comma-separated event types"),
        turn_id: str | None = Query(default=None),
        cursor: float | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ):
        """Flat, paginated audit event list."""
        qs = _query_service()
        if qs is None:
            return _unavailable()

        sid_list = [s.strip() for s in session_ids.split(",")] if session_ids else None
        et_list = [e.strip() for e in event_types.split(",")] if event_types else None

        return await qs.query_events(
            since=since,
            until=until,
            session_ids=sid_list,
            project_id=project_id,
            event_types=et_list,
            turn_id=turn_id,
            cursor=cursor,
            limit=limit,
            offset=offset,
        )

    @router.get("/api/audit/turns")
    @handle_exceptions("audit turns")
    async def get_audit_turns(
        since: float | None = Query(default=None),
        until: float | None = Query(default=None),
        session_ids: str | None = Query(default=None, description="Comma-separated session IDs"),
        project_id: str | None = Query(default=None),
        event_types: str | None = Query(default=None, description="Comma-separated event types"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        """Turn-grouped audit feed."""
        qs = _query_service()
        if qs is None:
            return _unavailable()

        sid_list = [s.strip() for s in session_ids.split(",")] if session_ids else None
        et_list = [e.strip() for e in event_types.split(",")] if event_types else None

        return await qs.query_turns(
            since=since,
            until=until,
            session_ids=sid_list,
            project_id=project_id,
            event_types=et_list,
            limit=limit,
            offset=offset,
        )

    @router.get("/api/poll/audit")
    @handle_exceptions("poll audit")
    async def poll_audit(
        cursor: float = Query(default=0.0),
        since: float | None = Query(default=None),
        session_ids: str | None = Query(default=None),
        event_types: str | None = Query(default=None),
        timeout: int = Query(default=25, ge=1, le=30),
    ):
        """Long-poll for new audit events (Stream tab live tail)."""
        qs = _query_service()
        if qs is None:
            return _unavailable()

        sid_list = [s.strip() for s in session_ids.split(",")] if session_ids else None
        et_list = [e.strip() for e in event_types.split(",")] if event_types else None

        effective_timeout = min(float(timeout), 25.0)
        audit_queue = getattr(webui, "audit_queue", None)
        # Snapshot cursor before first query so any flush in the gap advances
        # current_cursor past the snapshot and wait_for_events returns immediately.
        audit_cursor = audit_queue.current_cursor if audit_queue is not None else 0

        effective_since = since if since is not None else (time.time() - 3600)
        result = await qs.query_events(
            since=effective_since,
            session_ids=sid_list,
            event_types=et_list,
            cursor=cursor if cursor > 0 else None,
            limit=100,
        )
        if result["events"]:
            return result
        if audit_queue is not None:
            await audit_queue.wait_for_events(audit_cursor, timeout=effective_timeout)
        return await qs.query_events(
            since=effective_since,
            session_ids=sid_list,
            event_types=et_list,
            cursor=cursor if cursor > 0 else None,
            limit=100,
        )

    return router
