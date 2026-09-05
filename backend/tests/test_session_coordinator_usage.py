"""Unit tests for usage normalisation against SDK 0.1.72+ (issue #1287)."""
from backend.session_coordinator import _delta_from_baseline, _normalize_result_usage


def test_prefers_usage_when_populated():
    usage = {"input_tokens": 100, "output_tokens": 50,
             "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}
    out, cost = _normalize_result_usage(usage, None)
    assert out == usage
    assert cost is None


def test_falls_back_to_model_usage_when_usage_none():
    model_usage = {
        "claude-sonnet-4-6": {
            "inputTokens": 200, "outputTokens": 80,
            "cacheCreationInputTokens": 20, "cacheReadInputTokens": 4,
            "costUSD": 0.0123,
        }
    }
    out, cost = _normalize_result_usage(None, model_usage)
    assert out == {
        "input_tokens": 200, "output_tokens": 80,
        "cache_creation_input_tokens": 20, "cache_read_input_tokens": 4,
    }
    assert abs(cost - 0.0123) < 1e-9


def test_falls_back_when_usage_is_all_zeros():
    usage = {"input_tokens": 0, "output_tokens": 0}
    model_usage = {
        "claude-sonnet-4-6": {
            "inputTokens": 50, "outputTokens": 25,
            "cacheCreationInputTokens": 0, "cacheReadInputTokens": 0,
            "costUSD": 0.001,
        }
    }
    out, cost = _normalize_result_usage(usage, model_usage)
    assert out["input_tokens"] == 50
    assert out["output_tokens"] == 25
    assert cost is not None


def test_aggregates_across_multiple_models():
    model_usage = {
        "claude-sonnet-4-6": {
            "inputTokens": 100, "outputTokens": 40,
            "cacheCreationInputTokens": 5, "cacheReadInputTokens": 1,
            "costUSD": 0.002,
        },
        "claude-haiku-4-5": {
            "inputTokens": 50, "outputTokens": 20,
            "cacheCreationInputTokens": 2, "cacheReadInputTokens": 1,
            "costUSD": 0.0005,
        },
    }
    out, cost = _normalize_result_usage(None, model_usage)
    assert out == {
        "input_tokens": 150, "output_tokens": 60,
        "cache_creation_input_tokens": 7, "cache_read_input_tokens": 2,
    }
    assert abs(cost - 0.0025) < 1e-9


def test_empty_inputs_return_empty_dict():
    out, cost = _normalize_result_usage(None, None)
    assert out == {}
    assert cost is None
    out, cost = _normalize_result_usage({}, {})
    assert out == {}
    assert cost is None


# --- Issue #1838: per-turn delta computation from cumulative SDK snapshots ---


def test_t1_single_epoch_no_restart_deltas_sum_to_last_cumulative():
    cumulative_costs = [24.79, 84.98, 90.60, 91.43, 93.78, 96.58, 117.62]
    baseline = None
    total_cost = 0.0
    for c in cumulative_costs:
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost
    assert abs(total_cost - cumulative_costs[-1]) < 1e-9


def test_t2_restart_new_epoch_stays_below_old_peak():
    baseline = None
    total_cost = 0.0
    for c in (50.0, 80.0):
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost
    assert total_cost == 80.0

    # Simulate the start_session() reset hook for a new epoch.
    baseline = None
    for c in (10.0, 30.0):
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost

    # Old epoch's peak (80.0) is fully preserved; new epoch's own 30.0 is added on top.
    assert total_cost == 110.0


def test_t3_restart_new_epoch_first_turn_exceeds_old_peak():
    baseline = None
    total_cost = 0.0
    for c in (50.0, 80.0):
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost

    # Restart: new epoch's first turn already exceeds the old epoch's peak.
    baseline = None
    _, delta_cost, baseline = _delta_from_baseline({}, 200.0, baseline)
    total_cost += delta_cost

    # Old epoch's cost must still be fully counted, not dropped/overwritten.
    assert total_cost == 280.0


def test_t4_multiple_restarts_every_epoch_counted_once():
    epochs = [[24.79, 84.98], [1.08, 5.0], [0.5]]
    total_cost = 0.0
    for epoch in epochs:
        baseline = None
        for c in epoch:
            _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
            total_cost += delta_cost
    assert abs(total_cost - (84.98 + 5.0 + 0.5)) < 1e-9


def test_regression_issue_1838_real_numbers_not_600_86():
    """Reproduces the issue's real session: summing cumulative snapshots directly
    gives $600.86; true per-turn deltas must sum to ~$118.70."""
    epoch1_cumulative = [24.79, 84.98, 90.60, 91.43, 93.78, 96.58, 117.62]
    epoch2_cumulative = [1.08]

    naive_sum = sum(epoch1_cumulative) + sum(epoch2_cumulative)
    assert abs(naive_sum - 600.86) < 1e-2  # sanity-check the buggy baseline

    total_cost = 0.0
    baseline = None
    for c in epoch1_cumulative:
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost

    baseline = None  # simulate start_session()'s new-SDK-creation reset hook
    for c in epoch2_cumulative:
        _, delta_cost, baseline = _delta_from_baseline({}, c, baseline)
        total_cost += delta_cost

    assert abs(total_cost - 118.70) < 1e-6


def test_missing_usage_fields_leave_baseline_untouched():
    """A field absent from `usage` must contribute 0 delta this turn but must NOT
    zero out its baseline, matching backend/tests/fixtures/multi_turn/messages.jsonl's
    real ResultMessage with `usage` entirely absent."""
    baseline = {"input_tokens": 100.0, "output_tokens": 50.0}

    delta, delta_cost, new_baseline = _delta_from_baseline({}, None, baseline)

    assert delta == {
        "input_tokens": 0.0,
        "output_tokens": 0.0,
        "cache_creation_input_tokens": 0.0,
        "cache_read_input_tokens": 0.0,
    }
    assert delta_cost is None
    assert new_baseline["input_tokens"] == 100.0
    assert new_baseline["output_tokens"] == 50.0
    assert "total_cost_usd" not in new_baseline

    # The following turn with real values must compute its delta against the
    # preserved baseline, not a spuriously zeroed one.
    delta2, _, _ = _delta_from_baseline(
        {"input_tokens": 150, "output_tokens": 80}, None, new_baseline
    )
    assert delta2["input_tokens"] == 50.0
    assert delta2["output_tokens"] == 30.0


def test_none_baseline_treated_as_all_zero():
    delta, delta_cost, new_baseline = _delta_from_baseline(
        {"input_tokens": 10, "output_tokens": 5}, 1.5, None
    )
    assert delta == {
        "input_tokens": 10.0,
        "output_tokens": 5.0,
        "cache_creation_input_tokens": 0.0,
        "cache_read_input_tokens": 0.0,
    }
    assert delta_cost == 1.5
    assert new_baseline == {
        "input_tokens": 10.0,
        "output_tokens": 5.0,
        "total_cost_usd": 1.5,
    }
