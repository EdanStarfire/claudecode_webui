---
description: Implement changes for a GitHub issue (run inside worktree)
argument-hint: <issue_number>
allowed-tools: [Read, Write, Edit, Grep, Glob, Bash, Task]
---

## Work on GitHub Issue (Inside Worktree)

This command implements the changes for a GitHub issue, commits them, and creates a pull request. **This command must be run inside a worktree created by `/prep_issue`**, not in the main repository.

### Prerequisites

This command assumes:
1. You ran `/prep_issue $1` in the main repository
2. You opened a new Claude Code instance with working directory set to the worktree
3. You're ready to implement the changes

### Workflow

You are working on GitHub issue #$1. Follow these steps:

#### 0. Verify Worktree Environment
- Check if running in a worktree: `git rev-parse --git-common-dir` vs `git rev-parse --git-dir`
- If these are the same, **STOP** - you're in the main repository, not a worktree
- Inform user: "❌ This command must be run inside a worktree. Please use `/prep_issue $1` in the main repository first, then open a new Claude Code instance in the worktree directory."
- If different, proceed with implementation

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
- Inform user: "✅ Pull request created for issue #$1. You can continue working in this worktree or switch back to the main repository."
- **DO NOT change directories** - let the user manage their Claude Code instances

### Important Notes
- **Must run in worktree** - this command verifies it's not in the main repository
- **Stay focused on the issue** - don't add unrelated changes
- **Follow existing code patterns** - maintain consistency
- **Test your changes** - ensure functionality works as expected
- **User manages directory switching** - don't try to cd back to main repository

### Error Handling
- If not in a worktree, instruct user to run `/prep_issue $1` first
- If gh commands fail, check authentication: `gh auth status`
- If tests fail, fix issues before committing
- If push fails, check remote branch status
