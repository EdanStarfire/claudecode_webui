---
description: Work on a GitHub issue in a dedicated git worktree
argument-hint: <issue_number>
allowed-tools: [Read, Write, Edit, WebFetch, Grep, Glob, Task]
---

## Work on GitHub Issue

This command creates a dedicated git worktree for a GitHub issue, implements the requested changes, and creates a pull request.

### Workflow

You are working on GitHub issue #$1. Follow these steps carefully:

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
- Change to worktree directory: `cd ../<branch-name>`

#### 4. Review Existing Code
- Read relevant files mentioned in the issue
- Use grep/glob to find related code if needed
- Understand the current implementation and architecture
- Identify all files that need to be modified

#### 5. Create or Update Implementation Plan
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

#### 6. Implement Changes
- Work through the implementation plan step by step
- Make only the changes required by the issue - nothing more
- Ensure code follows existing patterns and conventions in the codebase
- Test changes as you go
- **Stay focused**: Each issue addresses one specific thing

#### 7. Commit Changes
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

#### 8. Push and Create PR
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

#### 9. Return to Main Repository
- Change back to main repository: `cd -` or `cd <original-path>`
- Inform user: "✅ Pull request created for issue #$1. Worktree remains at `../<branch-name>` for further work if needed."

### Important Notes
- **Always validate requirements before starting** - unclear issues lead to wasted work
- **Stay focused on the issue** - don't add unrelated changes
- **Follow existing code patterns** - maintain consistency
- **Test your changes** - ensure functionality works as expected
- **Worktrees persist** - you can return to them later with `cd ../<branch-name>`
- **Clean up worktrees** when done with: `git worktree remove <branch-name>`

### Error Handling
- If worktree already exists, ask user if they want to:
  - Continue working in existing worktree
  - Remove and recreate worktree
  - Choose a different branch name
- If gh commands fail, check authentication: `gh auth status`
- If tests fail, fix issues before committing
