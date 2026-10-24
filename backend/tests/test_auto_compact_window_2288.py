"""Issue #2288 — CLAUDE_CODE_AUTO_COMPACT_WINDOW derivation for LiteLLM-routed sessions."""

import logging
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.litellm_proxy_manager import LiteLLMProxyManager, compute_auto_compact_window
from backend.provider_catalog import CatalogModelEntry, ProviderCatalogManager
from backend.session_launcher import _resolve_auto_compact_window


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], None),
        ([None, None], None),
        ([99999], None),
        ([100000], 100000),
        ([999999], 999999),
        ([1000000], None),
        ([1048576], None),
        ([200000, 8192], None),
        ([200000, None, 128000], 128000),
        ([200000], 200000),
        ([1000000, 200000], 200000),
    ],
)
def test_compute_auto_compact_window(values, expected):
    assert compute_auto_compact_window(values) == expected


def test_catalog_model_roundtrip_context_window():
    m = CatalogModelEntry.from_dict({"id": "a", "litellm_model": "x/y", "context_window": 200000})
    assert m.context_window == 200000
    assert m.to_dict()["context_window"] == 200000
    plain = CatalogModelEntry.from_dict({"id": "a", "litellm_model": "x/y"})
    assert plain.context_window is None
    assert "context_window" not in plain.to_dict()


def _entry(cw):
    model = {"id": "m", "litellm_model": "openai/my-custom", "drop_params": False}
    if cw is not None:
        model["context_window"] = cw
    return {"id": "c", "provider_type": "openai", "litellm_params_template": {}, "models": [model]}


@pytest.mark.parametrize("bad", [999, 0, -5, "200000", 1.5, True])
def test_validate_entry_rejects_bad_context_window(bad):
    mgr = ProviderCatalogManager.__new__(ProviderCatalogManager)
    with pytest.raises(ValueError, match="context_window"):
        mgr._validate_entry(_entry(bad))


def test_validate_entry_accepts_good_or_missing_context_window():
    mgr = ProviderCatalogManager.__new__(ProviderCatalogManager)
    mgr._validate_entry(_entry(200000))
    mgr._validate_entry(_entry(None))


def _proxy(entries):
    cm = MagicMock()
    cm.list_entries = AsyncMock(return_value=entries)
    cm.resolve_params = AsyncMock(return_value={})
    return LiteLLMProxyManager(cm, MagicMock(), 4000)


@pytest.mark.asyncio
async def test_catalog_field_wins_over_litellm(monkeypatch):
    pm = _proxy([_entry(150000)])
    fake = types.SimpleNamespace(get_model_info=MagicMock(return_value={"max_input_tokens": 1}))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    assert await pm.get_model_context_window("c--m") == 150000
    fake.get_model_info.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_litellm_then_none(monkeypatch):
    pm = _proxy([_entry(None)])
    fake = types.SimpleNamespace(get_model_info=MagicMock(return_value={"max_input_tokens": 128000}))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    assert await pm.get_model_context_window("c--m") == 128000
    fake.get_model_info.side_effect = Exception("not mapped")
    assert await pm.get_model_context_window("c--m") is None
    assert await pm.get_model_context_window("c--unknown") is None


@pytest.mark.asyncio
async def test_model_list_carries_model_info():
    pm = _proxy([_entry(150000)])
    ml = await pm._build_model_list()
    assert ml[0]["model_info"] == {"max_input_tokens": 150000}
    pm = _proxy([_entry(None)])
    assert "model_info" not in (await pm._build_model_list())[0]


def _mgr_windows(windows):
    m = MagicMock()
    m.get_model_context_window = AsyncMock(side_effect=lambda a: windows.get(a))
    return m


@pytest.mark.asyncio
async def test_resolver_single_and_tier_min():
    assert await _resolve_auto_compact_window(_mgr_windows({"a": 200000}), ["a"], "s") == 200000
    m = _mgr_windows({"a": 200000, "b": 128000, "c": None})
    assert await _resolve_auto_compact_window(m, ["a", "b", "c"], "s") == 128000


@pytest.mark.asyncio
async def test_resolver_small_model_skips_and_warns(caplog):
    m = _mgr_windows({"a": 200000, "b": 8192})
    with caplog.at_level(logging.WARNING):
        assert await _resolve_auto_compact_window(m, ["a", "b"], "s") is None
    assert any("b" in r.getMessage() and "8192" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_resolver_unknown_and_1m_emit_nothing():
    assert await _resolve_auto_compact_window(_mgr_windows({}), ["a"], "s") is None
    assert await _resolve_auto_compact_window(_mgr_windows({"a": 1048576}), ["a"], "s") is None


# ── Launcher integration ──────────────────────────────────────────────────────

from backend.session_config import SessionConfig  # noqa: E402
from backend.session_coordinator import SessionCoordinator  # noqa: E402
from backend.tests.test_session_provider_routing import (  # noqa: E402,F401
    _create_session,
    _make_proxy_manager,
    _make_sdk_factory,
    _tier_config,
)

_KEY = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"


@pytest.fixture
async def coordinator():
    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(Path(tmp))
        await coord.initialize()
        yield coord
        await coord.cleanup()


def _proxy_with_windows(windows):
    p = _make_proxy_manager()
    p.list_model_aliases = AsyncMock(return_value=set())
    p.get_model_context_window = AsyncMock(side_effect=lambda a: windows.get(a))
    return p


async def _launch(coordinator, config, windows):
    coordinator.litellm_proxy_manager = _proxy_with_windows(windows)
    session_id = await _create_session(coordinator, config)
    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)
    await coordinator.start_session(session_id)
    return factory.call_args.kwargs.get("extra_env") or {}


@pytest.mark.asyncio
async def test_launcher_single_non_docker_emits(coordinator):
    cfg = SessionConfig(provider_catalog_id="bedrock", provider_model_id="m")
    env = await _launch(coordinator, cfg, {"bedrock--m": 200000})
    assert env[_KEY] == "200000"


@pytest.mark.asyncio
async def test_launcher_extra_env_wins(coordinator):
    cfg = SessionConfig(
        provider_catalog_id="bedrock", provider_model_id="m", extra_env={_KEY: "150k"}
    )
    env = await _launch(coordinator, cfg, {"bedrock--m": 200000})
    assert env[_KEY] == "150k"


@pytest.mark.asyncio
async def test_launcher_non_litellm_session_untouched(coordinator):
    env = await _launch(coordinator, SessionConfig(), {})
    assert _KEY not in env


@pytest.mark.asyncio
async def test_launcher_docker_tier_uses_min(coordinator):
    import json

    windows = {
        "bedrock--haiku-fast": 200000,
        "bedrock--sonnet-balanced": 128000,
        "bedrock--opus-power": None,
    }
    env = await _launch(coordinator, _tier_config(True), windows)
    assert json.loads(env["CLAUDE_DOCKER_EXTRA_ENV"])[_KEY] == "128000"


@pytest.mark.asyncio
async def test_launcher_docker_tier_small_model_not_emitted(coordinator):
    import json

    windows = {"bedrock--haiku-fast": 8192, "bedrock--sonnet-balanced": 200000}
    env = await _launch(coordinator, _tier_config(True), windows)
    assert _KEY not in json.loads(env["CLAUDE_DOCKER_EXTRA_ENV"])


@pytest.mark.asyncio
async def test_launcher_non_docker_tier_uses_default_tier_only(coordinator):
    windows = {"bedrock--sonnet-balanced": 200000, "bedrock--haiku-fast": 8192}
    env = await _launch(coordinator, _tier_config(False), windows)
    assert env[_KEY] == "200000"
