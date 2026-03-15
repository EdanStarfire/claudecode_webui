"""
MCP Server Configuration Manager for Claude Code WebUI

Manages global MCP server configurations that can be attached to sessions.
Configs are stored as JSON files in data/mcp_servers/ with slug-based filenames.

Issue #676: Global MCP server configuration with per-session injection.
"""

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

mcp_logger = get_logger('mcp_config', category='MCP_CONFIG')
logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert config name to a filesystem-safe slug."""
    slug = name.strip().lower()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    return slug.strip('_')


@dataclass
class McpServerConfig:
    """Global MCP server configuration."""
    id: str
    name: str
    slug: str
    type: str  # "stdio" | "sse" | "http"
    # stdio fields
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # sse/http fields
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # metadata
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'McpServerConfig':
        """Create from dict loaded from JSON."""
        data.setdefault('args', [])
        data.setdefault('env', {})
        data.setdefault('headers', {})
        data.setdefault('enabled', True)
        if isinstance(data.get('created_at'), str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        if isinstance(data.get('updated_at'), str):
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        return cls(**data)

    def to_sdk_config(self) -> dict[str, Any]:
        """Convert to SDK-compatible TypedDict for ClaudeAgentOptions.mcp_servers.

        Returns the appropriate config dict based on server type:
        - stdio: McpStdioServerConfig
        - sse: McpSSEServerConfig
        - http: McpHttpServerConfig
        """
        if self.type == "stdio":
            config: dict[str, Any] = {
                "type": "stdio",
                "command": self.command,
                "args": self.args,
            }
            if self.env:
                config["env"] = self.env
            return config
        elif self.type == "sse":
            config = {
                "type": "sse",
                "url": self.url,
            }
            if self.headers:
                config["headers"] = self.headers
            return config
        elif self.type == "http":
            config = {
                "type": "http",
                "url": self.url,
            }
            if self.headers:
                config["headers"] = self.headers
            return config
        else:
            raise ValueError(f"Unknown MCP server type: {self.type}")


class McpConfigManager:
    """Manages global MCP server configurations.

    Configs are stored as JSON files in data/mcp_servers/ with slug-based filenames.
    """

    def __init__(self, data_dir: Path):
        self.configs_dir = data_dir / "mcp_servers"
        self.configs_dir.mkdir(parents=True, exist_ok=True)
        self.configs: dict[str, McpServerConfig] = {}
        mcp_logger.debug(f"McpConfigManager initialized with data_dir: {data_dir}")

    async def load_configs(self):
        """Load all configs from disk on startup."""
        self.configs.clear()
        loaded_count = 0

        for config_file in self.configs_dir.glob("*.json"):
            try:
                with open(config_file) as f:
                    data = json.load(f)
                config = McpServerConfig.from_dict(data)
                self.configs[config.id] = config
                loaded_count += 1
                mcp_logger.debug(f"Loaded MCP config: {config.name} ({config.id})")
            except Exception as e:
                logger.error(f"Error loading MCP config {config_file}: {e}")

        mcp_logger.info(f"Loaded {loaded_count} MCP configs from disk")

    async def create_config(
        self,
        name: str,
        server_type: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> McpServerConfig:
        """Create a new MCP server configuration."""
        if not name or not name.strip():
            raise ValueError("MCP server name cannot be empty")

        if server_type not in ("stdio", "sse", "http"):
            raise ValueError(f"Invalid server type: {server_type}. Must be stdio, sse, or http")

        if server_type == "stdio" and not command:
            raise ValueError("Command is required for stdio MCP servers")

        if server_type in ("sse", "http") and not url:
            raise ValueError(f"URL is required for {server_type} MCP servers")

        slug = _slugify(name)
        # Check slug uniqueness
        if any(c.slug == slug for c in self.configs.values()):
            raise ValueError(f"MCP server with name '{name}' (slug: {slug}) already exists")

        config = McpServerConfig(
            id=str(uuid.uuid4()),
            name=name.strip(),
            slug=slug,
            type=server_type,
            command=command,
            args=args or [],
            env=env or {},
            url=url,
            headers=headers or {},
            enabled=enabled,
        )

        await self._save_config(config)
        self.configs[config.id] = config
        mcp_logger.info(f"Created MCP config: {config.name} ({config.id})")
        return config

    async def get_config(self, config_id: str) -> McpServerConfig | None:
        """Get config by ID."""
        return self.configs.get(config_id)

    async def list_configs(self) -> list[McpServerConfig]:
        """List all configs."""
        return list(self.configs.values())

    async def update_config(
        self,
        config_id: str,
        name: str | None = None,
        server_type: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool | None = None,
    ) -> McpServerConfig:
        """Update existing MCP server configuration."""
        config = self.configs.get(config_id)
        if not config:
            raise ValueError(f"MCP config {config_id} not found")

        old_slug = config.slug

        if name is not None and name.strip() != config.name:
            new_slug = _slugify(name)
            if any(c.slug == new_slug and c.id != config_id for c in self.configs.values()):
                raise ValueError(f"MCP server with name '{name}' (slug: {new_slug}) already exists")
            config.name = name.strip()
            config.slug = new_slug

        if server_type is not None:
            if server_type not in ("stdio", "sse", "http"):
                raise ValueError(f"Invalid server type: {server_type}")
            config.type = server_type

        if command is not None:
            config.command = command
        if args is not None:
            config.args = args
        if env is not None:
            config.env = env
        if url is not None:
            config.url = url
        if headers is not None:
            config.headers = headers
        if enabled is not None:
            config.enabled = enabled

        config.updated_at = datetime.now(UTC)

        # Remove old file if slug changed
        if config.slug != old_slug:
            self._remove_file_by_slug(old_slug)

        await self._save_config(config)
        mcp_logger.info(f"Updated MCP config: {config.name} ({config.id})")
        return config

    async def delete_config(self, config_id: str) -> bool:
        """Delete MCP server configuration."""
        if config_id not in self.configs:
            return False

        config = self.configs[config_id]
        self._remove_file_by_slug(config.slug)
        del self.configs[config_id]
        mcp_logger.info(f"Deleted MCP config: {config.name} ({config_id})")
        return True

    def get_configs_by_ids(self, config_ids: list[str]) -> list[McpServerConfig]:
        """Get multiple configs by IDs, skipping missing/disabled ones."""
        configs = []
        for config_id in config_ids:
            config = self.configs.get(config_id)
            if config and config.enabled:
                configs.append(config)
            elif not config:
                mcp_logger.warning(f"MCP config {config_id} not found (may have been deleted)")
        return configs

    def _remove_file_by_slug(self, slug: str):
        """Remove JSON file for a given slug."""
        f = self.configs_dir / f"{slug}.json"
        if f.exists():
            f.unlink()

    async def _save_config(self, config: McpServerConfig):
        """Save config to disk as JSON file."""
        config_file = self.configs_dir / f"{config.slug}.json"
        with open(config_file, 'w') as f:
            json.dump(config.to_dict(), f, indent=2)
        mcp_logger.debug(f"Saved MCP config to disk: {config_file}")
