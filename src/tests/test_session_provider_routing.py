"""Tests for LiteLLM catalog routing in session_coordinator — issue #1427 Phase 2."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from ..session_config import SessionConfig
from ..session_coordinator import SessionCoordinator


def _make_proxy_manager(port: int = 4000) -> MagicMock:
    """Return a mock LiteLLMProxyManager."""
    mgr = MagicMock()
    mgr.port = port
    mgr.is_running = True
    mgr.register_session_key.return_value = "lc-test-virtual-key"
    mgr.register_session_routing = MagicMock()
    mgr.unregister_session_key = MagicMock()
    mgr.unregister_session_routing = MagicMock()
    return mgr


def _make_sdk_factory():
    """Return (factory, mock_instance) pair."""
    mock_sdk = AsyncMock()
    mock_sdk.start.return_value = True
    mock_sdk.is_running.return_value = False
    factory = Mock(return_value=mock_sdk)
    return factory, mock_sdk


@pytest.fixture
async def coordinator():
    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(Path(tmp))
        await coord.initialize()
        yield coord
        await coord.cleanup()


async def _create_session(coordinator, config: SessionConfig, working_dir: str = "/tmp"):
    """Helper to create a project+session and return session_id."""
    project = await coordinator.project_manager.create_project(
        name="Test", working_directory=working_dir
    )
    import uuid
    session_id = str(uuid.uuid4())
    await coordinator.create_session(
        session_id=session_id,
        project_id=project.project_id,
        config=config,
    )
    return session_id


# ── P2-A: Non-Docker + catalog selected ───────────────────────────────────────


@pytest.mark.asyncio
async def test_non_docker_catalog_injects_base_url_and_api_key(coordinator):
    """SDK factory receives ANTHROPIC_BASE_URL + ANTHROPIC_API_KEY in extra_env."""
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="bedrock",
        provider_model_id="claude-3-5-sonnet",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    call_kwargs = factory.call_args.kwargs
    extra_env = call_kwargs.get("extra_env") or {}
    assert extra_env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:4000/"
    assert extra_env.get("ANTHROPIC_API_KEY") == "lc-test-virtual-key"


@pytest.mark.asyncio
async def test_non_docker_catalog_overrides_model_to_alias(coordinator):
    """SDK config.model is set to '{catalog_id}--{model_id}'."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="bedrock",
        provider_model_id="claude-3-5-sonnet",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    sdk_config = factory.call_args.kwargs["config"]
    assert sdk_config.model == "bedrock--claude-3-5-sonnet"


@pytest.mark.asyncio
async def test_non_docker_catalog_calls_register_session_key(coordinator):
    """register_session_key is called once with the session_id."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="my-catalog",
        provider_model_id="my-model",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    proxy.register_session_key.assert_called_once_with(session_id)


@pytest.mark.asyncio
async def test_non_docker_catalog_does_not_call_register_session_routing(coordinator):
    """register_session_routing is NOT called for non-Docker sessions."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="cat",
        provider_model_id="mod",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    proxy.register_session_routing.assert_not_called()


# ── P2-B: Non-Docker + no catalog (native pass-through) ───────────────────────


@pytest.mark.asyncio
async def test_native_session_no_catalog_no_url_override(coordinator):
    """Sessions without catalog selection get no ANTHROPIC_BASE_URL override."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig()  # No provider_catalog_id
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    call_kwargs = factory.call_args.kwargs
    extra_env = call_kwargs.get("extra_env") or {}
    assert "ANTHROPIC_BASE_URL" not in extra_env
    assert "ANTHROPIC_API_KEY" not in extra_env


@pytest.mark.asyncio
async def test_native_session_model_unchanged(coordinator):
    """Sessions without catalog selection keep their original model string."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(model="claude-opus-4-7")
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    sdk_config = factory.call_args.kwargs["config"]
    assert sdk_config.model == "claude-opus-4-7"


# ── P2-C: extra_env reaches non-Docker SDK (regression fix) ───────────────────


@pytest.mark.asyncio
async def test_extra_env_reaches_non_docker_sdk(coordinator):
    """Non-Docker sessions receive effective_config.extra_env (regression test)."""
    config = SessionConfig(extra_env={"MY_TEST_VAR": "hello"})
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    call_kwargs = factory.call_args.kwargs
    extra_env = call_kwargs.get("extra_env") or {}
    assert extra_env.get("MY_TEST_VAR") == "hello"


# ── P2-D: Virtual key lifecycle ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminate_unregisters_key_and_routing(coordinator):
    """terminate_session calls unregister_session_key and unregister_session_routing."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="cat",
        provider_model_id="mod",
    )
    session_id = await _create_session(coordinator, config)

    factory, mock_sdk = _make_sdk_factory()
    mock_sdk.terminate = AsyncMock()
    coordinator.set_sdk_factory(factory)
    await coordinator.start_session(session_id)

    await coordinator.terminate_session(session_id)

    proxy.unregister_session_key.assert_called_with(session_id)
    proxy.unregister_session_routing.assert_called_with(session_id)


# ── Backward compat: no proxy manager injected ────────────────────────────────


@pytest.mark.asyncio
async def test_no_proxy_manager_start_is_noop(coordinator):
    """start_session with no proxy manager and catalog set completes without error."""
    assert coordinator.litellm_proxy_manager is None

    config = SessionConfig(
        provider_catalog_id="cat",
        provider_model_id="mod",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    # Should not raise
    result = await coordinator.start_session(session_id)
    assert result is True


@pytest.mark.asyncio
async def test_no_proxy_manager_terminate_is_noop(coordinator):
    """terminate_session with no proxy manager does not raise."""
    assert coordinator.litellm_proxy_manager is None

    config = SessionConfig()
    session_id = await _create_session(coordinator, config)

    factory, mock_sdk = _make_sdk_factory()
    mock_sdk.terminate = AsyncMock()
    coordinator.set_sdk_factory(factory)
    await coordinator.start_session(session_id)

    # Should not raise
    await coordinator.terminate_session(session_id)


# ── Docker + catalog: routing stashed, not in extra_env ───────────────────────


@pytest.mark.asyncio
async def test_docker_catalog_routes_via_registry_not_env(coordinator):
    """Docker sessions with catalog call register_session_routing, not extra_env injection.

    Using cli_path to bypass the docker-resolve block while keeping docker_enabled=True,
    so the catalog injection sees docker_enabled and calls register_session_routing.
    """
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    # cli_path bypasses the `if effective_config.docker_enabled and not effective_config.cli_path:`
    # block, but docker_enabled=True is still visible to the catalog injection code.
    config = SessionConfig(
        provider_catalog_id="cat",
        provider_model_id="mod",
        docker_enabled=True,
        cli_path="/usr/bin/claude",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    proxy.register_session_routing.assert_called_once_with(
        session_id, "lc-test-virtual-key", "http://127.0.0.1:4000/",
        model_map={}, default_model="cat--mod",
    )

    # ANTHROPIC_BASE_URL must NOT appear in extra_env for Docker sessions
    call_kwargs = factory.call_args.kwargs
    extra_env = call_kwargs.get("extra_env") or {}
    assert "ANTHROPIC_BASE_URL" not in extra_env


# ── Issue #1469: Docker single-model — SDK model UNSET ───────────────────────


@pytest.mark.asyncio
async def test_issue_1469_docker_single_model_sdk_model_unset(coordinator):
    """Docker single-provider: ClaudeAgentOptions.model must NOT be set to the alias.

    R1 backward-compat risk: proxy rewrites the body; SDK model left unset.
    """
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="bedrock",
        provider_model_id="claude-sonnet",
        docker_enabled=True,
        cli_path="/usr/bin/claude",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    sdk_config = factory.call_args.kwargs["config"]
    # model must NOT be set to the alias — proxy body rewrite handles it
    assert sdk_config.model is None or sdk_config.model not in ("bedrock--claude-sonnet",)


@pytest.mark.asyncio
async def test_issue_1469_docker_single_model_register_routing_called_with_defaults(coordinator):
    """Docker single-model calls register_session_routing with model_map={}, correct alias."""
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="openai",
        provider_model_id="gpt-4o",
        docker_enabled=True,
        cli_path="/usr/bin/claude",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    proxy.register_session_routing.assert_called_once_with(
        session_id, "lc-test-virtual-key", "http://127.0.0.1:4000/",
        model_map={}, default_model="openai--gpt-4o",
    )


# ── Issue #1469: Docker tier breakout ────────────────────────────────────────


def _tier_config(docker_enabled=True):
    return SessionConfig(
        provider_haiku_catalog_id="bedrock",
        provider_haiku_model_id="haiku-fast",
        provider_sonnet_catalog_id="bedrock",
        provider_sonnet_model_id="sonnet-balanced",
        provider_opus_catalog_id="bedrock",
        provider_opus_model_id="opus-power",
        provider_default_tier="sonnet",
        docker_enabled=docker_enabled,
        cli_path="/usr/bin/claude",
    )


@pytest.mark.asyncio
async def test_issue_1469_docker_tier_sdk_model_is_short_alias(coordinator):
    """Docker tier breakout: SDK model set to short alias for default tier."""
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    session_id = await _create_session(coordinator, _tier_config())

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    sdk_config = factory.call_args.kwargs["config"]
    # SDK model = short alias for default tier ("sonnet")
    assert sdk_config.model == "sonnet"


@pytest.mark.asyncio
async def test_issue_1469_docker_tier_register_routing_has_model_map(coordinator):
    """Docker tier breakout: register_session_routing called with populated model_map."""
    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    session_id = await _create_session(coordinator, _tier_config())

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    call_kwargs = proxy.register_session_routing.call_args.kwargs
    model_map = call_kwargs["model_map"]
    assert model_map["haiku"] == "bedrock--haiku-fast"
    assert model_map["sonnet"] == "bedrock--sonnet-balanced"
    assert model_map["opus"] == "bedrock--opus-power"
    assert model_map["default"] == "bedrock--sonnet-balanced"
    assert call_kwargs["default_model"] == "bedrock--sonnet-balanced"


# ── Issue #1469: Non-Docker + tier breakout → warning + fallback ──────────────


@pytest.mark.asyncio
async def test_issue_1469_non_docker_tier_warns_and_falls_back(coordinator, caplog):
    """Non-Docker + tier breakout: logs warning, falls back to default-tier single-model."""
    import logging

    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    # No docker_enabled, but tier fields set
    config = SessionConfig(
        provider_haiku_catalog_id="bedrock",
        provider_haiku_model_id="haiku-fast",
        provider_sonnet_catalog_id="bedrock",
        provider_sonnet_model_id="sonnet-balanced",
        provider_opus_catalog_id="bedrock",
        provider_opus_model_id="opus-power",
        provider_default_tier="sonnet",
        docker_enabled=False,
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    with caplog.at_level(logging.WARNING):
        await coordinator.start_session(session_id)

    # Warning logged
    assert any("per-tier routing" in r.message for r in caplog.records)

    # Falls back to default-tier alias, passed as SDK model
    sdk_config = factory.call_args.kwargs["config"]
    assert sdk_config.model == "bedrock--sonnet-balanced"


# ── Issue #1467: attribution header suppression ───────────────────────────────


@pytest.mark.asyncio
async def test_non_docker_catalog_suppresses_attribution_header(coordinator):
    """Non-Docker catalog sessions receive CLAUDE_CODE_ATTRIBUTION_HEADER=0 in extra_env."""
    proxy = _make_proxy_manager()
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="bedrock",
        provider_model_id="claude-3-5-sonnet",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    extra_env = factory.call_args.kwargs.get("extra_env") or {}
    assert extra_env.get("CLAUDE_CODE_ATTRIBUTION_HEADER") == "0"


@pytest.mark.asyncio
async def test_native_session_no_attribution_header(coordinator):
    """Sessions without a catalog provider do NOT get the attribution header suppressed."""
    config = SessionConfig()
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    extra_env = factory.call_args.kwargs.get("extra_env") or {}
    assert "CLAUDE_CODE_ATTRIBUTION_HEADER" not in extra_env


@pytest.mark.asyncio
async def test_docker_catalog_attribution_header_in_extra_env_json(coordinator):
    """Docker catalog sessions encode CLAUDE_CODE_ATTRIBUTION_HEADER=0 in CLAUDE_DOCKER_EXTRA_ENV."""
    import json

    proxy = _make_proxy_manager(port=4000)
    coordinator.litellm_proxy_manager = proxy

    config = SessionConfig(
        provider_catalog_id="cat",
        provider_model_id="mod",
        docker_enabled=True,
        cli_path="/usr/bin/claude",
    )
    session_id = await _create_session(coordinator, config)

    factory, _ = _make_sdk_factory()
    coordinator.set_sdk_factory(factory)

    await coordinator.start_session(session_id)

    extra_env = factory.call_args.kwargs.get("extra_env") or {}
    raw = extra_env.get("CLAUDE_DOCKER_EXTRA_ENV", "{}")
    container_env = json.loads(raw)
    assert container_env.get("CLAUDE_CODE_ATTRIBUTION_HEADER") == "0"
