"""
Hook Configuration Manager for Claude Code WebUI

Manages global, reusable hook configurations that can be attached to sessions,
templates, and config profiles by ID (mirrors backend/mcp_config_manager.py).
Configs are stored as JSON files in data/hooks/ with slug-based filenames.

WebUI-defined hooks are always purely additive against any disk-based
.claude/settings.json hooks — there is no merge-mode/replace concept (see
issue #1629's plan Scope Note). This module never reads or modifies
setting_sources; that remains an independent, pre-existing control.

Issue #1629: Per-session hook configuration with profile/template support.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from shared.logging_config import get_logger

from .slug_utils import slugify as _slugify

hook_logger = get_logger('hook_config', category='HOOK_CONFIG')
logger = logging.getLogger(__name__)

# The frontend's autosuggest candidate list for the ~11 documented Claude Code
# hook events lives in frontend/src/stores/hooks.js (KNOWN_HOOK_EVENTS) — kept
# there only since nothing here reads it; any string is still accepted, never
# validated against either list.

_ENTRY_FIELDS = (
    "id", "events", "matcher", "enabled", "type", "command",
    "timeout", "url", "headers", "allowed_env_vars",
)


@dataclass
class HookEntry:
    """One hook rule within a HookConfig — may target multiple events at once.

    Expanded into one settings.json rule per (event, matcher) pair at
    injection time — see HookConfigManager.to_sdk_hooks_payload().
    """
    id: str
    events: list[str] = field(default_factory=list)
    matcher: str | None = None
    enabled: bool = True
    type: Literal["command", "http"] = "command"
    # command type
    command: str | None = None
    timeout: float | None = None
    # http type
    url: str | None = None
    headers: dict[str, str] | None = None
    allowed_env_vars: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'HookEntry':
        # `.get(k) or default` rather than `.setdefault` — callers (e.g. the create/update
        # request models) may send an explicit `None` for an unset field, which setdefault
        # would leave in place instead of replacing (id: null persisting is the concrete
        # bug this guards against).
        data = dict(data)
        data['id'] = data.get('id') or str(uuid.uuid4())
        data['events'] = data.get('events') or []
        data['matcher'] = data.get('matcher')
        data['enabled'] = data.get('enabled') if data.get('enabled') is not None else True
        data['type'] = data.get('type') or 'command'
        data['command'] = data.get('command')
        data['timeout'] = data.get('timeout')
        data['url'] = data.get('url')
        data['headers'] = data.get('headers')
        data['allowed_env_vars'] = data.get('allowed_env_vars')
        return cls(**{k: data[k] for k in _ENTRY_FIELDS})


@dataclass
class HookConfig:
    """Global, reusable hook configuration (mirrors McpServerConfig).

    Persisted as data/hooks/{slug}.json. Attached to sessions/templates/
    profiles by ID via SessionConfig.hook_ids.
    """
    id: str
    name: str
    slug: str
    enabled: bool = True
    hooks: list[HookEntry] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "enabled": self.enabled,
            "hooks": [h.to_dict() for h in self.hooks],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'HookConfig':
        hooks = [HookEntry.from_dict(h) for h in data.get('hooks') or []]
        created_at = data.get('created_at')
        updated_at = data.get('updated_at')
        return cls(
            id=data['id'],
            name=data['name'],
            slug=data['slug'],
            enabled=data.get('enabled', True),
            hooks=hooks,
            created_at=(
                datetime.fromisoformat(created_at) if isinstance(created_at, str) else datetime.now(UTC)
            ),
            updated_at=(
                datetime.fromisoformat(updated_at) if isinstance(updated_at, str) else datetime.now(UTC)
            ),
        )


class HookConfigManager:
    """Manages global hook configurations.

    Configs are stored as JSON files in data/hooks/ with slug-based filenames.
    """

    def __init__(self, data_dir: Path):
        self.configs_dir = data_dir / "hooks"
        self.configs_dir.mkdir(parents=True, exist_ok=True)
        self.configs: dict[str, HookConfig] = {}
        hook_logger.debug(f"HookConfigManager initialized with data_dir: {data_dir}")

    async def load_configs(self):
        """Load all hook configs from disk on startup."""
        self.configs.clear()
        loaded_count = 0

        for config_file in self.configs_dir.glob("*.json"):
            try:
                with open(config_file) as f:
                    data = json.load(f)
                config = HookConfig.from_dict(data)
                self.configs[config.id] = config
                loaded_count += 1
                hook_logger.debug(f"Loaded hook config: {config.name} ({config.id})")
            except Exception as e:
                logger.error(f"Error loading hook config {config_file}: {e}")

        hook_logger.info(f"Loaded {loaded_count} hook configs from disk")

    async def create_config(
        self,
        name: str,
        enabled: bool = True,
        hooks: list[dict[str, Any]] | None = None,
    ) -> HookConfig:
        """Create a new hook configuration."""
        if not name or not name.strip():
            raise ValueError("Hook config name cannot be empty")

        slug = _slugify(name)
        if any(c.slug == slug for c in self.configs.values()):
            raise ValueError(f"Hook config with name '{name}' (slug: {slug}) already exists")

        entries = [HookEntry.from_dict(h) for h in (hooks or [])]

        config = HookConfig(
            id=str(uuid.uuid4()),
            name=name.strip(),
            slug=slug,
            enabled=enabled,
            hooks=entries,
        )

        await self._save_config(config)
        self.configs[config.id] = config
        hook_logger.info(f"Created hook config: {config.name} ({config.id})")
        return config

    async def get_config(self, config_id: str) -> HookConfig | None:
        """Get config by ID."""
        return self.configs.get(config_id)

    async def list_configs(self) -> list[HookConfig]:
        """List all configs."""
        return list(self.configs.values())

    async def update_config(
        self,
        config_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        hooks: list[dict[str, Any]] | None = None,
    ) -> HookConfig:
        """Update existing hook configuration."""
        config = self.configs.get(config_id)
        if not config:
            raise ValueError(f"Hook config {config_id} not found")

        old_slug = config.slug

        if name is not None and name.strip() != config.name:
            new_slug = _slugify(name)
            if any(c.slug == new_slug and c.id != config_id for c in self.configs.values()):
                raise ValueError(f"Hook config with name '{name}' (slug: {new_slug}) already exists")
            config.name = name.strip()
            config.slug = new_slug

        if enabled is not None:
            config.enabled = enabled

        if hooks is not None:
            config.hooks = [HookEntry.from_dict(h) for h in hooks]

        config.updated_at = datetime.now(UTC)

        # Remove old file if slug changed
        if config.slug != old_slug:
            self._remove_file_by_slug(old_slug)

        await self._save_config(config)
        hook_logger.info(f"Updated hook config: {config.name} ({config.id})")
        return config

    async def delete_config(self, config_id: str) -> bool:
        """Delete a hook configuration."""
        if config_id not in self.configs:
            return False

        config = self.configs[config_id]
        self._remove_file_by_slug(config.slug)
        del self.configs[config_id]
        hook_logger.info(f"Deleted hook config: {config.name} ({config_id})")
        return True

    def get_configs_by_ids(self, config_ids: list[str]) -> list[HookConfig]:
        """Get multiple configs by IDs, skipping missing/disabled ones."""
        configs = []
        for config_id in config_ids:
            config = self.configs.get(config_id)
            if config and config.enabled:
                configs.append(config)
            elif not config:
                hook_logger.warning(f"Hook config {config_id} not found (may have been deleted)")
        return configs

    def to_sdk_hooks_payload(self, configs: list[HookConfig]) -> dict[str, Any]:
        """Flatten enabled hook configs into the on-disk settings.json ``hooks`` shape.

        Shape: {"hooks": {"<Event>": [{"matcher": "...", "hooks": [<hook-config>]}]}}.
        Expands each multi-event HookEntry into one array entry per (event, matcher)
        pair. Disabled entries and disabled configs are excluded.

        Concatenation order follows the order of *configs* (caller-controlled — the
        session's hook_ids order) then entry order within each config. This is the
        only ordering WebUI hooks control; relative order against disk-based hooks
        is left entirely to the CLI's own additive union (see plan Finding #4) —
        setting_sources is never read or touched here.

        Returns {} (not {"hooks": {}}) when there is nothing to inject, so callers
        can `if payload:` to decide whether to merge it into a settings file.
        """
        events: dict[str, list[dict[str, Any]]] = {}
        for config in configs:
            if not config.enabled:
                continue
            for entry in config.hooks:
                if not entry.enabled or not entry.events:
                    continue
                hook_config = self._entry_to_hook_config(entry)
                if hook_config is None:
                    continue
                for event in entry.events:
                    rule: dict[str, Any] = {"hooks": [hook_config]}
                    if entry.matcher:
                        rule["matcher"] = entry.matcher
                    events.setdefault(event, []).append(rule)

        return {"hooks": events} if events else {}

    @staticmethod
    def _entry_to_hook_config(entry: HookEntry) -> dict[str, Any] | None:
        """Build one CLI hook-config object (BashCommandHookSchema/HttpHookSchema)."""
        if entry.type == "http":
            if not entry.url:
                return None
            cfg: dict[str, Any] = {"type": "http", "url": entry.url}
            if entry.timeout is not None:
                cfg["timeout"] = entry.timeout
            if entry.headers:
                cfg["headers"] = entry.headers
            if entry.allowed_env_vars:
                cfg["allowedEnvVars"] = entry.allowed_env_vars
            return cfg

        # command (default)
        if not entry.command:
            return None
        cfg = {"type": "command", "command": entry.command}
        if entry.timeout is not None:
            cfg["timeout"] = entry.timeout
        return cfg

    def _remove_file_by_slug(self, slug: str):
        """Remove JSON file for a given slug."""
        f = self.configs_dir / f"{slug}.json"
        if f.exists():
            f.unlink()

    async def _save_config(self, config: HookConfig):
        """Save config to disk as JSON file."""
        config_file = self.configs_dir / f"{config.slug}.json"
        with open(config_file, 'w') as f:
            json.dump(config.to_dict(), f, indent=2)
        hook_logger.debug(f"Saved hook config to disk: {config_file}")
