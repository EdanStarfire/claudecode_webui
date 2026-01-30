---
description: Spawn a minion to work on a GitHub issue in an isolated worktree
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, mcp__legion__spawn_minion, mcp__legion__list_templates]
---

## Spawn Issue Worker

**DEPRECATED**: Consider using `/plan_issue` instead for the new Planner → Builder workflow.

This command spawns a single worker minion that handles both planning and implementation. For better separation of concerns, use:
- `/plan_issue <number>` - Spawns a Planner for user collaboration
- `/approve_plan <number>` - Swaps Planner for Builder when plan is ready

### Legacy Behavior

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
  1. SPAWN CLARIFICATION SUBMINION to analyze issue requirements
     - Spawn child minion with template "Code Expert" in same worktree
     - Name: "Clarifier-$1"
     - Task: Analyze issue #$1 for ambiguities and create detailed implementation plan
     - Child should:
       a. Use github-issue-reader skill to fetch issue details
       b. Use codebase-explorer skill to understand current implementation
       c. Identify ANY ambiguities, assumptions, or unclear requirements
       d. If ambiguous: Return specific clarifying questions (DO NOT guess or assume)
       e. If clear: Analyze intended behavior vs current behavior
       f. Create detailed implementation plan with clear acceptance criteria
       g. Post plan as GitHub comment on issue #$1 (use gh cli)
       h. Return finalized plan to parent
     - WAIT for child to complete analysis before proceeding
     - DISPOSE CHILD: Use mcp__legion__dispose_minion("Clarifier-$1") immediately after receiving plan

  2. Review the finalized plan from clarification subminion (child is now disposed)
  3. SEND COMM: "Issue #$1 - Plan finalized and posted, starting implementation"
  4. Implement the required changes according to finalized plan

  5. BUILD AND VERIFY (BEFORE committing):
     a. If frontend code changed: Build frontend (npm run build or appropriate command)
     b. Start test server on port 8{issue_number}
        - Do NOT use --data-dir flag (use default data/)
        - DO use --port 8{issue_number} for testing
     c. VERIFY changes work:
        - Server starts without errors
        - Check logs for relevant changes
        - Test affected API endpoints (use curl/requests if needed)
        - Run unit tests if components were modified
        - Verify observable changes match plan
     d. Fix any issues found during verification
     e. ONLY proceed to commit once ALL verification passes

  6. SEND COMM: "Issue #$1 - Testing complete, all verification passed"
  7. Commit changes with semantic commit message (use git-commit-composer skill)
     - Commit should represent a working, tested change
     - Do NOT commit broken or untested code
  8. Push branch and create PR (use github-pr-manager skill)
  9. SEND COMM (REQUIRED): "Issue #$1 complete - PR #<number>" with details

  CRITICAL Communication Requirements:
  - MUST send comm if clarification subminion identifies ambiguities needing user input
  - MUST send comm to Orchestrator after plan is finalized and posted
  - MUST send comm if blocked (use comm_type="question")
  - MUST send comm after testing and verification complete (before committing)
  - MUST send comm when PR is created (REQUIRED)

  Use mcp__legion__send_comm tool:
  - to_minion_name: "Orchestrator"
  - summary: "<specific, actionable summary>"
  - content: "<detailed information>"
  - comm_type: "report" (or "question" if blocked/needs help)
  - interrupt_priority: "none" (always use "none")

  CRITICAL Testing and Commit Requirements:
  - NEVER commit before testing and verification
  - Build frontend if frontend code changed (before testing)
  - Always test on port 8{issue_number} to avoid conflicts
  - Verify server starts, check logs, test API endpoints
  - Run unit tests for modified components
  - Fix any issues found during testing
  - ONLY commit once all verification passes
  - Every commit must represent working, tested code
  - Use backend-tester skill for automated testing
  - Do NOT use --data-dir flag when testing

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
- Status: Analyzing requirements and creating plan

The worker will:
1. Spawn clarification subminion to analyze issue for ambiguities
2. Post detailed implementation plan as GitHub comment
3. Alert you if clarifications are needed
4. Implement changes according to finalized plan
5. Build frontend (if frontend changed)
6. Test and verify changes thoroughly (server, logs, APIs, tests)
7. Only commit after all verification passes
8. Create PR and report completion
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
