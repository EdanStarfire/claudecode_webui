"""Pure SQL aggregation helpers for the analytics usage endpoint (issue #1132).

All functions are stateless — they receive an AnalyticsDB and a PricingConfig so
they can be unit-tested with in-memory databases and custom pricing.
"""
from __future__ import annotations

from typing import Any

from ..config_manager import PricingConfig, compute_cost
from .database import AnalyticsDB

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")

_BUCKET_FMT = {
    "hour": "%Y-%m-%dT%H:00:00",
    "day": "%Y-%m-%d",
}


def _cost_for_row(
    pricing: PricingConfig, model: str | None, counts: dict[str, Any]
) -> tuple[float | None, bool]:
    """Return (estimated_cost_usd, rates_known) for a token-count dict."""
    rates, rates_known = pricing.get_rates(model)
    if rates is None:
        return None, False
    return compute_cost(rates, counts), rates_known


async def aggregate_by_session(
    db: AnalyticsDB,
    pricing: PricingConfig,
    since: float,
    until: float,
    session_ids: list[str] | None = None,
    models: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return one row per session with summed token counts and estimated cost.

    Enrichment fields (session_name, is_minion, parent_session_id) are left as
    their defaults and filled in by the router layer.
    """
    wheres = ["ts >= ?", "ts <= ?"]
    params: list[Any] = [since, until]

    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        wheres.append(f"session_id IN ({placeholders})")
        params.extend(session_ids)
    if models:
        placeholders = ",".join("?" * len(models))
        wheres.append(f"model IN ({placeholders})")
        params.extend(models)

    where_clause = " AND ".join(wheres)
    sql = f"""
        SELECT
            session_id,
            model,
            COUNT(*)                    AS turn_count,
            SUM(input_tokens)           AS input_tokens,
            SUM(output_tokens)          AS output_tokens,
            SUM(cache_write_tokens)     AS cache_write_tokens,
            SUM(cache_read_tokens)      AS cache_read_tokens,
            SUM(sdk_total_cost_usd)     AS sdk_reported_cost_usd,
            MAX(ts)                     AS last_active
        FROM turn_usage
        WHERE {where_clause}
        GROUP BY session_id
        ORDER BY last_active DESC
    """
    raw = await db.execute_read(sql, params)

    result: list[dict[str, Any]] = []
    for row in raw:
        counts = {f: row.get(f) or 0 for f in _TOKEN_FIELDS}
        estimated_cost, rates_known = _cost_for_row(pricing, row["model"], counts)
        result.append(
            {
                "session_id": row["session_id"],
                "session_name": None,
                "is_minion": False,
                "parent_session_id": None,
                "model": row["model"],
                "turn_count": row["turn_count"] or 0,
                **counts,
                "estimated_cost_usd": estimated_cost,
                "sdk_reported_cost_usd": row["sdk_reported_cost_usd"],
                "rates_known": rates_known,
                "last_active": row["last_active"],
            }
        )
    return result


async def aggregate_by_time(
    db: AnalyticsDB,
    pricing: PricingConfig,
    group_by: str,
    since: float,
    until: float,
    session_ids: list[str] | None = None,
    models: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return time-bucketed rows with by_token_type and by_model breakdowns.

    group_by must be 'hour' or 'day'.  Both breakdowns are computed in a single
    pass so the caller can toggle between them client-side without re-querying.
    """
    fmt = _BUCKET_FMT[group_by]
    wheres = ["ts >= ?", "ts <= ?"]
    params: list[Any] = [since, until]

    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        wheres.append(f"session_id IN ({placeholders})")
        params.extend(session_ids)
    if models:
        placeholders = ",".join("?" * len(models))
        wheres.append(f"model IN ({placeholders})")
        params.extend(models)

    where_clause = " AND ".join(wheres)
    # Group by bucket+model so we can build the per-model breakdown directly.
    sql = f"""
        SELECT
            strftime('{fmt}', ts, 'unixepoch')                               AS bucket_label,
            CAST(strftime('%s', strftime('{fmt}', ts, 'unixepoch')) AS INTEGER) AS bucket_ts,
            model,
            SUM(input_tokens)       AS input_tokens,
            SUM(output_tokens)      AS output_tokens,
            SUM(cache_write_tokens) AS cache_write_tokens,
            SUM(cache_read_tokens)  AS cache_read_tokens
        FROM turn_usage
        WHERE {where_clause}
        GROUP BY bucket_label, model
        ORDER BY bucket_label, model
    """
    raw = await db.execute_read(sql, params)

    # Aggregate per bucket
    bucket_map: dict[str, dict[str, Any]] = {}
    for row in raw:
        label = row["bucket_label"]
        if label not in bucket_map:
            bucket_map[label] = {
                "bucket_ts": row["bucket_ts"],
                "by_token_type": {f: 0 for f in _TOKEN_FIELDS},
                "by_model": [],
            }
        bucket = bucket_map[label]
        counts = {f: row.get(f) or 0 for f in _TOKEN_FIELDS}
        for f in _TOKEN_FIELDS:
            bucket["by_token_type"][f] += counts[f]
        estimated_cost, _ = _cost_for_row(pricing, row["model"], counts)
        bucket["by_model"].append(
            {
                "model": row["model"],
                **counts,
                "estimated_cost_usd": estimated_cost,
            }
        )

    # Add aggregate cost to by_token_type (sum of per-model costs)
    result: list[dict[str, Any]] = []
    for bucket in bucket_map.values():
        agg_cost = sum(
            m["estimated_cost_usd"] for m in bucket["by_model"] if m["estimated_cost_usd"] is not None
        )
        bucket["by_token_type"]["estimated_cost_usd"] = agg_cost
        result.append(bucket)

    return result


def compute_session_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute fleet-level totals across a list of session rows."""
    totals: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "estimated_cost_usd": 0.0,
        "session_count": len(rows),
        "top_session": None,
    }
    top: dict[str, Any] | None = None
    for row in rows:
        for f in _TOKEN_FIELDS:
            totals[f] += row.get(f) or 0
        cost = row.get("estimated_cost_usd") or 0.0
        totals["estimated_cost_usd"] += cost
        if top is None or cost > (top.get("estimated_cost_usd") or 0.0):
            top = row

    if top:
        totals["top_session"] = {
            "session_id": top["session_id"],
            "session_name": top.get("session_name"),
            "estimated_cost_usd": top.get("estimated_cost_usd"),
        }

    return totals


def compute_bucket_totals(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute fleet-level totals across a list of time buckets."""
    totals: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "estimated_cost_usd": 0.0,
        "session_count": None,
        "top_session": None,
    }
    for bucket in buckets:
        tt = bucket["by_token_type"]
        for f in _TOKEN_FIELDS:
            totals[f] += tt.get(f) or 0
        totals["estimated_cost_usd"] += tt.get("estimated_cost_usd") or 0.0

    return totals
