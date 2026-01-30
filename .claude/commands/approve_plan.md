---
description: Approve a plan and spawn a Builder to implement it
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__spawn_minion, mcp__legion__dispose_minion, mcp__legion__get_minion_info, mcp__legion__send_comm]
---

## Approve Plan

This command is called when a Planner has finished and the user approves the plan. It disposes the Planner and spawns a Builder to implement the approved plan.

### Workflow

You are transitioning from planning to building for issue #$1. Follow these steps:

#### 1. Verify Planner Status

**Invoke `mcp__legion__get_minion_info`** for `Planner-$1`:
- Verify Planner exists
- Confirm Planner's work is complete

If Planner doesn't exist, check if planning was done manually and proceed to spawn Builder.

#### 2. Verify Plan Exists

Check that the plan was posted to GitHub:
```bash
gh issue view $1 --json comments --jq '.comments[-1].body' | head -20
```

Also verify `ready-to-build` label:
```bash
gh issue view $1 --json labels --jq '.labels[].name' | grep -q "ready-to-build"
```

If plan is not posted or label missing, warn user and ask if they want to proceed anyway.

#### 3. Dispose Planner

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `Planner-$1`
- **delete**: true

The Planner's knowledge is archived before deletion.

#### 4. Calculate Ports

Calculate test ports for this issue:
- Backend Port = 8000 + ($1 % 1000)
- Vite Port = 5000 + ($1 % 1000)

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
  You are Builder-$1, responsible for implementing GitHub issue #$1.

  Working Directory: [worktree path]
  Branch: feature/issue-$1
  Issue: #$1

  Test Server Configuration:
  - Backend Port: [calculated] (8000 + issue % 1000)
  - Vite Port: [calculated] (5000 + issue % 1000)
  - Data Directory: Default (data/) - DO NOT use --data-dir flag

  Your mission:
  1. Fetch issue #$1 and find the approved implementation plan in comments
  2. Create task list from the plan using TaskCreate
  3. Implement changes systematically
  4. Build frontend if frontend code changed
  5. Start test servers on assigned ports
  6. Run tests and verify all acceptance criteria
  7. Commit changes with semantic message (Fixes #$1)
  8. Push and create PR using github-pr-manager skill
  9. Send comm to Orchestrator with PR link and test URLs

  CRITICAL: Leave test servers running for user review.
  The user will use /approve_issue $1 when satisfied.
  ```

#### 7. Start Builder Working

**Invoke `mcp__legion__send_comm`** to the new Builder:
- to_minion_name: `Builder-$1`
- summary: "Begin building issue #$1"
- content: "Start the building workflow for issue #$1. Fetch the approved plan from GitHub and begin implementation."
- comm_type: "task"
- interrupt_priority: "none"

#### 8. Confirm to User

Inform user:
```
✅ Plan approved for issue #$1
- Planner disposed (knowledge archived)
- Builder spawned: Builder-$1
- Worktree: worktrees/issue-$1/

The Builder will:
1. Fetch the approved plan from GitHub
2. Create task list and implement changes
3. Build and test thoroughly
4. Start test servers (Backend: 8xxx, Vite: 5xxx)
5. Create PR and report completion

When the Builder reports completion, you can:
- Test using the running servers
- Review the PR on GitHub
- Iterate with the Builder if needed
- Run /approve_issue $1 when satisfied
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Planner status
- **mcp__legion__dispose_minion** - Dispose Planner
- **mcp__legion__spawn_minion** - Create Builder minion
- **mcp__legion__send_comm** - Start Builder working
- **github-issue-reader** - Verify plan exists

### Important Notes

- Planner is disposed before Builder is spawned
- Builder fetches plan from GitHub, not filesystem
- Test servers stay running for user review
- User uses /approve_issue when satisfied
