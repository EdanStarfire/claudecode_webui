"""Unit tests for PricingConfig, ModelRates, and compute_cost (issue #1125)."""


from src.config_manager import (
    ModelRates,
    PricingConfig,
    compute_cost,
    normalize_model_id,
)

_SONNET = "claude-sonnet-4-6"
_OPUS = "claude-opus-4-7"
_HAIKU = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# ModelRates
# ---------------------------------------------------------------------------

def test_model_rates_defaults():
    r = ModelRates()
    assert r.input == 0.0
    assert r.output == 0.0
    assert r.cache_write == 0.0
    assert r.cache_read == 0.0


def test_model_rates_round_trip():
    r = ModelRates(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)
    d = r.to_dict()
    r2 = ModelRates.from_dict(d)
    assert r2.input == r.input
    assert r2.output == r.output
    assert r2.cache_write == r.cache_write
    assert r2.cache_read == r.cache_read


# ---------------------------------------------------------------------------
# PricingConfig defaults
# ---------------------------------------------------------------------------

def test_pricing_config_default_rates_exist():
    p = PricingConfig()
    assert _SONNET in p.rates
    assert _OPUS in p.rates
    assert _HAIKU in p.rates


def test_pricing_config_default_model():
    p = PricingConfig()
    assert p.default_model == _SONNET


def test_pricing_config_sonnet_rates_positive():
    p = PricingConfig()
    r = p.rates[_SONNET]
    assert r.input > 0
    assert r.output > 0
    assert r.cache_write > 0
    assert r.cache_read > 0


# ---------------------------------------------------------------------------
# PricingConfig round-trip serialisation
# ---------------------------------------------------------------------------

def test_pricing_config_round_trip():
    p = PricingConfig()
    d = p.to_dict()
    p2 = PricingConfig.from_dict(d)

    assert p2.default_model == p.default_model
    for model_id, rates in p.rates.items():
        assert model_id in p2.rates
        r2 = p2.rates[model_id]
        assert r2.input == rates.input
        assert r2.output == rates.output


def test_pricing_config_from_dict_merges_with_defaults():
    """Custom rates in config must not erase built-in defaults for other models."""
    data = {
        "default_model": _SONNET,
        "rates": {
            "my-model": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
        },
    }
    p = PricingConfig.from_dict(data)
    assert "my-model" in p.rates
    assert _SONNET in p.rates  # built-in default retained


# ---------------------------------------------------------------------------
# get_rates
# ---------------------------------------------------------------------------

def test_get_rates_known_model():
    p = PricingConfig()
    rates, known = p.get_rates(_SONNET)
    assert rates is not None
    assert known is True


def test_get_rates_alias():
    p = PricingConfig()
    rates, known = p.get_rates("sonnet")
    assert rates is not None
    assert known is True


def test_get_rates_unknown_model_falls_back():
    p = PricingConfig()
    rates, known = p.get_rates("some-future-model-xyz")
    assert known is False
    # Falls back to default_model (sonnet)
    assert rates is not None


def test_get_rates_none_model_falls_back():
    p = PricingConfig()
    rates, known = p.get_rates(None)
    assert known is False
    assert rates is not None


# ---------------------------------------------------------------------------
# compute_cost
# ---------------------------------------------------------------------------

def test_compute_cost_simple():
    rates = ModelRates(input=3.0, output=15.0, cache_write=0.0, cache_read=0.0)
    counts = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    cost = compute_cost(rates, counts)
    assert abs(cost - 18.0) < 1e-9


def test_compute_cost_cache_tokens():
    rates = ModelRates(input=0.0, output=0.0, cache_write=3.75, cache_read=0.30)
    counts = {"cache_write_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    cost = compute_cost(rates, counts)
    assert abs(cost - 4.05) < 1e-9


def test_compute_cost_zero_tokens():
    rates = ModelRates(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)
    cost = compute_cost(rates, {})
    assert cost == 0.0


def test_compute_cost_partial_tokens():
    rates = ModelRates(input=3.0, output=15.0, cache_write=0.0, cache_read=0.0)
    counts = {"input_tokens": 500_000}
    cost = compute_cost(rates, counts)
    assert abs(cost - 1.5) < 1e-9


# ---------------------------------------------------------------------------
# normalize_model_id
# ---------------------------------------------------------------------------

def test_normalize_alias_sonnet():
    assert normalize_model_id("sonnet") == _SONNET


def test_normalize_alias_opus():
    assert normalize_model_id("opus") == _OPUS


def test_normalize_alias_haiku():
    assert normalize_model_id("haiku") == _HAIKU


def test_normalize_opusplan_alias():
    assert normalize_model_id("opusplan") == _OPUS


def test_normalize_unknown_returns_as_is():
    assert normalize_model_id("some-custom-model") == "some-custom-model"


def test_normalize_none_returns_none():
    assert normalize_model_id(None) is None
