---
description: Merge a GitHub PR and clean up branches
argument-hint: <pr_number>
allowed-tools: [Bash]
---

## Merge Pull Request

This command merges a GitHub pull request using squash merge, then switches back to main and pulls the latest changes.

### Workflow

You are merging GitHub pull request #$1. Follow these steps:

#### 1. Fetch PR Information
- Get PR details: `gh pr view $1 --json headRefName,state,mergeable`
- Verify PR is open and mergeable
- Extract branch name from PR

#### 2. Verify Current Branch
- Check current branch: `git branch --show-current`
- If not on the PR's branch:
  - Ask user if they want to switch to the PR branch first
  - If yes: `git checkout <pr-branch>`
  - If no: Continue with merge (can merge from any branch)

#### 3. Merge PR
- Squash merge the PR: `gh pr merge $1 --squash --delete-branch`
- This will:
  - Squash all commits into one
  - Merge to main on remote
  - Delete the remote branch automatically

#### 4. Clean Up Local Repository
- Switch to main: `git checkout main`
- Pull latest changes: `git pull origin main`
- Delete local branch is not needed - the `--delete-branch` above also deletes the local branch.
- Show final status: `git status`

#### 5. Completion
- Inform user: "✅ PR #$1 merged and cleaned up. You're now on main with the latest changes."

### Important Notes
- **Squash merge** - All commits from the PR are combined into a single commit on main
- **Auto-delete remote branch** - `gh pr merge --delete-branch` removes the remote branch
- **Clean local state** - Switches to main and removes local branch copy
- **Ready for next task** - Main is up to date and ready for new work

### Error Handling
- If PR is not open, inform user and exit
- If PR is not mergeable (conflicts, checks failing), inform user to resolve issues first
- If gh commands fail, check authentication: `gh auth status`
- If branch deletion fails (branch in use), ignore and continue
- If user has uncommitted changes when switching branches, prompt to stash or commit first
