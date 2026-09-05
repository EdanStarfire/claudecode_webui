"""
Regression tests for issue #1825 — LiteLLM proxy port defaults to a hardcoded 4000,
causing two backend instances on one host to collide. The fix makes dynamic
OS-assigned allocation the default, with CLI flag > persisted providers.json value >
dynamic port as the resolution precedence, and never persists the dynamic pick.
"""

from __future__ import annotations

import json
import socket

import pytest

from backend.web_server import BackendApp, _read_litellm_port_sync

# ---------------------------------------------------------------------------
# _read_litellm_port_sync
# ---------------------------------------------------------------------------


def test_read_litellm_port_sync_returns_none_when_nothing_persisted(tmp_path):
    assert _read_litellm_port_sync(tmp_path) is None


def test_read_litellm_port_sync_returns_persisted_value(tmp_path):
    (tmp_path / "providers.json").write_text(
        json.dumps({"entries": [], "litellm_port": 4321, "pending_changes": False}),
        encoding="utf-8",
    )
    assert _read_litellm_port_sync(tmp_path) == 4321


# ---------------------------------------------------------------------------
# BackendApp resolution precedence
# ---------------------------------------------------------------------------


def test_backend_app_default_resolves_dynamic_bindable_port(tmp_path):
    """No CLI flag, no persisted config — BackendApp picks a real, bindable port."""
    app = BackendApp(data_dir=tmp_path)
    port = app.litellm_proxy_manager.port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_two_backend_app_instances_get_different_dynamic_ports(tmp_path):
    """Two BackendApp instances with no explicit litellm config/CLI flag don't collide."""
    app_a = BackendApp(data_dir=tmp_path / "a")
    app_b = BackendApp(data_dir=tmp_path / "b")

    assert app_a.litellm_proxy_manager.port != app_b.litellm_proxy_manager.port


def test_cli_pinned_port_takes_precedence_over_persisted_config(tmp_path):
    """An explicit --litellm-port pin wins even when providers.json has its own value."""
    (tmp_path / "providers.json").write_text(
        json.dumps({"entries": [], "litellm_port": 5555, "pending_changes": False}),
        encoding="utf-8",
    )

    app = BackendApp(data_dir=tmp_path, litellm_port=9999)

    assert app.litellm_proxy_manager.port == 9999


def test_persisted_config_used_when_no_cli_flag(tmp_path):
    """A persisted providers.json value is honored when no CLI flag overrides it."""
    (tmp_path / "providers.json").write_text(
        json.dumps({"entries": [], "litellm_port": 5555, "pending_changes": False}),
        encoding="utf-8",
    )

    app = BackendApp(data_dir=tmp_path)

    assert app.litellm_proxy_manager.port == 5555


def test_dynamic_port_never_persisted_to_providers_json(tmp_path):
    """A dynamically-allocated default port must not be written to providers.json —
    only an explicit CLI flag or an explicit prior persisted value should ever
    appear there, so restarts keep picking fresh ports."""
    BackendApp(data_dir=tmp_path)

    providers_file = tmp_path / "providers.json"
    assert not providers_file.exists()


# ---------------------------------------------------------------------------
# Crash regression: port collision must raise RuntimeError, not SystemExit,
# and BackendApp.initialize() must swallow it and complete anyway.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_litellm_start_raises_runtime_error_not_system_exit_on_port_collision(tmp_path):
    app = BackendApp(data_dir=tmp_path)
    port = app.litellm_proxy_manager.port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("0.0.0.0", port))
        blocker.listen(1)

        with pytest.raises(RuntimeError):
            await app.litellm_proxy_manager.start()


@pytest.mark.asyncio
async def test_backend_app_initialize_survives_litellm_port_collision(tmp_path):
    """The original crash from issue #1825: a bind failure inside the LiteLLM proxy
    manager must not take down BackendApp startup — native (non-catalog) sessions
    still need to work."""
    app = BackendApp(data_dir=tmp_path)
    port = app.litellm_proxy_manager.port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("0.0.0.0", port))
        blocker.listen(1)

        await app.initialize()  # must not raise

    assert app.litellm_proxy_manager.is_running is False
    await app.cleanup()
