---
description: Prepare a git worktree for working on a GitHub issue
argument-hint: <issue_number>
allowed-tools: [Read, Bash, WebFetch]
---

## Prepare GitHub Issue Worktree

This command reviews a GitHub issue, validates requirements, and creates a dedicated git worktree ready for development. This is the first phase - after running this command, you'll open a new Claude Code instance in the worktree directory and run `/work_issue <issue_number>` there.

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

#### 3. Create Git Worktree
- Ensure you're up to date: `git fetch origin`
- Determine appropriate branch prefix based on issue type:
  - `feat/` for new features
  - `fix/` for bug fixes
  - `chore/` for maintenance/tooling
  - `docs/` for documentation
  - `refactor/` for code refactoring
  - `test/` for test-related changes
- Create branch name from issue title (kebab-case, max 50 chars): `<prefix>/description-from-title`
- Create worktree: `git worktree add ../<branch-name> -b <branch-name> origin/main`
- **DO NOT change to the worktree directory** - that's for the next phase

#### 4. Next Steps
- Inform user: "✅ Worktree created at `../<branch-name>`. Next steps:
  1. Set working directory to `../<branch-name>`
  2. Open a new Claude Code instance
  3. Run `/work_issue $1` in the new instance"

### Important Notes
- **Always validate requirements before creating worktree** - unclear issues lead to wasted work
- **This command stays in the main repository** - don't change directories
- **The worktree is ready for development** - all setup work is done

### Error Handling
- If worktree already exists, ask user if they want to:
  - Use existing worktree (inform them to run `/work_issue $1` there)
  - Remove and recreate worktree
  - Choose a different branch name
- If gh commands fail, check authentication: `gh auth status`
