"""Tests for ClaudeSDK._resolve_env_vars() — issue #1126."""

import tempfile
from unittest.mock import patch

from backend.claude_sdk import ClaudeSDK
from backend.config_manager import AppConfig, BackgroundCallsConfig, FeaturesConfig
from backend.session_config import SessionConfig


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
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_ENABLE_TASKS"] == "true"

    def test_global_defaults_applied_to_env_dict(self):
        # Use session config that does not opt back in to anything
        config = SessionConfig(auto_memory_mode="disabled", enable_claudeai_mcp_servers=False)
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        assert env["CLAUDE_CODE_DISABLE_CRON"] == "1"
        assert env["CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY"] == "1"
        assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"
        assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in env
        assert env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] == "1"

    def test_auto_memory_claude_mode_removes_disable_var(self):
        config = SessionConfig(auto_memory_mode="claude")
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in env

    def test_auto_memory_session_mode_does_not_set_disable_var(self):
        # Issue #1408: session mode must clear CLAUDE_CODE_DISABLE_AUTO_MEMORY even when global
        # suppression (disable_auto_memory=True) has already set it. Using _suppression_off_config()
        # was vacuous — the var was never set in the first place.
        config = SessionConfig(auto_memory_mode="session")
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_DISABLE_AUTO_MEMORY" not in env

    def test_auto_memory_disabled_mode_sets_disable_var(self):
        config = SessionConfig(auto_memory_mode="disabled")
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"

    def test_enable_claudeai_mcp_true_removes_global_disable(self):
        config = SessionConfig(enable_claudeai_mcp_servers=True)
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "ENABLE_CLAUDEAI_MCP_SERVERS" not in env

    def test_enable_claudeai_mcp_false_sets_disable(self):
        config = SessionConfig(enable_claudeai_mcp_servers=False)
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"

    def test_extra_env_overrides_all_layers(self):
        sdk = _make_sdk(
            extra_env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0", "MY_CUSTOM": "yes"}
        )
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "0"
        assert env["MY_CUSTOM"] == "yes"

    def test_experimental_flag_adds_agent_teams_var(self):
        sdk = _make_sdk(experimental=True)
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"

    def test_issue_1427_extra_env_reaches_non_docker_sdk(self):
        """Regression: extra_env passed to ClaudeSDK constructor appears in _resolve_env_vars() output."""
        sdk = _make_sdk(extra_env={"FOO": "bar", "ANOTHER": "val"})
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env.get("FOO") == "bar"
        assert env.get("ANOTHER") == "val"


class TestMaxSubagentSpawnDepth:
    """Issue #1669 — CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH is always set, defaulting to 1."""

    def test_default_config_sets_depth_1(self):
        sdk = _make_sdk()
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"

    def test_configured_depth_reflected_in_env(self):
        config = SessionConfig(max_subagent_spawn_depth=3)
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "3"

    def test_var_always_present_even_under_full_suppression(self):
        sdk = _make_sdk()
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH" in env

    def test_extra_env_can_override(self):
        sdk = _make_sdk(extra_env={"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2"})
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "2"


class TestProcessWrapper:
    """Issue #1672 — CLAUDE_CODE_PROCESS_WRAPPER is set only when process_wrapper is configured."""

    def test_unset_by_default(self):
        sdk = _make_sdk()
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_PROCESS_WRAPPER" not in env

    def test_configured_value_reflected_in_env(self):
        config = SessionConfig(process_wrapper="/opt/launcher/wrapper.sh")
        sdk = _make_sdk(config=config)
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_PROCESS_WRAPPER"] == "/opt/launcher/wrapper.sh"

    def test_absent_under_full_suppression_when_unset(self):
        sdk = _make_sdk()
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_PROCESS_WRAPPER" not in env

    def test_extra_env_can_override(self):
        config = SessionConfig(process_wrapper="/opt/launcher/wrapper.sh")
        sdk = _make_sdk(config=config, extra_env={"CLAUDE_CODE_PROCESS_WRAPPER": "/other/wrapper"})
        with patch("backend.config_manager.load_config", return_value=_suppression_off_config()):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_PROCESS_WRAPPER"] == "/other/wrapper"


class TestMaxSubagentsPerSession:
    """Issue #1670 — CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION is opt-in, only set when < 200."""

    def test_absent_at_default_value_200(self):
        sdk = _make_sdk()
        config = AppConfig(features=FeaturesConfig(max_subagents_per_session=200))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION" not in env

    def test_present_when_below_200(self):
        sdk = _make_sdk()
        config = AppConfig(features=FeaturesConfig(max_subagents_per_session=50))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] == "50"

    def test_extra_env_can_override(self):
        sdk = _make_sdk(extra_env={"CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION": "10"})
        config = AppConfig(features=FeaturesConfig(max_subagents_per_session=50))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] == "10"


class TestForwardSubagentText:
    """Issue #1671 — CLAUDE_CODE_FORWARD_SUBAGENT_TEXT is always set, defaulting to on."""

    def test_default_config_sets_var_to_1(self):
        sdk = _make_sdk()
        config = AppConfig(features=FeaturesConfig(forward_subagent_text=True))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_FORWARD_SUBAGENT_TEXT"] == "1"

    def test_toggled_off_sets_var_to_0(self):
        sdk = _make_sdk()
        config = AppConfig(features=FeaturesConfig(forward_subagent_text=False))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_FORWARD_SUBAGENT_TEXT"] == "0"

    def test_var_always_present_even_under_full_suppression(self):
        sdk = _make_sdk()
        with patch("backend.config_manager.load_config", return_value=_all_suppressed_config()):
            env = sdk._resolve_env_vars()
        assert "CLAUDE_CODE_FORWARD_SUBAGENT_TEXT" in env

    def test_extra_env_can_override(self):
        sdk = _make_sdk(extra_env={"CLAUDE_CODE_FORWARD_SUBAGENT_TEXT": "0"})
        config = AppConfig(features=FeaturesConfig(forward_subagent_text=True))
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_FORWARD_SUBAGENT_TEXT"] == "0"


class TestAllowBackgroundAgentOverridesDisableBackgroundTasks:
    """Issue #1690 — allow_background_agent must win over disable_background_tasks."""

    def test_default_off_unchanged(self):
        sdk = _make_sdk()
        config = AppConfig(
            background_calls=BackgroundCallsConfig(disable_background_tasks=True),
            features=FeaturesConfig(allow_background_agent=False),
        )
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    def test_allow_background_agent_overrides_suppression_on(self):
        sdk = _make_sdk()
        config = AppConfig(
            background_calls=BackgroundCallsConfig(disable_background_tasks=True),
            features=FeaturesConfig(allow_background_agent=True),
        )
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "0"

    def test_allow_background_agent_overrides_suppression_off(self):
        sdk = _make_sdk()
        config = AppConfig(
            background_calls=BackgroundCallsConfig(disable_background_tasks=False),
            features=FeaturesConfig(allow_background_agent=True),
        )
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "0"

    def test_extra_env_still_overrides(self):
        sdk = _make_sdk(extra_env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
        config = AppConfig(
            background_calls=BackgroundCallsConfig(disable_background_tasks=True),
            features=FeaturesConfig(allow_background_agent=True),
        )
        with patch("backend.config_manager.load_config", return_value=config):
            env = sdk._resolve_env_vars()
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
