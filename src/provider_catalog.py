"""CRUD manager for the provider catalog stored in AppConfig."""

from __future__ import annotations

import re
from typing import Any

from src.logging_config import get_logger

legion_logger = get_logger("legion", category="PROVIDER_CATALOG")

_SECRET_PLACEHOLDER_RE = re.compile(r"\$\{secret:([^}]+)\}")


class ProviderCatalogManager:
    """
    Manages the provider catalog: list, create, update, delete entries,
    and resolve ${secret:name} placeholders to live values from the vault.
    """

    def __init__(self, config_manager):
        self._config_manager = config_manager

    async def list_entries(self) -> list[dict]:
        config = await self._config_manager.get_config()
        return [e.to_dict() for e in config.provider_catalog.entries]

    async def get_entry(self, entry_id: str) -> dict | None:
        config = await self._config_manager.get_config()
        for e in config.provider_catalog.entries:
            if e.id == entry_id:
                return e.to_dict()
        return None

    async def create_entry(self, entry_data: dict) -> dict:
        self._validate_entry(entry_data)
        config = await self._config_manager.get_config()
        if any(e.id == entry_data["id"] for e in config.provider_catalog.entries):
            raise ValueError(f"Entry with id '{entry_data['id']}' already exists")
        config.provider_catalog.entries.append(self._dict_to_entry(entry_data))
        config.provider_catalog.pending_changes = True
        await self._config_manager.save_config(config)
        legion_logger.info(f"Provider catalog entry created: {entry_data['id']}")
        return entry_data

    async def update_entry(self, entry_id: str, entry_data: dict) -> dict:
        self._validate_entry(entry_data)
        config = await self._config_manager.get_config()
        for i, e in enumerate(config.provider_catalog.entries):
            if e.id == entry_id:
                config.provider_catalog.entries[i] = self._dict_to_entry(entry_data)
                config.provider_catalog.pending_changes = True
                await self._config_manager.save_config(config)
                return entry_data
        raise KeyError(f"Entry '{entry_id}' not found")

    async def delete_entry(self, entry_id: str) -> None:
        config = await self._config_manager.get_config()
        if not any(e.id == entry_id for e in config.provider_catalog.entries):
            raise KeyError(f"Entry '{entry_id}' not found")
        config.provider_catalog.entries = [
            e for e in config.provider_catalog.entries if e.id != entry_id
        ]
        config.provider_catalog.pending_changes = True
        await self._config_manager.save_config(config)

    async def clear_pending_changes(self) -> None:
        config = await self._config_manager.get_config()
        config.provider_catalog.pending_changes = False
        await self._config_manager.save_config(config)

    async def resolve_params(self, entry: dict, vault) -> dict[str, Any]:
        """Resolve ${secret:name} placeholders in litellm_params_template."""
        template = entry.get("litellm_params_template", {})
        resolved = {}
        for k, v in template.items():
            if isinstance(v, str):
                resolved[k] = await self._resolve_value(v, vault)
            else:
                resolved[k] = v
        return resolved

    async def _resolve_value(self, value: str, vault) -> str:
        matches = list(_SECRET_PLACEHOLDER_RE.finditer(value))
        if not matches:
            return value
        # Fetch each unique secret name once, then substitute all occurrences.
        unique_names = {m.group(1) for m in matches}
        secrets: dict[str, str] = {}
        for name in unique_names:
            secret_value = await vault.get_secret(name)
            if secret_value is None:
                raise ValueError(f"Secret '{name}' not found in vault")
            secrets[name] = secret_value
        result = value
        for match in matches:
            result = result.replace(match.group(0), secrets[match.group(1)])
        return result

    def _validate_entry(self, entry_data: dict) -> None:
        required = ["id", "display_name", "provider_type", "litellm_params_template", "models"]
        for f in required:
            if f not in entry_data:
                raise ValueError(f"Missing required field: {f}")
        model_ids = [m["id"] for m in entry_data.get("models", [])]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Duplicate model ids within entry")

    @staticmethod
    def _dict_to_entry(data: dict):
        from src.config_manager import ProviderCatalogEntry

        return ProviderCatalogEntry.from_dict(data)
