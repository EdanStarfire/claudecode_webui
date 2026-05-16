"""Unit tests for ProviderCatalogManager — CRUD and secret resolution."""

from unittest.mock import AsyncMock

import pytest

from src.provider_catalog import ProviderCatalogManager, ProviderCatalogStore


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


@pytest.fixture
def catalog_store(tmp_path):
    return ProviderCatalogStore(tmp_path)


@pytest.fixture
def catalog(catalog_store):
    return ProviderCatalogManager(catalog_store)


@pytest.fixture
def vault():
    v = AsyncMock()
    v.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    return v


# ── List ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_entries_empty(catalog):
    assert await catalog.list_entries() == []


# ── Create ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_entry_stores_and_sets_pending(catalog, catalog_store):
    entry = _sample_entry()
    await catalog.create_entry(entry)

    entries = await catalog.list_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == "bedrock-1"
    assert catalog_store.pending_changes is True


@pytest.mark.asyncio
async def test_create_entry_duplicate_id_raises(catalog):
    await catalog.create_entry(_sample_entry())
    with pytest.raises(ValueError, match="already exists"):
        await catalog.create_entry(_sample_entry())


# ── Update ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_entry_replaces_and_sets_pending(catalog, catalog_store):
    await catalog.create_entry(_sample_entry())
    await catalog_store.set_pending_changes(False)  # reset

    updated = _sample_entry()
    updated["display_name"] = "Updated Name"
    await catalog.update_entry("bedrock-1", updated)

    entries = await catalog.list_entries()
    assert entries[0]["display_name"] == "Updated Name"
    assert catalog_store.pending_changes is True


@pytest.mark.asyncio
async def test_update_entry_missing_raises(catalog):
    with pytest.raises(KeyError, match="not found"):
        await catalog.update_entry("nonexistent", _sample_entry("nonexistent"))


# ── Delete ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_entry_removes_and_sets_pending(catalog, catalog_store):
    await catalog.create_entry(_sample_entry())
    await catalog_store.set_pending_changes(False)  # reset

    await catalog.delete_entry("bedrock-1")

    assert await catalog.list_entries() == []
    assert catalog_store.pending_changes is True


@pytest.mark.asyncio
async def test_delete_entry_missing_raises(catalog):
    with pytest.raises(KeyError, match="not found"):
        await catalog.delete_entry("does-not-exist")


# ── Clear Pending ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_pending_changes(catalog, catalog_store):
    await catalog.create_entry(_sample_entry())
    assert catalog_store.pending_changes is True

    await catalog.clear_pending_changes()
    assert catalog_store.pending_changes is False


# ── Resolve Params ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_params_replaces_secret_placeholder(catalog, vault):
    vault.resolve_secrets_for_assignment = AsyncMock(
        return_value=[{"name": "openai_key", "value": "my-api-key"}]
    )
    entry = {
        "id": "openai-1",
        "litellm_params_template": {"api_key": "${secret:openai_key}"},
        "models": [],
    }
    resolved = await catalog.resolve_params(entry, vault)
    assert resolved["api_key"] == "my-api-key"
    vault.resolve_secrets_for_assignment.assert_called_once_with(["openai_key"])


@pytest.mark.asyncio
async def test_resolve_params_missing_secret_raises(catalog, vault):
    vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
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
    vault.resolve_secrets_for_assignment.assert_not_called()


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
async def test_entry_roundtrips_through_store(catalog):
    """_entry_to_dict(._dict_to_entry(data)) == data."""
    entry = _sample_entry()
    await catalog.create_entry(entry)
    retrieved = await catalog.get_entry("bedrock-1")
    assert retrieved is not None
    assert retrieved["id"] == "bedrock-1"
    assert retrieved["models"][0]["litellm_model"] == (
        "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
    )


# ── Persistence ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_persists_to_disk(tmp_path):
    """Mutations are written to providers.json immediately."""
    store = ProviderCatalogStore(tmp_path)
    mgr = ProviderCatalogManager(store)
    await mgr.create_entry(_sample_entry())

    # Reload from disk
    store2 = ProviderCatalogStore(tmp_path)
    await store2.load()
    assert len(store2.entries) == 1
    assert store2.entries[0].id == "bedrock-1"
    assert store2.pending_changes is True
