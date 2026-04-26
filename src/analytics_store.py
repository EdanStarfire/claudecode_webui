"""
Analytics store for per-session token usage and cost tracking (issue #1125).

Delegates all SQLite I/O to AnalyticsDB (issue #1127), which owns the shared
connection, WAL mode configuration, and asyncio write lock.
"""

from __future__ import annotations

import logging
from time import time

from src.analytics.database import AnalyticsDB

logger = logging.getLogger(__name__)

_SESSION_COLS = [
    "session_id", "model", "turn_count",
    "input_tokens", "output_tokens",
    "cache_write_tokens", "cache_read_tokens",
    "sdk_total_cost_usd", "last_updated",
]


class AnalyticsStore:
    """Per-session token usage store backed by AnalyticsDB.

    AnalyticsDB owns the connection and write lock; this class contains only
    business logic (INSERT OR IGNORE replay safety, aggregate upsert math).
    """

    def __init__(self, db: AnalyticsDB) -> None:
        self._db = db

    async def record_turn(
        self,
        session_id: str,
        turn_seq: int,
        model: str | None,
        usage: dict,
        sdk_total_cost_usd: float | None,
    ) -> None:
        """Insert a turn_usage row (idempotent) and upsert session aggregate."""
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        # SDK uses cache_creation_input_tokens; normalize to cache_write_tokens
        cache_write_tokens = int(
            usage.get("cache_creation_input_tokens")
            or usage.get("cache_write_tokens")
            or 0
        )
        cache_read_tokens = int(
            usage.get("cache_read_input_tokens")
            or usage.get("cache_read_tokens")
            or 0
        )
        now = time()

        try:
            await self._db.execute_write(
                """
                INSERT OR IGNORE INTO turn_usage
                  (session_id, turn_seq, model,
                   input_tokens, output_tokens,
                   cache_write_tokens, cache_read_tokens,
                   sdk_total_cost_usd, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, turn_seq, model,
                    input_tokens, output_tokens,
                    cache_write_tokens, cache_read_tokens,
                    sdk_total_cost_usd, now,
                ),
            )
            # Upsert session aggregate by re-summing from turn_usage so the
            # result is consistent even after idempotent replays.
            await self._db.execute_write(
                """
                INSERT INTO session_usage
                  (session_id, model, turn_count,
                   input_tokens, output_tokens,
                   cache_write_tokens, cache_read_tokens,
                   sdk_total_cost_usd, last_updated)
                SELECT
                  ?,
                  ?,
                  COUNT(*),
                  COALESCE(SUM(input_tokens), 0),
                  COALESCE(SUM(output_tokens), 0),
                  COALESCE(SUM(cache_write_tokens), 0),
                  COALESCE(SUM(cache_read_tokens), 0),
                  COALESCE(SUM(COALESCE(sdk_total_cost_usd, 0)), 0),
                  ?
                FROM turn_usage WHERE session_id = ?
                ON CONFLICT(session_id) DO UPDATE SET
                  model              = excluded.model,
                  turn_count         = excluded.turn_count,
                  input_tokens       = excluded.input_tokens,
                  output_tokens      = excluded.output_tokens,
                  cache_write_tokens = excluded.cache_write_tokens,
                  cache_read_tokens  = excluded.cache_read_tokens,
                  sdk_total_cost_usd = excluded.sdk_total_cost_usd,
                  last_updated       = excluded.last_updated
                """,
                (session_id, model, now, session_id),
            )
        except Exception:
            logger.exception("Failed to record turn usage for session %s", session_id)

    async def get_session_usage(self, session_id: str) -> dict | None:
        """Return session aggregate as dict, or None if no data exists yet."""
        rows = await self._db.execute_read(
            """
            SELECT session_id, model, turn_count,
                   input_tokens, output_tokens,
                   cache_write_tokens, cache_read_tokens,
                   sdk_total_cost_usd, last_updated
            FROM session_usage WHERE session_id = ?
            """,
            (session_id,),
        )
        return rows[0] if rows else None

    async def get_turn_count(self, session_id: str) -> int:
        """Return current turn count (used to seed turn_seq on restart)."""
        val = await self._db.execute_scalar(
            "SELECT turn_count FROM session_usage WHERE session_id = ?",
            (session_id,),
        )
        return int(val) if val is not None else 0

    async def delete_session(self, session_id: str) -> None:
        """Remove all analytics rows for a session."""
        try:
            await self._db.execute_write(
                "DELETE FROM turn_usage WHERE session_id = ?", (session_id,)
            )
            await self._db.execute_write(
                "DELETE FROM session_usage WHERE session_id = ?", (session_id,)
            )
        except Exception:
            logger.exception("Failed to delete analytics for session %s", session_id)
