"""
Template Manager for Claude Code WebUI

Manages minion templates storage and retrieval with full CRUD operations.
Templates are stored as JSON+MD file pairs in data/templates/:
  - <template_id>.json: Configuration (name, permission_mode, allowed_tools, etc.)
  - <template_id>.md: System prompt (optional, loaded as default_system_prompt)

Default templates are shipped as source files in src/default_templates/ and
seeded into data/templates/ on first run (or when new defaults are added).
"""

import importlib.resources
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .logging_config import get_logger
from .models.minion_template import MinionTemplate

# Get specialized logger for template operations
template_logger = get_logger('template_manager', category='TEMPLATE_MANAGER')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


def _get_default_templates_dir() -> Path:
    """Get the path to bundled default template source files."""
    # Use importlib.resources for package-relative path resolution
    return Path(importlib.resources.files("src") / "default_templates")


class TemplateManager:
    """Manages minion templates storage and retrieval.

    Templates are stored as file pairs in data/templates/:
      - <template_id>.json for configuration
      - <template_id>.md for system prompt (optional)
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

                # Load companion .md file as default_system_prompt
                md_file = template_file.with_suffix(".md")
                if md_file.exists():
                    data["default_system_prompt"] = md_file.read_text(encoding="utf-8").strip()
                elif "default_system_prompt" not in data:
                    data["default_system_prompt"] = None

                template = MinionTemplate.from_dict(data)
                self.templates[template.template_id] = template
                loaded_count += 1
                template_logger.debug(
                    f"Loaded template: {template.name} ({template.template_id})"
                )
            except Exception as e:
                logger.error(f"Error loading template {template_file}: {e}")

        template_logger.info(f"Loaded {loaded_count} templates from disk")

    async def create_template(
        self,
        name: str,
        permission_mode: str,
        allowed_tools: list[str] | None = None,
        default_role: str | None = None,
        default_system_prompt: str | None = None,
        description: str | None = None
    ) -> MinionTemplate:
        """Create a new template."""
        # Validate name is not empty
        if not name or not name.strip():
            raise ValueError("Template name cannot be empty")

        # Validate name uniqueness
        if any(t.name == name for t in self.templates.values()):
            raise ValueError(f"Template with name '{name}' already exists")

        # Validate permission_mode
        valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
        if permission_mode not in valid_modes:
            raise ValueError(
                f"Invalid permission_mode. Must be one of: {', '.join(valid_modes)}"
            )

        # Create template
        template = MinionTemplate(
            template_id=str(uuid.uuid4()),
            name=name.strip(),
            permission_mode=permission_mode,
            allowed_tools=allowed_tools if allowed_tools is not None else [],
            default_role=default_role,
            default_system_prompt=default_system_prompt,
            description=description
        )

        # Save to disk
        await self._save_template(template)

        # Cache in memory
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
        default_role: str | None = None,
        default_system_prompt: str | None = None,
        description: str | None = None
    ) -> MinionTemplate:
        """Update existing template."""
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        # Check name uniqueness if changing name
        if name and name.strip() != template.name:
            if any(t.name == name.strip() for t in self.templates.values()):
                raise ValueError(f"Template with name '{name}' already exists")
            template.name = name.strip()

        # Update fields if provided
        if permission_mode:
            valid_modes = ["default", "acceptEdits", "plan", "bypassPermissions"]
            if permission_mode not in valid_modes:
                raise ValueError("Invalid permission_mode")
            template.permission_mode = permission_mode

        if allowed_tools is not None:
            template.allowed_tools = allowed_tools

        if default_role is not None:
            template.default_role = default_role

        if default_system_prompt is not None:
            template.default_system_prompt = default_system_prompt

        if description is not None:
            template.description = description

        # Update timestamp
        template.updated_at = datetime.now(UTC)

        # Save to disk
        await self._save_template(template)

        template_logger.info(f"Updated template: {template.name} ({template.template_id})")
        return template

    async def delete_template(self, template_id: str) -> bool:
        """Delete template (both .json and .md files)."""
        if template_id not in self.templates:
            return False

        template = self.templates[template_id]

        # Delete JSON file
        template_file = self.templates_dir / f"{template_id}.json"
        if template_file.exists():
            template_file.unlink()

        # Delete companion .md file
        md_file = self.templates_dir / f"{template_id}.md"
        if md_file.exists():
            md_file.unlink()

        # Remove from cache
        del self.templates[template_id]

        template_logger.info(f"Deleted template: {template.name} ({template_id})")
        return True

    async def _save_template(self, template: MinionTemplate):
        """Save template to disk as JSON + optional MD file pair.

        The JSON file contains all config fields except default_system_prompt.
        The .md file contains the system prompt separately for easy editing.
        """
        # Build dict for JSON, excluding default_system_prompt
        data = template.to_dict()
        system_prompt = data.pop("default_system_prompt", None)

        # Write JSON config
        template_file = self.templates_dir / f"{template.template_id}.json"
        with open(template_file, 'w') as f:
            json.dump(data, f, indent=2)

        # Write .md system prompt (create/update/delete as needed)
        md_file = self.templates_dir / f"{template.template_id}.md"
        if system_prompt:
            md_file.write_text(system_prompt, encoding="utf-8")
        elif md_file.exists():
            md_file.unlink()

        template_logger.debug(f"Saved template to disk: {template_file}")

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
                    if source_prompt and not existing.default_system_prompt:
                        runtime_md = self.templates_dir / f"{existing.template_id}.md"
                        if not runtime_md.exists():
                            runtime_md.write_text(source_prompt, encoding="utf-8")
                            existing.default_system_prompt = source_prompt
                            prompt_seeded_count += 1
                            template_logger.info(
                                f"Seeded system prompt for existing template: {name}"
                            )
                    continue

                await self.create_template(
                    name=data["name"],
                    permission_mode=data["permission_mode"],
                    allowed_tools=data.get("allowed_tools"),
                    default_role=data.get("default_role"),
                    default_system_prompt=source_prompt,
                    description=data.get("description"),
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
