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

import dataclasses
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
from .session_config import SessionConfig
from .slug_utils import slugify as _slugify

# Fields set directly by create_template() — must not be overridden by config spread.
# system_prompt exists on both SessionConfig and MinionTemplate; the direct param wins.
_DIRECT_FIELDS = {"template_id", "name", "role", "system_prompt", "description",
                  "capabilities", "profile_ids", "template_overrides", "created_at", "updated_at"}

# MinionTemplate field names — computed once at import time for performance.
_TEMPLATE_FIELDS = {f.name for f in dataclasses.fields(MinionTemplate)}


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

        for template_file in self.templates_dir.glob("*.json"):
            try:
                with open(template_file) as f:
                    data = json.load(f)

                # Load companion .md file as system_prompt
                md_file = template_file.with_suffix(".md")
                if md_file.exists():
                    data["system_prompt"] = md_file.read_text(
                        encoding="utf-8"
                    ).strip()
                elif "system_prompt" not in data:
                    data["system_prompt"] = None

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
        config: SessionConfig,
        role: str | None = None,
        system_prompt: str | None = None,
        description: str | None = None,
        capabilities: list[str] | None = None,
        profile_ids: dict[str, str] | None = None,
        template_overrides: dict[str, Any] | None = None,
        watchdog: dict[str, Any] | None = None,
    ) -> MinionTemplate:
        """Create a new template.

        Args:
            name: Template display name
            config: Bundled configuration (permission_mode, tools, model, etc.)
            role: Default role for minions using this template
            system_prompt: System prompt content
            description: Template description
            capabilities: Capability tags
        """
        if not name or not name.strip():
            raise ValueError("Template name cannot be empty")

        if any(t.name == name for t in self.templates.values()):
            raise ValueError(f"Template with name '{name}' already exists")

        if config.permission_mode not in PermissionMode._value2member_map_:
            raise ValueError(
                f"Invalid permission_mode. Must be one of: {', '.join(PermissionMode._value2member_map_)}"
            )

        # Extract config values for fields shared between SessionConfig and MinionTemplate,
        # excluding fields managed directly by this method. MinionTemplate.__post_init__
        # converts any None list fields to [] so we do not need to guard them here.
        config_data = {k: v for k, v in config.model_dump().items()
                       if k in _TEMPLATE_FIELDS and k not in _DIRECT_FIELDS}

        template = MinionTemplate(
            template_id=str(uuid.uuid4()),
            name=name.strip(),
            role=role,
            system_prompt=system_prompt,
            description=description,
            capabilities=capabilities if capabilities is not None else [],
            profile_ids=profile_ids if profile_ids is not None else {},
            template_overrides=template_overrides if template_overrides is not None else {},
            watchdog=watchdog,
            **config_data,
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
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        role: str | None = None,
        system_prompt: str | None = None,
        description: str | None = None,
        model: str | None = None,
        capabilities: list[str] | None = None,
        override_system_prompt: bool | None = None,
        sandbox_enabled: bool | None = None,
        sandbox_config: dict | None = None,
        cli_path: str | None = None,
        additional_directories: list[str] | None = None,
        # Docker session isolation (issue #496)
        docker_enabled: bool | None = None,
        docker_image: str | None = None,
        docker_extra_mounts: list[str] | None = None,
        # Docker proxy configuration (issue #1116)
        docker_home_directory: str | None = None,
        docker_proxy_enabled: bool | None = None,
        docker_proxy_image: str | None = None,
        docker_proxy_credential_names: list[str] | None = None,
        docker_proxy_allowlist_domains: list[str] | None = None,
        # Thinking and effort configuration (issue #580)
        thinking_mode: str | None = None,
        thinking_budget_tokens: int | None = None,
        effort: str | None = None,
        history_distillation_enabled: bool | None = None,
        auto_memory_mode: str | None = None,
        auto_memory_directory: str | None = None,
        skill_creating_enabled: bool | None = None,
        mcp_server_ids: list[str] | None = None,
        # MCP toggle configuration (issue #786)
        enable_claudeai_mcp_servers: bool | None = None,
        strict_mcp_config: bool | None = None,
        # Runtime feature flags (issue #1116)
        setting_sources: list[str] | None = None,
        bare_mode: bool | None = None,
        env_scrub_enabled: bool | None = None,
        # Composable profiles (issue #1062)
        profile_ids: dict[str, str] | None = None,
        template_overrides: dict[str, Any] | None = None,
        watchdog: dict[str, Any] | None = None,
    ) -> MinionTemplate:
        """Update existing template."""
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        old_name = template.name

        # Check name uniqueness if changing name
        if name and name.strip() != template.name:
            if any(t.name == name.strip() for t in self.templates.values()):
                raise ValueError(f"Template with name '{name}' already exists")
            template.name = name.strip()

        if permission_mode:
            if permission_mode not in PermissionMode._value2member_map_:
                raise ValueError("Invalid permission_mode")
            template.permission_mode = permission_mode

        if allowed_tools is not None:
            template.allowed_tools = allowed_tools

        if disallowed_tools is not None:
            template.disallowed_tools = disallowed_tools

        if role is not None:
            template.role = role

        if system_prompt is not None:
            template.system_prompt = system_prompt

        if description is not None:
            template.description = description

        if model is not None:
            template.model = model

        if capabilities is not None:
            template.capabilities = capabilities

        if override_system_prompt is not None:
            template.override_system_prompt = override_system_prompt

        if sandbox_enabled is not None:
            template.sandbox_enabled = sandbox_enabled

        if sandbox_config is not None:
            template.sandbox_config = sandbox_config

        if cli_path is not None:
            template.cli_path = cli_path

        if additional_directories is not None:
            template.additional_directories = additional_directories

        # Docker session isolation (issue #496)
        if docker_enabled is not None:
            template.docker_enabled = docker_enabled
        if docker_image is not None:
            template.docker_image = docker_image
        if docker_extra_mounts is not None:
            template.docker_extra_mounts = docker_extra_mounts

        # Docker proxy configuration (issue #1116)
        if docker_home_directory is not None:
            template.docker_home_directory = docker_home_directory
        if docker_proxy_enabled is not None:
            template.docker_proxy_enabled = docker_proxy_enabled
        if docker_proxy_image is not None:
            template.docker_proxy_image = docker_proxy_image
        if docker_proxy_credential_names is not None:
            template.docker_proxy_credential_names = docker_proxy_credential_names
        if docker_proxy_allowlist_domains is not None:
            template.docker_proxy_allowlist_domains = docker_proxy_allowlist_domains

        # Thinking and effort configuration (issue #580)
        if thinking_mode is not None:
            template.thinking_mode = thinking_mode
        if thinking_budget_tokens is not None:
            template.thinking_budget_tokens = thinking_budget_tokens
        if effort is not None:
            template.effort = effort

        if history_distillation_enabled is not None:
            template.history_distillation_enabled = history_distillation_enabled

        if auto_memory_mode is not None:
            template.auto_memory_mode = auto_memory_mode

        if auto_memory_directory is not None:
            template.auto_memory_directory = auto_memory_directory

        if skill_creating_enabled is not None:
            template.skill_creating_enabled = skill_creating_enabled

        if mcp_server_ids is not None:
            template.mcp_server_ids = mcp_server_ids

        if enable_claudeai_mcp_servers is not None:
            template.enable_claudeai_mcp_servers = enable_claudeai_mcp_servers

        if strict_mcp_config is not None:
            template.strict_mcp_config = strict_mcp_config

        # Runtime feature flags (issue #1116)
        if setting_sources is not None:
            template.setting_sources = setting_sources
        if bare_mode is not None:
            template.bare_mode = bare_mode
        if env_scrub_enabled is not None:
            template.env_scrub_enabled = env_scrub_enabled

        if profile_ids is not None:
            template.profile_ids = profile_ids

        if template_overrides is not None:
            template.template_overrides = template_overrides

        if watchdog is not None:
            template.watchdog = watchdog

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
        The JSON file contains all config fields except default_system_prompt.
        The .md file contains the system prompt separately for easy editing.
        """
        slug = _slugify(template.name)

        # Build dict for JSON, excluding system_prompt (stored in .md file)
        data = template.to_dict()
        system_prompt = data.pop("system_prompt", None)

        # Write JSON config
        template_file = self.templates_dir / f"{slug}.json"
        with open(template_file, 'w') as f:
            json.dump(data, f, indent=2)

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

        # Pydantic v2 ignores extra keys (name, role, description, etc.) and
        # applies field defaults for any missing keys — handles both legacy v1
        # envelopes and future fields without requiring manual enumeration.
        config = SessionConfig.model_validate(sanitized)

        # Carry profile_ids and template_overrides through import.
        # Warn if profile_ids reference profiles that don't exist locally; don't block.
        imported_profile_ids = sanitized.get("profile_ids") or {}
        if imported_profile_ids:
            template_logger.warning(
                f"Imported template '{name}' references profile_ids: {imported_profile_ids}. "
                "Verify these profiles exist locally; missing profiles fall back to template flat fields."
            )

        template = await self.create_template(
            name=name,
            config=config,
            role=sanitized.get("role"),
            system_prompt=sanitized.get("system_prompt"),
            description=sanitized.get("description"),
            capabilities=sanitized.get("capabilities"),
            profile_ids=imported_profile_ids if imported_profile_ids else None,
            template_overrides=sanitized.get("template_overrides"),
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
                    # Template exists — seed .md prompt file if missing
                    if source_prompt and not existing.system_prompt:
                        slug = _slugify(existing.name)
                        runtime_md = self.templates_dir / f"{slug}.md"
                        if not runtime_md.exists():
                            runtime_md.write_text(source_prompt, encoding="utf-8")
                            existing.system_prompt = source_prompt
                            prompt_seeded_count += 1
                            template_logger.info(
                                f"Seeded system prompt for existing template: {name}"
                            )
                    continue

                # Pydantic v2 ignores extra keys (name, role, description, etc.)
                # and defaults any missing fields — identical semantics to the
                # previous manual .get() calls but automatically covers new fields.
                seed_config = SessionConfig.model_validate(data)
                await self.create_template(
                    name=data["name"],
                    config=seed_config,
                    role=data.get("role") or data.get("default_role"),
                    system_prompt=source_prompt,
                    description=data.get("description"),
                    capabilities=data.get("capabilities"),
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
