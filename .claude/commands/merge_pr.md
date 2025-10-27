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

**Invoke the `git-state-validator` skill** to check current branch:
- Get current branch name
- Check for uncommitted changes

**If not on PR's branch:**
- Ask user if they want to switch to PR branch first
- If yes, invoke `git-branch-manager` to switch
- If no, continue (can merge from any branch)

#### 3. Merge PR

**Invoke the `github-pr-manager` skill** to perform merge:
- Squash merge the PR
- Delete remote branch automatically
- Confirm merge success

This will:
- Squash all commits into one
- Merge to main on remote
- Delete the remote branch

#### 4. Clean Up Local Repository

**Invoke the `git-branch-manager` skill** to clean up:
- Switch to main branch
- Pull latest changes from remote
- Verify local branch is deleted

**Invoke the `git-state-validator` skill** to confirm:
- Show final git status
- Verify on main with latest changes

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
