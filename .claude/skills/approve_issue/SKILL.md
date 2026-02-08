---
name: approve_issue
description: Approve completed issue work, merge PR, and clean up resources
disable-model-invocation: true
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__dispose_minion, mcp__legion__get_minion_info]
---

## Approve Issue

This skill is called when the user is satisfied with the completed work. It merges the PR, runs project-specific cleanup, disposes the Builder, and removes the worktree.

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

#### 3. Run Project-Specific Cleanup (if custom skill exists)

Check if `custom-cleanup-process` skill exists:
```bash
ls .claude/skills/custom-cleanup-process/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-cleanup-process` skill** with issue_number=$1.
The custom skill handles project-specific cleanup such as:
- Stopping test servers
- Cleaning up test data
- Any other project-specific resource cleanup

If it does not exist, skip project-specific cleanup and proceed with generic cleanup.

#### 4. Merge PR

**Invoke the `github-pr-manager` skill** to merge:
- Squash merge the PR only
- Do NOT use `--delete-branch` flag (branch cleanup happens with worktree removal)

```bash
gh pr merge --squash
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
Issue #$1 approved and cleaned up!

Completed Actions:
- PR merged (squash merge)
- Project-specific cleanup completed (if configured)
- Builder disposed (knowledge archived)
- Worktree removed: worktrees/issue-$1/
- Branch cleaned up with worktree
- Main branch updated

Ready to start new issues with /plan_issue <number>
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Builder status
- **mcp__legion__dispose_minion** - Dispose Builder
- **custom-cleanup-process** - Project-specific cleanup (if exists)
- **github-pr-manager** - Check and merge PR
- **worktree-manager** - Remove worktree

### Error Handling

**PR not mergeable:**
- Inform user of specific blockers
- Do NOT proceed with cleanup
- Keep servers running for debugging

**Custom cleanup fails:**
- Warn but continue with generic cleanup
- Log the failure for debugging

**Worktree not found:**
- Warn but continue with cleanup
- May have been cleaned up manually

**Builder not found:**
- Warn but continue with PR merge and cleanup
- Manual work doesn't require minion disposal

### Important Notes

- This is a destructive operation - resources are permanently removed
- PR is squash-merged for clean history
- Project-specific cleanup runs before merge (via custom-cleanup-process)
- All minions are disposed
- Worktree is permanently deleted
- Main branch is updated with merged changes
