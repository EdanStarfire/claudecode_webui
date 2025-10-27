---
description: Implement changes for a GitHub issue (run after /prep_issue)
argument-hint: <issue_number>
allowed-tools: [Read, Write, Edit, Grep, Glob, Bash, Task, Skill]
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

**Invoke the `git-state-validator` skill** to verify branch state:
- Check current branch is not `main` or `master`
- Verify working directory state

**If on main branch:**
- **STOP** - Inform user: "❌ You're on the main branch. Please run `/prep_issue $1` first to create a feature branch."

**If on feature branch:**
- Proceed with implementation

#### 1. Review Issue Context

**Invoke the `github-issue-reader` skill** to understand requirements:
- Fetch issue details and comments
- Extract acceptance criteria
- Note any implementation plans in comments

#### 2. Analyze Codebase

**Invoke the `codebase-explorer` skill** to understand current implementation:
- Find files related to the feature/bug
- Understand architecture patterns
- Identify dependencies

**Invoke the `change-impact-analyzer` skill** to assess scope:
- Determine what files need modification
- Identify potential side effects
- Assess risk level
- Plan testing approach

#### 3. Create or Update Implementation Plan

**Invoke the `implementation-planner` skill** to create detailed plan:
- Break down into specific steps
- Identify files and components to modify
- Define testing strategy
- Note risks and considerations

**Post plan to issue:**
```bash
gh issue comment $1 --body "$(cat <<'EOF'
## Implementation Plan
[detailed plan from implementation-planner skill]
EOF
)"
```

#### 4. Implement Changes

- Work through the implementation plan step by step
- Make only the changes required by the issue - nothing more
- Ensure code follows existing patterns and conventions
- Test changes as you go (see Testing section below)
- **Stay focused**: Each issue addresses one specific thing

#### 4.1. Testing Backend Changes

**Invoke the `backend-tester` skill** if changes involve Python backend:
- Run isolated test environment (port 8001, test_data/)
- Automated API testing with curl (preferred)
- Manual testing with background server (if UI needed)
- Verify all functionality works

**Invoke the `process-manager` skill** for background servers:
- Start server in background
- Find process by PID
- Stop server safely by PID
- Verify cleanup

#### 5. Commit Changes

**Invoke the `git-state-validator` skill** to review changes:
- Check `git status` and `git diff`
- Verify all intended changes are included
- Ensure no unintended changes

**Invoke the `git-commit-composer` skill** to create commit:
- Generate semantic commit message
- Include detailed explanation
- Link to issue with `Fixes #$1`
- Add co-author attribution

Add and commit:
```bash
git add .
git commit -m "$(cat <<'EOF'
[commit message from git-commit-composer skill]
EOF
)"
```

#### 6. Push and Create PR

Push branch to remote:
```bash
git push -u origin HEAD
```

**Invoke the `github-pr-manager` skill** to create pull request:
- Generate PR title and description
- Link to issue: `Resolves #$1`
- Summarize changes made
- Document testing approach

#### 7. Completion

Inform user: "✅ Pull request created for issue #$1. You can:
- Continue working on this branch
- Switch back to main: `git checkout main`
- Work on another issue: run `/prep_issue <number>`"

### Skills Used

This command orchestrates these composable skills:
- **git-state-validator** - Verify branch and working directory
- **github-issue-reader** - Understand requirements
- **codebase-explorer** - Find relevant code
- **change-impact-analyzer** - Assess scope and risks
- **implementation-planner** - Create structured plan
- **backend-tester** - Test Python backend changes
- **process-manager** - Manage background processes
- **git-commit-composer** - Create semantic commits
- **github-pr-manager** - Create and manage PRs
- **github-authenticator** - Handle auth issues (if needed)

### Important Notes

- **Skills handle the details** - command coordinates the workflow
- **Stay focused on the issue** - don't add unrelated changes
- **Test incrementally** - verify each step before proceeding
