"""Unit tests for src/analytics/aggregator.py (issue #1132)."""
from __future__ import annotations

import pytest

from src.analytics.aggregator import (
    aggregate_by_session,
    aggregate_by_time,
    compute_bucket_totals,
    compute_session_totals,
)
from src.analytics.database import AnalyticsDB
from src.config_manager import ModelRates, PricingConfig

_SONNET = "claude-sonnet-4-6"
_HAIKU = "claude-haiku-4-5"
_UNKNOWN = "future-model-xyz"

# Deterministic pricing for tests (input=2.0, output=10.0 USD/M tokens)
_PRICING = PricingConfig(
    rates={
        _SONNET: ModelRates(input=2.0, output=10.0, cache_write=0.0, cache_read=0.0),
        _HAIKU: ModelRates(input=1.0, output=5.0, cache_write=0.0, cache_read=0.0),
    },
    default_model=_SONNET,
)

# Reference timestamp: 2024-01-15 12:00:00 UTC
_BASE_TS = 1705320000.0
_HOUR = 3600.0
_DAY = 86400.0


@pytest.fixture
async def db(tmp_path):
    d = AnalyticsDB(tmp_path / "analytics.db")
    await d.initialize()
    yield d
    await d.close()


async def _seed(db: AnalyticsDB, rows: list[dict]) -> None:
    """Insert rows into turn_usage for testing."""
    sql = """
        INSERT INTO turn_usage
            (session_id, turn_seq, model, input_tokens, output_tokens,
             cache_write_tokens, cache_read_tokens, sdk_total_cost_usd, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, turn_seq) DO NOTHING
    """
    for r in rows:
        await db.execute_write(sql, (
            r["session_id"],
            r["turn_seq"],
            r.get("model", _SONNET),
            r.get("input_tokens", 0),
            r.get("output_tokens", 0),
            r.get("cache_write_tokens", 0),
            r.get("cache_read_tokens", 0),
            r.get("sdk_total_cost_usd"),
            r.get("ts", _BASE_TS),
        ))


# ---------------------------------------------------------------------------
# aggregate_by_session
# ---------------------------------------------------------------------------


async def test_session_empty_range(db):
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS, _BASE_TS + _DAY)
    assert rows == []


async def test_session_one_session(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 1000, "output_tokens": 500, "ts": _BASE_TS},
        {"session_id": "s1", "turn_seq": 2, "input_tokens": 2000, "output_tokens": 300, "ts": _BASE_TS + 60},
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s1"
    assert row["turn_count"] == 2
    assert row["input_tokens"] == 3000
    assert row["output_tokens"] == 800
    # cost = (3000 * 2.0 + 800 * 10.0) / 1_000_000 = 0.006 + 0.008 = 0.014
    assert abs(row["estimated_cost_usd"] - 0.014) < 1e-9
    assert row["rates_known"] is True
    assert row["session_name"] is None  # not enriched at aggregator level
    assert row["is_minion"] is False


async def test_session_two_sessions(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 1000, "ts": _BASE_TS},
        {"session_id": "s2", "turn_seq": 1, "input_tokens": 2000, "ts": _BASE_TS + 60},
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS - 1, _BASE_TS + _DAY)
    sids = {r["session_id"] for r in rows}
    assert sids == {"s1", "s2"}


async def test_session_since_until_filter(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 100, "ts": _BASE_TS - 100},  # before range
        {"session_id": "s2", "turn_seq": 1, "input_tokens": 200, "ts": _BASE_TS + 60},   # inside range
        {"session_id": "s3", "turn_seq": 1, "input_tokens": 300, "ts": _BASE_TS + _DAY + 100},  # after range
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS, _BASE_TS + _DAY)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s2"


async def test_session_id_filter(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 100, "ts": _BASE_TS},
        {"session_id": "s2", "turn_seq": 1, "input_tokens": 200, "ts": _BASE_TS},
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS - 1, _BASE_TS + _DAY, session_ids=["s1"])
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"


async def test_model_filter(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "model": _SONNET, "input_tokens": 100, "ts": _BASE_TS},
        {"session_id": "s2", "turn_seq": 1, "model": _HAIKU, "input_tokens": 200, "ts": _BASE_TS},
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS - 1, _BASE_TS + _DAY, models=[_HAIKU])
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s2"


async def test_unknown_model_no_rates(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "model": _UNKNOWN, "input_tokens": 1000, "ts": _BASE_TS},
    ])
    # Use a pricing config with no default fallback match
    pricing_no_default = PricingConfig(rates={}, default_model=_UNKNOWN)
    rows = await aggregate_by_session(db, pricing_no_default, _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(rows) == 1
    assert rows[0]["estimated_cost_usd"] is None
    assert rows[0]["rates_known"] is False


async def test_cache_tokens_included(db):
    await _seed(db, [
        {
            "session_id": "s1", "turn_seq": 1,
            "input_tokens": 0, "output_tokens": 0,
            "cache_write_tokens": 1000, "cache_read_tokens": 2000,
            "ts": _BASE_TS,
        },
    ])
    rows = await aggregate_by_session(db, _PRICING, _BASE_TS - 1, _BASE_TS + _DAY)
    assert rows[0]["cache_write_tokens"] == 1000
    assert rows[0]["cache_read_tokens"] == 2000


# ---------------------------------------------------------------------------
# compute_session_totals
# ---------------------------------------------------------------------------


def test_session_totals_empty():
    totals = compute_session_totals([])
    assert totals["session_count"] == 0
    assert totals["top_session"] is None
    assert totals["estimated_cost_usd"] == 0.0


def test_session_totals_top_session():
    rows = [
        {"session_id": "s1", "session_name": "A", "input_tokens": 100, "output_tokens": 0,
         "cache_write_tokens": 0, "cache_read_tokens": 0, "estimated_cost_usd": 0.5},
        {"session_id": "s2", "session_name": "B", "input_tokens": 200, "output_tokens": 0,
         "cache_write_tokens": 0, "cache_read_tokens": 0, "estimated_cost_usd": 1.2},
    ]
    totals = compute_session_totals(rows)
    assert totals["session_count"] == 2
    assert totals["input_tokens"] == 300
    assert abs(totals["estimated_cost_usd"] - 1.7) < 1e-9
    assert totals["top_session"]["session_id"] == "s2"


# ---------------------------------------------------------------------------
# aggregate_by_time (hour)
# ---------------------------------------------------------------------------


async def test_hourly_empty_range(db):
    buckets = await aggregate_by_time(db, _PRICING, "hour", _BASE_TS, _BASE_TS + _DAY)
    assert buckets == []


async def test_hourly_single_bucket(db):
    # Two turns in the same hour
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 1000, "ts": _BASE_TS},
        {"session_id": "s1", "turn_seq": 2, "input_tokens": 500, "ts": _BASE_TS + 30},
    ])
    buckets = await aggregate_by_time(db, _PRICING, "hour", _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket["by_token_type"]["input_tokens"] == 1500
    assert bucket["bucket_ts"] is not None


async def test_hourly_two_buckets(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 1000, "ts": _BASE_TS},
        {"session_id": "s1", "turn_seq": 2, "input_tokens": 500, "ts": _BASE_TS + _HOUR + 10},
    ])
    buckets = await aggregate_by_time(db, _PRICING, "hour", _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(buckets) == 2
    total_input = sum(b["by_token_type"]["input_tokens"] for b in buckets)
    assert total_input == 1500


async def test_daily_grouping(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "input_tokens": 100, "ts": _BASE_TS},
        {"session_id": "s1", "turn_seq": 2, "input_tokens": 200, "ts": _BASE_TS + _DAY + 10},
    ])
    buckets = await aggregate_by_time(db, _PRICING, "day", _BASE_TS - 1, _BASE_TS + 2 * _DAY)
    assert len(buckets) == 2


async def test_time_by_model_breakdown(db):
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "model": _SONNET, "input_tokens": 1000, "ts": _BASE_TS},
        {"session_id": "s2", "turn_seq": 1, "model": _HAIKU, "input_tokens": 500, "ts": _BASE_TS + 60},
    ])
    buckets = await aggregate_by_time(db, _PRICING, "hour", _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(buckets) == 1
    by_model = buckets[0]["by_model"]
    models = {m["model"] for m in by_model}
    assert models == {_SONNET, _HAIKU}


async def test_time_token_type_matches_model_sum(db):
    """Per-token-type total must equal the sum of per-model totals for the same bucket."""
    await _seed(db, [
        {"session_id": "s1", "turn_seq": 1, "model": _SONNET, "input_tokens": 1000, "output_tokens": 200, "ts": _BASE_TS},
        {"session_id": "s2", "turn_seq": 1, "model": _HAIKU, "input_tokens": 500, "output_tokens": 100, "ts": _BASE_TS + 60},
    ])
    buckets = await aggregate_by_time(db, _PRICING, "hour", _BASE_TS - 1, _BASE_TS + _DAY)
    assert len(buckets) == 1
    bucket = buckets[0]
    by_model_input = sum(m["input_tokens"] for m in bucket["by_model"])
    by_model_output = sum(m["output_tokens"] for m in bucket["by_model"])
    assert bucket["by_token_type"]["input_tokens"] == by_model_input
    assert bucket["by_token_type"]["output_tokens"] == by_model_output


# ---------------------------------------------------------------------------
# compute_bucket_totals
# ---------------------------------------------------------------------------


def test_bucket_totals_empty():
    totals = compute_bucket_totals([])
    assert totals["input_tokens"] == 0
    assert totals["estimated_cost_usd"] == 0.0
    assert totals["session_count"] is None


def test_bucket_totals_sums():
    buckets = [
        {"by_token_type": {"input_tokens": 100, "output_tokens": 50, "cache_write_tokens": 0, "cache_read_tokens": 0, "estimated_cost_usd": 0.5}},
        {"by_token_type": {"input_tokens": 200, "output_tokens": 80, "cache_write_tokens": 10, "cache_read_tokens": 20, "estimated_cost_usd": 0.7}},
    ]
    totals = compute_bucket_totals(buckets)
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 130
    assert totals["cache_write_tokens"] == 10
    assert totals["cache_read_tokens"] == 20
    assert abs(totals["estimated_cost_usd"] - 1.2) < 1e-9
