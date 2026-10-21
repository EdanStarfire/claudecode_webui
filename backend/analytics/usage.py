"""Pure usage/cost-normalization helpers for turn-level analytics.

Relocated verbatim from session_coordinator.py (issue #2267, epic #1990 stage
7 residual) — these carry no coupling beyond stdlib, so the move is a pure
relocation, not a rewrite.
"""

from __future__ import annotations


def _normalize_result_usage(
    usage: dict | None,
    model_usage: dict | None,
) -> tuple[dict, float | None]:
    """Return (snake_case usage dict, aggregate cost from model_usage or None).

    Prefers SDK 'usage' when it carries non-zero token counts. Otherwise
    aggregates per-model 'model_usage' entries (camelCase keys, keyed by
    model name) into the snake_case shape that AnalyticsStore.record_turn
    already understands.
    """
    snake_keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    if isinstance(usage, dict) and any(int(usage.get(k) or 0) for k in snake_keys):
        return dict(usage), None

    if not isinstance(model_usage, dict) or not model_usage:
        return (dict(usage) if isinstance(usage, dict) else {}), None

    agg = {k: 0 for k in snake_keys}
    cost = 0.0
    has_cost = False
    for entry in model_usage.values():
        if not isinstance(entry, dict):
            continue
        agg["input_tokens"] += int(entry.get("inputTokens") or 0)
        agg["output_tokens"] += int(entry.get("outputTokens") or 0)
        agg["cache_creation_input_tokens"] += int(entry.get("cacheCreationInputTokens") or 0)
        agg["cache_read_input_tokens"] += int(entry.get("cacheReadInputTokens") or 0)
        c = entry.get("costUSD")
        if c is not None:
            cost += float(c)
            has_cost = True
    return agg, (cost if has_cost else None)


def _delta_from_baseline(
    usage: dict,
    sdk_cost: float | None,
    baseline: dict[str, float] | None,
) -> tuple[dict, float | None, dict[str, float]]:
    """Compute per-turn deltas from cumulative-since-subprocess snapshots.

    `baseline` is None at the first turn of a new epoch (post-restart or
    brand new session) — treated as an all-zero prior snapshot, so the
    delta equals the turn's full raw value, exactly as intended.

    A field's absence is NOT the same as a real zero: `usage` is not
    guaranteed to carry all 4 token keys on every ResultMessage — e.g.
    backend/tests/fixtures/multi_turn/messages.jsonl has ResultMessages
    with `total_cost_usd` present and `usage` entirely absent, and
    `_normalize_result_usage()`'s passthrough branch
    (`return dict(usage), None`) copies whatever partial shape the SDK
    sent rather than guaranteeing all 4 keys. Treating a missing key as 0
    and writing that 0 into the baseline would corrupt the *next* turn's
    delta for that field (the same failure mode `sdk_cost is None`
    already guards against for cost) — so missing/None fields contribute
    0 delta for *this* turn but leave that field's baseline untouched,
    symmetric with the cost handling below.
    """
    fields = (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    )
    baseline = dict(baseline or {})
    delta: dict[str, float] = {}
    for k in fields:
        if k in usage and usage[k] is not None:
            current = float(usage[k])
            delta[k] = max(0.0, current - baseline.get(k, 0.0))
            baseline[k] = current
        else:
            delta[k] = 0.0
            # field not reported this turn — leave baseline[k] untouched

    delta_cost = None
    if sdk_cost is not None:
        delta_cost = max(0.0, float(sdk_cost) - baseline.get("total_cost_usd", 0.0))
        baseline["total_cost_usd"] = float(sdk_cost)
    # else: no cost reported this turn — leave total_cost_usd baseline
    # untouched so the next turn's delta is still computed correctly.

    return delta, delta_cost, baseline


def _accumulate_subagent_usage(
    accum: dict[str, float] | None,
    usage: dict,
) -> dict[str, float]:
    """Sum one subagent AssistantMessage's usage into a running per-session accumulator.

    Unlike the main thread's cumulative-since-subprocess-start snapshots (see
    `_delta_from_baseline`), each subagent `AssistantMessage.usage` is already a
    per-call delta for that individual API call — it must be summed directly,
    never baseline-diffed (diffing would silently zero out everything after the
    first call, since there's no shared cumulative counter to diff against).
    """
    accum = dict(accum or {})
    cache_creation = usage.get("cache_creation") or {}
    cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
    cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
    accum["input_tokens"] = accum.get("input_tokens", 0) + int(usage.get("input_tokens") or 0)
    accum["output_tokens"] = accum.get("output_tokens", 0) + int(usage.get("output_tokens") or 0)
    accum["cache_read_input_tokens"] = accum.get("cache_read_input_tokens", 0) + int(
        usage.get("cache_read_input_tokens") or 0
    )
    accum["cache_write_tokens_5m"] = accum.get("cache_write_tokens_5m", 0) + cache_5m
    accum["cache_write_tokens_1h"] = accum.get("cache_write_tokens_1h", 0) + cache_1h
    # Flat total, kept in sync with the two TTL tiers above so callers never need
    # to re-derive it — AnalyticsStore.record_turn()/compute_cost() key off of this
    # flat name (or "cache_creation_input_tokens"), not the TTL-tier split.
    accum["cache_write_tokens"] = accum.get("cache_write_tokens", 0) + cache_5m + cache_1h
    return accum
