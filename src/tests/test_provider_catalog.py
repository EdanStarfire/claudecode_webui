"""Unit tests for ProviderCatalogManager — CRUD and secret resolution."""

from unittest.mock import AsyncMock

import pytest

from src.config_manager import AppConfig
from src.provider_catalog import ProviderCatalogManager


def _sample_entry(entry_id: str = "bedrock-1") -> dict:
    return {
        "id": entry_id,
        "display_name": "Bedrock (us-east-1)",
        "provider_type": "bedrock",
        "litellm_params_template": {"aws_region_name": "us-east-1"},
        "models": [
            {
                "id": "sonnet",
                "display_name": "Claude Sonnet",
                "litellm_model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
            }
        ],
    }


class _InMemoryConfigManager:
    """Minimal config manager backed by a mutable AppConfig for tests."""

    def __init__(self):
        self._config = AppConfig()
        self._saved: list[AppConfig] = []

    async def get_config(self) -> AppConfig:
        return self._config

    async def save_config(self, config: AppConfig) -> None:
        self._config = config
        self._saved.append(config)


@pytest.fixture
def config_manager():
    return _InMemoryConfigManager()


@pytest.fixture
def catalog(config_manager):
    return ProviderCatalogManager(config_manager)


@pytest.fixture
def vault():
    v = AsyncMock()
    v.get_secret.return_value = "secret-value-123"
    return v


# ── List ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_entries_empty(catalog):
    assert await catalog.list_entries() == []


# ── Create ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_entry_stores_and_sets_pending(catalog, config_manager):
    entry = _sample_entry()
    await catalog.create_entry(entry)

    entries = await catalog.list_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == "bedrock-1"
    assert config_manager._config.provider_catalog.pending_changes is True


@pytest.mark.asyncio
async def test_create_entry_duplicate_id_raises(catalog):
    await catalog.create_entry(_sample_entry())
    with pytest.raises(ValueError, match="already exists"):
        await catalog.create_entry(_sample_entry())


# ── Update ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_entry_replaces_and_sets_pending(catalog, config_manager):
    await catalog.create_entry(_sample_entry())
    config_manager._config.provider_catalog.pending_changes = False  # reset

    updated = _sample_entry()
    updated["display_name"] = "Updated Name"
    await catalog.update_entry("bedrock-1", updated)

    entries = await catalog.list_entries()
    assert entries[0]["display_name"] == "Updated Name"
    assert config_manager._config.provider_catalog.pending_changes is True


@pytest.mark.asyncio
async def test_update_entry_missing_raises(catalog):
    with pytest.raises(KeyError, match="not found"):
        await catalog.update_entry("nonexistent", _sample_entry("nonexistent"))


# ── Delete ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_entry_removes_and_sets_pending(catalog, config_manager):
    await catalog.create_entry(_sample_entry())
    config_manager._config.provider_catalog.pending_changes = False  # reset

    await catalog.delete_entry("bedrock-1")

    assert await catalog.list_entries() == []
    assert config_manager._config.provider_catalog.pending_changes is True


@pytest.mark.asyncio
async def test_delete_entry_missing_raises(catalog):
    with pytest.raises(KeyError, match="not found"):
        await catalog.delete_entry("does-not-exist")


# ── Clear Pending ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_pending_changes(catalog, config_manager):
    await catalog.create_entry(_sample_entry())
    assert config_manager._config.provider_catalog.pending_changes is True

    await catalog.clear_pending_changes()
    assert config_manager._config.provider_catalog.pending_changes is False


# ── Resolve Params ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_params_replaces_secret_placeholder(catalog, vault):
    vault.get_secret.return_value = "my-api-key"
    entry = {
        "id": "openai-1",
        "litellm_params_template": {"api_key": "${secret:openai_key}"},
        "models": [],
    }
    resolved = await catalog.resolve_params(entry, vault)
    assert resolved["api_key"] == "my-api-key"
    vault.get_secret.assert_called_once_with("openai_key")


@pytest.mark.asyncio
async def test_resolve_params_missing_secret_raises(catalog, vault):
    vault.get_secret.return_value = None
    entry = {
        "id": "openai-1",
        "litellm_params_template": {"api_key": "${secret:missing_key}"},
        "models": [],
    }
    with pytest.raises(ValueError, match="not found in vault"):
        await catalog.resolve_params(entry, vault)


@pytest.mark.asyncio
async def test_resolve_params_no_placeholders(catalog, vault):
    entry = {
        "id": "bedrock-1",
        "litellm_params_template": {"aws_region_name": "us-east-1"},
        "models": [],
    }
    resolved = await catalog.resolve_params(entry, vault)
    assert resolved == {"aws_region_name": "us-east-1"}
    vault.get_secret.assert_not_called()


# ── Validation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_entry_missing_required_field_raises(catalog):
    bad_entry = _sample_entry()
    del bad_entry["id"]
    with pytest.raises(ValueError, match="Missing required field"):
        await catalog.create_entry(bad_entry)


@pytest.mark.asyncio
async def test_validate_entry_duplicate_model_ids_raises(catalog):
    entry = _sample_entry()
    entry["models"] = [
        {"id": "m1", "display_name": "Model A", "litellm_model": "provider/model-a"},
        {"id": "m1", "display_name": "Model B", "litellm_model": "provider/model-b"},
    ]
    with pytest.raises(ValueError, match="Duplicate model ids"):
        await catalog.create_entry(entry)


# ── Round-trip ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_roundtrips_through_config(catalog):
    """_entry_to_dict(._dict_to_entry(data)) == data."""
    entry = _sample_entry()
    await catalog.create_entry(entry)
    retrieved = await catalog.get_entry("bedrock-1")
    assert retrieved is not None
    assert retrieved["id"] == "bedrock-1"
    assert retrieved["models"][0]["litellm_model"] == (
        "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
    )
