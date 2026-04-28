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

import os
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


# ---------------------------------------------------------------------------
# 7. Issue #1179 — proxy_extra_mounts plumbing
# ---------------------------------------------------------------------------

def test_proxy_extra_mounts_env_var_set_when_provided():
    """docker_proxy_extra_mounts list is joined and set as CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS."""
    _, env = resolve_docker_cli_path(
        docker_proxy_extra_mounts=["/host/a:/etc/proxy/a:ro", "/host/b:/etc/proxy/b:ro"],
        proxy_image="claude-proxy:local",
    )
    assert env.get("CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS") == "/host/a:/etc/proxy/a:ro,/host/b:/etc/proxy/b:ro"


def test_proxy_extra_mounts_env_var_unset_when_empty():
    """CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS is absent when docker_proxy_extra_mounts is None or []."""
    _, env = resolve_docker_cli_path(docker_proxy_extra_mounts=None)
    assert "CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS" not in env

    _, env = resolve_docker_cli_path(docker_proxy_extra_mounts=[])
    assert "CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS" not in env


def test_proxy_extra_mounts_does_not_leak_into_agent_mounts():
    """Agent and proxy mount env vars are populated independently without cross-contamination."""
    _, env = resolve_docker_cli_path(
        docker_extra_mounts=["/agent/x:/agent/x"],
        docker_proxy_extra_mounts=["/host/token:/etc/proxy/session_token:ro"],
    )
    assert env.get("CLAUDE_DOCKER_EXTRA_MOUNTS") == "/agent/x:/agent/x"
    assert env.get("CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS") == "/host/token:/etc/proxy/session_token:ro"


@pytest.mark.asyncio
async def test_session_coordinator_routes_token_to_proxy_only(tmp_path):
    """
    session_token and session_id mount specs land in CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS,
    not CLAUDE_DOCKER_EXTRA_MOUNTS, when docker_proxy_enabled=True.
    """
    from unittest.mock import AsyncMock, MagicMock

    from src.session_coordinator import SessionCoordinator

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    coordinator = SessionCoordinator(data_dir=tmp_path)
    await coordinator.initialize()

    project = await coordinator.project_manager.create_project(
        name="Proxy Token Test",
        working_directory=str(workspace),
    )

    config = SessionConfig(
        docker_enabled=True,
        docker_proxy_enabled=True,
    )
    session_id = await coordinator.create_session(
        session_id="test-1179-token",
        project_id=project.project_id,
        config=config,
    )

    captured: dict = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return ("/mock/cli", {})

    mock_sdk = MagicMock()
    mock_sdk.is_running.return_value = False
    mock_sdk.start = AsyncMock(return_value=True)
    mock_sdk.auto_approval_callback = None

    coordinator._sdk_factory = lambda **kwargs: mock_sdk

    with patch("src.docker_utils.resolve_docker_cli_path", side_effect=fake_resolve):
        await coordinator.start_session(session_id)

    agent_mounts: list[str] = captured.get("docker_extra_mounts") or []
    proxy_mounts: list[str] = captured.get("docker_proxy_extra_mounts") or []

    assert any("/etc/proxy/session_token" in m for m in proxy_mounts), (
        f"session_token not found in proxy mounts: {proxy_mounts}"
    )
    assert any("/etc/proxy/session_id" in m for m in proxy_mounts), (
        f"session_id not found in proxy mounts: {proxy_mounts}"
    )
    assert not any("/etc/proxy/session_token" in m for m in agent_mounts), (
        f"session_token leaked into agent mounts: {agent_mounts}"
    )
    assert not any("/etc/proxy/session_id" in m for m in agent_mounts), (
        f"session_id leaked into agent mounts: {agent_mounts}"
    )


@pytest.mark.asyncio
async def test_issue_1181_proxy_token_files_world_readable(tmp_path):
    """
    Issue #1181: session_token and session_id files must be 0o644 so mitmdump
    inside the proxy container (running as a different UID) can read them.
    """
    import stat
    from unittest.mock import AsyncMock, MagicMock

    from src.session_coordinator import SessionCoordinator

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    coordinator = SessionCoordinator(data_dir=tmp_path)
    await coordinator.initialize()

    project = await coordinator.project_manager.create_project(
        name="Chmod Test",
        working_directory=str(workspace),
    )

    config = SessionConfig(
        docker_enabled=True,
        docker_proxy_enabled=True,
    )
    session_id = await coordinator.create_session(
        session_id="test-1181-chmod",
        project_id=project.project_id,
        config=config,
    )

    captured: dict = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return ("/mock/cli", {})

    mock_sdk = MagicMock()
    mock_sdk.is_running.return_value = False
    mock_sdk.start = AsyncMock(return_value=True)
    mock_sdk.auto_approval_callback = None

    coordinator._sdk_factory = lambda **kwargs: mock_sdk

    with patch("src.docker_utils.resolve_docker_cli_path", side_effect=fake_resolve):
        await coordinator.start_session(session_id)

    proxy_mounts: list[str] = captured.get("docker_proxy_extra_mounts") or []

    token_host_path = None
    session_id_host_path = None
    for mount in proxy_mounts:
        parts = mount.split(":")
        host_path = parts[0]
        container_path = parts[1] if len(parts) >= 2 else ""
        if container_path == "/etc/proxy/session_token":
            token_host_path = host_path
        elif container_path == "/etc/proxy/session_id":
            session_id_host_path = host_path

    assert token_host_path is not None, f"session_token mount not found in: {proxy_mounts}"
    assert session_id_host_path is not None, f"session_id mount not found in: {proxy_mounts}"

    token_mode = stat.S_IMODE(os.stat(token_host_path).st_mode)
    session_id_mode = stat.S_IMODE(os.stat(session_id_host_path).st_mode)

    assert token_mode == 0o644, f"session_token file mode is {oct(token_mode)}, expected 0o644"
    assert session_id_mode == 0o644, f"session_id file mode is {oct(session_id_mode)}, expected 0o644"


def test_proxy_extra_mounts_dedup():
    """
    Coordinator dedup logic: when proxy_extra_mounts has duplicate container destinations,
    first-seen wins. Verify the algorithm mirrors the agent-mounts dedup (issue #1089).
    """
    # Simulate the coordinator's proxy_extra_mounts dedup algorithm verbatim.
    proxy_extra_mounts = [
        "/host/path1:/etc/proxy/session_token:ro",
        "/host/path2:/etc/proxy/session_token:ro",  # duplicate container dest
        "/host/session_id:/etc/proxy/session_id:ro",
    ]

    _seen: set[str] = set()
    _deduped: list[str] = []
    for _mount in proxy_extra_mounts:
        _parts = _mount.split(":", 2)
        _container_path = _parts[1] if len(_parts) >= 2 else _mount
        if _container_path not in _seen:
            _seen.add(_container_path)
            _deduped.append(_mount)

    assert len(_deduped) == 2
    assert _deduped[0] == "/host/path1:/etc/proxy/session_token:ro"
    assert _deduped[1] == "/host/session_id:/etc/proxy/session_id:ro"

    # Verify the deduped list translates correctly through resolve_docker_cli_path.
    _, env = resolve_docker_cli_path(docker_proxy_extra_mounts=_deduped)
    val = env.get("CLAUDE_DOCKER_PROXY_EXTRA_MOUNTS", "")
    assert "/host/path1:/etc/proxy/session_token:ro" in val
    assert "/host/path2:/etc/proxy/session_token:ro" not in val
