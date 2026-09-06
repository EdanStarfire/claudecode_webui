"""Unit tests for AnalyticsStore (issue #1125)."""

import pytest

from backend.analytics.database import AnalyticsDB
from backend.analytics_store import AnalyticsStore


@pytest.fixture
async def store(tmp_path):
    db = AnalyticsDB(tmp_path / "analytics.db")
    await db.initialize()
    s = AnalyticsStore(db)
    yield s
    await db.close()


# ---------------------------------------------------------------------------
# Basic insert and retrieval
# ---------------------------------------------------------------------------

async def test_get_session_usage_returns_none_when_empty(store):
    result = await store.get_session_usage("sid-missing")
    assert result is None


async def test_record_turn_creates_session_aggregate(store):
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    }
    await store.record_turn("sid-1", 1, "claude-sonnet-4-6", usage, 0.001)

    agg = await store.get_session_usage("sid-1")
    assert agg is not None
    assert agg["session_id"] == "sid-1"
    assert agg["turn_count"] == 1
    assert agg["input_tokens"] == 100
    assert agg["output_tokens"] == 50
    assert agg["cache_write_tokens"] == 10
    assert agg["cache_read_tokens"] == 5
    assert agg["model"] == "claude-sonnet-4-6"


async def test_multiple_turns_accumulate(store):
    usage = {"input_tokens": 100, "output_tokens": 50}
    await store.record_turn("sid-2", 1, "claude-sonnet-4-6", usage, 0.001)
    await store.record_turn("sid-2", 2, "claude-sonnet-4-6", usage, 0.001)

    agg = await store.get_session_usage("sid-2")
    assert agg["turn_count"] == 2
    assert agg["input_tokens"] == 200
    assert agg["output_tokens"] == 100


# ---------------------------------------------------------------------------
# Idempotent replay via UNIQUE(session_id, turn_seq)
# ---------------------------------------------------------------------------

async def test_duplicate_turn_seq_is_ignored(store):
    usage = {"input_tokens": 100, "output_tokens": 50}
    await store.record_turn("sid-3", 1, "claude-sonnet-4-6", usage, None)
    # Insert same turn_seq again — should be ignored (INSERT OR IGNORE)
    await store.record_turn("sid-3", 1, "claude-sonnet-4-6", usage, None)

    agg = await store.get_session_usage("sid-3")
    assert agg["turn_count"] == 1
    assert agg["input_tokens"] == 100


# ---------------------------------------------------------------------------
# get_turn_count initialisation helper
# ---------------------------------------------------------------------------

async def test_get_turn_count_returns_zero_for_unknown_session(store):
    count = await store.get_turn_count("nonexistent")
    assert count == 0


async def test_get_turn_count_returns_correct_value(store):
    usage = {"input_tokens": 10}
    await store.record_turn("sid-4", 1, None, usage, None)
    await store.record_turn("sid-4", 2, None, usage, None)

    count = await store.get_turn_count("sid-4")
    assert count == 2


# ---------------------------------------------------------------------------
# Cascade delete
# ---------------------------------------------------------------------------

async def test_delete_session_removes_rows(store):
    usage = {"input_tokens": 100}
    await store.record_turn("sid-5", 1, None, usage, None)
    await store.delete_session("sid-5")

    agg = await store.get_session_usage("sid-5")
    assert agg is None
    count = await store.get_turn_count("sid-5")
    assert count == 0


async def test_delete_nonexistent_session_is_safe(store):
    # Should not raise
    await store.delete_session("no-such-session")


# ---------------------------------------------------------------------------
# sdk_total_cost_usd aggregation
# ---------------------------------------------------------------------------

async def test_sdk_cost_is_summed(store):
    await store.record_turn("sid-6", 1, None, {}, 0.001)
    await store.record_turn("sid-6", 2, None, {}, 0.002)

    agg = await store.get_session_usage("sid-6")
    assert abs(agg["sdk_total_cost_usd"] - 0.003) < 1e-9


# ---------------------------------------------------------------------------
# Alternative SDK field name (cache_read_input_tokens)
# ---------------------------------------------------------------------------

async def test_cache_field_aliases_are_mapped(store):
    usage = {
        "input_tokens": 50,
        "output_tokens": 20,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 4,
    }
    await store.record_turn("sid-7", 1, None, usage, None)

    agg = await store.get_session_usage("sid-7")
    assert agg["cache_write_tokens"] == 8
    assert agg["cache_read_tokens"] == 4


# ---------------------------------------------------------------------------
# record_turn() success/failure signal (issue #1838 follow-up)
# ---------------------------------------------------------------------------


async def test_record_turn_returns_true_on_success(store):
    assert await store.record_turn("sid-8", 1, None, {"input_tokens": 10}, 0.01) is True


async def test_record_turn_returns_false_on_write_failure(store, monkeypatch):
    async def failing_execute_write(*args, **kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(store._db, "execute_write", failing_execute_write)

    assert await store.record_turn("sid-9", 1, None, {"input_tokens": 10}, 0.01) is False

    # No row should have been recorded.
    agg = await store.get_session_usage("sid-9")
    assert agg is None


# ---------------------------------------------------------------------------
# Issue #1840: subagent usage TTL-tier columns + is_subagent flag
# ---------------------------------------------------------------------------


async def test_cache_write_tokens_ttl_tiers_are_persisted(store):
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_write_tokens_5m": 20,
        "cache_write_tokens_1h": 7,
    }
    await store.record_turn("sid-10", 1, "claude-sonnet-4-6", usage, None)

    agg = await store.get_session_usage("sid-10")
    assert agg["cache_write_tokens_5m"] == 20
    assert agg["cache_write_tokens_1h"] == 7


async def test_ttl_tiers_default_to_zero_when_absent(store):
    """AC4: a turn with no subagent contribution must not introduce non-zero
    TTL-tier values."""
    await store.record_turn("sid-11", 1, None, {"input_tokens": 10}, None)

    agg = await store.get_session_usage("sid-11")
    assert agg["cache_write_tokens_5m"] == 0
    assert agg["cache_write_tokens_1h"] == 0


async def test_ttl_tiers_aggregate_across_multiple_turns(store):
    usage1 = {"cache_write_tokens_5m": 10, "cache_write_tokens_1h": 1}
    usage2 = {"cache_write_tokens_5m": 5, "cache_write_tokens_1h": 2}
    await store.record_turn("sid-12", 1, None, usage1, None)
    await store.record_turn("sid-12", 2, None, usage2, None)

    agg = await store.get_session_usage("sid-12")
    assert agg["cache_write_tokens_5m"] == 15
    assert agg["cache_write_tokens_1h"] == 3


async def test_is_subagent_catch_up_row_is_recorded(store):
    usage = {"input_tokens": 3, "cache_write_tokens_5m": 1}
    result = await store.record_turn(
        "sid-13", 1, None, usage, None, is_subagent=True
    )
    assert result is True

    rows = await store._db.execute_read(
        "SELECT is_subagent FROM turn_usage WHERE session_id = ?", ("sid-13",)
    )
    assert rows[0]["is_subagent"] == 1


async def test_is_subagent_defaults_to_false(store):
    await store.record_turn("sid-14", 1, None, {"input_tokens": 1}, None)

    rows = await store._db.execute_read(
        "SELECT is_subagent FROM turn_usage WHERE session_id = ?", ("sid-14",)
    )
    assert rows[0]["is_subagent"] == 0


# ---------------------------------------------------------------------------
# Issue #1840: migration of a pre-existing DB file with the old schema
# ---------------------------------------------------------------------------


async def test_migration_adds_missing_columns_to_existing_db(tmp_path):
    """A DB file created before #1840 (no cache_write_tokens_5m/1h/is_subagent
    columns) must be upgraded in place on initialize(), without raising and
    without losing existing rows."""
    import sqlite3

    from backend.analytics.database import AnalyticsDB

    db_path = tmp_path / "legacy_analytics.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE turn_usage (
          id                  INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id          TEXT    NOT NULL,
          turn_seq            INTEGER NOT NULL,
          model               TEXT,
          input_tokens        INTEGER NOT NULL DEFAULT 0,
          output_tokens       INTEGER NOT NULL DEFAULT 0,
          cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
          cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
          sdk_total_cost_usd  REAL,
          ts                  REAL    NOT NULL,
          UNIQUE(session_id, turn_seq)
        );
        CREATE TABLE session_usage (
          session_id          TEXT PRIMARY KEY,
          model               TEXT,
          turn_count          INTEGER NOT NULL DEFAULT 0,
          input_tokens        INTEGER NOT NULL DEFAULT 0,
          output_tokens       INTEGER NOT NULL DEFAULT 0,
          cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
          cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
          sdk_total_cost_usd  REAL,
          last_updated        REAL    NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO turn_usage (session_id, turn_seq, input_tokens, ts) VALUES (?, ?, ?, ?)",
        ("legacy-sid", 1, 42, 0.0),
    )
    conn.execute(
        "INSERT INTO session_usage (session_id, input_tokens, last_updated) VALUES (?, ?, ?)",
        ("legacy-sid", 42, 0.0),
    )
    conn.commit()
    conn.close()

    db = AnalyticsDB(db_path)
    await db.initialize()
    try:
        store = AnalyticsStore(db)
        agg = await store.get_session_usage("legacy-sid")
        assert agg is not None
        assert agg["input_tokens"] == 42, "pre-existing row must survive the migration"
        assert agg["cache_write_tokens_5m"] == 0
        assert agg["cache_write_tokens_1h"] == 0

        # New writes against the upgraded schema must work normally.
        await store.record_turn(
            "legacy-sid", 2, None, {"cache_write_tokens_5m": 9}, None
        )
        agg2 = await store.get_session_usage("legacy-sid")
        assert agg2["cache_write_tokens_5m"] == 9
    finally:
        await db.close()


async def test_get_turn_count_includes_subagent_catch_up_rows_for_turn_seq_reseed(store):
    """Regression: get_turn_count() feeds SessionCoordinator's turn_seq reseed
    after a restart and must count is_subagent=1 catch-up rows too, even though
    they're excluded from the displayed session_usage.turn_count — otherwise a
    reseeded turn_seq would collide with a turn_seq already used by a catch-up
    row (INSERT OR IGNORE would then silently drop that turn's real usage)."""
    await store.record_turn("sid-15", 1, "claude-sonnet-4-6", {"input_tokens": 1}, None)
    await store.record_turn("sid-15", 2, "claude-sonnet-4-6", {"input_tokens": 1}, None)
    await store.record_turn(
        "sid-15", 3, None, {"input_tokens": 1}, None, is_subagent=True
    )

    agg = await store.get_session_usage("sid-15")
    assert agg["turn_count"] == 2  # display value excludes the catch-up row

    next_seq_source = await store.get_turn_count("sid-15")
    assert next_seq_source == 3, "must count all 3 rows so the next turn_seq is 4, not 3"


async def test_migration_is_idempotent_across_multiple_initialize_calls(tmp_path):
    """Re-running the migration on an already-upgraded DB (e.g. process restart)
    must not raise 'duplicate column' errors."""
    from backend.analytics.database import AnalyticsDB

    db_path = tmp_path / "analytics.db"
    db = AnalyticsDB(db_path)
    await db.initialize()
    await db.close()

    # Second initialize() against the same file, simulating a restart.
    db2 = AnalyticsDB(db_path)
    await db2.initialize()
    await db2.close()
