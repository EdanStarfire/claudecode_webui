"""AuditQueryService: parameterized SELECT queries over audit_events.

Serves the /api/audit/events and /api/audit/turns endpoints.
All filtering is done in SQL; Python only enriches rows with session_name.
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import AnalyticsDB


class AuditQueryService:
    """Read-only query service for audit_events."""

    def __init__(self, db: AnalyticsDB, session_manager=None) -> None:
        self._db = db
        self._sm = session_manager  # Optional SessionManager for session_name join

    # ------------------------------------------------------------------
    # Flat event list  (GET /api/audit/events)
    # ------------------------------------------------------------------

    async def query_events(
        self,
        since: float | None = None,
        until: float | None = None,
        session_ids: list[str] | None = None,
        project_id: str | None = None,
        event_types: list[str] | None = None,
        turn_id: str | None = None,
        cursor: float | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return paginated flat event list."""
        if since is None:
            since = time.time() - 3600
        limit = min(limit, 1000)

        conditions: list[str] = []
        params: list[Any] = []

        if cursor is not None:
            conditions.append("timestamp > ?")
            params.append(cursor)
        else:
            conditions.append("timestamp >= ?")
            params.append(since)
            if until is not None:
                conditions.append("timestamp <= ?")
                params.append(until)

        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            conditions.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)

        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)

        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            conditions.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if turn_id:
            conditions.append("turn_id = ?")
            params.append(turn_id)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        order = "ASC" if cursor is not None else "DESC"

        sql = (
            f"SELECT id, timestamp, source_ts, session_id, project_id, legion_id, "
            f"turn_id, event_type, tool_name, status, summary, message_id, extra_json "
            f"FROM audit_events {where} "
            f"ORDER BY timestamp {order}, id {order} "
            f"LIMIT ? OFFSET ?"
        )
        rows = await self._db.execute_read(sql, params + [limit, offset])

        count_sql = f"SELECT COUNT(*) FROM audit_events {where}"
        total = await self._db.execute_scalar(count_sql, params) or 0

        events = [self._enrich_row(r) for r in rows]

        next_cursor = events[-1]["timestamp"] if events else None

        return {
            "events": events,
            "next_cursor": next_cursor,
            "total_estimate": total,
        }

    # ------------------------------------------------------------------
    # Turn-grouped feed  (GET /api/audit/turns)
    # ------------------------------------------------------------------

    async def query_turns(
        self,
        since: float | None = None,
        until: float | None = None,
        session_ids: list[str] | None = None,
        project_id: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return turn-grouped feed plus standalone lifecycle/comm events."""
        if since is None:
            since = time.time() - 3600
        limit = min(limit, 200)

        conditions: list[str] = ["timestamp >= ?"]
        params: list[Any] = [since]

        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until)

        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            conditions.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)

        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)

        where = "WHERE " + " AND ".join(conditions)

        # Query turn-level aggregates
        turn_sql = (
            f"SELECT session_id, turn_id, project_id, "
            f"MIN(timestamp) AS started_at, MAX(timestamp) AS ended_at, "
            f"COUNT(*) AS event_count, "
            f"SUM(CASE WHEN event_type='tool_call' THEN 1 ELSE 0 END) AS tool_count, "
            f"SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_count, "
            f"SUM(CASE WHEN status='denied' THEN 1 ELSE 0 END) AS denial_count, "
            f"SUM(CASE WHEN event_type='permission' THEN 1 ELSE 0 END) AS permission_count, "
            f"GROUP_CONCAT(event_type || ':' || COALESCE(status,'?'), ',') AS sparkline_raw "
            f"FROM audit_events {where} AND turn_id IS NOT NULL "
            f"GROUP BY session_id, turn_id "
            f"ORDER BY started_at DESC "
            f"LIMIT ? OFFSET ?"
        )
        turn_rows = await self._db.execute_read(turn_sql, params + [limit, offset])

        # Standalone events (no turn_id) — lifecycle, comm, watchdog
        standalone_sql = (
            f"SELECT id, timestamp, source_ts, session_id, project_id, legion_id, "
            f"event_type, tool_name, status, summary, message_id, extra_json "
            f"FROM audit_events {where} AND turn_id IS NULL "
            f"ORDER BY timestamp DESC "
            f"LIMIT 100"
        )
        standalone_rows = await self._db.execute_read(standalone_sql, params)

        turns = [self._build_turn(r) for r in turn_rows]
        standalones = [self._enrich_row(r) for r in standalone_rows]

        # Cursor: oldest started_at of returned turns
        next_cursor = turns[-1]["started_at"] if turns else None

        return {
            "turns": turns,
            "standalones": standalones,
            "next_cursor": next_cursor,
        }

    # ------------------------------------------------------------------
    # Recent events for long-poll (since cursor)
    # ------------------------------------------------------------------

    async def query_since_cursor(
        self,
        cursor: float,
        session_ids: list[str] | None = None,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return events newer than cursor for long-poll incremental feed."""
        return await self.query_events(
            cursor=cursor,
            session_ids=session_ids,
            event_types=event_types,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enrich_row(self, row: dict) -> dict:
        extra = row.get("extra_json")
        row["extra"] = json.loads(extra) if extra else None
        row.pop("extra_json", None)
        row["session_name"] = self._get_session_name(row.get("session_id"))
        return row

    def _build_turn(self, row: dict) -> dict:
        sparkline = _parse_sparkline(row.get("sparkline_raw", ""))
        last_status = row.get("sparkline_raw", "")
        is_in_progress = "result" not in last_status and "errored" not in last_status
        status = "in_progress" if is_in_progress else (
            "errored" if "errored" in last_status else "completed"
        )
        return {
            "turn_id": row["turn_id"],
            "session_id": row["session_id"],
            "session_name": self._get_session_name(row["session_id"]),
            "project_id": row.get("project_id"),
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "in_progress": is_in_progress,
            "event_count": row["event_count"],
            "tool_count": row["tool_count"],
            "error_count": row["error_count"],
            "denial_count": row["denial_count"],
            "permission_count": row["permission_count"],
            "status": status,
            "sparkline": sparkline,
        }

    def _get_session_name(self, session_id: str | None) -> str | None:
        if not session_id or self._sm is None:
            return None
        try:
            session = self._sm._active_sessions.get(session_id)
            return session.name if session else None
        except Exception:
            return None


def _parse_sparkline(raw: str) -> list[str]:
    """Convert 'tool_call:ok,tool_call:error,...' into a list of short labels."""
    if not raw:
        return []
    labels = []
    for token in raw.split(","):
        if not token:
            continue
        parts = token.split(":", 1)
        etype = parts[0]
        status = parts[1] if len(parts) > 1 else ""
        if etype == "tool_call":
            labels.append("error" if status == "error" else "tool")
        elif etype == "permission":
            labels.append("denied" if status == "denied" else "perm")
        elif etype == "lifecycle":
            labels.append(status or "lifecycle")
        else:
            labels.append(etype)
    return labels
