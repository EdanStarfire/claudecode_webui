"""Tests for src/config_manager.py."""

import json

import pytest

from src.config_manager import (
    AppConfig,
    NetworkingConfig,
    check_network_binding,
    ensure_config_file,
    load_config,
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
