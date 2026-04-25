"""Tests for ClaudeSDK._resolve_env_vars() — issue #1126."""

import tempfile
from unittest.mock import patch

from src.claude_sdk import ClaudeSDK
from src.config_manager import AppConfig, BackgroundCallsConfig
from src.session_config import SessionConfig


def _make_sdk(config: SessionConfig | None = None, experimental: bool = False, extra_env: dict | None = None) -> ClaudeSDK:
    """Build a minimal ClaudeSDK instance for env-var testing."""
    with tempfile.TemporaryDirectory() as tmp:
        return ClaudeSDK(
            session_id="test-env-session",
            working_directory=tmp,
            config=config or SessionConfig(),
            experimental=experimental,
            extra_env=extra_env or {},
        )


def _all_suppressed_config() -> AppConfig:
    """AppConfig with all suppression flags ON."""
    return AppConfig(background_calls=BackgroundCallsConfig())


def _suppression_off_config() -> AppConfig:
    """AppConfig with all suppression flags OFF."""
    return AppConfig(
        background_calls=BackgroundCallsConfig(
            disable_auto_memory=False,
            disable_claudeai_mcp_servers=False,
            disable_background_tasks=False,
            disable_nonessential_traffic=False,
            disable_cron=False,
            disable_feedback_survey=False,
            disable_telemetry=False,
            subprocess_env_scrub=False,
            skip_version_check=False,
            dont_inherit_env=False,
        )
    )


class TestResolveEnvVars:
    def test_enable_tasks_always_set(self):
        sdk = _make_sdk()
        with patch("src.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_ENABLE_TASKS"] == "true"

    def test_global_defaults_applied_to_env_dict(self):
        # Use session config that does not opt back in to anything
        config = SessionConfig(auto_memory_mode="disabled", enable_claudeai_mcp_servers=False)
        sdk = _make_sdk(config=config)
        with patch("src.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        assert env["CLAUDE_CODE_DISABLE_CRON"] == "1"
        assert env["CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY"] == "1"
        assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"
        assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
        assert env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"

    def test_auto_memory_claude_mode_removes_disable_var(self):
        config = SessionConfig(auto_memory_mode="claude")
        sdk = _make_sdk(config=config)
        with patch("src.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in env

    def test_auto_memory_session_mode_keeps_disable_var(self):
        config = SessionConfig(auto_memory_mode="session")
        sdk = _make_sdk(config=config)
        with patch("src.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_enable_claudeai_mcp_true_removes_global_disable(self):
        config = SessionConfig(enable_claudeai_mcp_servers=True)
        sdk = _make_sdk(config=config)
        with patch("src.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "ENABLE_CLAUDEAI_MCP_SERVERS" not in env

    def test_enable_claudeai_mcp_false_sets_disable(self):
        config = SessionConfig(enable_claudeai_mcp_servers=False)
        sdk = _make_sdk(config=config)
        with patch("src.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"

    def test_extra_env_overrides_all_layers(self):
        sdk = _make_sdk(
            extra_env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0", "MY_CUSTOM": "yes"}
        )
        with patch("src.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "0"
        assert env["MY_CUSTOM"] == "yes"

    def test_experimental_flag_adds_agent_teams_var(self):
        sdk = _make_sdk(experimental=True)
        with patch("src.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"
