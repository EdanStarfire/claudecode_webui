---
name: approve_issue
description: Approve completed issue work, merge PR, and clean up resources
disable-model-invocation: true
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__dispose_minion, mcp__legion__get_minion_info]
---

## Approve Issue

This skill is called when the user is satisfied with the completed work. It merges the PR, stops test servers, disposes the Builder, and removes the worktree.

### Workflow

You are approving and cleaning up issue #$1. Follow these steps:

#### 1. Verify Builder Status

**Invoke `mcp__legion__get_minion_info`** for `Builder-$1`:
- Verify Builder exists and has completed work
- Get worktree path and PR information

If Builder doesn't exist, check if work was done manually.

#### 2. Verify PR Status

**Invoke the `github-pr-manager` skill** to check PR status:
- Find PR for branch `feature/issue-$1`
- Verify PR is open and mergeable
- Check CI checks are passing
- Verify no merge conflicts

If PR is not ready:
- Inform user of blockers (conflicts, failing checks)
- Ask user to resolve issues first
- Exit without merging

#### 3. Stop Test Servers

Calculate ports:
- Backend Port = 8000 + ($1 % 1000)
- Vite Port = 5000 + ($1 % 1000)

**Invoke the `process-manager` skill** to stop servers:
```bash
# Find and kill processes on the ports
lsof -ti :${BACKEND_PORT} | xargs -r kill 2>/dev/null
lsof -ti :${VITE_PORT} | xargs -r kill 2>/dev/null
```

Verify servers are stopped:
```bash
lsof -i :${BACKEND_PORT} 2>/dev/null
lsof -i :${VITE_PORT} 2>/dev/null
```

#### 4. Merge PR

**Invoke the `github-pr-manager` skill** to merge:
- Squash merge the PR
- Delete the remote branch after merge

```bash
gh pr merge --squash --delete-branch
```

#### 5. Dispose Builder

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `Builder-$1`
- **delete**: true

The Builder's knowledge is archived before deletion.

#### 6. Remove Worktree

**Invoke the `worktree-manager` skill** to remove worktree:
- Worktree: `issue-$1`
- Location: `worktrees/issue-$1/`

The skill will:
- Verify worktree exists
- Remove worktree using `git worktree remove`
- Clean up any locks if needed
- Prune worktree references

#### 7. Clean Up Local Branch

Delete the local branch (if exists):
```bash
git branch -d feature/issue-$1 2>/dev/null || true
```

#### 8. Pull Latest Main

Update main with the merged changes:
```bash
git checkout main
git pull origin main
```

#### 9. Confirm Cleanup

Inform user:
```
✅ Issue #$1 approved and cleaned up!

Completed Actions:
- PR merged (squash merge)
- Test servers stopped (ports ${BACKEND_PORT}/${VITE_PORT})
- Builder disposed (knowledge archived)
- Worktree removed: worktrees/issue-$1/
- Branch deleted: feature/issue-$1
- Main branch updated

Ready to start new issues with /plan_issue <number>
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Builder status
- **mcp__legion__dispose_minion** - Dispose Builder
- **github-pr-manager** - Check and merge PR
- **process-manager** - Stop test servers
- **worktree-manager** - Remove worktree
- **git-branch-manager** - Clean up branches

### Port Convention

- Backend: 8000 + (issue_number % 1000)
- Vite: 5000 + (issue_number % 1000)

### Error Handling

**PR not mergeable:**
- Inform user of specific blockers
- Do NOT proceed with cleanup
- Keep servers running for debugging

**Servers not found:**
- Warn but continue with cleanup
- Servers may have already been stopped

**Worktree not found:**
- Warn but continue with cleanup
- May have been cleaned up manually

**Builder not found:**
- Warn but continue with PR merge and cleanup
- Manual work doesn't require minion disposal

### Important Notes

- This is a destructive operation - resources are permanently removed
- PR is squash-merged for clean history
- All servers and minions are stopped
- Worktree is permanently deleted
- Main branch is updated with merged changes
