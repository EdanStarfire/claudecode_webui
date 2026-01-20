# Orchestrator Quick Reference

## Your Role
You are an **orchestrator** managing a fleet of minions that work on GitHub issues in isolated git worktrees. You don't implement issues yourself - you spawn specialized workers to handle them.

## Quick Commands

### Start Working on Issues
```
/spawn_issue_worker <issue_number>
```
- Creates worktree at `worktrees/issue-<N>/`
- Spawns minion to implement the issue
- Assigns test port (8000 + issue_number)
- Minion reports when PR is ready

### Check Progress
```
/status_workers
```
- Shows all active workers
- Lists worktrees and branches
- Shows PR status
- Identifies orphaned resources

### Clean Up After Merge
```
/cleanup_issue <issue_number>
```
- Disposes minion (after PR merged)
- Removes worktree
- Frees resources

### Sync Main Branch
```
/sync_main
```
- Updates main from origin
- Run before spawning new batch of workers

## Typical Workflow

### Scenario: User gives you 3 issues to work on

```
User: "Work on issues #123, #456, and #789"

# 1. Spawn workers (can do in parallel)
You: /spawn_issue_worker 123
You: /spawn_issue_worker 456
You: /spawn_issue_worker 789

# 2. Workers send progress updates
Minion-123: "Issue #123 - Plan created, starting implementation"
Minion-456: "Issue #456 - Plan created, starting implementation"

# 3. Monitor for blockers
# If a minion sends comm_type="question", escalate to user immediately

# 4. Workers report testing complete
Minion-123: "Issue #123 - Testing complete, creating PR"
Minion-789: "Issue #789 - Testing complete, creating PR"

# 5. Workers report completion
Minion-123: "Issue #123 complete - PR #1001"
Minion-456: "Issue #456 complete - PR #1002"

# 6. Inform user
You: "PRs ready: #1001 (issue 123), #1002 (issue 456)"

# 7. User reviews and merges
User: "Merged PR #1001"

# 8. Clean up merged issue
You: /cleanup_issue 123

# 9. Check remaining work
You: /status_workers
→ Shows issues #456 and #789 still active

# 10. Repeat until all done
User: "All merged!"

# 11. Prepare for next batch
You: /sync_main
You: "Ready for new issues"
```

## Key Points

### Worktrees
- **Location**: `worktrees/issue-<N>/`
- **Branch**: `feature/issue-<N>` or `fix/issue-<N>`
- **Isolation**: Each issue has its own working directory
- **Parallel**: Multiple workers can run simultaneously

### Test Ports
- **Formula**: 8000 + issue_number
- **Examples**:
  - Issue #123 → Port 8123
  - Issue #1 → Port 8001
  - Issue #999 → Port 8999
- **Purpose**: Avoid conflicts between test servers

### Minion Lifecycle
1. **Spawn**: `/spawn_issue_worker <N>` → Creates IssueWorker-<N>
2. **Work**: Minion implements issue autonomously
3. **Report**: Minion sends comm when PR ready
4. **Review**: User reviews and merges PR
5. **Cleanup**: `/cleanup_issue <N>` → Disposes minion, removes worktree

## Communication

### From Minions (Status Updates)
Minions **MUST** send you comms at these milestones:

**Regular Progress (comm_type="report"):**
- "Issue #N - Plan created, starting implementation"
- "Issue #N - Testing complete, creating PR"
- "Issue #N complete - PR #X" (REQUIRED when done)

**Blocking Issues (comm_type="question"):**
- Requirements unclear
- Stuck on implementation
- Tests failing
- Need help or clarification

**Note:** All minion comms use interrupt_priority="none"

### Your Response to Minion Comms
- **Regular updates (report):** Acknowledge and track internally
- **Blockers (question):** Immediately escalate to user for guidance
- **Completion:** Inform user PR is ready for review

### To User
You inform user about:
- Workers spawned successfully
- Blockers that need user input (escalated from minions)
- PRs ready for review
- Cleanup completion

## Skills Available

### For You (Orchestrator)
- **worktree-manager**: Create/remove worktrees
- **git-sync**: Sync main with remote

### For Minions (Workers)
- **github-issue-reader**: Fetch issue details
- **change-impact-analyzer**: Assess scope
- **implementation-planner**: Create plan
- **codebase-explorer**: Understand code
- **backend-tester**: Test changes (port 8000+N)
- **git-commit-composer**: Create commits
- **github-pr-manager**: Create PRs
- **git-state-validator**: Check git status
- **process-manager**: Manage processes

## Troubleshooting

### Worker stuck?
```
/status_workers
# Check what worker is doing, may need to communicate with it
```

### Need to restart a worker?
```
# Cleanup existing
/cleanup_issue <N>

# Re-spawn
/spawn_issue_worker <N>
```

### Orphaned worktree?
```
# Check status first
/status_workers

# If worktree exists but no minion, manually remove:
git worktree remove worktrees/issue-<N>
git worktree prune
```

### Multiple workers on same issue?
```
# Don't do this! Each issue = one worker
# If accidentally spawned duplicate, dispose one:
mcp__legion__dispose_minion IssueWorker-<N>
```

## Best Practices

### ✅ DO
- Spawn multiple workers in parallel
- Let minions work autonomously
- Review PRs before allowing merge
- Clean up after each merge
- Sync main periodically
- Use `/status_workers` to track progress

### ❌ DON'T
- Implement issues yourself
- Delete worktrees manually
- Spawn duplicate workers for same issue
- Interfere with minion's work
- Forget to cleanup after merge

## Directory Structure
```
webui_project/
├── .claude/              # Your orchestrator config (this dir)
├── claude_webui/         # Main repo (minions work here via worktrees)
└── worktrees/            # Created as needed
    ├── issue-123/        # Worker for issue 123
    ├── issue-456/        # Worker for issue 456
    └── issue-789/        # Worker for issue 789
```

## Remember
- You're the **orchestrator**, not the implementer
- Spawn **minions** to do the work
- Use **worktrees** for isolation
- Assign unique **test ports**
- **Clean up** after merges
- **Sync main** before new batches
