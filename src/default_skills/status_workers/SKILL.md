---
name: status_workers
description: Show status of all active issue workers and their progress
allowed-tools:
  - Bash(ls:*)
  - Skill(worktree-manager)
  - Skill(custom-environment-setup)
---

## Status Workers

This skill displays the status of all active Planners, Builders, and their worktrees, including stage information and plan file paths.

### Workflow

#### 1. List All Active Minions

**Invoke `mcp__legion__list_minions`** to get all active minions

#### 2. Filter Issue Workers

Filter for minions matching patterns:
- `Planner-*` - Planning phase
- `Builder-*` - Building phase

Parse the minion name to extract issue number and optional stage:
- `Planner-42` → issue 42, no stage
- `Planner-501-backend` → issue 501, stage "backend"
- `Builder-501-frontend` → issue 501, stage "frontend"

Pattern: `{Role}-{issue_number}[-{stage}]`

Build list of active workers with their parsed issue numbers and stages.

#### 3. Get Detailed Status

For each worker, **invoke `mcp__legion__get_minion_info`**:
- Current state (idle, working, waiting for user)
- Working directory
- Role (Planner or Builder)
- Any pending communications

#### 4. Check Worktree Status

**Invoke the `worktree-manager` skill** to list all worktrees:
- Verify each worker has corresponding worktree
- Check for orphaned worktrees (no minion)
- Check for branch status

#### 5. Check Plan Files

For each active worker, check for plan file:
```bash
ls "$HOME/.cc_webui/plans/" 2>/dev/null
```

Match plan files to workers:
- `issue-42.md` → issue 42, no stage
- `issue-501-backend.md` → issue 501, stage "backend"

Identify orphaned plan files (plan exists but no active worker).

#### 6. Check Running Servers (if custom skill exists)

Check if `custom-environment-setup` skill exists:
```bash
ls .claude/skills/custom-environment-setup/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-environment-setup` skill** to get environment info for each active issue, including:
- Expected port numbers for each worker
- How to check for running servers

If it does not exist, skip server/port status display.

#### 7. Display Summary

Show formatted status:
```
Issue Worker Status
======================

Active Workers: <count>

Issue #42 - Planner-42 (Planning Phase)
  Status: Collaborating with user
  Worktree: worktrees/issue-42/
  Branch: feat/issue-42
  Plan: ~/.cc_webui/plans/issue-42.md (exists/pending)

Issue #501 - Planner-501-backend (Planning Phase, stage: backend)
  Status: Idle (awaiting /approve_plan 501 backend)
  Worktree: worktrees/issue-501-backend/
  Branch: feat/issue-501-backend
  Plan: ~/.cc_webui/plans/issue-501-backend.md (exists)

Issue #501 - Builder-501-frontend (Building Phase, stage: frontend)
  Status: Implementing
  Worktree: worktrees/issue-501-frontend/
  Branch: feat/issue-501-frontend
  Plan: ~/.cc_webui/plans/issue-501-frontend.md (exists)
  [Environment info from custom-environment-setup if available]

Issue #123 - Builder-123 (Building Phase)
  Status: Testing
  Worktree: worktrees/issue-123/
  Branch: feat/issue-123
  Plan: ~/.cc_webui/plans/issue-123.md (exists)

Orphaned Worktrees: <count if any>
  - worktrees/issue-789/ (no minion found)

Orphaned Plan Files: <count if any>
  - ~/.cc_webui/plans/issue-800.md (no active worker)
```

### Skills Used

- **mcp__legion__list_minions** - Get all active minions
- **mcp__legion__get_minion_info** - Get detailed minion status
- **worktree-manager** - List and check worktrees
- **custom-environment-setup** - Get environment info per issue (if exists)

### Important Notes

- Shows real-time status of all Planners and Builders
- Parses stage suffix from minion names for multi-stage display
- Shows plan file path and existence status per worker
- Identifies which phase each issue is in (Planning vs Building)
- Shows running servers and ports if custom-environment-setup is available
- Identifies orphaned worktrees (no minion) and orphaned plan files (no worker)
- Helps coordinate multiple parallel issues and stages
