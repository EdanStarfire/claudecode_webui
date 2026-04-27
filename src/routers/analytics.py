"""Analytics usage endpoint: GET /api/analytics/usage (issue #1132)."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ..analytics.aggregator import (
    aggregate_by_session,
    aggregate_by_time,
    compute_bucket_totals,
    compute_session_totals,
)
from ..config_manager import PricingConfig, load_config
from ..exception_handlers import handle_exceptions

_VALID_GROUP_BY = {"session", "hour", "day"}


def build_router(webui) -> APIRouter:
    router = APIRouter()

    def _get_pricing() -> PricingConfig:
        try:
            cfg = load_config()
            return cfg.pricing if cfg.pricing else PricingConfig()
        except Exception:
            return PricingConfig()

    def _db():
        db = getattr(webui, "analytics_db", None)
        if db is None or not db._initialized:
            return None
        return db

    def _unavailable():
        return JSONResponse(
            status_code=503,
            content={"detail": "Analytics subsystem not available"},
        )

    async def _enrich_session_rows(rows: list[dict], webui) -> None:
        """Fill session_name, is_minion, parent_session_id from SessionManager."""
        try:
            sm = webui.coordinator.session_manager
        except AttributeError:
            return
        for row in rows:
            sid = row["session_id"]
            info = await sm.get_session_info(sid)
            if info is None:
                row["session_name"] = "(deleted)"
            else:
                row["session_name"] = getattr(info, "name", None) or sid[:8]
                row["is_minion"] = getattr(info, "is_minion", False) or False
                row["parent_session_id"] = getattr(info, "parent_overseer_id", None)

    @router.get("/api/analytics/usage")
    @handle_exceptions("analytics usage")
    async def get_analytics_usage(
        since: int | None = Query(default=None, description="Start of range (Unix seconds)"),
        until: int | None = Query(default=None, description="End of range (Unix seconds)"),
        session_ids: str | None = Query(default=None, description="Comma-separated session IDs"),
        models: str | None = Query(default=None, description="Comma-separated model names"),
        group_by: str = Query(..., description="Aggregation mode: session | hour | day"),
    ):
        if group_by not in _VALID_GROUP_BY:
            raise HTTPException(
                status_code=422,
                detail=f"group_by must be one of: {', '.join(sorted(_VALID_GROUP_BY))}",
            )

        db = _db()
        if db is None:
            return _unavailable()

        now = time.time()
        effective_since = float(since) if since is not None else now - 86400.0
        effective_until = float(until) if until is not None else now

        sid_list = [s.strip() for s in session_ids.split(",")] if session_ids else None
        model_list = [m.strip() for m in models.split(",")] if models else None

        pricing = _get_pricing()

        if group_by == "session":
            rows = await aggregate_by_session(
                db, pricing, effective_since, effective_until, sid_list, model_list
            )
            await _enrich_session_rows(rows, webui)
            totals = compute_session_totals(rows)
            return {
                "group_by": "session",
                "since": effective_since,
                "until": effective_until,
                "rows": rows,
                "totals": totals,
            }
        else:
            buckets = await aggregate_by_time(
                db, pricing, group_by, effective_since, effective_until, sid_list, model_list
            )
            totals = compute_bucket_totals(buckets)
            return {
                "group_by": group_by,
                "since": effective_since,
                "until": effective_until,
                "buckets": buckets,
                "totals": totals,
            }

    return router
