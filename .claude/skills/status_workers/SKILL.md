---
name: status_workers
description: Show status of all active issue workers and their progress
allowed-tools: [Bash, Skill, mcp__legion__list_minions, mcp__legion__get_minion_info]
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

#### 5. Check Running Servers

Check for test servers on expected ports:
```bash
# Check backend ports (8xxx)
lsof -i :8000-8999 2>/dev/null | grep LISTEN

# Check vite ports (5xxx)
lsof -i :5000-5999 2>/dev/null | grep LISTEN
```

#### 6. Display Summary

Show formatted status:
```
📊 Issue Worker Status
======================

Active Workers: <count>

Issue #42 - Planner-42 (Planning Phase)
  Status: Collaborating with user
  Worktree: worktrees/issue-42/
  Branch: feature/issue-42
  Ports: Backend 8042, Vite 5042 (not started - planning)

Issue #123 - Builder-123 (Building Phase)
  Status: Implementing
  Worktree: worktrees/issue-123/
  Branch: feature/issue-123
  Ports: Backend 8123 ✓, Vite 5123 ✓ (servers running)
  PR: #789 (ready for review)

Issue #456 - Builder-456 (Building Phase)
  Status: Testing
  Worktree: worktrees/issue-456/
  Branch: fix/issue-456
  Ports: Backend 8456 ✓, Vite 5456 (not needed)

Orphaned Worktrees: <count if any>
  - worktrees/issue-789/ (no minion found)

Port Summary:
  In Use: 8042, 8123, 8456, 5123
  Available: 8000-8041, 8043-8122, ...
```

### Skills Used

- **mcp__legion__list_minions** - Get all active minions
- **mcp__legion__get_minion_info** - Get detailed minion status
- **worktree-manager** - List and check worktrees

### Important Notes

- Shows real-time status of all Planners and Builders
- Identifies which phase each issue is in (Planning vs Building)
- Shows running test servers and their ports
- Identifies orphaned resources for cleanup
- Helps coordinate multiple parallel issues
