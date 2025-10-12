---
description: Prepare a git branch for working on a GitHub issue
argument-hint: <issue_number>
allowed-tools: [Read, Bash, WebFetch]
---

## Prepare GitHub Issue Branch

This command reviews a GitHub issue, validates requirements, and creates a dedicated git branch ready for development. This is the first phase - after running this command, you'll run `/work_issue <issue_number>` to implement the changes.

### Workflow

You are preparing to work on GitHub issue #$1. Follow these steps:

#### 1. Review Issue and Gather Context
- Use `gh issue view $1` to fetch the full issue details
- Read all comments on the issue using `gh issue view $1 --comments`
- Analyze the issue description and any existing implementation plans in comments
- Review the issue title to understand the category (feat/fix/chore/docs/refactor/etc.)

#### 2. Validate Requirements
- Determine if the issue description and comments provide sufficient clarity on:
  - What needs to be implemented/fixed
  - How it should be implemented (if applicable)
  - What success looks like
- **If requirements are unclear or ambiguous**: STOP and ask the user for clarification before proceeding
- **If requirements are clear**: Summarize your understanding and confirm with user before continuing

#### 3. Create Git Branch
- Check for uncommitted changes: `git status`
  - If there are uncommitted changes, ask user if they want to:
    - Stash changes: `git stash`
    - Commit changes first
    - Abort branch creation
- Ensure you're on latest main:
  - `git checkout main` (switch to main if not already there)
  - `git fetch origin` (download latest changes)
  - `git pull origin main` (update local main)
- Determine appropriate branch prefix based on issue type:
  - `feat/` for new features
  - `fix/` for bug fixes
  - `chore/` for maintenance/tooling
  - `docs/` for documentation
  - `refactor/` for code refactoring
  - `test/` for test-related changes
- Create branch name from issue title (kebab-case, max 50 chars): `<prefix>/description-from-title`
- Create and switch to branch: `git checkout -b <branch-name>`

#### 4. Next Steps
- Inform user: "✅ Branch `<branch-name>` created and checked out. Next step: Run `/work_issue $1` to implement the changes."

### Important Notes
- **Always validate requirements before creating branch** - unclear issues lead to wasted work
- **Simple single-folder workflow** - no worktrees, just branch switching
- **Handle uncommitted changes** - don't lose work when switching branches

### Error Handling
- If branch already exists, ask user if they want to:
  - Switch to existing branch: `git checkout <branch-name>`
  - Delete and recreate: `git branch -D <branch-name> && git checkout -b <branch-name>`
  - Choose a different branch name
- If gh commands fail, check authentication: `gh auth status`
- If there are merge conflicts when pulling, help resolve them first
