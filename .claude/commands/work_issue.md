---
description: Implement changes for a GitHub issue (run after /prep_issue)
argument-hint: <issue_number>
allowed-tools: [Read, Write, Edit, Grep, Glob, Bash, Task]
---

## Work on GitHub Issue

This command implements the changes for a GitHub issue, commits them, and creates a pull request. **This command must be run after `/prep_issue`** to ensure you're on the correct branch.

### Prerequisites

This command assumes:
1. You ran `/prep_issue $1` to create and checkout the feature branch
2. You're ready to implement the changes

### Workflow

You are working on GitHub issue #$1. Follow these steps:

#### 0. Verify Branch Environment
- Check current branch: `git branch --show-current`
- Verify it's not `main` or `master`:
  - If on `main`/`master`, **STOP** - you need to run `/prep_issue $1` first
  - Inform user: "❌ You're on the main branch. Please run `/prep_issue $1` first to create a feature branch."
- If on a feature branch, proceed with implementation

#### 1. Review Issue Context
- Fetch the issue details: `gh issue view $1`
- Read any implementation plan from comments: `gh issue view $1 --comments`
- Understand what needs to be implemented

#### 2. Review Existing Code
- Read relevant files mentioned in the issue
- Use grep/glob to find related code if needed
- Understand the current implementation and architecture
- Identify all files that need to be modified

#### 3. Create or Update Implementation Plan
- If the issue has an existing plan in comments, review and update it based on current code
- If no plan exists, create a detailed implementation plan
- Post/update the plan as a comment on the issue using:
  ```bash
  gh issue comment $1 --body "$(cat <<'EOF'
  ## Implementation Plan
  [your detailed plan here]
  EOF
  )"
  ```
- Include in the plan:
  - Files to be modified
  - Specific changes needed
  - Testing approach
  - Any risks or considerations

#### 4. Implement Changes
- Work through the implementation plan step by step
- Make only the changes required by the issue - nothing more
- Ensure code follows existing patterns and conventions in the codebase
- Test changes as you go
- **Stay focused**: Each issue addresses one specific thing

#### 5. Commit Changes
- Review all changes: `git status` and `git diff`
- Add all modified files: `git add .`
- Create a descriptive commit message following this format:
  ```
  <type>: <brief description>

  <detailed explanation of what changed and why>

  Fixes #$1

  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  Co-Authored-By: Claude <noreply@anthropic.com>
  ```
- Commit: `git commit -m "$(cat <<'EOF' ... EOF)"`

#### 6. Push and Create PR
- Push branch: `git push -u origin HEAD`
- Create pull request:
  ```bash
  gh pr create --title "<type>: <description>" --body "$(cat <<'EOF'
  ## Summary
  Resolves #$1

  [Brief description of changes]

  ## Changes Made
  - [List key changes]

  ## Testing
  - [How to test these changes]

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

#### 7. Completion
- Inform user: "✅ Pull request created for issue #$1. You can:
  - Continue working on this branch
  - Switch back to main: `git checkout main`
  - Work on another issue: run `/prep_issue <number>` (will handle stashing/committing)"

### Important Notes
- **Must run on feature branch** - this command verifies you're not on main
- **Stay focused on the issue** - don't add unrelated changes
- **Follow existing code patterns** - maintain consistency
- **Test your changes** - ensure functionality works as expected
- **Simple workflow** - just branch switching, no worktree complexity

### Error Handling
- If on main branch, instruct user to run `/prep_issue $1` first
- If gh commands fail, check authentication: `gh auth status`
- If tests fail, fix issues before committing
- If push fails, check remote branch status
