"""Tests for src/frontend_config.py's save path (issue #498 review finding).

save_frontend_config() previously did a bare read-modify-write with no
cross-process synchronization: two saves racing between Frontend and Backend
(they share one config.json) could interleave so one process's write silently
dropped the other's. Fixed with a cross-process advisory lock plus an atomic
temp-file+rename write.
"""

import json

from src.frontend_config import (
    BackendConnectionConfig,
    FrontendConfig,
    NetworkingConfig,
    load_frontend_config,
    save_frontend_config,
)


class TestSaveFrontendConfig:
    def test_preserves_backend_owned_sections(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "features": {"max_peek_cards": 42},
            "pricing": {"default_model": "claude-opus-4-7"},
        }))

        config = FrontendConfig(networking=NetworkingConfig(allow_network_binding=True, acknowledged_risk=True))
        save_frontend_config(config, config_file)

        data = json.loads(config_file.read_text())
        assert data["features"] == {"max_peek_cards": 42}
        assert data["pricing"] == {"default_model": "claude-opus-4-7"}
        assert data["networking"] == {"allow_network_binding": True, "acknowledged_risk": True}

    def test_writes_own_sections_and_round_trips(self, tmp_path):
        config_file = tmp_path / "config.json"
        config = FrontendConfig(
            networking=NetworkingConfig(allow_network_binding=True, acknowledged_risk=True),
            backend_connection=BackendConnectionConfig(
                remote_backend_url="http://127.0.0.1:8100", remote_backend_token="t"
            ),
        )

        save_frontend_config(config, config_file)
        loaded = load_frontend_config(config_file)

        assert loaded.networking.allow_network_binding is True
        assert loaded.backend_connection.remote_backend_url == "http://127.0.0.1:8100"

    def test_handles_missing_file(self, tmp_path):
        config_file = tmp_path / "nested" / "config.json"
        save_frontend_config(FrontendConfig(), config_file)
        assert config_file.exists()

    def test_no_stray_temp_or_lock_file_left_behind_in_final_state(self, tmp_path):
        """The atomic-write temp file must be renamed away, not left dangling; the
        lock file is expected to persist (it's the mutex, not scratch state)."""
        config_file = tmp_path / "config.json"
        save_frontend_config(FrontendConfig(), config_file)

        tmp_sibling = config_file.with_suffix(config_file.suffix + ".tmp")
        assert not tmp_sibling.exists()
