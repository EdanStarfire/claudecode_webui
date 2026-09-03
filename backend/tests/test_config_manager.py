"""Tests for src/config_manager.py."""

import json

import pytest

from backend.config_manager import (
    AppConfig,
    BackgroundCallsConfig,
    NetworkingConfig,
    check_network_binding,
    ensure_config_file,
    load_config,
    save_config,
)


class TestEnsureConfigFile:
    def test_creates_directory_and_file(self, tmp_path):
        config_file = tmp_path / "subdir" / "config.json"
        result = ensure_config_file(config_file)

        assert result == config_file
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["networking"]["allow_network_binding"] is False
        assert data["networking"]["acknowledged_risk"] is False

    def test_preserves_existing_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        custom_data = {"networking": {"allow_network_binding": True, "acknowledged_risk": True}}
        config_file.write_text(json.dumps(custom_data))

        ensure_config_file(config_file)

        data = json.loads(config_file.read_text())
        assert data["networking"]["allow_network_binding"] is True
        assert data["networking"]["acknowledged_risk"] is True


class TestSaveConfig:
    """Regression tests for issue #498: the shared config.json also carries Frontend-
    owned top-level keys (networking, backend_connection) that aren't AppConfig
    fields. save_config() must not clobber them — found live while testing the
    Phase 2 config split (a Frontend write followed by a Backend write silently
    dropped Frontend's backend_connection section)."""

    def test_save_config_preserves_unknown_top_level_keys(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "backend_connection": {"remote_backend_url": "http://127.0.0.1:8100", "remote_backend_token": "t"},
        }))

        config = load_config(config_file)
        save_config(config, config_file)

        data = json.loads(config_file.read_text())
        assert data["backend_connection"] == {
            "remote_backend_url": "http://127.0.0.1:8100",
            "remote_backend_token": "t",
        }

    def test_save_config_still_writes_known_fields(self, tmp_path):
        config_file = tmp_path / "config.json"
        ensure_config_file(config_file)

        config = load_config(config_file)
        config.features.max_peek_cards = 42
        save_config(config, config_file)

        data = json.loads(config_file.read_text())
        assert data["features"]["max_peek_cards"] == 42

    def test_save_config_handles_missing_file(self, tmp_path):
        config_file = tmp_path / "does_not_exist" / "config.json"
        config = AppConfig()
        save_config(config, config_file)
        assert config_file.exists()

    def test_save_config_does_not_clobber_frontend_owned_networking_section(self, tmp_path):
        """Regression test for issue #498 review finding: AppConfig itself carries a
        NetworkingConfig field (read by Backend's own check_network_binding for manual/
        remote startup), so a naive top-level merge still round-trips Backend's own
        stale copy of "networking" into the file — reverting a Frontend-owned value the
        instant Backend saves for an unrelated reason (e.g. a pricing update)."""
        config_file = tmp_path / "config.json"
        ensure_config_file(config_file)

        # Backend loads its own view (defaults: allow_network_binding=False) — this is
        # the in-memory snapshot it will still be holding when it later calls save_config().
        config = load_config(config_file)
        assert config.networking.allow_network_binding is False

        # Frontend independently enables network binding via its own save path, after
        # Backend already loaded the (now stale) snapshot above.
        data = json.loads(config_file.read_text())
        data["networking"] = {"allow_network_binding": True, "acknowledged_risk": True}
        config_file.write_text(json.dumps(data))

        # Backend now saves for an unrelated reason, using its stale in-memory config.
        config.features.max_peek_cards = 99
        save_config(config, config_file)

        result = json.loads(config_file.read_text())
        assert result["networking"] == {"allow_network_binding": True, "acknowledged_risk": True}
        assert result["features"]["max_peek_cards"] == 99


class TestLoadConfig:
    def test_returns_defaults_from_fresh_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        ensure_config_file(config_file)

        config = load_config(config_file)

        assert config.networking.allow_network_binding is False
        assert config.networking.acknowledged_risk is False
        assert config.networking.network_binding_allowed is False

    def test_handles_malformed_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        config = load_config(config_file)

        assert config.networking.allow_network_binding is False
        assert config.networking.acknowledged_risk is False

    def test_handles_missing_keys(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"networking": {"allow_network_binding": True}}))

        config = load_config(config_file)

        assert config.networking.allow_network_binding is True
        assert config.networking.acknowledged_risk is False

    def test_handles_missing_file(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"

        config = load_config(config_file)

        assert config.networking.allow_network_binding is False


class TestCheckNetworkBinding:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_allows_localhost_variants(self, host, tmp_path):
        config = AppConfig()
        config_file = tmp_path / "config.json"
        assert check_network_binding(host, config, config_file) is True

    def test_blocks_when_both_false(self, tmp_path, capsys):
        config = AppConfig()
        config_file = tmp_path / "config.json"
        assert check_network_binding("0.0.0.0", config, config_file) is False

        captured = capsys.readouterr()
        assert "ERROR: Network binding requires explicit configuration" in captured.out

    def test_blocks_when_only_allow_true(self, tmp_path, capsys):
        config = AppConfig(networking=NetworkingConfig(allow_network_binding=True))
        config_file = tmp_path / "config.json"
        assert check_network_binding("0.0.0.0", config, config_file) is False

        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    def test_blocks_when_only_acknowledged_true(self, tmp_path, capsys):
        config = AppConfig(networking=NetworkingConfig(acknowledged_risk=True))
        config_file = tmp_path / "config.json"
        assert check_network_binding("0.0.0.0", config, config_file) is False

        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    def test_allows_when_both_true(self, tmp_path):
        config = AppConfig(
            networking=NetworkingConfig(allow_network_binding=True, acknowledged_risk=True)
        )
        config_file = tmp_path / "config.json"
        assert check_network_binding("0.0.0.0", config, config_file) is True


class TestAppConfig:
    def test_from_dict_ignores_comment_key(self):
        data = {
            "networking": {
                "_comment": "This is a comment",
                "allow_network_binding": True,
                "acknowledged_risk": True,
            }
        }
        config = AppConfig.from_dict(data)
        assert config.networking.allow_network_binding is True
        assert config.networking.acknowledged_risk is True

    def test_to_dict_roundtrip(self):
        original = AppConfig(
            networking=NetworkingConfig(allow_network_binding=True, acknowledged_risk=True)
        )
        data = original.to_dict()
        restored = AppConfig.from_dict(data)

        assert restored.networking.allow_network_binding == original.networking.allow_network_binding
        assert restored.networking.acknowledged_risk == original.networking.acknowledged_risk

    def test_from_dict_empty(self):
        config = AppConfig.from_dict({})
        assert config.networking.allow_network_binding is False
        assert config.networking.acknowledged_risk is False

    def test_network_binding_allowed_property(self):
        assert NetworkingConfig(True, True).network_binding_allowed is True
        assert NetworkingConfig(True, False).network_binding_allowed is False
        assert NetworkingConfig(False, True).network_binding_allowed is False
        assert NetworkingConfig(False, False).network_binding_allowed is False


class TestBackgroundCallsConfig:
    def test_defaults_all_suppression_on_except_dont_inherit_env(self):
        cfg = BackgroundCallsConfig()
        assert cfg.disable_auto_memory is True
        assert cfg.disable_claudeai_mcp_servers is True
        assert cfg.disable_background_tasks is True
        assert cfg.disable_nonessential_traffic is True
        assert cfg.disable_cron is True
        assert cfg.disable_feedback_survey is True
        assert cfg.disable_telemetry is True
        assert cfg.subprocess_env_scrub is False
        assert cfg.skip_version_check is True
        assert cfg.dont_inherit_env is False

    def test_background_calls_defaults_present_in_new_config_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        ensure_config_file(config_file)
        data = json.loads(config_file.read_text())
        bg = data["background_calls"]
        assert bg["disable_auto_memory"] is True
        assert bg["disable_claudeai_mcp_servers"] is True
        assert bg["subprocess_env_scrub"] is False
        assert bg["dont_inherit_env"] is False

    def test_background_calls_round_trip_serialization(self):
        original = AppConfig(
            background_calls=BackgroundCallsConfig(
                disable_auto_memory=False,
                dont_inherit_env=True,
            )
        )
        restored = AppConfig.from_dict(original.to_dict())
        assert restored.background_calls.disable_auto_memory is False
        assert restored.background_calls.dont_inherit_env is True
        assert restored.background_calls.disable_telemetry is True

    def test_background_calls_missing_section_uses_defaults(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"networking": {}}))
        config = load_config(config_file)
        assert config.background_calls.disable_auto_memory is True
        assert config.background_calls.dont_inherit_env is False

    def test_background_calls_partial_section_fills_missing_fields(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"background_calls": {"disable_auto_memory": False}}))
        config = load_config(config_file)
        assert config.background_calls.disable_auto_memory is False
        assert config.background_calls.disable_telemetry is True
        assert config.background_calls.dont_inherit_env is False
