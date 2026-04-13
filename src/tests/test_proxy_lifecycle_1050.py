"""
Unit tests for issue #1050 — backend proxy lifecycle management (sidecar model).

Tests:
1. Config round-trip
2. Config backward compat (no 'proxy' key)
3. SessionConfig field propagation → SessionInfo
4. SessionInfo backward compat (no docker_proxy_enabled key)
5. Effective image resolution matrix
6. docker_utils env var logic
"""

from unittest.mock import patch

import pytest

from src.config_manager import AppConfig, ProxyConfig
from src.docker_utils import resolve_docker_cli_path
from src.session_config import SessionConfig

# ---------------------------------------------------------------------------
# 1. Config round-trip
# ---------------------------------------------------------------------------

def test_proxy_config_round_trip():
    """ProxyConfig survives to_dict → from_dict with custom image."""
    config = AppConfig()
    config.proxy.proxy_image = "custom-proxy:v1"

    data = config.to_dict()
    assert data["proxy"]["proxy_image"] == "custom-proxy:v1"

    restored = AppConfig.from_dict(data)
    assert restored.proxy.proxy_image == "custom-proxy:v1"


def test_proxy_config_default():
    """Default proxy image is 'claude-proxy:local'."""
    config = AppConfig()
    assert config.proxy.proxy_image == "claude-proxy:local"

    data = config.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.proxy.proxy_image == "claude-proxy:local"


# ---------------------------------------------------------------------------
# 2. Config backward compat
# ---------------------------------------------------------------------------

def test_proxy_config_backward_compat_no_proxy_key():
    """AppConfig.from_dict without 'proxy' key defaults to ProxyConfig()."""
    data = {
        "networking": {"allow_network_binding": False, "acknowledged_risk": False},
        "features": {"skill_sync_enabled": True},
        # 'proxy' key intentionally absent
    }
    config = AppConfig.from_dict(data)
    assert isinstance(config.proxy, ProxyConfig)
    assert config.proxy.proxy_image == "claude-proxy:local"


# ---------------------------------------------------------------------------
# 3. SessionConfig field propagation → SessionInfo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_config_propagation_to_session_info(tmp_path):
    """docker_proxy_enabled and docker_proxy_image propagate from SessionConfig to SessionInfo."""
    from src.session_manager import SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(
        docker_proxy_enabled=True,
        docker_proxy_image="custom-proxy:v2",
    )
    await sm.create_session("test-session-1", config)

    info = await sm.get_session_info("test-session-1")
    assert info.docker_proxy_enabled is True
    assert info.docker_proxy_image == "custom-proxy:v2"

    # Also verify to_dict exposes both fields
    d = info.to_dict()
    assert d["docker_proxy_enabled"] is True
    assert d["docker_proxy_image"] == "custom-proxy:v2"


# ---------------------------------------------------------------------------
# 4. SessionInfo backward compat
# ---------------------------------------------------------------------------

def test_session_info_backward_compat_no_proxy_enabled():
    """SessionInfo.from_dict without docker_proxy_enabled defaults to False."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    data = {
        "session_id": "back-compat-test",
        "state": SessionState.CREATED.value,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        # docker_proxy_enabled intentionally absent
        "docker_proxy_image": "some-image:v1",
    }
    info = SessionInfo.from_dict(data)
    assert info.docker_proxy_enabled is False
    assert info.docker_proxy_image == "some-image:v1"


# ---------------------------------------------------------------------------
# 5. Effective image resolution matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_effective_proxy_image_disabled(tmp_path):
    """docker_proxy_enabled=False → proxy_image=None (no proxy)."""
    from src.session_manager import SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(docker_proxy_enabled=False, docker_proxy_image="some-image:v1")
    await sm.create_session("eff-test-1", config)
    info = await sm.get_session_info("eff-test-1")

    # Simulate the resolution logic from SessionCoordinator
    proxy_image = (
        (info.docker_proxy_image or "claude-proxy:local")
        if info.docker_proxy_enabled
        else None
    )
    assert proxy_image is None


@pytest.mark.asyncio
async def test_effective_proxy_image_enabled_default(tmp_path):
    """docker_proxy_enabled=True, docker_proxy_image=None → uses app config default."""
    from src.session_manager import SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(docker_proxy_enabled=True, docker_proxy_image=None)
    await sm.create_session("eff-test-2", config)
    info = await sm.get_session_info("eff-test-2")

    with patch("src.config_manager.load_config") as mock_load:
        mock_app_config = AppConfig()
        mock_app_config.proxy.proxy_image = "claude-proxy:local"
        mock_load.return_value = mock_app_config

        proxy_image = (
            (info.docker_proxy_image or mock_app_config.proxy.proxy_image)
            if info.docker_proxy_enabled
            else None
        )
    assert proxy_image == "claude-proxy:local"


@pytest.mark.asyncio
async def test_effective_proxy_image_enabled_override(tmp_path):
    """docker_proxy_enabled=True, docker_proxy_image='custom:v2' → uses override."""
    from src.session_manager import SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(docker_proxy_enabled=True, docker_proxy_image="custom:v2")
    await sm.create_session("eff-test-3", config)
    info = await sm.get_session_info("eff-test-3")

    proxy_image = (
        (info.docker_proxy_image or "claude-proxy:local")
        if info.docker_proxy_enabled
        else None
    )
    assert proxy_image == "custom:v2"


# ---------------------------------------------------------------------------
# 6. docker_utils env var logic
# ---------------------------------------------------------------------------

def test_resolve_docker_cli_path_proxy_image_set():
    """resolve_docker_cli_path with proxy_image='test:img' sets CLAUDE_DOCKER_PROXY_IMAGE."""
    _, env = resolve_docker_cli_path(proxy_image="test:img")
    assert env.get("CLAUDE_DOCKER_PROXY_IMAGE") == "test:img"


def test_resolve_docker_cli_path_proxy_image_none():
    """resolve_docker_cli_path with proxy_image=None does not set CLAUDE_DOCKER_PROXY_IMAGE."""
    _, env = resolve_docker_cli_path(proxy_image=None)
    assert "CLAUDE_DOCKER_PROXY_IMAGE" not in env
