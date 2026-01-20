# Minion Communication Protocol

## Overview

Minions working on issues **MUST** send status updates to the Orchestrator at key milestones. This ensures the Orchestrator can:
1. Track progress across all workers
2. Identify and escalate blockers to the user
3. Inform the user when PRs are ready
4. Maintain visibility into the fleet

## Required Communication Points

### 1. Plan Created (Regular Update)

**When:** After creating implementation plan with `implementation-planner` skill

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - Plan created, starting implementation"
content: "Implementation plan ready. High-level approach: <brief summary>. Starting implementation now."
comm_type: "report"
interrupt_priority: "none"
```

**Purpose:** Confirms minion has understood the issue and has a plan

---

### 2. Testing Complete (Regular Update)

**When:** After testing changes on assigned port (before creating PR)

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - Testing complete, creating PR"
content: "Testing complete on port 8<N>. All tests passing. Ready to commit and create PR."
comm_type: "report"
interrupt_priority: "none"
```

**Purpose:** Confirms changes are tested and working

---

### 3. PR Created (REQUIRED Update)

**When:** After successfully creating PR with `github-pr-manager` skill

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N complete - PR #<pr_number>"
content: "Pull request created: https://github.com/repo/pulls/<pr_number>

Changes:
- <brief description of changes>

Testing:
- Tested on port 8<N>
- <test results summary>

Notes:
- <any caveats, follow-up items, or special instructions>"
comm_type: "report"
interrupt_priority: "none"
```

**Purpose:** Informs Orchestrator that work is complete and ready for user review

---

### 4. Blocked / Need Help (CRITICAL Update)

**When:** Any time the minion is stuck, unclear, or needs assistance

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - BLOCKED: <specific reason>"
content: "Blocker details:
- What I was trying to do: <description>
- What went wrong: <problem>
- What I've tried: <attempts>
- What I need: <specific help needed>

Current state: <safe to pause, or action needed>"
comm_type: "question"
interrupt_priority: "none"
```

**Purpose:** Notify Orchestrator of blocker so they can escalate to user for guidance

**Examples of blockers:**
- Issue requirements are unclear or incomplete
- Discovered issue is already fixed
- Missing dependencies or conflicting changes
- Tests failing unexpectedly
- Scope is much larger than expected
- Stuck for more than 5 minutes on any task
- Any error preventing progress

---

## Orchestrator Response Protocol

### For Regular Updates (priority="none")

**Orchestrator action:**
1. Acknowledge receipt internally
2. Update internal tracking of worker status
3. Continue monitoring
4. Do NOT interrupt user unless they ask

**Example:**
```
Minion: "Issue #123 - Plan created, starting implementation"
Orchestrator: <tracks internally, no user notification needed>
```

### For Completion Updates (priority="none", PR created)

**Orchestrator action:**
1. Acknowledge receipt
2. Update tracking (mark as "PR ready")
3. **Inform user** that PR is ready for review

**Example:**
```
Minion: "Issue #123 complete - PR #1001"
Orchestrator → User: "PR #1001 ready for review (issue #123)"
```

### For Blockers (comm_type="question")

**Orchestrator action:**
1. **IMMEDIATELY** escalate to user
2. Provide full blocker details
3. Wait for user guidance
4. Relay guidance back to minion using `send_comm`

**Example:**
```
Minion: (comm_type="question") "Issue #123 - BLOCKED: Requirements unclear on auth method"
Orchestrator → User: "IssueWorker-123 blocked on issue #123 - needs clarification on authentication method (OAuth vs JWT vs session-based). Please advise."
User → Orchestrator: "Use session-based auth"
Orchestrator → Minion: (via send_comm) "Use session-based authentication as specified in <details>"
```

## Minion Best Practices

### DO Send Comms When:
✅ Plan is ready (after implementation-planner) - comm_type="report"
✅ Testing is complete (before creating PR) - comm_type="report"
✅ PR is created (REQUIRED) - comm_type="report"
✅ Stuck or blocked on anything - comm_type="question"
✅ Requirements unclear - comm_type="question"
✅ Need user decision or clarification - comm_type="question"

### DON'T Send Comms When:
❌ Making normal progress on clear tasks
❌ Using skills as designed (no permission needed)
❌ Minor implementation decisions (use your judgment)
❌ Just to say "working on it" without status change

### Key Principle
**Keep Orchestrator informed of progress and blockers, but work autonomously on clear tasks.**

## Communication Examples

### Good Examples

#### Example 1: Regular Progress
```
# After creating plan
summary: "Issue #456 - Plan created, starting implementation"
content: "Plan ready. Will add user profile endpoint to backend and create ProfileView component in frontend. Estimated 3-4 files modified. Starting backend implementation."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 2: Testing Complete
```
# After successful testing
summary: "Issue #456 - Testing complete, creating PR"
content: "Backend and frontend tested on port 8456. Manual testing confirmed:
- Profile page loads correctly
- Avatar upload works
- Data persists across sessions
Creating PR now."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 3: PR Ready
```
# After creating PR
summary: "Issue #456 complete - PR #1002"
content: "PR created: https://github.com/user/repo/pull/1002

Changes:
- Added /api/profile endpoint in web_server.py
- Created ProfileView.vue component
- Added profile route to router

Testing:
- All API endpoints tested with curl
- Manual testing confirmed UI works
- No regressions detected

Ready for review."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 4: Blocked on Unclear Requirements
```
# When stuck
summary: "Issue #789 - BLOCKED: Unclear error handling requirements"
content: "Issue #789 asks to 'improve error handling' but doesn't specify:
1. Which errors need improvement? (Auth? Network? Validation?)
2. What's the desired behavior? (User notification? Logging? Both?)
3. Should this apply to all endpoints or specific ones?

Current state: Analyzed codebase, ready to implement once requirements are clear.

Need: Clarification on error handling scope and desired behavior."
comm_type: "question"
interrupt_priority: "none"
```

### Bad Examples (Don't Do This)

#### Bad Example 1: Too Frequent
```
# Don't send for every minor step
summary: "Issue #123 - Reading file auth.py"
content: "About to read auth.py to understand current implementation."
# ❌ Too granular, work autonomously
```

#### Bad Example 2: Missing Details
```
# Don't send vague completion reports
summary: "Issue #456 done"
content: "Finished the work."
# ❌ Missing PR number, changes description, testing info
```

#### Bad Example 3: Not Using Question for Blockers
```
# Don't use "report" type when blocked
summary: "Issue #789 - Not sure what to do"
content: "Requirements are unclear."
comm_type: "report"
interrupt_priority: "none"
# ❌ Should use comm_type="question" for blockers
```

## Orchestrator Tracking

The Orchestrator should maintain internal tracking:

```
Active Workers:
  Issue #123 - IssueWorker-123
    Status: Plan created, implementing
    Last Update: "Plan created, starting implementation" (10:30 AM)
    Port: 8123
    Worktree: worktrees/issue-123/

  Issue #456 - IssueWorker-456
    Status: PR ready
    Last Update: "Issue #456 complete - PR #1002" (11:15 AM)
    PR: #1002
    Port: 8456
    Worktree: worktrees/issue-456/

  Issue #789 - IssueWorker-789
    Status: BLOCKED
    Last Update: "BLOCKED: Unclear requirements" (11:45 AM)
    Blocker: Needs auth method clarification
    Port: 8789
    Worktree: worktrees/issue-789/
```

## Summary

This communication protocol ensures:
- ✅ Visibility into all worker progress
- ✅ Immediate escalation of blockers
- ✅ User informed when PRs ready
- ✅ Autonomous work on clear tasks
- ✅ Efficient coordination across fleet

Minions work autonomously but communicate at key milestones. Orchestrator monitors the fleet and escalates to user when needed.
