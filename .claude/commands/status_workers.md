---
description: Show status of all active issue workers and their progress
allowed-tools: [Bash, Skill, mcp__legion__list_minions, mcp__legion__get_minion_info]
---

## Status Workers

This command displays the status of all active issue workers and their worktrees.

### Workflow

#### 1. List All Active Minions

**Invoke `mcp__legion__list_minions`** to get all active minions

#### 2. Filter Issue Workers

Filter for minions matching pattern `IssueWorker-*`:
- Extract issue numbers
- Build list of active workers

#### 3. Get Detailed Status

For each issue worker, **invoke `mcp__legion__get_minion_info`**:
- Current state (idle, working, waiting for user)
- Working directory
- Any pending communications

#### 4. Check Worktree Status

**Invoke the `worktree-manager` skill** to list all worktrees:
- Verify each worker has corresponding worktree
- Check for orphaned worktrees (no minion)
- Check for branch status

#### 5. Display Summary

Show formatted status:
```
📊 Issue Worker Status
======================

Active Workers: <count>

Issue #123 - IssueWorker-123
  Status: In Progress
  Worktree: worktrees/issue-123/
  Branch: feature/issue-123
  Test Port: 8123
  Last Activity: <timestamp>

Issue #456 - IssueWorker-456
  Status: Waiting for Review
  Worktree: worktrees/issue-456/
  Branch: fix/issue-456
  Test Port: 8456
  PR: #789
  Last Activity: <timestamp>

Orphaned Worktrees: <count if any>
  - worktrees/issue-789/ (minion not found)

Available Test Ports: <list of free ports>
```

### Skills Used

- **mcp__legion__list_minions** - Get all active minions
- **mcp__legion__get_minion_info** - Get detailed minion status
- **worktree-manager** - List and check worktrees

### Important Notes

- Shows real-time status of all workers
- Identifies orphaned resources
- Helps track which issues are in progress
- Shows which test ports are in use
