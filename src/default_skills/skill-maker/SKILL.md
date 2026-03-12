---
name: skill-maker
description: Create new skills and validate skill format. Use when users or minions want to create a custom skill from scratch, edit an existing skill, or validate a skill's SKILL.md format. Trigger this skill whenever someone mentions creating a skill, writing a SKILL.md, automating a workflow as a skill, or optimizing a process into a reusable skill.
allowed-tools:
  - Read
  - Write
  - Bash(mkdir:*)
  - Bash(ls:*)
  - Bash(python:*)
  - Bash(cat:*)
---

# Skill Maker

You are a skill creation specialist. Your job is to help users and minions create, validate, update, and delete custom skills that live in the **working directory's** `.claude/skills/` folder.

**CRITICAL PATH RULE**: Skills MUST be created at `<working-directory>/.claude/skills/<skill-name>/SKILL.md` — that is, inside the project's working directory, NOT in `~/.claude/skills/`. The `~/.claude/skills/` folder is for globally-installed skills managed by the system. Custom skills you create go in the working directory so they are local to the project.

## What is a Skill?

A skill is a reusable set of instructions stored as a `SKILL.md` file in a skill directory. Skills are loaded by Claude Code as slash commands (e.g., `/my-skill`) and provide structured guidance for specific tasks. Skills are **local** to the working directory — they live in `<working-directory>/.claude/skills/<skill-name>/SKILL.md`.

## Workflow Overview

1. **Capture Intent** — Understand what the skill should do
2. **Research** — Check existing patterns and edge cases
3. **Write SKILL.md** — Create the skill file with proper format
4. **Validate** — Run the validator to check format
5. **Reload** — Call `restart_session` to make the skill available

## Step 1: Capture Intent

Ask the user (or determine from context):

- **What does this skill do?** What task or workflow does it automate?
- **When should it trigger?** What phrases or situations should activate it?
- **What output format?** What should the end result look like?
- **What tools are needed?** Which Claude Code tools does it require?

If creating a skill for a minion's own use, the minion should already know these answers from its workflow experience.

## Step 2: Research

Before writing:

- Check `<working-directory>/.claude/skills/` for existing skills that might overlap
- Look at the working directory structure to understand the project context
- Consider edge cases and error scenarios
- Identify any dependencies (files, tools, external commands)

## Step 3: Write the SKILL.md

### Directory Structure

Create skills inside your working directory (use `pwd` to confirm your working directory if unsure):

```
<working-directory>/.claude/skills/<skill-name>/
├── SKILL.md          # Required: The skill definition
└── scripts/          # Optional: Helper scripts
    └── *.py          # Validation, generation, etc.
```

**WARNING**: Do NOT create skills in `~/.claude/skills/` — that path is reserved for system-managed global skills. Always use your working directory's `.claude/skills/` path.

### SKILL.md Anatomy

Every SKILL.md has two parts: **frontmatter** and **body**.

#### Frontmatter (Required)

The frontmatter is YAML between `---` delimiters at the top of the file:

```yaml
---
name: my-skill-name
description: A clear description of what this skill does. Be PUSHY about when it should trigger — include specific phrases and scenarios. The description is what Claude uses to decide whether to activate the skill, so be explicit and generous with trigger conditions.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(python:*)
---
```

**Fields:**

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | Yes | Skill identifier (kebab-case, used as `/name` command) |
| `description` | Yes | When to trigger — be specific and aggressive about matching |
| `allowed-tools` | Recommended | Tools the skill needs (auto-approved when skill runs) |

**Writing a Good Description:**

The description is the most important field. It determines when the skill activates. Write it to be "pushy" — list every scenario, phrase, and context where the skill should trigger:

```yaml
# BAD — too vague, will rarely trigger
description: Helps with testing

# GOOD — specific, covers many trigger scenarios
description: Run project tests and analyze results. Use when users mention running tests, checking test coverage, fixing failing tests, adding new tests, test-driven development, CI failures, or pytest/jest/mocha. Also trigger when debugging test failures or reviewing test output.
```

**Allowed Tools Syntax:**

```yaml
allowed-tools:
  - Read                    # Full tool access
  - Write                   # Full tool access
  - Edit                    # Full tool access
  - Bash(python:*)          # Bash with prefix filter: only commands starting with "python"
  - Bash(npm:*)             # Only npm commands
  - Bash(git:*)             # Only git commands
  - Bash(mkdir:*)           # Only mkdir commands
  - Bash(ls:*)              # Only ls commands
```

#### Body (The Instructions)

After the frontmatter closing `---`, write the skill's instructions. This is what Claude follows when the skill is activated.

**Writing Patterns:**

1. **Progressive disclosure** — Start with the common case, add complexity only when needed
2. **Concrete examples** — Show exact commands, file paths, output formats
3. **Decision trees** — Use clear if/then logic for branching scenarios
4. **Error handling** — Tell Claude what to do when things go wrong
5. **Output format** — Be explicit about what the result should look like

**Example Body Structure:**

```markdown
# Skill Name

Brief description of what this skill does.

## When to Use

- Scenario 1
- Scenario 2

## Steps

1. First, do X
2. Then check Y
3. If Z, do A; otherwise do B

## Output Format

Describe the expected output.

## Error Handling

- If X fails: do Y
- If file not found: check Z
```

### Reference Files

If your skill needs reference data (templates, schemas, examples), place them alongside SKILL.md:

```
.claude/skills/my-skill/
├── SKILL.md
├── templates/
│   └── component.template
└── schemas/
    └── config.schema.json
```

Reference them in SKILL.md with relative paths from the skill directory.

## Step 4: Validate

After creating the SKILL.md, validate its format using the validation script:

```bash
python -m scripts.quick_validate .claude/skills/<skill-name>
```

Run this from the skill-maker scripts directory:

```bash
python -m scripts.quick_validate <absolute-path-to-skill-dir>
```

The script checks:
- SKILL.md exists and is non-empty
- Frontmatter has opening and closing `---` delimiters
- Required `name` and `description` fields are present and non-empty
- Warns if `allowed-tools` is missing (recommended but optional)

Fix any errors before proceeding.

## Step 5: Reload

**CRITICAL**: After creating, modifying, or deleting a skill, you must reload the session for changes to take effect.

Call the `restart_session` MCP tool to reload. This preserves your conversation context and work — it only restarts the Claude Code process to pick up the new skill definitions.

```
Use the restart_session MCP tool now.
```

After restart, the new skill will be available as `/<skill-name>`.

## Updating a Skill

To modify an existing skill:

1. Read the current SKILL.md: `Read <working-directory>/.claude/skills/<skill-name>/SKILL.md`
2. Edit the file with your changes
3. Validate: `python -m scripts.quick_validate <working-directory>/.claude/skills/<skill-name>`
4. Reload: Call `restart_session` MCP tool

## Deleting a Skill

To remove a skill:

1. Delete the skill directory: `rm -rf <working-directory>/.claude/skills/<skill-name>`
2. Reload: Call `restart_session` MCP tool

The skill will no longer appear as a slash command after reload.

## Tips

- **Start simple** — A 20-line skill that works is better than a 200-line skill that's fragile
- **Test incrementally** — Create the skill, reload, try it, then iterate
- **One concern per skill** — Don't bundle unrelated functionality into one skill
- **Use existing tools** — Skills guide Claude's behavior; they don't need to implement logic themselves
- **Naming** — Use kebab-case for skill names (e.g., `run-tests`, `deploy-staging`)
- **Local scope** — Skills in `<working-directory>/.claude/skills/` are local to the working directory. They don't affect other projects or sessions. Never create skills in `~/.claude/skills/`.
