---
name: custom-local-skills-guidance
description: Guide to the 6 custom injection points available for extending dev-setup workflows with project-specific behavior.
---

# Custom Local Skills Guidance

## Instructions

### When to Invoke This Skill
- Developer asks about extending the dev-setup workflow
- Developer wants to add project-specific behavior to plan, build, or cleanup phases
- Developer needs to understand which injection points are available and when they fire

### Overview

The dev-setup plugin workflow checks for 6 custom local skills at defined checkpoints. Each skill is an optional `.claude/skills/<name>/SKILL.md` file in your project. If present, the workflow invokes it automatically; if absent, the step is skipped or falls back to defaults.

### The 6 Custom Injection Points

#### 1. custom-plan-manager

| | |
|---|---|
| **Workflow Stage** | `plan_issue` (fetch/write) and `approve_plan` (verify/read) |
| **Purpose** | Replace GitHub as the issue/plan storage backend |
| **Expected Behavior** | Supports operations: `fetch-issue`, `write-plan`, `verify-plan`, `read-plan`. Each takes an `issue_number` parameter. |
| **Fallback** | `github-issue-reader` skill + `gh issue comment` for plan posting |

**When to implement:** Your team uses a tracker other than GitHub (e.g., Jira, Linear) or you want custom plan formatting/storage.

```
.claude/skills/custom-plan-manager/SKILL.md
```

#### 2. custom-environment-setup

| | |
|---|---|
| **Workflow Stage** | `plan_issue`, `approve_plan`, `status_workers` |
| **Purpose** | Provide project-specific environment context (running servers, ports, URLs, env vars) |
| **Expected Behavior** | Returns environment info for a given `issue_number`: dev server URLs, database connections, required services, port assignments. |
| **Fallback** | Skipped — no environment context is injected |

**When to implement:** Your project needs dev servers, databases, or other services configured per-issue.

```
.claude/skills/custom-environment-setup/SKILL.md
```

#### 3. custom-build-process

| | |
|---|---|
| **Workflow Stage** | Builder phase (step 5 in `approve_plan`) |
| **Purpose** | Run project-specific build steps after implementation |
| **Expected Behavior** | Executes build commands (compilation, asset generation, marketplace sync, etc.). Should exit non-zero on failure. |
| **Fallback** | Skipped — no build step runs |

**When to implement:** Your project has a build step (frontend compilation, marketplace.json sync, code generation).

```
.claude/skills/custom-build-process/SKILL.md
```

#### 4. custom-quality-check

| | |
|---|---|
| **Workflow Stage** | Builder phase (step 5 in `approve_plan`, after build) |
| **Purpose** | Run project-specific linting, formatting, or static analysis |
| **Expected Behavior** | Runs quality checks on changed files. Should report issues clearly and exit non-zero on failure. |
| **Fallback** | Skipped — follows CLAUDE.md conventions manually |

**When to implement:** Your project uses linters, formatters, or static analysis tools.

```
.claude/skills/custom-quality-check/SKILL.md
```

#### 5. custom-test-process

| | |
|---|---|
| **Workflow Stage** | Builder phase (step 6 in `approve_plan`, after quality checks) |
| **Purpose** | Run project-specific test suites, start test servers, verify functionality |
| **Expected Behavior** | Starts any required servers, runs tests, reports results. Servers should be left running for user review. |
| **Fallback** | Skipped — manual verification only |

**When to implement:** Your project has automated tests or needs test servers running for verification.

```
.claude/skills/custom-test-process/SKILL.md
```

#### 6. custom-cleanup-process

| | |
|---|---|
| **Workflow Stage** | `approve_issue` (before merge and worktree removal) |
| **Purpose** | Run project-specific cleanup before finalizing an issue |
| **Expected Behavior** | Stops servers, cleans temp files, reverts environment changes for a given `issue_number`. Runs before branch merge and worktree deletion. |
| **Fallback** | Skipped — standard git cleanup only |

**When to implement:** Your project starts services or creates resources that need cleanup after issue completion.

```
.claude/skills/custom-cleanup-process/SKILL.md
```

### Implementation Guide

Each custom skill is a standard SKILL.md file with YAML front matter:

```markdown
---
name: custom-build-process
description: Project-specific build steps for my-project.
---

# Custom Build Process

## Instructions

1. Run the build command
2. Verify output
3. Report results
```

Place the file at `.claude/skills/<injection-point-name>/SKILL.md` in your project root. The workflow detects it automatically — no configuration needed.

### Important Notes

- All 6 injection points are optional — implement only what your project needs
- Skills are detected by checking `ls .claude/skills/<name>/SKILL.md` at runtime
- Each skill can include supporting scripts in a `scripts/` subdirectory
- Custom skills run in the project's working directory (or worktree for isolated work)
- If a custom skill fails, the workflow reports the error but continues where possible
