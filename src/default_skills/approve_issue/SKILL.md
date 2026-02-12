---
name: approve_issue
description: Approve completed issue work, merge PR, and clean up resources
disable-model-invocation: true
argument-hint: <issue_number> [stage]
allowed-tools:
  - Bash(git branch -d:*)
  - Bash(git checkout:*)
  - Bash(git pull:*)
  - Bash(gh pr:*)
  - Bash(rm:*)
  - Bash(ls:*)
  - Skill(github-pr-manager)
  - Skill(custom-cleanup-process)
  - Skill(custom-plan-manager)
  - Skill(plan-manager)
  - Skill(worktree-manager)
---

## Approve Issue

This skill is called when the user is satisfied with the completed work. It merges the PR, runs project-specific cleanup, disposes both the Planner and Builder, deletes the plan file, and removes the worktree.

### Arguments

- `$1` — Issue number (required)
- `$2` — Stage name (optional, must match the stage used in `/plan_issue`)

### Suffix Convention

Compute the suffix (same as plan_issue):
- If `$2` (stage) is provided: suffix = `$1-$2`
- If no stage: suffix = `$1`

### Workflow

You are approving and cleaning up issue #$1. Follow these steps:

#### 1. Compute Suffix

Determine the naming suffix:
- If `$2` is provided: suffix = `$1-$2`
- If `$2` is not provided: suffix = `$1`

#### 2. Verify Builder Status

**Invoke `mcp__legion__get_minion_info`** for `Builder-{suffix}`:
- Verify Builder exists and has completed work
- Get worktree path and PR information

If Builder doesn't exist, check if work was done manually.

#### 3. Verify PR Status

**Invoke the `github-pr-manager` skill** to check PR status:
- Find PR for branch `feat/issue-{suffix}`
- Verify PR is open and mergeable
- Check CI checks are passing
- Verify no merge conflicts

If PR is not ready:
- Inform user of blockers (conflicts, failing checks)
- Ask user to resolve issues first
- Exit without merging

#### 4. Run Project-Specific Cleanup (if custom skill exists)

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

#### 5. Merge PR

**Invoke the `github-pr-manager` skill** to merge:
- Squash merge the PR only
- Do NOT use `--delete-branch` flag (branch cleanup happens with worktree removal)

```bash
gh pr merge --squash
```

#### 6. Dispose Planner

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `Planner-{suffix}`
- **delete**: true

If Planner doesn't exist (already disposed or manual workflow), warn but continue.

#### 7. Dispose Builder

**Invoke `mcp__legion__dispose_minion`** with:
- **minion_name**: `Builder-{suffix}`
- **delete**: true

The Builder's knowledge is archived before deletion.

If Builder doesn't exist (manual workflow), warn but continue.

#### 8. Delete Plan File

Check if `custom-plan-manager` skill exists:
```bash
ls .claude/skills/custom-plan-manager/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-plan-manager` skill** with operation=`delete-plan`, issue_number=$1, and stage=$2 (if provided).

If it does not exist, **invoke the `plan-manager` skill** with operation=`delete-plan`, issue_number=$1, and stage=$2 (if provided).

This removes the plan file from `~/.cc_webui/plans/` (or removes the `ready-to-build` label if using GitHub).

#### 9. Remove Worktree

**Invoke the `worktree-manager` skill** to remove worktree:
- Worktree: `issue-{suffix}`
- Location: `worktrees/issue-{suffix}/`

The skill will:
- Verify worktree exists
- Remove worktree using `git worktree remove`
- Clean up any locks if needed
- Prune worktree references

#### 10. Clean Up Local Branch

Delete the local branch (if exists):
```bash
git branch -d feat/issue-{suffix} 2>/dev/null || true
```

#### 11. Pull Latest Main

Update main with the merged changes:
```bash
git checkout main
git pull origin main
```

#### 12. Confirm Cleanup

Inform user:
```
Issue #$1 approved and cleaned up!{" (stage: $2)" if stage provided}

Completed Actions:
- PR merged (squash merge)
- Project-specific cleanup completed (if configured)
- Planner disposed: Planner-{suffix} (knowledge archived)
- Builder disposed: Builder-{suffix} (knowledge archived)
- Plan file deleted: ~/.cc_webui/plans/issue-{suffix}.md
- Worktree removed: worktrees/issue-{suffix}/
- Branch cleaned up with worktree
- Main branch updated

Ready to start new issues with /plan_issue <number> [stage]
```

### Skills Used

- **mcp__legion__get_minion_info** - Verify Builder status
- **mcp__legion__dispose_minion** - Dispose Planner and Builder
- **custom-plan-manager** - Delete plan (if exists, overrides plan-manager)
- **plan-manager** - Delete plan file (default)
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

**Planner not found:**
- Warn but continue with cleanup
- May have been disposed manually or was never spawned

**Builder not found:**
- Warn but continue with PR merge and cleanup
- Manual work doesn't require minion disposal

**Plan file not found:**
- Warn but continue with cleanup
- May have been deleted manually

### Important Notes

- This is a destructive operation - resources are permanently removed
- PR is squash-merged for clean history
- Project-specific cleanup runs before merge (via custom-cleanup-process)
- Both Planner and Builder are disposed (Planner was kept alive since /approve_plan)
- Plan file is deleted from ~/.cc_webui/plans/
- Worktree is permanently deleted
- Main branch is updated with merged changes
