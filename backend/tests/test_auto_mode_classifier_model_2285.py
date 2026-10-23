"""Issue #2285 — launcher vetting of auto_mode_classifier_model + template plumbing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.session_config import SessionConfig
from backend.session_launcher import _vet_classifier_model


def _mgr(aliases):
    m = MagicMock()
    m.list_model_aliases = AsyncMock(return_value=set(aliases))
    return m


@pytest.mark.asyncio
async def test_catalog_alias_routed_kept_and_passthrough():
    cfg = SessionConfig(auto_mode_classifier_model="cat--m1")
    out, pt = await _vet_classifier_model(cfg, _mgr({"cat--m1"}), True, "s")
    assert out.auto_mode_classifier_model == "cat--m1"
    assert pt == ["cat--m1"]


@pytest.mark.asyncio
async def test_catalog_alias_not_routed_dropped():
    cfg = SessionConfig(auto_mode_classifier_model="cat--m1")
    out, pt = await _vet_classifier_model(cfg, _mgr({"cat--m1"}), False, "s")
    assert out.auto_mode_classifier_model is None
    assert pt == []


@pytest.mark.asyncio
async def test_plain_model_untouched():
    cfg = SessionConfig(auto_mode_classifier_model="claude-haiku-4-5")
    out, pt = await _vet_classifier_model(cfg, _mgr({"cat--m1"}), False, "s")
    assert out.auto_mode_classifier_model == "claude-haiku-4-5"
    assert pt == []


@pytest.mark.asyncio
async def test_unknown_alias_like_value_untouched_and_no_proxy():
    cfg = SessionConfig(auto_mode_classifier_model="x--y")
    out, _ = await _vet_classifier_model(cfg, _mgr({"cat--m1"}), False, "s")
    assert out.auto_mode_classifier_model == "x--y"
    out, pt = await _vet_classifier_model(cfg, None, False, "s")
    assert out.auto_mode_classifier_model == "x--y" and pt == []


def test_empty_string_normalized_to_none():
    assert SessionConfig(auto_mode_classifier_model="  ").auto_mode_classifier_model is None
