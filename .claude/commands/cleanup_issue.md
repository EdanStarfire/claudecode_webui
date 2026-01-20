---
description: Clean up after a merged issue - dispose minion and remove worktree
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__dispose_minion, mcp__legion__get_minion_info]
---

## Cleanup Issue

This command cleans up after an issue has been merged - disposes the worker minion and removes the worktree.

### Workflow

You are cleaning up after issue #$1 was merged. Follow these steps:

#### 1. Verify Minion Exists

**Invoke `mcp__legion__get_minion_info`** for `IssueWorker-$1`:
- Verify minion exists
- Check if minion has any active children (warn if so)

#### 2. Dispose Minion

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `IssueWorker-$1`

This transfers the minion's knowledge to you before termination.

#### 3. Remove Worktree

**Invoke the `worktree-manager` skill** to remove worktree:
- Worktree: `issue-$1`
- Location: `worktrees/issue-$1/`

The skill will:
- Verify worktree exists
- Remove worktree using `git worktree remove`
- Clean up any locks if needed
- Prune worktree references

#### 4. Update Tracking

Remove from internal tracking:
- Issue number entry
- Free up test port
- Update status to completed

#### 5. Confirm Cleanup

Inform user:
```
✅ Issue #$1 cleanup complete
- Minion IssueWorker-$1 disposed
- Worktree worktrees/issue-$1/ removed
- Resources freed

Ready to spawn workers for new issues.
```

### Skills Used

- **mcp__legion__dispose_minion** - Terminate worker minion
- **mcp__legion__get_minion_info** - Verify minion state
- **worktree-manager** - Remove worktree safely

### Important Notes

- Only run this AFTER PR is merged
- Minion's knowledge is transferred before disposal
- Worktree is permanently removed
- Test port becomes available for reuse
