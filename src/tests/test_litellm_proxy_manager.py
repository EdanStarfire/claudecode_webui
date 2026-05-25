"""Unit tests for LiteLLMProxyManager — lifecycle, key registry, auth callback."""

import asyncio
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-import real LiteLLM counter classes so they live in sys.modules before any
# test mocks litellm. _launch_server imports OpenAITokenCounter by its full dotted
# path; Python finds the already-cached real module even when `litellm` itself is
# replaced with a MagicMock in sys.modules.
from litellm.llms.anthropic.count_tokens.token_counter import AnthropicTokenCounter
from litellm.llms.bedrock.count_tokens.bedrock_token_counter import BedrockTokenCounter
from litellm.llms.gemini.common_utils import GoogleAIStudioTokenCounter
from litellm.llms.openai.responses.count_tokens.token_counter import OpenAITokenCounter
from litellm.llms.vertex_ai.common_utils import VertexAITokenCounter


def _make_manager():
    """Build a LiteLLMProxyManager with LiteLLM imports fully mocked."""
    mock_app = MagicMock()
    mock_proxy_server = MagicMock(app=mock_app)
    mock_types = MagicMock()

    modules = {
        "litellm": MagicMock(),
        "litellm.proxy": MagicMock(),
        "litellm.proxy.proxy_server": mock_proxy_server,
        "litellm.proxy._types": mock_types,
    }
    with patch.dict("sys.modules", modules):
        from src.litellm_proxy_manager import LiteLLMProxyManager

        catalog = AsyncMock()
        catalog.list_entries.return_value = []
        catalog.resolve_params.return_value = {}

        vault = AsyncMock()
        vault.get_secret.return_value = "test-secret-value"

        mgr = LiteLLMProxyManager.__new__(LiteLLMProxyManager)
        mgr._catalog_manager = catalog
        mgr._vault = vault
        mgr._port = 4000
        mgr._key_registry = {}
        mgr._routing_registry = {}
        mgr._server_task = None
        mgr._rebuild_lock = asyncio.Lock()
        mgr._running = False
        mgr._last_restart = None
        mgr._last_error = None
        mgr._model_count = 0
        mgr._config_file = None
        return mgr, mock_types


@pytest.fixture
def manager():
    mgr, _ = _make_manager()
    return mgr


@pytest.fixture
def manager_with_types():
    return _make_manager()


# ── Key Registry Tests ─────────────────────────────────────────────────────


def test_register_session_key_returns_lc_prefixed_key(manager):
    key = manager.register_session_key("sess-1")
    assert key.startswith("lc-")


def test_register_session_key_unique_per_call(manager):
    key1 = manager.register_session_key("sess-1")
    key2 = manager.register_session_key("sess-1")
    assert key1 != key2


def test_lookup_session_for_registered_key(manager):
    key = manager.register_session_key("sess-abc")
    assert manager.lookup_session_for_key(key) == "sess-abc"


def test_lookup_session_for_unknown_key_returns_none(manager):
    assert manager.lookup_session_for_key("nonexistent-key") is None


def test_unregister_session_key_removes_all_keys_for_session(manager):
    key1 = manager.register_session_key("sess-x")
    key2 = manager.register_session_key("sess-x")
    manager.unregister_session_key("sess-x")
    assert manager.lookup_session_for_key(key1) is None
    assert manager.lookup_session_for_key(key2) is None


def test_unregister_nonexistent_session_is_noop(manager):
    manager.unregister_session_key("never-registered")
    assert manager._key_registry == {}


# ── Auth Callback Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_callback_valid_key_returns_user_api_key_auth(manager_with_types):
    mgr, mock_types = manager_with_types

    # Configure mock UserAPIKeyAuth to return a simple object
    mock_auth_obj = MagicMock()
    mock_auth_obj.user_id = "sess-valid"
    mock_types.UserAPIKeyAuth.return_value = mock_auth_obj

    key = mgr.register_session_key("sess-valid")

    modules = {
        "litellm": MagicMock(),
        "litellm.proxy": MagicMock(),
        "litellm.proxy.proxy_server": MagicMock(),
        "litellm.proxy._types": mock_types,
    }
    with patch.dict("sys.modules", modules):
        result = await mgr.auth_callback(request=MagicMock(), api_key=key)

    mock_types.UserAPIKeyAuth.assert_called_once_with(api_key=key, user_id="sess-valid")
    assert result is mock_auth_obj


@pytest.mark.asyncio
async def test_auth_callback_invalid_key_raises_401(manager):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await manager.auth_callback(request=MagicMock(), api_key="bad-key")

    assert exc_info.value.status_code == 401


# ── Lifecycle State Tests ──────────────────────────────────────────────────


def test_is_running_false_before_start(manager):
    assert manager.is_running is False


# ── Routing Registry Tests (issue #1427 Phase 2) ──────────────────────────────


def test_register_session_routing_stores_entry(manager):
    manager.register_session_routing("sess-1", "lc-abc", "http://127.0.0.1:4000/")
    entry = manager.get_session_routing("sess-1")
    assert entry["virtual_key"] == "lc-abc"
    assert entry["base_url"] == "http://127.0.0.1:4000/"
    assert entry["model_map"] == {}
    assert entry["default_model"] is None


def test_get_session_routing_unknown_returns_none(manager):
    assert manager.get_session_routing("never-registered") is None


def test_unregister_session_routing_removes_entry(manager):
    manager.register_session_routing("sess-2", "lc-xyz", "http://127.0.0.1:4000/")
    manager.unregister_session_routing("sess-2")
    assert manager.get_session_routing("sess-2") is None


def test_unregister_session_routing_nonexistent_is_noop(manager):
    manager.unregister_session_routing("does-not-exist")
    assert manager._routing_registry == {}


def test_routing_registry_independent_of_key_registry(manager):
    """Key registry and routing registry are separate — operations on one don't affect the other."""
    manager.register_session_key("sess-3")
    manager.register_session_routing("sess-3", "lc-key", "http://127.0.0.1:4000/")

    manager.unregister_session_key("sess-3")
    # Routing entry should still be present
    assert manager.get_session_routing("sess-3") is not None

    manager.unregister_session_routing("sess-3")
    # Key registry should still be clean
    assert manager._key_registry == {}


# ── Telemetry Attribute Tests (issue #1427 Phase 4) ───────────────────────────


def test_telemetry_attributes_default_to_none(manager):
    assert manager.last_restart is None
    assert manager.last_error is None


def test_model_count_zero_before_start(manager):
    assert manager.model_count == 0


@pytest.mark.asyncio
async def test_build_model_list_updates_model_count(manager):
    manager._catalog_manager.list_entries.return_value = [
        {
            "id": "prov-a",
            "models": [
                {"id": "m1", "litellm_model": "anthropic/claude-sonnet-4-6"},
                {"id": "m2", "litellm_model": "anthropic/claude-haiku-4-5"},
            ],
            "litellm_params_template": {},
        }
    ]
    manager._catalog_manager.resolve_params.return_value = {}

    result = await manager._build_model_list()

    assert len(result) == 2
    assert manager.model_count == 2


@pytest.mark.asyncio
async def test_build_model_list_empty_catalog_sets_count_zero(manager):
    manager._catalog_manager.list_entries.return_value = []

    result = await manager._build_model_list()

    assert result == []
    assert manager.model_count == 0


@pytest.mark.asyncio
async def test_build_model_list_includes_drop_params(manager):
    """drop_params=True is forwarded into litellm_params; absent field defaults to False."""
    manager._catalog_manager.list_entries.return_value = [
        {
            "id": "prov-a",
            "models": [
                {"id": "m1", "litellm_model": "anthropic/claude-sonnet-4-6", "drop_params": True},
                {"id": "m2", "litellm_model": "anthropic/claude-haiku-4-5"},
            ],
            "litellm_params_template": {},
        }
    ]
    manager._catalog_manager.resolve_params.return_value = {}

    result = await manager._build_model_list()

    assert result[0]["litellm_params"]["drop_params"] is True
    assert result[1]["litellm_params"]["drop_params"] is False


# ── Issue #1473: OpenAI count_tokens Responses API patch tests ────────────────


@pytest.fixture
def _restore_openai_counter():
    """Restore OpenAITokenCounter.should_use_token_counting_api after each test."""
    original = OpenAITokenCounter.should_use_token_counting_api
    yield original
    OpenAITokenCounter.should_use_token_counting_api = original


def _write_config(tmp_path: Path, *, flag: bool) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"features": {"disable_openai_responses_count_tokens_api": flag}})
    )
    return config_path


def _make_launch_context():
    """Return (modules, real_state, mock_app) for _launch_server tests.

    Uses a real SimpleNamespace for app.state so getattr fallbacks work
    correctly (MagicMock attributes are always truthy, masking missing keys).

    IMPORTANT: `litellm.proxy` must expose `proxy_server` as an explicit
    attribute so that `from litellm.proxy import proxy_server as _ps` inside
    _launch_server resolves to mock_proxy_server rather than a dynamic child
    MagicMock. Python's _handle_fromlist skips submodule import when hasattr
    returns True (always True for MagicMock), so we must wire the attribute.
    """
    real_state = types.SimpleNamespace()
    mock_app = MagicMock()
    mock_app.state = real_state
    mock_proxy_server = MagicMock()
    mock_proxy_server.app = mock_app

    # Wire proxy_server onto the litellm.proxy mock so `from litellm.proxy
    # import proxy_server` gets our mock_proxy_server.
    mock_litellm_proxy = MagicMock()
    mock_litellm_proxy.proxy_server = mock_proxy_server

    mock_server = MagicMock()
    mock_server.started = True
    mock_server.serve = AsyncMock(return_value=None)
    mock_uvicorn = MagicMock()
    mock_uvicorn.Server.return_value = mock_server
    mock_uvicorn.Config.return_value = MagicMock()

    modules = {
        "litellm": MagicMock(),
        "litellm.proxy": mock_litellm_proxy,
        "litellm.proxy.proxy_server": mock_proxy_server,
        "litellm.proxy._types": MagicMock(),
        "litellm.proxy.anthropic_endpoints": MagicMock(),
        "litellm.proxy.anthropic_endpoints.endpoints": MagicMock(),
        "uvicorn": mock_uvicorn,
    }
    return modules, real_state, mock_app


def _make_manager_stub(config_file: Path | None):
    """Build a minimal LiteLLMProxyManager without calling __init__."""
    from src.litellm_proxy_manager import LiteLLMProxyManager

    catalog = AsyncMock()
    catalog.list_entries.return_value = []
    catalog.resolve_params.return_value = {}

    mgr = LiteLLMProxyManager.__new__(LiteLLMProxyManager)
    mgr._catalog_manager = catalog
    mgr._vault = AsyncMock()
    mgr._port = 4000
    mgr._key_registry = {}
    mgr._routing_registry = {}
    mgr._server_task = None
    mgr._rebuild_lock = asyncio.Lock()
    mgr._running = False
    mgr._last_restart = None
    mgr._last_error = None
    mgr._model_count = 0
    mgr._config_file = config_file
    return mgr


@pytest.mark.asyncio
async def test_launch_server_disables_openai_responses_count_tokens_api_by_default(
    tmp_path, _restore_openai_counter
):
    original = _restore_openai_counter
    config_path = _write_config(tmp_path, flag=True)
    modules, real_state, _ = _make_launch_context()

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])

    assert OpenAITokenCounter().should_use_token_counting_api("openai") is False
    assert real_state._openai_count_tokens_disabled is True
    assert real_state._openai_count_tokens_original is original


@pytest.mark.asyncio
async def test_launch_server_does_not_patch_when_feature_flag_false(
    tmp_path, _restore_openai_counter
):
    original = _restore_openai_counter
    config_path = _write_config(tmp_path, flag=False)
    modules, real_state, _ = _make_launch_context()

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])

    assert not getattr(real_state, "_openai_count_tokens_disabled", False)
    assert OpenAITokenCounter.should_use_token_counting_api is original


@pytest.mark.asyncio
async def test_launch_server_patch_is_idempotent_across_rebuilds(
    tmp_path, _restore_openai_counter
):
    original = _restore_openai_counter
    config_path = _write_config(tmp_path, flag=True)
    modules, real_state, _ = _make_launch_context()

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])
        captured_first = real_state._openai_count_tokens_original

        mgr._server_task = None
        await mgr._launch_server([])
        captured_second = real_state._openai_count_tokens_original

    # Both captures must reference the real original, not the lambda.
    assert captured_first is original
    assert captured_second is original


@pytest.mark.asyncio
async def test_launch_server_preserves_anthropic_endpoints_registration(
    tmp_path, _restore_openai_counter
):
    config_path = _write_config(tmp_path, flag=True)
    modules, real_state, mock_app = _make_launch_context()

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])

    assert real_state._anthropic_endpoints_registered is True
    mock_app.include_router.assert_called_once()


@pytest.mark.asyncio
async def test_patch_does_not_affect_other_provider_token_counters(
    tmp_path, _restore_openai_counter
):
    config_path = _write_config(tmp_path, flag=True)
    modules, _, _ = _make_launch_context()

    before_anthropic = AnthropicTokenCounter.should_use_token_counting_api
    before_bedrock = BedrockTokenCounter.should_use_token_counting_api
    before_gemini = GoogleAIStudioTokenCounter.should_use_token_counting_api
    before_vertex = VertexAITokenCounter.should_use_token_counting_api

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])

    assert AnthropicTokenCounter.should_use_token_counting_api is before_anthropic
    assert BedrockTokenCounter.should_use_token_counting_api is before_bedrock
    assert GoogleAIStudioTokenCounter.should_use_token_counting_api is before_gemini
    assert VertexAITokenCounter.should_use_token_counting_api is before_vertex


@pytest.mark.asyncio
async def test_launch_server_unpatches_when_flag_flips_to_false(
    tmp_path, _restore_openai_counter
):
    original = _restore_openai_counter
    config_path = _write_config(tmp_path, flag=True)
    modules, real_state, _ = _make_launch_context()

    with patch.dict("sys.modules", modules):
        mgr = _make_manager_stub(config_path)
        await mgr._launch_server([])
        assert real_state._openai_count_tokens_disabled is True

        # Flip the flag and rebuild
        config_path.write_text(
            json.dumps({"features": {"disable_openai_responses_count_tokens_api": False}})
        )
        mgr._server_task = None
        await mgr._launch_server([])

    assert OpenAITokenCounter.should_use_token_counting_api is original
    assert real_state._openai_count_tokens_disabled is False


def test_features_config_round_trips_disable_openai_responses_count_tokens_api():
    from src.config_manager import AppConfig

    cfg_default = AppConfig.from_dict({})
    assert cfg_default.features.disable_openai_responses_count_tokens_api is True
    assert cfg_default.to_dict()["features"]["disable_openai_responses_count_tokens_api"] is True

    cfg_false = AppConfig.from_dict(
        {"features": {"disable_openai_responses_count_tokens_api": False}}
    )
    assert cfg_false.features.disable_openai_responses_count_tokens_api is False
    assert cfg_false.to_dict()["features"]["disable_openai_responses_count_tokens_api"] is False
