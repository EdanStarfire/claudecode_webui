"""Issue #2287 (stage A) — opt-in CLI behavior options."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.claude_sdk import ClaudeSDK
from backend.config_manager import AppConfig, FeaturesConfig
from backend.config_resolution import FIELD_TO_AREA, PROFILE_AREAS
from backend.routers._models import TemplateUpdateRequest
from backend.session_config import CONFIG_FIELDS, SessionConfig

FIELDS = (
    "disable_web_fetch",
    "disable_file_mentions",
    "disable_idle_compaction",
    "idle_compact_min_tokens",
)
ENV = {
    "disable_web_fetch": "CLAUDE_CODE_DISABLE_WEB_FETCH",
    "disable_file_mentions": "CLAUDE_CODE_DISABLE_FILE_MENTIONS",
    "idle_compact_min_tokens": "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS",
}


def _sdk(config=None, extra_env=None, restricted=False):
    cfg = config or SessionConfig()
    if restricted:
        cfg = cfg.model_copy(update={"restricted_mode": True})
    with tempfile.TemporaryDirectory() as tmp:
        return ClaudeSDK(
            session_id="s2287", working_directory=tmp, config=cfg, extra_env=extra_env or {}
        )


def _app_cfg(block=False):
    return AppConfig(features=FeaturesConfig(block_cross_session_messaging=block))


def _env(sdk):
    with patch("backend.config_manager.load_config", return_value=_app_cfg()):
        return sdk._resolve_env_vars()


class TestEnvEmission:
    @pytest.mark.parametrize("field", ["disable_web_fetch", "disable_file_mentions"])
    def test_bool_env(self, field):
        assert _env(_sdk(SessionConfig(**{field: True})))[ENV[field]] == "1"
        assert ENV[field] not in _env(_sdk(SessionConfig(**{field: False})))
        assert ENV[field] not in _env(_sdk(SessionConfig()))

    def test_independent_of_restricted_mode(self):
        env = _env(_sdk(SessionConfig(disable_web_fetch=True), restricted=True))
        assert env["CLAUDE_CODE_DISABLE_WEB_FETCH"] == "1"

    def test_min_tokens(self):
        name = ENV["idle_compact_min_tokens"]
        assert _env(_sdk(SessionConfig(idle_compact_min_tokens=150000)))[name] == "150000"
        assert name not in _env(_sdk(SessionConfig()))

    def test_extra_env_wins(self):
        sdk = _sdk(
            SessionConfig(disable_web_fetch=True, idle_compact_min_tokens=150000),
            extra_env={"CLAUDE_CODE_DISABLE_WEB_FETCH": "0",
                       "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "300000"},
        )
        env = _env(sdk)
        assert env["CLAUDE_CODE_DISABLE_WEB_FETCH"] == "0"
        assert env["CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS"] == "300000"

    def test_idle_compaction_never_in_env(self):
        env = _env(_sdk(SessionConfig(disable_idle_compaction=True)))
        assert not any("IDLE_COMPACT" in k and "MIN_TOKENS" not in k for k in env)


class TestSettingsPayload:
    def _payload(self, config, block=False):
        with patch("backend.config_manager.load_config", return_value=_app_cfg(block)):
            sdk = _sdk(config)
            opts = sdk._get_sdk_options()
        if opts.settings is None:
            return None
        try:
            return json.loads(Path(opts.settings).read_text())
        finally:
            sdk._cleanup_settings_temp_file()

    def test_true_writes_false(self):
        assert self._payload(SessionConfig(disable_idle_compaction=True)) == {
            "idleCompaction": False
        }

    def test_merges_with_existing_keys(self):
        assert self._payload(SessionConfig(disable_idle_compaction=True), block=True) == {
            "crossSessionInbound": "refuse",
            "idleCompaction": False,
        }

    @pytest.mark.parametrize("val", [None, False])
    def test_unset_or_false_omitted(self, val):
        assert self._payload(SessionConfig(disable_idle_compaction=val)) is None


class TestCascadeAndBounds:
    def test_fields_registered(self):
        for f in FIELDS:
            assert f in CONFIG_FIELDS
            assert f in PROFILE_AREAS["features"]
            assert FIELD_TO_AREA[f] == "features"

    def test_min_tokens_bounds(self):
        with pytest.raises(ValidationError):
            SessionConfig(idle_compact_min_tokens=0)
        with pytest.raises(ValidationError):
            TemplateUpdateRequest(idle_compact_min_tokens=0)
        assert SessionConfig(idle_compact_min_tokens=1).idle_compact_min_tokens == 1
