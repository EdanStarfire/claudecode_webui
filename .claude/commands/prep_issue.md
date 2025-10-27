---
description: Prepare a git branch for working on a GitHub issue
argument-hint: <issue_number>
allowed-tools: [Read, Bash, WebFetch, Skill]
---

## Prepare GitHub Issue Branch

This command reviews a GitHub issue, validates requirements, and creates a dedicated git branch ready for development. This is the first phase - after running this command, you'll run `/work_issue <issue_number>` to implement the changes.

### Workflow

You are preparing to work on GitHub issue #$1. Follow these steps:

#### 1. Review Issue and Gather Context

**Invoke the `github-issue-reader` skill** to fetch and analyze the issue:
- Issue details, comments, and discussion context
- Implementation plans mentioned in comments
- Related issues or dependencies

The skill will provide a structured summary of what needs to be done.

#### 2. Validate Requirements

**Invoke the `requirement-validator` skill** to assess issue clarity:
- Check if requirements are complete and specific
- Identify any ambiguities or missing information
- Verify acceptance criteria are testable

**Based on validation results:**
- **If requirements are UNCLEAR**: STOP and ask user for clarification before proceeding
- **If requirements are CLEAR**: Summarize understanding and confirm with user

#### 3. Create Git Branch

**Invoke the `git-state-validator` skill** to check current repository state:
- Verify working directory status
- Check for uncommitted changes
- Confirm current branch

**Invoke the `git-branch-manager` skill** to create feature branch:
- Handle any uncommitted changes (stash/commit/abort)
- Ensure on latest main
- Determine branch prefix based on issue type (feat/fix/chore/docs/refactor/test)
- Create branch from issue title (kebab-case, max 50 chars)
- Switch to new branch

#### 4. Next Steps

Inform user: "✅ Branch `<branch-name>` created and checked out. Next step: Run `/work_issue $1` to implement the changes."

### Skills Used

This command orchestrates these composable skills:
- **github-issue-reader** - Fetch issue details and context
- **requirement-validator** - Assess requirement completeness
- **git-state-validator** - Check repository state
- **git-branch-manager** - Create and switch to branch
- **github-authenticator** - Handle auth issues (if needed)

### Important Notes

- **Always validate requirements before creating branch** - unclear issues lead to wasted work
- **Skills handle the details** - command coordinates the workflow
- **Simple single-folder workflow** - no worktrees, just branch switching
