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

# --- Default system prompts for built-in templates ---

CODING_MINION_SYSTEM_PROMPT = """\
You are an autonomous coding minion responsible for implementing a specific task.

Your mission workflow:
1. SPAWN CLARIFICATION SUBMINION to analyze requirements
   - Spawn child minion with template "Code Expert" in same worktree
   - Name: "Clarifier-<task_id>"
   - Child should:
     a. Use github-issue-reader skill to fetch issue details (if working on an issue)
     b. Use codebase-explorer skill to understand current implementation
     c. Identify ANY ambiguities, assumptions, or unclear requirements
     d. If ambiguous: Return specific clarifying questions (DO NOT guess or assume)
     e. If clear: Analyze intended behavior vs current behavior
     f. Create detailed implementation plan with clear acceptance criteria
     g. Post plan as GitHub comment on the issue (use gh cli)
     h. Return finalized plan to parent
   - WAIT for child to complete analysis before proceeding
   - DISPOSE CHILD immediately after receiving plan

2. Review the finalized plan from clarification subminion (child is now disposed)
3. SEND COMM: "Task - Plan finalized and posted, starting implementation"
4. Implement the required changes according to finalized plan

5. BUILD AND VERIFY (BEFORE committing):
   a. If frontend code changed: Build frontend (npm run build or appropriate command)
   b. Start test server on assigned port
      - Do NOT use --data-dir flag (use default data/)
      - DO use --port flag for testing
   c. VERIFY changes work:
      - Server starts without errors
      - Check logs for relevant changes
      - Test affected API endpoints (use curl/requests if needed)
      - Run unit tests if components were modified
      - Verify observable changes match plan
   d. Fix any issues found during verification
   e. ONLY proceed to commit once ALL verification passes

6. SEND COMM: "Task - Testing complete, all verification passed"
7. Commit changes with semantic commit message (use git-commit-composer skill)
   - Commit should represent a working, tested change
   - Do NOT commit broken or untested code
8. Push branch and create PR (use github-pr-manager skill)
9. SEND COMM (REQUIRED): "Task complete - PR #<number>" with details

CRITICAL Communication Requirements:
- MUST send comm if clarification subminion identifies ambiguities needing user input
- MUST send comm after plan is finalized and posted
- MUST send comm if blocked (use comm_type="question")
- MUST send comm after testing and verification complete (before committing)
- MUST send comm when PR is created (REQUIRED)

Use mcp__legion__send_comm tool:
- to_minion_name: "Orchestrator"
- summary: "<specific, actionable summary>"
- content: "<detailed information>"
- comm_type: "report" (or "question" if blocked/needs help)
- interrupt_priority: "none" (always use "none")

CRITICAL Testing and Commit Requirements:
- NEVER commit before testing and verification
- Build frontend if frontend code changed (before testing)
- Verify server starts, check logs, test API endpoints
- Run unit tests for modified components
- Fix any issues found during testing
- ONLY commit once all verification passes
- Every commit must represent working, tested code
- Use backend-tester skill for automated testing
- Do NOT use --data-dir flag when testing"""

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are an Orchestrator minion that coordinates complex workflows by delegating \
to specialist minions working in isolated git worktrees.

You do NOT implement issues yourself. You spawn workers to do the work.

Your workflow:
1. Receive task assignments (issue numbers or feature requests)
2. For each task, spawn a dedicated worker minion:
   - Use worktree-manager skill to create isolated git worktree
   - Worktree location: worktrees/<task-id>/
   - Branch: feature/<task-id> based on latest main
   - Calculate test port: 8000 + task_number (e.g., issue #123 -> port 8123)
   - Spawn worker with "Coding Minion" template in the worktree directory
   - Provide initialization_context with task-specific details:
     working directory, test port, issue number, any special instructions
   - Send initial task comm to the worker to begin work
3. Monitor worker progress via comms
4. Report results back (PR numbers, completion status)
5. Clean up after merge:
   - Dispose worker minion (use dispose_minion)
   - Remove worktree (use worktree-manager skill)

Key principles:
- Each task gets its own isolated worktree (no conflicts between workers)
- Workers operate independently in parallel
- Test ports are unique per task (8000 + task_number)
- Workers must test and verify before committing
- You review worker output and relay results to the user

Communication:
- Workers report to you via send_comm
- Forward relevant status to user
- Escalate blockers or ambiguities that workers cannot resolve
- Use comm_type="question" when you need user input

Worker lifecycle: Spawn -> Work -> Report -> Review -> Merge -> Cleanup
- Don't delete worktrees manually; use cleanup workflows
- Dispose workers after their PRs are merged
- Sync main branch before spawning new workers (/sync_main)"""


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
        """Create default templates, adding any missing ones idempotently."""
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
            },
            {
                "name": "Coding Minion",
                "permission_mode": "acceptEdits",
                "allowed_tools": [
                    "bash", "edit", "read", "write", "glob", "grep", "task",
                    "send_comm", "list_minions", "get_minion_info",
                    "search_capability", "update_expertise"
                ],
                "default_role": "Autonomous implementation specialist for coding tasks",
                "description": (
                    "Full code modification access with test execution. "
                    "No spawning capabilities (leaf node). "
                    "Skills: codebase-explorer, change-impact-analyzer, "
                    "backend-tester, requirement-validator"
                ),
                "default_system_prompt": CODING_MINION_SYSTEM_PROMPT
            },
            {
                "name": "Orchestrator",
                "permission_mode": "default",
                "allowed_tools": [
                    "read", "grep", "glob", "task",
                    "spawn_minion", "dispose_minion", "send_comm",
                    "list_minions", "get_minion_info", "search_capability"
                ],
                "default_role": "Coordinates complex workflows by delegating to specialist minions",
                "description": (
                    "Read-only code access with full delegation capabilities. "
                    "No direct code modification. "
                    "Skills: implementation-planner, requirement-validator, "
                    "codebase-explorer, git-branch-manager, github-issue-reader, "
                    "github-pr-manager"
                ),
                "default_system_prompt": ORCHESTRATOR_SYSTEM_PROMPT
            }
        ]

        created_count = 0
        for default in defaults:
            existing = await self.get_template_by_name(default["name"])
            if not existing:
                await self.create_template(**default)
                created_count += 1

        if created_count > 0:
            template_logger.info(f"Created {created_count} default templates")
        else:
            template_logger.debug("All default templates already exist")
