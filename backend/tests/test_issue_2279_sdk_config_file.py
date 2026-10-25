"""Issue #2279 AC3: ClaudeSDK reads the tenant's config file, not the global default."""

import json
import tempfile
from pathlib import Path

import pytest

from backend.claude_sdk import ClaudeSDK
from backend.config_manager import AppConfig, BackgroundCallsConfig, FeaturesConfig, save_config
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.tenant_context import TenantContext
from backend.tests.test_session_provider_routing import _create_session, _make_sdk_factory


def _write_tmp_config(tmp_path: Path) -> Path:
    """A config whose flags differ from every default."""
    path = tmp_path / "tenant_config.json"
    save_config(
        AppConfig(
            background_calls=BackgroundCallsConfig(disable_cron=False, disable_telemetry=False),
            features=FeaturesConfig(block_cross_session_messaging=True),
        ),
        path,
    )
    return path


def _sdk(config_file: Path | None) -> ClaudeSDK:
    with tempfile.TemporaryDirectory() as wd:
        return ClaudeSDK(
            session_id="s2279",
            working_directory=wd,
            config=SessionConfig(),
            config_file=config_file,
        )


def test_resolve_env_vars_reads_given_config_file(tmp_path):
    env = _sdk(_write_tmp_config(tmp_path))._resolve_env_vars()
    # Default config has disable_cron / disable_telemetry ON; the tmp file turns them OFF.
    assert env.get("CLAUDE_CODE_DISABLE_CRON") != "1"
    assert env.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "0"


def test_resolve_env_vars_default_file_differs(tmp_path, monkeypatch):
    """Control: with no config_file the (patched-in) default file is used instead."""
    default = tmp_path / "default.json"
    save_config(AppConfig(), default)
    monkeypatch.setattr("backend.config_manager.default_config_file", lambda: default)
    env = _sdk(None)._resolve_env_vars()
    assert env["CLAUDE_CODE_DISABLE_CRON"] == "1"


def test_get_sdk_options_reads_given_config_file(tmp_path):
    sdk = _sdk(_write_tmp_config(tmp_path))
    opts = sdk._get_sdk_options()
    try:
        assert opts.settings is not None
        # block_cross_session_messaging=True only in the tmp file -> reflected in payload
        assert json.loads(Path(opts.settings).read_text())["crossSessionInbound"] == "refuse"
    finally:
        sdk._cleanup_settings_temp_file()


@pytest.mark.asyncio
async def test_launcher_passes_tenant_config_file(tmp_path):
    config_file = _write_tmp_config(tmp_path)
    tenant = TenantContext(data_dir=tmp_path / "data", config_file=config_file, host="127.0.0.1")
    coord = SessionCoordinator(tenant_context=tenant)
    await coord.initialize()
    try:
        session_id = await _create_session(coord, SessionConfig())
        factory, _ = _make_sdk_factory()
        coord.set_sdk_factory(factory)
        await coord.start_session(session_id)
        assert factory.call_args.kwargs["config_file"] == config_file
    finally:
        await coord.cleanup()
