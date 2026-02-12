---
name: approve_plan
description: Approve a plan and spawn a Builder to implement it
disable-model-invocation: true
argument-hint: <issue_number> [stage]
allowed-tools:
  - Bash(git worktree:*)
  - Bash(ls:*)
  - Skill(custom-plan-manager)
  - Skill(plan-manager)
  - Skill(custom-environment-setup)
---

## Approve Plan

This skill is called when a Planner has finished and the user approves the plan. It spawns a Builder to implement the approved plan. The Planner remains alive (idle) until `/approve_issue`.

### Arguments

- `$1` — Issue number (required)
- `$2` — Stage name (optional, must match the stage used in `/plan_issue`)

### Suffix Convention

Compute the suffix (same as plan_issue):
- If `$2` (stage) is provided: suffix = `$1-$2`
- If no stage: suffix = `$1`

### Workflow

You are transitioning from planning to building for issue #$1. Follow these steps:

#### 1. Compute Suffix

Determine the naming suffix:
- If `$2` is provided: suffix = `$1-$2`
- If `$2` is not provided: suffix = `$1`

#### 2. Verify Planner Status

**Invoke `mcp__legion__get_minion_info`** for `Planner-{suffix}`:
- Verify Planner exists
- Confirm Planner's work is complete

If Planner doesn't exist, check if planning was done manually and proceed to spawn Builder.

#### 3. Verify Plan Exists

Check if `custom-plan-manager` skill exists:
```bash
ls .claude/skills/custom-plan-manager/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-plan-manager` skill** with operation=`verify-plan`, issue_number=$1, and stage=$2 (if provided).
The custom skill handles checking that the plan was posted and marked as approved.

If it does not exist, **invoke the `plan-manager` skill** with operation=`verify-plan`, issue_number=$1, and stage=$2 (if provided).
The default skill checks that the plan file exists at `~/.cc_webui/plans/issue-{suffix}.md`.

If plan is not found, warn user and ask if they want to proceed anyway.

#### 4. Get Plan File Path

The plan file path for the Builder is:
- `$HOME/.cc_webui/plans/issue-{suffix}.md`

This path will be passed to the Builder in its initialization context so it can read the plan directly.

If using `custom-plan-manager`, the Builder will use the custom skill's `read-plan` operation instead.

#### 5. Get Environment Configuration (if custom skill exists)

Check if `custom-environment-setup` skill exists:
```bash
ls .claude/skills/custom-environment-setup/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-environment-setup` skill** with issue_number=$1 to get:
- Port configuration and test server settings
- Any project-specific initialization context for the Builder

Store the returned environment info for Builder initialization context.

If it does not exist, proceed without project-specific environment configuration.

#### 6. Get Worktree Path

Verify worktree exists:
```bash
git worktree list | grep "issue-{suffix}"
```

Extract the absolute path for the Builder's working directory.

#### 7. Spawn Builder Minion

**Invoke `mcp__legion__spawn_minion`** with:
- **name**: `Builder-{suffix}`
- **template_name**: `Issue Builder`
- **working_directory**: Full absolute path to `worktrees/issue-{suffix}/`
- **role**: `Issue Builder for #{suffix} - Implementation specialist`
- **initialization_context**:
  ```
  You are Builder-{suffix}, responsible for implementing issue #$1.

  Working Directory: [worktree path]
  Branch: feat/issue-{suffix}
  Issue: #$1
  Stage: {$2 if provided, else "default"}
  Plan File: $HOME/.cc_webui/plans/issue-{suffix}.md

  [Include environment info from custom-environment-setup if available]

  Your mission:
  1. Retrieve the approved plan (use custom-plan-manager read-plan if available, else plan-manager read-plan)
  2. Create task list from the plan using TaskCreate
  3. Implement changes systematically
  4. Run custom-build-process skill if it exists (project-specific build)
  5. Run custom-quality-check skill if it exists (project-specific linting)
  6. Run custom-test-process skill if it exists (project-specific testing)
  7. Commit changes with semantic message (Fixes #$1)
  8. Push and create PR using github-pr-manager skill
  9. Send comm to Orchestrator with PR link

  CRITICAL: If custom-test-process leaves servers running, leave them for user review.
  The user will use /approve_issue $1 {$2 if provided} when satisfied.
  ```

#### 8. Start Builder Working

**Invoke `mcp__legion__send_comm`** to the new Builder:
- to_minion_name: `Builder-{suffix}`
- summary: "Begin building issue #$1"
- content: "Start the building workflow for issue #$1. Retrieve the approved plan and begin implementation."
- comm_type: "task"
- interrupt_priority: "none"

#### 9. Confirm to User

Inform user:
```
Plan approved for issue #$1{" (stage: $2)" if stage provided}
- Planner: Planner-{suffix} (remains alive for reference)
- Builder spawned: Builder-{suffix}
- Worktree: worktrees/issue-{suffix}/
- Plan file: ~/.cc_webui/plans/issue-{suffix}.md

The Builder will:
1. Retrieve the approved plan from file
2. Create task list and implement changes
3. Run project-specific build, quality, and test steps (if configured)
4. Create PR and report completion

When the Builder reports completion, you can:
- Test using any running servers
- Review the PR on GitHub
- Iterate with the Builder if needed
- Run /approve_issue $1 {$2 if provided} when satisfied
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Planner status
- **custom-plan-manager** - Verify plan exists (if exists, overrides plan-manager)
- **plan-manager** - Verify plan exists (default, checks file at ~/.cc_webui/plans/)
- **custom-environment-setup** - Get project-specific environment config (if exists)
- **mcp__legion__spawn_minion** - Create Builder minion
- **mcp__legion__send_comm** - Start Builder working

### Important Notes

- **Planner is NOT disposed** — it remains alive (idle) until `/approve_issue`
- Builder receives plan file path in initialization context
- Builder reads plan via custom-plan-manager read-plan or plan-manager read-plan
- Plan verification uses custom-plan-manager if available, else plan-manager (file check)
- Environment configuration is project-specific via custom-environment-setup
- Test servers may be left running for user review (project-dependent)
- User uses /approve_issue when satisfied (disposes both Planner and Builder)
