---
name: status_workers
description: Show status of all active issue workers and their progress
allowed-tools: [Bash(ls:*), Skill, mcp__legion__list_minions, mcp__legion__get_minion_info]
---

## Status Workers

This skill displays the status of all active Planners, Builders, and their worktrees.

### Workflow

#### 1. List All Active Minions

**Invoke `mcp__legion__list_minions`** to get all active minions

#### 2. Filter Issue Workers

Filter for minions matching patterns:
- `Planner-*` - Planning phase
- `Builder-*` - Building phase

Extract issue numbers and build list of active workers.

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

#### 5. Check Running Servers (if custom skill exists)

Check if `custom-environment-setup` skill exists:
```bash
ls .claude/skills/custom-environment-setup/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-environment-setup` skill** to get environment info for each active issue, including:
- Expected port numbers for each worker
- How to check for running servers

If it does not exist, skip server/port status display.

#### 6. Display Summary

Show formatted status:
```
Issue Worker Status
======================

Active Workers: <count>

Issue #42 - Planner-42 (Planning Phase)
  Status: Collaborating with user
  Worktree: worktrees/issue-42/
  Branch: feature/issue-42

Issue #123 - Builder-123 (Building Phase)
  Status: Implementing
  Worktree: worktrees/issue-123/
  Branch: feature/issue-123
  [Environment info from custom-environment-setup if available]

Issue #456 - Builder-456 (Building Phase)
  Status: Testing
  Worktree: worktrees/issue-456/
  Branch: fix/issue-456

Orphaned Worktrees: <count if any>
  - worktrees/issue-789/ (no minion found)
```

### Skills Used

- **mcp__legion__list_minions** - Get all active minions
- **mcp__legion__get_minion_info** - Get detailed minion status
- **worktree-manager** - List and check worktrees
- **custom-environment-setup** - Get environment info per issue (if exists)

### Important Notes

- Shows real-time status of all Planners and Builders
- Identifies which phase each issue is in (Planning vs Building)
- Shows running servers and ports if custom-environment-setup is available
- Identifies orphaned resources for cleanup
- Helps coordinate multiple parallel issues
