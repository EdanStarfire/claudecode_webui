"""CRUD manager for the provider catalog stored in data/providers.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.logging_config import get_logger
from src.storage_utils import write_alphabetized_json

legion_logger = get_logger("legion", category="PROVIDER_CATALOG")

_SECRET_PLACEHOLDER_RE = re.compile(r"\$\{secret:([^}]+)\}")

_LEGACY_CONFIG_FILE = Path.home() / ".config" / "cc_webui" / "config.json"


# ---------------------------------------------------------------------------
# Dataclasses (moved from config_manager.py)
# ---------------------------------------------------------------------------


@dataclass
class CatalogModelEntry:
    """A single model offered by a provider catalog entry."""

    id: str
    litellm_model: str  # e.g. "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
    drop_params: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> CatalogModelEntry:
        return cls(
            id=data["id"],
            litellm_model=data["litellm_model"],
            drop_params=data.get("drop_params", False),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "litellm_model": self.litellm_model,
            "drop_params": self.drop_params,
        }


@dataclass
class ProviderCatalogEntry:
    """A provider (e.g. Bedrock, OpenAI) with its LiteLLM params and model list."""

    id: str
    provider_type: str  # "anthropic", "openai", "bedrock", "vertex", "lmstudio", etc.
    litellm_params_template: dict[str, str]  # may contain "${secret:name}" placeholders
    models: list[CatalogModelEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> ProviderCatalogEntry:
        return cls(
            id=data["id"],
            provider_type=data["provider_type"],
            litellm_params_template=data.get("litellm_params_template", {}),
            models=[CatalogModelEntry.from_dict(m) for m in data.get("models", [])],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider_type": self.provider_type,
            "litellm_params_template": self.litellm_params_template,
            "models": [m.to_dict() for m in self.models],
        }


# ---------------------------------------------------------------------------
# Store — pure file IO + in-memory state
# ---------------------------------------------------------------------------


class ProviderCatalogStore:
    """
    File-backed storage for the provider catalog.

    Owns data/providers.json; performs one-shot migration from legacy config.json
    when providers.json does not yet exist.
    """

    def __init__(self, data_dir: Path):
        self.providers_file = data_dir / "providers.json"
        self._entries: list[ProviderCatalogEntry] = []
        self._litellm_port: int = 4000
        self._pending_changes: bool = False

    @property
    def entries(self) -> list[ProviderCatalogEntry]:
        return self._entries

    @property
    def litellm_port(self) -> int:
        return self._litellm_port

    @property
    def pending_changes(self) -> bool:
        return self._pending_changes

    async def load(self) -> None:
        """Read providers.json, or migrate from legacy config.json on first run."""
        if self.providers_file.exists():
            data = json.loads(self.providers_file.read_text(encoding="utf-8"))
            self._entries = [ProviderCatalogEntry.from_dict(e) for e in data.get("entries", [])]
            self._litellm_port = data.get("litellm_port", 4000)
            self._pending_changes = data.get("pending_changes", False)
            return

        legacy = self._read_legacy_catalog()
        if legacy:
            self._entries = [ProviderCatalogEntry.from_dict(e) for e in legacy.get("entries", [])]
            self._litellm_port = legacy.get("litellm_port", 4000)
            self._pending_changes = legacy.get("pending_changes", False)
            await self._save()
            legion_logger.info(
                "Migrated provider catalog from config.json → data/providers.json (%d entries)",
                len(self._entries),
            )
        # else: empty catalog — defaults already set in __init__

    def _read_legacy_catalog(self) -> dict | None:
        """Return the provider_catalog dict from legacy config.json, or None if absent/empty."""
        try:
            data = json.loads(_LEGACY_CONFIG_FILE.read_text(encoding="utf-8"))
            pc = data.get("provider_catalog")
            if pc and (pc.get("entries") or pc.get("litellm_port", 4000) != 4000):
                return pc
        except Exception:
            pass
        return None

    async def _save(self) -> None:
        data = {
            "entries": [e.to_dict() for e in self._entries],
            "litellm_port": self._litellm_port,
            "pending_changes": self._pending_changes,
        }
        write_alphabetized_json(self.providers_file, data)

    async def set_entries(self, entries: list[ProviderCatalogEntry]) -> None:
        self._entries = entries
        await self._save()

    async def set_pending_changes(self, value: bool) -> None:
        self._pending_changes = value
        await self._save()


# ---------------------------------------------------------------------------
# Manager — business logic wrapping the store
# ---------------------------------------------------------------------------


class ProviderCatalogManager:
    """
    Manages the provider catalog: list, create, update, delete entries,
    and resolve ${secret:name} placeholders to live values from the vault.
    """

    def __init__(self, store: ProviderCatalogStore):
        self._store = store

    @property
    def pending_changes(self) -> bool:
        return self._store.pending_changes

    async def list_entries(self) -> list[dict]:
        return [e.to_dict() for e in self._store.entries]

    async def get_entry(self, entry_id: str) -> dict | None:
        for e in self._store.entries:
            if e.id == entry_id:
                return e.to_dict()
        return None

    async def create_entry(self, entry_data: dict) -> dict:
        self._validate_entry(entry_data)
        if any(e.id == entry_data["id"] for e in self._store.entries):
            raise ValueError(f"Entry with id '{entry_data['id']}' already exists")
        new_entries = list(self._store.entries) + [_dict_to_entry(entry_data)]
        await self._store.set_entries(new_entries)
        await self._store.set_pending_changes(True)
        legion_logger.info(f"Provider catalog entry created: {entry_data['id']}")
        return entry_data

    async def update_entry(self, entry_id: str, entry_data: dict) -> dict:
        self._validate_entry(entry_data)
        entries = list(self._store.entries)
        for i, e in enumerate(entries):
            if e.id == entry_id:
                entries[i] = _dict_to_entry(entry_data)
                await self._store.set_entries(entries)
                await self._store.set_pending_changes(True)
                return entry_data
        raise KeyError(f"Entry '{entry_id}' not found")

    async def delete_entry(self, entry_id: str) -> None:
        if not any(e.id == entry_id for e in self._store.entries):
            raise KeyError(f"Entry '{entry_id}' not found")
        new_entries = [e for e in self._store.entries if e.id != entry_id]
        await self._store.set_entries(new_entries)
        await self._store.set_pending_changes(True)

    async def clear_pending_changes(self) -> None:
        await self._store.set_pending_changes(False)

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
        unique_names = list({m.group(1) for m in matches})
        resolved = await vault.resolve_secrets_for_assignment(unique_names)
        secrets: dict[str, str] = {r["name"]: r["value"] for r in resolved}
        for name in unique_names:
            if name not in secrets:
                raise ValueError(f"Secret '{name}' not found in vault")
        result = value
        for match in matches:
            result = result.replace(match.group(0), secrets[match.group(1)])
        return result

    def _validate_entry(self, entry_data: dict) -> None:
        required = ["id", "provider_type", "litellm_params_template", "models"]
        for f in required:
            if f not in entry_data:
                raise ValueError(f"Missing required field: {f}")
        model_ids = [m["id"] for m in entry_data.get("models", [])]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Duplicate model ids within entry")


def _dict_to_entry(data: dict) -> ProviderCatalogEntry:
    return ProviderCatalogEntry.from_dict(data)
