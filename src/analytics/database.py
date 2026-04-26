"""AnalyticsDB: SQLite connection manager for the analytics subsystem.

Owns the audit_events table (issue #1127) and is the designated home for
#1125 cost-tracking tables when that PR lands.

Connection model:
- Single write connection serialized via asyncio.Lock (WAL allows concurrent reads).
- Separate read connection for queries (WAL allows concurrent readers alongside writer).
- busy_timeout=5000 ms on both connections.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     REAL    NOT NULL,
    source_ts     REAL,
    session_id    TEXT    NOT NULL,
    project_id    TEXT,
    legion_id     TEXT,
    turn_id       TEXT,
    event_type    TEXT    NOT NULL,
    tool_name     TEXT,
    status        TEXT,
    summary       TEXT,
    message_id    TEXT,
    extra_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts
    ON audit_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_session_ts
    ON audit_events(session_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event_type_ts
    ON audit_events(event_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_project_ts
    ON audit_events(project_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_session_turn
    ON audit_events(session_id, turn_id);
"""


class AnalyticsDB:
    """SQLite connection manager for analytics data.

    Usage::

        db = AnalyticsDB(Path("data/analytics.db"))
        await db.initialize()
        # write
        await db.execute_write("INSERT INTO audit_events ...", params)
        # read
        rows = await db.execute_read("SELECT * FROM audit_events WHERE ...", params)
        await db.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._write_conn: sqlite3.Connection | None = None
        self._read_conn: sqlite3.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the database file, apply schema, and configure connections."""
        if self._initialized:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_initialize)
        self._initialized = True
        logger.info("AnalyticsDB initialized at %s", self._path)

    def _sync_initialize(self) -> None:
        self._write_conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._write_conn.row_factory = sqlite3.Row
        self._write_conn.execute("PRAGMA journal_mode=WAL")
        self._write_conn.execute("PRAGMA busy_timeout=5000")
        self._write_conn.executescript(_DDL)
        self._write_conn.commit()

        self._read_conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._read_conn.row_factory = sqlite3.Row
        self._read_conn.execute("PRAGMA journal_mode=WAL")
        self._read_conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_close)
        self._initialized = False

    def _sync_close(self) -> None:
        if self._write_conn:
            try:
                self._write_conn.close()
            except Exception:
                pass
            self._write_conn = None
        if self._read_conn:
            try:
                self._read_conn.close()
            except Exception:
                pass
            self._read_conn = None

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def execute_write(self, sql: str, params: tuple | list = ()) -> int | None:
        """Execute a single write statement; returns lastrowid."""
        if not self._initialized:
            raise RuntimeError("AnalyticsDB not initialized")
        async with self._write_lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._sync_write, sql, params)

    def _sync_write(self, sql: str, params: tuple | list) -> int | None:
        cur = self._write_conn.execute(sql, params)
        self._write_conn.commit()
        return cur.lastrowid

    async def execute_write_many(self, sql: str, rows: list[tuple | list]) -> None:
        """Batch insert multiple rows."""
        if not rows:
            return
        if not self._initialized:
            raise RuntimeError("AnalyticsDB not initialized")
        async with self._write_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sync_write_many, sql, rows)

    def _sync_write_many(self, sql: str, rows: list) -> None:
        self._write_conn.executemany(sql, rows)
        self._write_conn.commit()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def execute_read(
        self, sql: str, params: tuple | list = ()
    ) -> list[dict[str, Any]]:
        """Execute a SELECT query; returns list of row dicts."""
        if not self._initialized:
            raise RuntimeError("AnalyticsDB not initialized")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_read, sql, params)

    def _sync_read(self, sql: str, params: tuple | list) -> list[dict[str, Any]]:
        cur = self._read_conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    async def execute_scalar(self, sql: str, params: tuple | list = ()) -> Any:
        """Execute a scalar SELECT (e.g. COUNT); returns first column of first row."""
        if not self._initialized:
            raise RuntimeError("AnalyticsDB not initialized")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_scalar, sql, params)

    def _sync_scalar(self, sql: str, params: tuple | list) -> Any:
        cur = self._read_conn.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
