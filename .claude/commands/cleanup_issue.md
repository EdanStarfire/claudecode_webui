---
description: Clean up after a merged issue - dispose minion and remove worktree
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__dispose_minion, mcp__legion__get_minion_info]
---

## Cleanup Issue

This command cleans up after an issue has been merged - stops test servers, disposes worker minions (Planner and/or Builder), and removes the worktree.

**Note:** For the full approval workflow (merge + cleanup), use `/approve_issue` instead. This command is for cleanup only (when PR was merged manually or cleanup is needed without merging).

### Workflow

You are cleaning up resources for issue #$1. Follow these steps:

#### 1. Calculate Ports

Calculate test ports for this issue:
- Backend Port = 8000 + ($1 % 1000)
- Vite Port = 5000 + ($1 % 1000)

#### 2. Stop Test Servers

**Invoke the `process-manager` skill** to stop any running servers:
```bash
# Find and kill processes on the ports
lsof -ti :${BACKEND_PORT} | xargs -r kill 2>/dev/null
lsof -ti :${VITE_PORT} | xargs -r kill 2>/dev/null
```

Verify servers are stopped (warn if they weren't running):
```bash
lsof -i :${BACKEND_PORT} 2>/dev/null
lsof -i :${VITE_PORT} 2>/dev/null
```

#### 3. Dispose Minions

Check for and dispose any minions for this issue:

**Check for Planner:**
**Invoke `mcp__legion__get_minion_info`** for `Planner-$1`:
- If exists, dispose with `mcp__legion__dispose_minion`

**Check for Builder:**
**Invoke `mcp__legion__get_minion_info`** for `Builder-$1`:
- If exists, check for active children (warn if so)
- Dispose with `mcp__legion__dispose_minion`

**Fallback - Check for legacy IssueWorker:**
**Invoke `mcp__legion__get_minion_info`** for `IssueWorker-$1`:
- If exists, dispose with `mcp__legion__dispose_minion`

Minion knowledge is archived before disposal.

#### 4. Remove Worktree

**Invoke the `worktree-manager` skill** to remove worktree:
- Worktree: `issue-$1`
- Location: `worktrees/issue-$1/`

The skill will:
- Verify worktree exists
- Check for uncommitted changes (warn user)
- Remove worktree using `git worktree remove`
- Clean up any locks if needed
- Prune worktree references

#### 5. Clean Up Local Branch

Delete the local branch (if exists):
```bash
git branch -d feature/issue-$1 2>/dev/null || true
git branch -d fix/issue-$1 2>/dev/null || true
git branch -d refactor/issue-$1 2>/dev/null || true
git branch -d docs/issue-$1 2>/dev/null || true
```

#### 6. Confirm Cleanup

Inform user:
```
✅ Issue #$1 cleanup complete

Stopped:
- Test servers on ports ${BACKEND_PORT}/${VITE_PORT}

Disposed:
- [Planner-$1 | Builder-$1 | IssueWorker-$1] (knowledge archived)

Removed:
- Worktree: worktrees/issue-$1/
- Branch: feature/issue-$1

Resources freed. Ready for new issues.
```

### Skills Used

- **process-manager** - Stop test servers by port
- **mcp__legion__get_minion_info** - Check minion existence
- **mcp__legion__dispose_minion** - Terminate worker minion(s)
- **worktree-manager** - Remove worktree safely

### Port Convention

- Backend: 8000 + (issue_number % 1000)
- Vite: 5000 + (issue_number % 1000)

### Error Handling

**Servers not running:**
- Warn but continue - servers may have been stopped manually

**Minion not found:**
- Warn but continue - minion may have been disposed manually

**Worktree not found:**
- Warn but continue - may have been cleaned up manually

**Uncommitted changes:**
- Warn user and ask for confirmation before proceeding
- Offer option to abort cleanup

### Important Notes

- This command does NOT merge the PR - use `/approve_issue` for that
- Use this for cleanup after manual merge or to abandon work
- Stops test servers before disposing minions
- All minion variants are checked (Planner, Builder, IssueWorker)
- Worktree is permanently removed
- All resources are freed for reuse
