"""
Template Manager for Claude Code WebUI

Manages minion templates storage and retrieval with full CRUD operations.
Templates are stored as JSON+MD file pairs in data/templates/:
  - <slug>.json: Configuration (name, permission_mode, allowed_tools, etc.)
  - <slug>.md: System prompt (optional, loaded as system_prompt)

File names are derived from the template name (e.g., "Coding Minion" ->
"coding_minion.json" / "coding_minion.md") so users can easily identify
and directly edit template files.

Default templates are shipped as source files in src/default_templates/ and
seeded into data/templates/ on first run (or when new defaults are added).
"""

import importlib.resources
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session_manager import SessionManager

from .logging_config import get_logger
from .models.minion_template import MinionTemplate
from .models.permission_mode import PermissionMode
from .session_config import CONFIG_FIELDS, SessionConfig
from .slug_utils import slugify as _slugify


def _migrate_template_to_config_dict(raw: dict) -> tuple[dict, bool]:
    """Promote pre-1230 flat CONFIG_FIELDS + template_overrides into a ``config`` dict.

    Returns (migrated_raw, changed). Idempotent: if ``config`` is already present,
    returns input unchanged with changed=False.
    """
    if "config" in raw:
        return raw, False

    from .session_config import CONFIG_FIELDS, DEFAULTS

    raw = dict(raw)
    config: dict = {}

    # Promote non-default flat fields (default-valued fields are dropped — the
    # template was implicitly inheriting them via the resolution chain anyway).
    for field_name in list(CONFIG_FIELDS):
        if field_name not in raw:
            continue
        value = raw.pop(field_name)
        if value is None:
            continue
        if value == DEFAULTS.get(field_name):
            continue
        config[field_name] = value

    # All template_overrides entries were explicit user intent — promote unconditionally.
    overrides = raw.pop("template_overrides", None) or {}
    for k, v in overrides.items():
        if k in CONFIG_FIELDS:
            config[k] = v

    # Handle legacy system_prompt flat field (it's a CONFIG_FIELD but stored in .md too)
    if "system_prompt" in raw:
        sp = raw.pop("system_prompt")
        if sp:
            config["system_prompt"] = sp

    # Handle legacy backward-compat field renames that may appear in very old data
    if "default_system_prompt" in raw:
        sp = raw.pop("default_system_prompt")
        if sp and "system_prompt" not in config:
            config["system_prompt"] = sp

    raw["config"] = config
    return raw, True


class TemplateConflictError(Exception):
    """Raised when importing a template whose name already exists."""

    def __init__(self, existing_id: str, name: str):
        super().__init__(f"Template with name '{name}' already exists")
        self.existing_id = existing_id
        self.name = name


class TemplateInUseError(Exception):
    """Raised when attempting to delete a template that has linked non-terminated sessions."""

    def __init__(self, template_id: str, session_ids: list[str], session_names: list[str]):
        self.template_id = template_id
        self.session_ids = session_ids
        self.session_names = session_names
        names_preview = ", ".join(session_names[:5])
        if len(session_names) > 5:
            names_preview += "..."
        super().__init__(
            f"Template {template_id} is in use by {len(session_ids)} session(s): {names_preview}"
        )

# Get specialized logger for template operations
template_logger = get_logger('template_manager', category='TEMPLATE_MANAGER')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


def _get_default_templates_dir() -> Path:
    """Get the path to bundled default template source files."""
    return Path(importlib.resources.files("src") / "default_templates")


class TemplateManager:
    """Manages minion templates storage and retrieval.

    Templates are stored as file pairs in data/templates/:
      - <slug>.json for configuration
      - <slug>.md for system prompt (optional)

    Slugs are derived from the template name for human readability.
    """

    def __init__(self, data_dir: Path):
        self.templates_dir = data_dir / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self.templates: dict[str, MinionTemplate] = {}
        template_logger.debug(f"TemplateManager initialized with data_dir: {data_dir}")

    async def load_templates(self):
        """Load all templates from disk on startup.

        For each .json file in templates_dir, loads config and checks for
        a matching .md file to use as default_system_prompt.
        """
        self.templates.clear()
        loaded_count = 0

        from .storage_utils import backup_legacy_dir_once, write_alphabetized_json
        backup_legacy_dir_once(self.templates_dir)

        for template_file in self.templates_dir.glob("*.json"):
            try:
                with open(template_file) as f:
                    data = json.load(f)

                # Phase 3 migration: promote flat CONFIG_FIELDS → config dict
                data, _changed = _migrate_template_to_config_dict(data)
                if _changed:
                    write_alphabetized_json(template_file, data)

                # Load companion .md file as config["system_prompt"]
                md_file = template_file.with_suffix(".md")
                data.setdefault("config", {})
                if md_file.exists():
                    data["config"]["system_prompt"] = md_file.read_text(encoding="utf-8").strip()
                else:
                    data["config"].setdefault("system_prompt", None)

                template = MinionTemplate.from_dict(data)
                self.templates[template.template_id] = template
                loaded_count += 1
                template_logger.debug(
                    f"Loaded template: {template.name} ({template.template_id})"
                )
            except Exception as e:
                logger.error(f"Error loading template {template_file}: {e}")

        template_logger.info(f"Loaded {loaded_count} templates from disk")

        # Migrate UUID-named files to slug-based names
        await self._migrate_legacy_filenames()

    async def _migrate_legacy_filenames(self):
        """Rename UUID-based template files to slug-based names.

        Handles upgrade from old format (template_id.json) to new format
        (slug.json). Only renames files that match UUID pattern.
        """
        uuid_pattern = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json$'
        )
        migrated = 0
        for json_file in list(self.templates_dir.glob("*.json")):
            if not uuid_pattern.match(json_file.name):
                continue

            # Find the template loaded from this file by matching template_id
            file_id = json_file.stem
            template = self.templates.get(file_id)
            if not template:
                continue

            slug = _slugify(template.name)
            new_json = self.templates_dir / f"{slug}.json"
            new_md = self.templates_dir / f"{slug}.md"
            old_md = json_file.with_suffix(".md")

            # Skip if slug file already exists (avoid collision)
            if new_json.exists():
                continue

            json_file.rename(new_json)
            if old_md.exists():
                old_md.rename(new_md)

            migrated += 1
            template_logger.debug(
                f"Migrated template files: {file_id} -> {slug} ({template.name})"
            )

        if migrated > 0:
            template_logger.info(f"Migrated {migrated} template files to slug-based names")

    async def create_template(
        self,
        name: str,
        config: SessionConfig | dict,
        role: str | None = None,
        system_prompt: str | None = None,
        description: str | None = None,
        capabilities: list[str] | None = None,
        profile_ids: dict[str, str] | None = None,
        watchdog: dict[str, Any] | None = None,
        is_default: bool = False,
        # Deprecated: template_overrides merged into config for backward compat with callers
        template_overrides: dict[str, Any] | None = None,
    ) -> MinionTemplate:
        """Create a new template.

        Args:
            name: Template display name
            config: Bundled configuration (SessionConfig) or explicit config dict
            role: Default role for minions using this template
            system_prompt: System prompt content (stored in config["system_prompt"])
            description: Template description
            capabilities: Capability tags
        """
        if not name or not name.strip():
            raise ValueError("Template name cannot be empty")

        if any(t.name == name for t in self.templates.values()):
            raise ValueError(f"Template with name '{name}' already exists")

        # Build config dict from SessionConfig (or accept explicit dict)
        if isinstance(config, dict):
            config_dict = {k: v for k, v in config.items() if k in CONFIG_FIELDS}
        else:
            if config.permission_mode not in PermissionMode._value2member_map_:
                raise ValueError(
                    f"Invalid permission_mode. Must be one of: {', '.join(PermissionMode._value2member_map_)}"
                )
            config_dict = {k: v for k, v in config.model_dump().items() if k in CONFIG_FIELDS}

        # system_prompt is a CONFIG_FIELD; the direct parameter takes precedence
        if system_prompt is not None:
            config_dict["system_prompt"] = system_prompt

        # Absorb legacy template_overrides into config (callers may still pass it)
        if template_overrides:
            for k, v in template_overrides.items():
                if k in CONFIG_FIELDS:
                    config_dict[k] = v

        template = MinionTemplate(
            template_id=str(uuid.uuid4()),
            name=name.strip(),
            role=role,
            description=description,
            capabilities=capabilities if capabilities is not None else [],
            profile_ids=profile_ids if profile_ids is not None else {},
            config=config_dict,
            watchdog=watchdog,
            is_default=is_default,
        )

        await self._save_template(template)
        self.templates[template.template_id] = template

        template_logger.info(f"Created template: {template.name} ({template.template_id})")
        return template

    async def get_template(self, template_id: str) -> MinionTemplate | None:
        """Get template by ID."""
        return self.templates.get(template_id)

    async def get_template_by_name(self, name: str) -> MinionTemplate | None:
        """Get template by name."""
        for template in self.templates.values():
            if template.name == name:
                return template
        return None

    async def list_templates(self) -> list[MinionTemplate]:
        """List all templates."""
        return list(self.templates.values())

    async def update_template(
        self,
        template_id: str,
        name: str | None = None,
        role: str | None = None,
        description: str | None = None,
        capabilities: list[str] | None = None,
        profile_ids: dict[str, str] | None = None,
        watchdog: dict[str, Any] | None = None,
        # config dict replaces all flat CONFIG_FIELD params (issue #1230)
        config: dict[str, Any] | None = None,
        # Deprecated flat-field params — absorbed into config for backward compat
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        override_system_prompt: bool | None = None,
        sandbox_enabled: bool | None = None,
        sandbox_config: dict | None = None,
        cli_path: str | None = None,
        additional_directories: list[str] | None = None,
        docker_enabled: bool | None = None,
        docker_image: str | None = None,
        docker_extra_mounts: list[str] | None = None,
        docker_home_directory: str | None = None,
        docker_proxy_enabled: bool | None = None,
        docker_proxy_image: str | None = None,
        assigned_secrets: list[str] | None = None,
        docker_proxy_allowlist_domains: list[str] | None = None,
        thinking_mode: str | None = None,
        thinking_budget_tokens: int | None = None,
        effort: str | None = None,
        history_distillation_enabled: bool | None = None,
        auto_memory_mode: str | None = None,
        auto_memory_directory: str | None = None,
        skill_creating_enabled: bool | None = None,
        mcp_server_ids: list[str] | None = None,
        enable_claudeai_mcp_servers: bool | None = None,
        strict_mcp_config: bool | None = None,
        setting_sources: list[str] | None = None,
        bare_mode: bool | None = None,
        env_scrub_enabled: bool | None = None,
        enable_streaming_text: bool | None = None,  # Issue #1486
        # Non-secret direct env passthrough (issue #1396)
        extra_env: dict[str, str] | None = None,
        provider_catalog_id: str | None = None,
        provider_model_id: str | None = None,
        # Deprecated: template_overrides absorbed into config
        template_overrides: dict[str, Any] | None = None,
    ) -> MinionTemplate:
        """Update existing template.

        When ``config`` is provided it replaces template.config entirely (explicit
        set/reset semantics). Individual CONFIG_FIELD kwargs are supported for
        backward compatibility with existing callers; they are merged into config.
        """
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        old_name = template.name

        # Check name uniqueness if changing name
        if name and name.strip() != template.name:
            if any(t.name == name.strip() for t in self.templates.values()):
                raise ValueError(f"Template with name '{name}' already exists")
            template.name = name.strip()

        if role is not None:
            template.role = role
        if description is not None:
            template.description = description
        if capabilities is not None:
            template.capabilities = capabilities
        if profile_ids is not None:
            template.profile_ids = profile_ids
        if watchdog is not None:
            template.watchdog = watchdog

        # Apply config dict if provided (replaces entire config)
        if config is not None:
            template.config = {k: v for k, v in config.items() if k in CONFIG_FIELDS}

        # Apply individual CONFIG_FIELD kwargs (backward compat + partial updates)
        flat_updates: dict[str, Any] = {}
        local_vars = {
            "permission_mode": permission_mode,
            "allowed_tools": allowed_tools,
            "disallowed_tools": disallowed_tools,
            "system_prompt": system_prompt,
            "model": model,
            "override_system_prompt": override_system_prompt,
            "sandbox_enabled": sandbox_enabled,
            "sandbox_config": sandbox_config,
            "cli_path": cli_path,
            "additional_directories": additional_directories,
            "docker_enabled": docker_enabled,
            "docker_image": docker_image,
            "docker_extra_mounts": docker_extra_mounts,
            "docker_home_directory": docker_home_directory,
            "docker_proxy_enabled": docker_proxy_enabled,
            "docker_proxy_image": docker_proxy_image,
            "assigned_secrets": assigned_secrets,
            "docker_proxy_allowlist_domains": docker_proxy_allowlist_domains,
            "thinking_mode": thinking_mode,
            "thinking_budget_tokens": thinking_budget_tokens,
            "effort": effort,
            "history_distillation_enabled": history_distillation_enabled,
            "auto_memory_mode": auto_memory_mode,
            "auto_memory_directory": auto_memory_directory,
            "skill_creating_enabled": skill_creating_enabled,
            "mcp_server_ids": mcp_server_ids,
            "enable_claudeai_mcp_servers": enable_claudeai_mcp_servers,
            "strict_mcp_config": strict_mcp_config,
            "setting_sources": setting_sources,
            "bare_mode": bare_mode,
            "env_scrub_enabled": env_scrub_enabled,
            "enable_streaming_text": enable_streaming_text,
            "extra_env": extra_env,
            "provider_catalog_id": provider_catalog_id,
            "provider_model_id": provider_model_id,
        }
        for field_name, value in local_vars.items():
            if value is not None and field_name in CONFIG_FIELDS:
                flat_updates[field_name] = value

        # Validate permission_mode if explicitly supplied
        if "permission_mode" in flat_updates:
            pm = flat_updates["permission_mode"]
            if pm not in PermissionMode._value2member_map_:
                raise ValueError("Invalid permission_mode")

        # Absorb legacy template_overrides into config
        if template_overrides:
            for k, v in template_overrides.items():
                if k in CONFIG_FIELDS:
                    flat_updates[k] = v

        template.config.update(flat_updates)
        template.updated_at = datetime.now(UTC)

        # If name changed, remove old files before saving with new slug
        if template.name != old_name:
            self._remove_files_by_slug(_slugify(old_name))

        await self._save_template(template)

        template_logger.info(f"Updated template: {template.name} ({template.template_id})")
        return template

    async def delete_template(
        self,
        template_id: str,
        session_manager: "SessionManager | None" = None,
    ) -> bool:
        """Delete template (both .json and .md files).

        Raises TemplateInUseError if session_manager is provided and non-terminated
        sessions are linked to this template (issue #1059 Phase 2).
        """
        if template_id not in self.templates:
            return False

        # Guard: block deletion when active sessions reference this template
        if session_manager:
            # Import here at runtime to avoid circular import
            # (session_manager.py imports template_manager.py at module level)
            from .session_manager import SessionState
            all_sessions = await session_manager.list_sessions()
            blocking = [
                s for s in all_sessions
                if s.template_id == template_id and s.state != SessionState.TERMINATED
            ]
            if blocking:
                raise TemplateInUseError(
                    template_id=template_id,
                    session_ids=[s.session_id for s in blocking],
                    session_names=[s.name or s.session_id for s in blocking],
                )

        template = self.templates[template_id]
        slug = _slugify(template.name)
        self._remove_files_by_slug(slug)

        del self.templates[template_id]

        template_logger.info(f"Deleted template: {template.name} ({template_id})")
        return True

    def _remove_files_by_slug(self, slug: str):
        """Remove .json and .md files for a given slug."""
        for ext in (".json", ".md"):
            f = self.templates_dir / f"{slug}{ext}"
            if f.exists():
                f.unlink()

    async def _save_template(self, template: MinionTemplate):
        """Save template to disk as JSON + optional MD file pair.

        Files are named using the slugified template name for readability.
        system_prompt lives in config["system_prompt"] but is stored in the
        companion .md file for easy direct editing.
        """
        from .storage_utils import write_alphabetized_json

        slug = _slugify(template.name)

        # Extract system_prompt from config before writing JSON
        data = template.to_dict()
        system_prompt = data["config"].pop("system_prompt", None)

        # Write JSON config (alphabetized)
        template_file = self.templates_dir / f"{slug}.json"
        write_alphabetized_json(template_file, data)

        # Write .md system prompt (create/update/delete as needed)
        md_file = self.templates_dir / f"{slug}.md"
        if system_prompt:
            md_file.write_text(system_prompt, encoding="utf-8")
        elif md_file.exists():
            md_file.unlink()

        template_logger.debug(f"Saved template to disk: {template_file}")

    async def import_template(
        self, data: dict, overwrite: bool = False
    ) -> MinionTemplate:
        """Import a template from an export envelope.

        Args:
            data: Export envelope with keys ``version`` and ``template``.
            overwrite: If True, delete any existing template with the same name
                before creating. If False, raise TemplateConflictError instead.

        Returns:
            The newly created MinionTemplate.

        Raises:
            ValueError: If the envelope is malformed or missing required fields.
            TemplateConflictError: On name conflict when overwrite=False.
        """
        if data.get("version") != 1:
            raise ValueError(f"Unsupported export version: {data.get('version')}")

        template_data = data.get("template")
        if not template_data or not isinstance(template_data, dict):
            raise ValueError("Export envelope missing 'template' object")

        name = template_data.get("name", "").strip()
        if not name:
            raise ValueError("Template name is required")

        # Check for name conflict
        existing = await self.get_template_by_name(name)
        if existing:
            if not overwrite:
                raise TemplateConflictError(existing.template_id, name)
            await self.delete_template(existing.template_id)

        # Strip identity/timestamp fields — they will be regenerated by create_template
        sanitized = {k: v for k, v in template_data.items()
                     if k not in ("template_id", "created_at", "updated_at")}

        # Carry profile_ids through import.
        # Warn if profile_ids reference profiles that don't exist locally; don't block.
        imported_profile_ids = sanitized.get("profile_ids") or {}
        if imported_profile_ids:
            template_logger.warning(
                f"Imported template '{name}' references profile_ids: {imported_profile_ids}. "
                "Verify these profiles exist locally; missing profiles fall back to defaults."
            )

        # Accept new-style envelope (has "config" key) or legacy flat-field envelope
        if "config" in sanitized:
            config_dict = sanitized["config"]
            system_prompt = config_dict.pop("system_prompt", None) or sanitized.get("system_prompt")
        else:
            # Legacy envelope: migrate flat fields into config dict inline
            migrated, _ = _migrate_template_to_config_dict(sanitized)
            config_dict = migrated.get("config", {})
            system_prompt = config_dict.pop("system_prompt", None) or sanitized.get("system_prompt")

        template = await self.create_template(
            name=name,
            config=config_dict,
            role=sanitized.get("role"),
            system_prompt=system_prompt,
            description=sanitized.get("description"),
            capabilities=sanitized.get("capabilities"),
            profile_ids=imported_profile_ids if imported_profile_ids else None,
        )

        template_logger.info(
            f"Imported template: {template.name} ({template.template_id})"
        )
        return template

    async def create_default_templates(self):
        """Seed default templates from source files into data/templates/.

        Reads JSON+MD pairs from src/default_templates/ and:
        1. Creates any templates whose names don't already exist
        2. For existing templates, seeds missing .md system prompt files
           from source defaults (upgrade path for pre-existing installs)

        This is idempotent: existing config is never overwritten, but
        missing .md prompt files are added from source defaults.
        """
        defaults_dir = _get_default_templates_dir()
        if not defaults_dir.exists():
            template_logger.warning(
                f"Default templates directory not found: {defaults_dir}"
            )
            return

        # Build name->template lookup for existing templates
        existing_by_name = {t.name: t for t in self.templates.values()}

        created_count = 0
        prompt_seeded_count = 0
        for json_file in sorted(defaults_dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)

                name = data.get("name", "")
                if not name:
                    continue

                # Load companion .md system prompt from source if present
                source_md = json_file.with_suffix(".md")
                source_prompt = None
                if source_md.exists():
                    source_prompt = source_md.read_text(encoding="utf-8").strip()

                existing = existing_by_name.get(name)
                if existing:
                    # Template exists — mark as default if not already, seed .md if missing
                    needs_save = False
                    if not existing.is_default:
                        existing.is_default = True
                        needs_save = True
                    if source_prompt and not existing.config.get("system_prompt"):
                        slug = _slugify(existing.name)
                        runtime_md = self.templates_dir / f"{slug}.md"
                        if not runtime_md.exists():
                            runtime_md.write_text(source_prompt, encoding="utf-8")
                            existing.config["system_prompt"] = source_prompt
                            needs_save = True
                            prompt_seeded_count += 1
                            template_logger.info(
                                f"Seeded system prompt for existing template: {name}"
                            )
                    if needs_save:
                        await self._save_template(existing)
                    continue

                # Migrate default template data → config dict shape
                migrated, _ = _migrate_template_to_config_dict(dict(data))
                config_dict = migrated.get("config", {})
                await self.create_template(
                    name=data["name"],
                    config=config_dict,
                    role=data.get("role") or data.get("default_role"),
                    system_prompt=source_prompt,
                    description=data.get("description"),
                    capabilities=data.get("capabilities"),
                    is_default=True,
                )
                existing_by_name[name] = await self.get_template_by_name(name)
                created_count += 1

            except Exception as e:
                logger.error(f"Error seeding default template {json_file.name}: {e}")

        if created_count > 0:
            template_logger.info(f"Created {created_count} default templates")
        if prompt_seeded_count > 0:
            template_logger.info(
                f"Seeded {prompt_seeded_count} system prompts for existing templates"
            )
        if created_count == 0 and prompt_seeded_count == 0:
            template_logger.debug("All default templates already exist")

        # Remove retired built-in templates no longer present in src/default_templates/
        retired_template_names = ["Orchestrator"]
        for retired_name in retired_template_names:
            existing = existing_by_name.get(retired_name)
            if existing:
                await self.delete_template(existing.template_id)
                template_logger.info(f"Removed retired template: {retired_name}")
