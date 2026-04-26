"""Unit tests for AnalyticsStore (issue #1125)."""

import pytest

from src.analytics.database import AnalyticsDB
from src.analytics_store import AnalyticsStore


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
