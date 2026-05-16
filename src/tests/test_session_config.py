"""
Unit tests for SessionConfig — issue #1396 extra_env field.
"""

from src.session_config import CONFIG_FIELDS, DEFAULTS, SessionConfig


def test_extra_env_default_none():
    """extra_env defaults to None when not supplied."""
    config = SessionConfig()
    assert config.extra_env is None


def test_extra_env_round_trip_populated():
    """extra_env survives Pydantic validation and serialisation."""
    data = {"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.test"}
    config = SessionConfig(extra_env=data)
    assert config.extra_env == data

    dumped = config.model_dump()
    restored = SessionConfig(**dumped)
    assert restored.extra_env == data


def test_extra_env_round_trip_none():
    """extra_env=None survives serialisation."""
    config = SessionConfig(extra_env=None)
    dumped = config.model_dump()
    restored = SessionConfig(**dumped)
    assert restored.extra_env is None


def test_extra_env_in_config_fields():
    """extra_env must be listed in CONFIG_FIELDS so templates can declare it."""
    assert "extra_env" in CONFIG_FIELDS


def test_extra_env_in_defaults():
    """extra_env must appear in DEFAULTS with value None."""
    assert "extra_env" in DEFAULTS
    assert DEFAULTS["extra_env"] is None


# ── Issue #1427 Phase 2: provider catalog routing fields ───────────────────────


def test_provider_catalog_id_defaults_none():
    assert SessionConfig().provider_catalog_id is None


def test_provider_model_id_defaults_none():
    assert SessionConfig().provider_model_id is None


def test_provider_catalog_fields_in_config_fields():
    assert "provider_catalog_id" in CONFIG_FIELDS
    assert "provider_model_id" in CONFIG_FIELDS


def test_provider_catalog_fields_in_defaults():
    assert DEFAULTS.get("provider_catalog_id") is None
    assert DEFAULTS.get("provider_model_id") is None
