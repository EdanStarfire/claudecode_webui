"""
Template Manager for Claude Code WebUI

Manages minion templates storage and retrieval with full CRUD operations.
"""

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


class TemplateManager:
    """Manages minion templates storage and retrieval."""

    def __init__(self, data_dir: Path):
        self.templates_dir = data_dir / "templates"
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self.templates: dict[str, MinionTemplate] = {}
        template_logger.debug(f"TemplateManager initialized with data_dir: {data_dir}")

    async def load_templates(self):
        """Load all templates from disk on startup."""
        self.templates.clear()
        loaded_count = 0

        for template_file in self.templates_dir.glob("*.json"):
            try:
                with open(template_file) as f:
                    data = json.load(f)
                    template = MinionTemplate.from_dict(data)
                    self.templates[template.template_id] = template
                    loaded_count += 1
                    template_logger.debug(f"Loaded template: {template.name} ({template.template_id})")
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
            raise ValueError(f"Invalid permission_mode. Must be one of: {', '.join(valid_modes)}")

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
        """Delete template."""
        if template_id not in self.templates:
            return False

        template = self.templates[template_id]

        # Delete from disk
        template_file = self.templates_dir / f"{template_id}.json"
        if template_file.exists():
            template_file.unlink()

        # Remove from cache
        del self.templates[template_id]

        template_logger.info(f"Deleted template: {template.name} ({template_id})")
        return True

    async def _save_template(self, template: MinionTemplate):
        """Save template to disk."""
        template_file = self.templates_dir / f"{template.template_id}.json"
        with open(template_file, 'w') as f:
            json.dump(template.to_dict(), f, indent=2)
        template_logger.debug(f"Saved template to disk: {template_file}")

    async def create_default_templates(self):
        """Create default example templates on first run."""
        # Check if any templates exist
        if self.templates:
            template_logger.debug("Templates already exist, skipping default template creation")
            return

        template_logger.info("Creating default templates")

        # Create default templates
        defaults = [
            {
                "name": "Code Expert",
                "permission_mode": "acceptEdits",
                "allowed_tools": ["bash", "edit", "read", "write", "glob", "grep"],
                "default_role": "Code review and refactoring specialist",
                "description": "Can freely edit code and run tests"
            },
            {
                "name": "Web Researcher",
                "permission_mode": "default",
                "allowed_tools": ["webfetch", "websearch", "read"],
                "default_role": "Documentation and research specialist",
                "description": "Can search web and read docs, requires permission for edits"
            },
            {
                "name": "Project Manager",
                "permission_mode": "acceptEdits",
                "allowed_tools": ["bash", "edit", "read", "send_comm", "spawn_minion"],
                "default_role": "Overseer for delegation and coordination",
                "description": "Can manage project structure and delegate tasks"
            },
            {
                "name": "Safe Sandbox",
                "permission_mode": "default",
                "allowed_tools": ["read"],
                "default_role": "Read-only analyst",
                "description": "Highly restricted for safe experimentation"
            }
        ]

        for default in defaults:
            await self.create_template(**default)

        template_logger.info(f"Created {len(defaults)} default templates")
