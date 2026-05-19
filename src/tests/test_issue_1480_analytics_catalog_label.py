"""
Unit tests for issue #1480: analytics records NULL model for LiteLLM catalog sessions.

The fix adds _resolve_analytics_model_label() to session_coordinator.py and uses it
instead of _eff.model directly.  These tests cover the helper in isolation plus one
end-to-end path through resolve_effective_config.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from src.session_config import SessionConfig
from src.session_coordinator import _resolve_analytics_model_label

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _cfg(**kwargs) -> SessionConfig:
    """Build a SessionConfig with only the fields relevant to the tests."""
    return SessionConfig(**kwargs)


def _tier_cfg(default_tier: str = "sonnet") -> SessionConfig:
    """SessionConfig with all 6 per-tier fields filled in."""
    return _cfg(
        provider_haiku_catalog_id="or",
        provider_haiku_model_id="anthropic/claude-haiku-3",
        provider_sonnet_catalog_id="or",
        provider_sonnet_model_id="anthropic/claude-3-5-sonnet",
        provider_opus_catalog_id="or",
        provider_opus_model_id="anthropic/claude-opus-4",
        provider_default_tier=default_tier,
    )


# ---------------------------------------------------------------------------
# Test 1: single provider-catalog → alias returned
# ---------------------------------------------------------------------------

def test_single_catalog_returns_alias():
    cfg = _cfg(provider_catalog_id="openrouter", provider_model_id="anthropic/claude-3.5-sonnet")
    result = _resolve_analytics_model_label(cfg)
    assert result == "openrouter--anthropic/claude-3.5-sonnet"


# ---------------------------------------------------------------------------
# Test 2: per-tier session → default-tier alias returned
# ---------------------------------------------------------------------------

def test_per_tier_returns_default_tier_alias():
    cfg = _tier_cfg(default_tier="sonnet")
    result = _resolve_analytics_model_label(cfg)
    assert result == "or--anthropic/claude-3-5-sonnet"


def test_per_tier_haiku_default():
    cfg = _tier_cfg(default_tier="haiku")
    result = _resolve_analytics_model_label(cfg)
    assert result == "or--anthropic/claude-haiku-3"


def test_per_tier_opus_default():
    cfg = _tier_cfg(default_tier="opus")
    result = _resolve_analytics_model_label(cfg)
    assert result == "or--anthropic/claude-opus-4"


# ---------------------------------------------------------------------------
# Test 3: plain (non-catalog) session → cfg.model returned unchanged
# ---------------------------------------------------------------------------

def test_plain_session_returns_sdk_model():
    cfg = _cfg(model="claude-sonnet-4-5")
    result = _resolve_analytics_model_label(cfg)
    assert result == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Test 4: no catalog, no model → None preserved (NULL in analytics)
# ---------------------------------------------------------------------------

def test_no_catalog_no_model_returns_none():
    cfg = _cfg()
    result = _resolve_analytics_model_label(cfg)
    assert result is None


# ---------------------------------------------------------------------------
# Test 5: single-catalog wins over per-tier when both present
# ---------------------------------------------------------------------------

def test_single_catalog_wins_over_tier():
    cfg = _tier_cfg(default_tier="sonnet")
    cfg = cfg.model_copy(update={
        "provider_catalog_id": "direct",
        "provider_model_id": "claude-opus-4",
    })
    result = _resolve_analytics_model_label(cfg)
    assert result == "direct--claude-opus-4"


# ---------------------------------------------------------------------------
# Test 6: defensive guard — unknown tier value falls back to cfg.model
# ---------------------------------------------------------------------------

def test_unknown_tier_falls_back_to_model():
    cfg = _tier_cfg(default_tier="turbo")  # not haiku/sonnet/opus
    cfg = cfg.model_copy(update={"model": "fallback-model"})
    result = _resolve_analytics_model_label(cfg)
    assert result == "fallback-model"


# ---------------------------------------------------------------------------
# Test 7: partial catalog config (catalog_id set, model_id missing) → fallback
# ---------------------------------------------------------------------------

def test_partial_catalog_missing_model_id_falls_back():
    cfg = _cfg(provider_catalog_id="openrouter", model="claude-sonnet-4-5")
    result = _resolve_analytics_model_label(cfg)
    assert result == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Test 8: end-to-end — resolve_effective_config with catalog fields
# ---------------------------------------------------------------------------

async def test_end_to_end_with_resolve_effective_config():
    """Verify the label resolves correctly through the full resolution chain."""
    from src.config_resolution import resolve_effective_config
    from src.session_manager import SessionInfo, SessionState

    sinfo = SessionInfo(
        session_id="test-1480",
        state=SessionState.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
        config={
            "provider_catalog_id": "openrouter",
            "provider_model_id": "anthropic/claude-3.5-sonnet",
        },
    )
    tm = MagicMock()
    tm.get_template = AsyncMock(return_value=None)

    eff = await resolve_effective_config(sinfo, tm, profile_manager=None)
    label = _resolve_analytics_model_label(eff)

    assert label == "openrouter--anthropic/claude-3.5-sonnet"
