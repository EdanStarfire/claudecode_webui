"""Unit tests for usage normalisation against SDK 0.1.72+ (issue #1287)."""
from src.session_coordinator import _normalize_result_usage


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
