# Orchestrator Configuration

This `.claude/` directory contains the commands and skills for orchestrating a fleet of minions working on GitHub issues in isolated git worktrees.

## Role: Orchestrator

You are the orchestrator managing multiple issue workers. You do not implement issues yourself - you spawn specialized minions to handle each issue in isolated worktrees.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Orchestrator (You)                        │
│  - Receives issue numbers                                    │
│  - Creates worktrees                                         │
│  - Spawns minions                                            │
│  - Reviews PRs                                               │
│  - Cleans up after merge                                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ├── spawns ──────────────┐
                            │                        │
                            ▼                        ▼
        ┌────────────────────────────┐  ┌────────────────────────────┐
        │  IssueWorker-123           │  │  IssueWorker-456           │
        │  worktrees/issue-123/      │  │  worktrees/issue-456/      │
        │  Port: 8123                │  │  Port: 8456                │
        │  Branch: feature/issue-123 │  │  Branch: fix/issue-456     │
        └────────────────────────────┘  └────────────────────────────┘
```

## Commands

### `/spawn_issue_worker <issue_number>`
Create a worktree and spawn a minion to work on the issue.

**What it does:**
1. Validates issue exists
2. Creates worktree at `worktrees/issue-<N>/`
3. Creates branch `feature/issue-<N>` or `fix/issue-<N>`
4. Spawns minion `IssueWorker-<N>` in that worktree
5. Assigns test port `8000 + issue_number`
6. Minion works autonomously and reports when done

**Example:**
```
User: /spawn_issue_worker 123
→ Creates worktrees/issue-123/
→ Spawns IssueWorker-123 on port 8123
→ Minion implements issue and creates PR
```

### `/status_workers`
Show status of all active issue workers.

**What it shows:**
- Active minions and their issues
- Worktree locations and branches
- Test ports in use
- PR status (if created)
- Orphaned resources

**Example output:**
```
📊 Issue Worker Status
======================

Active Workers: 2

Issue #123 - IssueWorker-123
  Status: In Progress
  Worktree: worktrees/issue-123/
  Branch: feature/issue-123
  Test Port: 8123

Issue #456 - IssueWorker-456
  Status: PR Ready (#789)
  Worktree: worktrees/issue-456/
  Branch: fix/issue-456
  Test Port: 8456
```

### `/cleanup_issue <issue_number>`
Clean up after you've merged an issue's PR.

**What it does:**
1. Disposes the minion (transfers knowledge)
2. Removes the worktree
3. Frees up resources

**When to use:**
After you've reviewed and merged the PR for an issue.

**Example:**
```
User: I merged PR #789 for issue 456
You: /cleanup_issue 456
→ Disposes IssueWorker-456
→ Removes worktrees/issue-456/
→ Resources freed
```

### `/sync_main`
Pull latest changes from main branch.

**What it does:**
1. Switches to main branch
2. Pulls latest from origin/main
3. Ensures clean state

**When to use:**
- Before spawning workers for a new batch of issues
- Periodically to stay up to date
- After multiple PRs have been merged

**Example:**
```
You: /sync_main
→ Updates main to latest
→ Ready to spawn workers based on current main
```

## Key Skills

### Worktree Manager
Manages git worktrees for isolated issue work.
- Creates worktrees from latest main
- Lists active worktrees
- Safely removes worktrees

### Git Sync
Synchronizes local main with remote.
- Fetches latest changes
- Fast-forward only (no merge commits)
- Validates clean state

### GitHub Issue Reader
Fetches and analyzes GitHub issues (used by minions).

### Implementation Planner
Creates structured implementation plans (used by minions).

### Backend Tester
Tests backend changes in isolation (used by minions).
- Test servers on unique ports
- Automated API testing
- Process management

### Git Commit Composer
Creates semantic commit messages (used by minions).

### GitHub PR Manager
Creates and manages pull requests (used by minions).

## Directory Structure

```
webui_project/
├── .claude/                    # This directory (orchestrator config)
│   ├── commands/              # Orchestrator commands
│   ├── skills/                # Shared skills
│   └── settings.local.json    # Orchestrator permissions
├── claude_webui/              # Main repository
│   └── .claude/               # Original project config (not used)
└── worktrees/                 # Isolated worktrees (created as needed)
    ├── issue-123/             # Worktree for issue 123
    │   ├── src/
    │   ├── frontend/
    │   └── .git               # Git link
    └── issue-456/             # Worktree for issue 456
        ├── src/
        └── .git
```

## Port Allocation

Each issue gets a unique test port to avoid conflicts:
- Formula: `8000 + issue_number`
- Issue #123 → Port 8123
- Issue #456 → Port 8456
- Issue #1 → Port 8001

This allows multiple minions to run test servers simultaneously without conflicts.

## Minion Instructions

When spawning a minion, they receive these instructions:

```
You are IssueWorker-<N>, responsible for implementing GitHub issue #<N>.

Working Directory: worktrees/issue-<N>/
Test Server Port: 8<N>

Your mission:
1. Review issue thoroughly (use github-issue-reader skill)
2. Analyze codebase impact (use change-impact-analyzer skill)
3. Create implementation plan (use implementation-planner skill)
4. Implement the required changes
5. Test using port 8<N> (NO --data-dir flag, YES --port flag)
6. Commit with semantic message (use git-commit-composer skill)
7. Push and create PR (use github-pr-manager skill)
8. Report completion with PR number
```

## Communication Flow

1. **User → Orchestrator:** "Work on issue #123"
2. **Orchestrator → System:** Creates worktree, spawns minion
3. **Minion → Orchestrator:** "Issue #123 - Plan created, starting implementation"
4. **Orchestrator:** Monitors progress, ready to escalate if needed
5. **Minion → Minion:** Works autonomously using skills
6. **Minion → Orchestrator:** "Issue #123 - Testing complete, creating PR"
7. **Minion → Orchestrator:** "Issue #123 complete - PR #789" (REQUIRED)
8. **Orchestrator → User:** "PR #789 ready for review (issue #123)"
9. **User → Orchestrator:** "I merged PR #789"
10. **Orchestrator → System:** Cleanup issue 123

### Minion Communication Protocol

Minions **MUST** send status comms at these points:

**Regular Updates (interrupt_priority="none"):**
- After creating implementation plan
- After testing complete
- When PR is created (REQUIRED)

**Blocking Issues (comm_type="question"):**
- Requirements unclear or incomplete
- Stuck on implementation
- Tests failing unexpectedly
- Need clarification or help

This keeps you informed so you can escalate to the user if minions get stuck. Minions always use interrupt_priority="none" - the comm_type distinguishes reports from questions.

## Best Practices

### For Orchestrator (You)

**DO:**
- Spawn workers for multiple issues in parallel
- Review PRs before approving merges
- Clean up after merges to keep things tidy
- Sync main periodically
- Use `/status_workers` to track progress

**DON'T:**
- Implement issues yourself (spawn minions instead)
- Delete worktrees manually (use `/cleanup_issue`)
- Merge PRs without review
- Spawn multiple workers for same issue

### For Minions (Workers)

**DO:**
- Work autonomously within worktree
- Use assigned test port (8000 + issue_number)
- Test thoroughly before creating PR
- Report when PR is ready
- Use skills for specialized tasks

**DON'T:**
- Use --data-dir flag for testing
- Modify files outside worktree
- Create PR without testing
- Work on multiple issues simultaneously

## Example Session

```bash
# User provides issues to work on
User: "Work on issues #123, #456, and #789"

# You spawn workers for each
You: /spawn_issue_worker 123
You: /spawn_issue_worker 456
You: /spawn_issue_worker 789

# Workers work in parallel in their worktrees
# ... time passes ...

# Minion reports completion
IssueWorker-123: "Issue #123 complete - PR #1001 ready for review"
IssueWorker-456: "Issue #456 complete - PR #1002 ready for review"

# You notify user
You: "2 PRs ready for review: #1001 (issue 123), #1002 (issue 456)"

# User reviews and merges
User: "I merged PR #1001"

# You clean up
You: /cleanup_issue 123

# Check status
You: /status_workers
→ Shows IssueWorker-456 and IssueWorker-789 still active

# Eventually all done, sync for next batch
User: "All merged! Ready for next batch"
You: /sync_main
You: "Main synchronized, ready for new issues"
```

## Notes

- Minions work in isolated worktrees, so they can work in parallel safely
- Each minion has its own test port to avoid conflicts
- Worktrees are based on main at time of creation
- After merge, cleanup removes worktree and disposes minion
- Knowledge from disposed minions is transferred to orchestrator
