---
description: Merge a GitHub PR and clean up branches
argument-hint: <pr_number>
allowed-tools: [Bash, Skill]
---

## Merge Pull Request

This command merges a GitHub pull request using squash merge, then switches back to main and pulls the latest changes.

### Workflow

You are merging GitHub pull request #$1. Follow these steps:

#### 1. Verify PR State

**Invoke the `github-pr-manager` skill** to check PR status:
- Verify PR is open and mergeable
- Check for merge conflicts
- Verify CI checks are passing
- Extract branch name

**If PR is not ready to merge:**
- Inform user of blockers (conflicts, failing checks, needs review)
- Exit and ask user to resolve issues first

#### 2. Verify Current Branch

**Invoke the `git-state-validator` skill** to verify repository state:
- Current branch name
- Uncommitted changes
- Working directory status

**If not on PR's branch**, ask user if they want to switch to PR branch first (optional, can merge from any branch)

#### 3. Merge PR

**Invoke the `github-pr-manager` skill** to perform squash merge and delete remote branch

#### 4. Clean Up Local Repository

**Invoke the `git-branch-manager` skill** to:
- Switch to main branch
- Pull latest changes
- Delete local branch (if exists, using safe deletion pattern)

**Invoke the `git-state-validator` skill** to confirm final state on main

#### 5. Completion

Inform user: "✅ PR #$1 merged and cleaned up. You're now on main with the latest changes. Ready for next task!"

### Skills Used

This command orchestrates these composable skills:
- **github-pr-manager** - Check PR state, perform merge
- **git-state-validator** - Verify current branch and status
- **git-branch-manager** - Switch branches, clean up
- **github-authenticator** - Handle auth issues (if needed)

### Important Notes

- **Squash merge** - All commits combined into single commit on main
- **Auto-cleanup** - Remote and local branches are deleted
- **Skills handle the details** - command coordinates the workflow
- **Ready for next task** - Main is up to date after merge
