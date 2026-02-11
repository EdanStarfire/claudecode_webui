---
name: approve_plan
description: Approve a plan and spawn a Builder to implement it
disable-model-invocation: true
argument-hint: <issue_number>
allowed-tools:
  - Bash(git worktree:*)
  - Bash(gh issue view:*)
  - Bash(ls:*)
  - Skill(custom-plan-manager)
  - Skill(custom-environment-setup)
---

## Approve Plan

This skill is called when a Planner has finished and the user approves the plan. It disposes the Planner and spawns a Builder to implement the approved plan.

### Workflow

You are transitioning from planning to building for issue #$1. Follow these steps:

#### 1. Verify Planner Status

**Invoke `mcp__legion__get_minion_info`** for `Planner-$1`:
- Verify Planner exists
- Confirm Planner's work is complete

If Planner doesn't exist, check if planning was done manually and proceed to spawn Builder.

#### 2. Verify Plan Exists

Check if `custom-plan-manager` skill exists:
```bash
ls .claude/skills/custom-plan-manager/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-plan-manager` skill** with operation=`verify-plan` and issue_number=$1.
The custom skill handles checking that the plan was posted and marked as approved.

If it does not exist, fall back to GitHub verification:
```bash
gh issue view $1 --json comments --jq '.comments[-1].body' | head -20
```

Also verify `ready-to-build` label:
```bash
gh issue view $1 --json labels --jq '.labels[].name' | grep -q "ready-to-build"
```

If plan is not posted or not marked as approved, warn user and ask if they want to proceed anyway.

#### 3. Dispose Planner

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `Planner-$1`
- **delete**: true

The Planner's knowledge is archived before deletion.

#### 4. Get Environment Configuration (if custom skill exists)

Check if `custom-environment-setup` skill exists:
```bash
ls .claude/skills/custom-environment-setup/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-environment-setup` skill** with issue_number=$1 to get:
- Port configuration and test server settings
- Any project-specific initialization context for the Builder

Store the returned environment info for Builder initialization context.

If it does not exist, proceed without project-specific environment configuration.

#### 5. Get Worktree Path

Verify worktree exists:
```bash
git worktree list | grep "issue-$1"
```

Extract the absolute path for the Builder's working directory.

#### 6. Spawn Builder Minion

**Invoke `mcp__legion__spawn_minion`** with:
- **name**: `Builder-$1`
- **template_name**: `Issue Builder`
- **working_directory**: Full absolute path to `worktrees/issue-$1/`
- **role**: `Issue Builder for #$1 - Implementation specialist`
- **initialization_context**:
  ```
  You are Builder-$1, responsible for implementing issue #$1.

  Working Directory: [worktree path]
  Branch: feature/issue-$1
  Issue: #$1

  [Include environment info from custom-environment-setup if available]

  Your mission:
  1. Retrieve the approved plan (use custom-plan-manager read-plan if available, else github-issue-reader + read from comments)
  2. Create task list from the plan using TaskCreate
  3. Implement changes systematically
  4. Run custom-build-process skill if it exists (project-specific build)
  5. Run custom-quality-check skill if it exists (project-specific linting)
  6. Run custom-test-process skill if it exists (project-specific testing)
  7. Commit changes with semantic message (Fixes #$1)
  8. Push and create PR using github-pr-manager skill
  9. Send comm to Orchestrator with PR link

  CRITICAL: If custom-test-process leaves servers running, leave them for user review.
  The user will use /approve_issue $1 when satisfied.
  ```

#### 7. Start Builder Working

**Invoke `mcp__legion__send_comm`** to the new Builder:
- to_minion_name: `Builder-$1`
- summary: "Begin building issue #$1"
- content: "Start the building workflow for issue #$1. Retrieve the approved plan and begin implementation."
- comm_type: "task"
- interrupt_priority: "none"

#### 8. Confirm to User

Inform user:
```
Plan approved for issue #$1
- Planner disposed (knowledge archived)
- Builder spawned: Builder-$1
- Worktree: worktrees/issue-$1/

The Builder will:
1. Retrieve the approved plan
2. Create task list and implement changes
3. Run project-specific build, quality, and test steps (if configured)
4. Create PR and report completion

When the Builder reports completion, you can:
- Test using any running servers
- Review the PR on GitHub
- Iterate with the Builder if needed
- Run /approve_issue $1 when satisfied
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Planner status
- **mcp__legion__dispose_minion** - Dispose Planner
- **custom-plan-manager** - Verify plan exists (if exists, replaces GitHub verification)
- **custom-environment-setup** - Get project-specific environment config (if exists)
- **mcp__legion__spawn_minion** - Create Builder minion
- **mcp__legion__send_comm** - Start Builder working

### Important Notes

- Planner is disposed before Builder is spawned
- Builder retrieves plan via custom-plan-manager or GitHub, not filesystem
- Plan verification uses custom-plan-manager if available, else GitHub labels
- Environment configuration is project-specific via custom-environment-setup
- Test servers may be left running for user review (project-dependent)
- User uses /approve_issue when satisfied
