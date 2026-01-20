---
description: Spawn a minion to work on a GitHub issue in an isolated worktree
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__spawn_minion, mcp__legion__list_templates]
---

## Spawn Issue Worker

This command creates an isolated git worktree for a GitHub issue and spawns a dedicated minion to work on it.

### Workflow

You are spawning a worker for GitHub issue #$1. Follow these steps:

#### 1. Validate Issue Number

Ensure the issue number is valid:
- Must be a positive integer
- Issue should exist in the repository

**Invoke the `github-issue-reader` skill** to verify issue exists and fetch details

#### 2. Create Git Worktree

**Invoke the `worktree-manager` skill** to create isolated worktree:
- Worktree name: `issue-$1`
- Location: `worktrees/issue-$1/`
- Branch: `feature/issue-$1` (or appropriate prefix based on issue type)
- Based on: latest `main` branch

The skill will:
- Ensure `worktrees/` directory exists
- Check if worktree already exists (error if so)
- Create worktree from main branch
- Switch to new feature branch in worktree

#### 3. Determine Test Port

Calculate test port for this issue:
- Port = 8000 + issue_number
- Example: Issue #123 → Port 8123

Store this for minion initialization context.

#### 4. List Available Minion Templates

**Invoke `mcp__legion__list_templates`** to see available minion types

#### 5. Spawn Minion Worker

**Invoke `mcp__legion__spawn_minion`** with:
- **name**: `IssueWorker-$1`
- **template_name**: `Code Expert` (or appropriate template)
- **working_directory**: `worktrees/issue-$1/` (absolute path)
- **role**: `GitHub Issue Worker - Implements and tests issue #$1`
- **initialization_context**:
  ```
  You are IssueWorker-$1, responsible for implementing GitHub issue #$1.

  Working Directory: worktrees/issue-$1/
  Test Server Port: 8{issue_number} (e.g., 8123 for issue #123)

  Your mission:
  1. Review issue #$1 thoroughly (use github-issue-reader skill)
  2. Analyze codebase impact (use change-impact-analyzer skill)
  3. Create implementation plan (use implementation-planner skill)
  4. SEND COMM: "Issue #$1 - Plan created, starting implementation"
  5. Implement the required changes
  6. Test your changes using isolated test server on port 8{issue_number}
     - Do NOT use --data-dir flag (use default data/)
     - DO use --port 8{issue_number} for testing
  7. SEND COMM: "Issue #$1 - Testing complete, creating PR"
  8. Commit changes with semantic commit message (use git-commit-composer skill)
  9. Push branch and create PR (use github-pr-manager skill)
  10. SEND COMM (REQUIRED): "Issue #$1 complete - PR #<number>" with details

  CRITICAL Communication Requirements:
  - MUST send comm to Orchestrator after creating plan
  - MUST send comm if blocked (use comm_type="question")
  - MUST send comm after testing complete
  - MUST send comm when PR is created (REQUIRED)

  Use mcp__legion__send_comm tool:
  - to_minion_name: "Orchestrator"
  - summary: "<specific, actionable summary>"
  - content: "<detailed information>"
  - comm_type: "report" (or "question" if blocked/needs help)
  - interrupt_priority: "none" (always use "none")

  IMPORTANT Testing Notes:
  - Always test on port 8{issue_number} to avoid conflicts
  - Use backend-tester skill for automated testing
  - Do NOT use --data-dir flag when testing
  - Test thoroughly before creating PR

  See MINION_TEMPLATE.md for complete detailed workflow and communication guidelines.
  ```
- **channels**: `#issue-workers` (for coordination with other workers)

#### 6. Track Active Worker

Add to internal tracking:
- Issue number → Minion name
- Issue number → Worktree path
- Issue number → Test port
- Issue number → Status (in_progress)

#### 7. Confirm Spawn

Inform user:
```
✅ Worker spawned for issue #$1
- Minion: IssueWorker-$1
- Worktree: worktrees/issue-$1/
- Test Port: 8{issue_number}
- Status: Working on implementation

The minion will report when PR is ready for your review.
```

### Skills Used

- **github-issue-reader** - Fetch and validate issue
- **worktree-manager** - Create isolated worktree
- **mcp__legion__spawn_minion** - Create worker minion
- **mcp__legion__list_templates** - Get available templates

### Important Notes

- Each issue gets its own isolated worktree
- Test ports are automatically assigned (8000 + issue_number)
- Minions work independently in parallel
- You'll review PRs before merging
- Worktrees cleaned up after merge
- IMPORTANT! If a directory path starts with `/var`, such as `/var/home/...`, remove the leading `/var` as it causes issues and is a side effect of a linked folder issue.
