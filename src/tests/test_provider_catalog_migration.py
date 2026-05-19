"""Unit tests for ProviderCatalogStore one-shot migration from legacy config.json."""

import json

import pytest

from src.provider_catalog import ProviderCatalogStore


def _write_legacy_config(path, catalog_data):
    """Write a legacy config.json with the given provider_catalog block."""
    payload = {"networking": {}, "provider_catalog": catalog_data}
    path.write_text(json.dumps(payload), encoding="utf-8")


# ── Fixtures ──────────────────────────────────────────────────────────────


def _sample_catalog():
    return {
        "entries": [
            {
                "id": "bedrock-prod",
                "display_name": "AWS Bedrock (Prod)",
                "provider_type": "bedrock",
                "litellm_params_template": {"aws_region_name": "us-west-2"},
                "models": [{"id": "m1", "display_name": "Sonnet", "litellm_model": "bedrock/m1"}],
            }
        ],
        "litellm_port": 4001,
        "pending_changes": False,
    }


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_from_legacy_config(tmp_path, monkeypatch):
    """Legacy config.json with non-empty provider_catalog → migrated on first load."""
    legacy_cfg = tmp_path / "config.json"
    _write_legacy_config(legacy_cfg, _sample_catalog())

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Point the store at the patched legacy path
    import src.provider_catalog as pc_mod
    monkeypatch.setattr(pc_mod, "_LEGACY_CONFIG_FILE", legacy_cfg)

    store = ProviderCatalogStore(data_dir)
    await store.load()

    providers_file = data_dir / "providers.json"
    assert providers_file.exists(), "providers.json should be created after migration"

    data = json.loads(providers_file.read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["id"] == "bedrock-prod"
    assert data["litellm_port"] == 4001

    assert len(store.entries) == 1
    assert store.entries[0].id == "bedrock-prod"
    assert store.litellm_port == 4001


@pytest.mark.asyncio
async def test_no_migration_when_catalog_absent(tmp_path, monkeypatch):
    """Legacy config.json without provider_catalog → empty store, no migration log."""
    legacy_cfg = tmp_path / "config.json"
    legacy_cfg.write_text(json.dumps({"networking": {}}), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    import src.provider_catalog as pc_mod
    monkeypatch.setattr(pc_mod, "_LEGACY_CONFIG_FILE", legacy_cfg)

    store = ProviderCatalogStore(data_dir)
    await store.load()

    assert not (data_dir / "providers.json").exists(), (
        "providers.json should not be created when nothing to migrate"
    )
    assert store.entries == []
    assert store.litellm_port == 4000


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path, monkeypatch):
    """Second load() with providers.json present does not re-read legacy config."""
    legacy_cfg = tmp_path / "config.json"
    _write_legacy_config(legacy_cfg, _sample_catalog())

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    import src.provider_catalog as pc_mod
    monkeypatch.setattr(pc_mod, "_LEGACY_CONFIG_FILE", legacy_cfg)

    # First load — migration
    store = ProviderCatalogStore(data_dir)
    await store.load()
    assert len(store.entries) == 1

    # Remove legacy config so a second migration attempt would yield empty
    legacy_cfg.unlink()

    # Second load — should read from providers.json, not re-migrate
    store2 = ProviderCatalogStore(data_dir)
    await store2.load()
    assert len(store2.entries) == 1
    assert store2.entries[0].id == "bedrock-prod"


# ── display_name / drop_params compatibility ──────────────────────────────────


@pytest.mark.asyncio
async def test_load_ignores_entry_display_name(tmp_path):
    """display_name on a provider entry in providers.json is silently ignored on load."""
    data = {
        "entries": [
            {
                "id": "bedrock-1",
                "display_name": "Legacy Display Name",
                "provider_type": "bedrock",
                "litellm_params_template": {},
                "models": [],
            }
        ],
        "litellm_port": 4000,
        "pending_changes": False,
    }
    (tmp_path / "providers.json").write_text(json.dumps(data), encoding="utf-8")

    store = ProviderCatalogStore(tmp_path)
    await store.load()

    assert len(store.entries) == 1
    assert store.entries[0].id == "bedrock-1"
    assert not hasattr(store.entries[0], "display_name")


@pytest.mark.asyncio
async def test_load_ignores_model_display_name(tmp_path):
    """display_name on a model entry in providers.json is silently ignored on load."""
    data = {
        "entries": [
            {
                "id": "bedrock-1",
                "provider_type": "bedrock",
                "litellm_params_template": {},
                "models": [
                    {
                        "id": "sonnet",
                        "display_name": "Claude Sonnet (legacy)",
                        "litellm_model": "bedrock/claude-3-5-sonnet",
                    }
                ],
            }
        ],
        "litellm_port": 4000,
        "pending_changes": False,
    }
    (tmp_path / "providers.json").write_text(json.dumps(data), encoding="utf-8")

    store = ProviderCatalogStore(tmp_path)
    await store.load()

    model = store.entries[0].models[0]
    assert model.id == "sonnet"
    assert model.litellm_model == "bedrock/claude-3-5-sonnet"
    assert not hasattr(model, "display_name")


@pytest.mark.asyncio
async def test_load_model_drop_params_defaults_false(tmp_path):
    """Existing models without drop_params field default to False on load."""
    data = {
        "entries": [
            {
                "id": "bedrock-1",
                "provider_type": "bedrock",
                "litellm_params_template": {},
                "models": [{"id": "sonnet", "litellm_model": "bedrock/claude-3-5-sonnet"}],
            }
        ],
        "litellm_port": 4000,
        "pending_changes": False,
    }
    (tmp_path / "providers.json").write_text(json.dumps(data), encoding="utf-8")

    store = ProviderCatalogStore(tmp_path)
    await store.load()

    assert store.entries[0].models[0].drop_params is False
