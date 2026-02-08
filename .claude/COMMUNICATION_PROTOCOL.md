# Minion Communication Protocol

## Overview

Minions working on issues **MUST** send status updates to the Orchestrator at key milestones. This ensures the Orchestrator can:
1. Track progress across all workers
2. Identify and escalate blockers to the user
3. Inform the user when PRs are ready
4. Maintain visibility into the fleet

## Planner Communication Points

### 1. Design Proposals (User Collaboration)

**When:** Throughout the planning phase, to collaborate with user on design

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - User stories ready for review"
content: "Presenting user stories and design for user feedback. <summary>"
comm_type: "report"
interrupt_priority: "none"
```

### 2. Plan Finalized (Completion)

**When:** After posting and marking approved plan

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - Plan ready, posted and approved"
content: "Implementation plan posted for issue #N. Ready for /approve_plan."
comm_type: "report"
interrupt_priority: "none"
```

---

## Builder Communication Points

### 1. Starting Implementation (Regular Update)

**When:** After retrieving plan and creating task list

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - Starting implementation"
content: "Plan retrieved. Task list created with X tasks. Starting implementation."
comm_type: "report"
interrupt_priority: "none"
```

### 2. Testing Complete (Regular Update)

**When:** After testing changes (before creating PR)

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N - Testing complete, creating PR"
content: "All tests passing. Ready to commit and create PR."
comm_type: "report"
interrupt_priority: "none"
```

### 3. PR Created (REQUIRED Update)

**When:** After successfully creating PR

**Format:**
```
to_minion_name: "Orchestrator"
summary: "Issue #N complete - PR #<pr_number>"
content: "Pull request created: <PR link>

Changes:
- <brief description of changes>

Testing:
- <test results summary>

Notes:
- <any caveats, follow-up items, or special instructions>"
comm_type: "report"
interrupt_priority: "none"
```

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

### For Planner Updates
**Orchestrator action:**
1. Relay design proposals to user for feedback
2. Relay user feedback back to Planner
3. When plan is ready, prompt user for `/approve_plan`

### For Builder Regular Updates (comm_type="report")
**Orchestrator action:**
1. Acknowledge receipt internally
2. Update internal tracking of worker status
3. Continue monitoring
4. Do NOT interrupt user unless they ask

### For Builder Completion (PR created)
**Orchestrator action:**
1. Acknowledge receipt
2. Update tracking (mark as "PR ready")
3. **Inform user** that PR is ready for review

### For Blockers (comm_type="question")
**Orchestrator action:**
1. **IMMEDIATELY** escalate to user
2. Provide full blocker details
3. Wait for user guidance
4. Relay guidance back to minion using `send_comm`

---

## Minion Best Practices

### DO Send Comms When:
- Plan is ready (Planner) - comm_type="report"
- Implementation starting (Builder) - comm_type="report"
- Testing is complete (Builder) - comm_type="report"
- PR is created (REQUIRED) - comm_type="report"
- Stuck or blocked on anything - comm_type="question"
- Requirements unclear - comm_type="question"
- Need user decision or clarification - comm_type="question"

### DON'T Send Comms When:
- Making normal progress on clear tasks
- Using skills as designed (no permission needed)
- Minor implementation decisions (use your judgment)
- Just to say "working on it" without status change

### Key Principle
**Keep Orchestrator informed of progress and blockers, but work autonomously on clear tasks.**

## Communication Examples

### Good Examples

#### Example 1: Builder Starting Work
```
summary: "Issue #456 - Starting implementation"
content: "Plan retrieved. Created 5 tasks covering backend endpoint, frontend component, and tests. Starting with backend changes."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 2: Builder Testing Complete
```
summary: "Issue #456 - Testing complete, creating PR"
content: "All tests passing. Manual testing confirmed:
- New endpoint responds correctly
- Frontend component renders properly
- Data persists across sessions
Creating PR now."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 3: Builder PR Ready
```
summary: "Issue #456 complete - PR #1002"
content: "PR created: https://github.com/user/repo/pull/1002

Changes:
- Added new API endpoint
- Created frontend component
- Added route configuration

Testing:
- All API endpoints tested
- Manual testing confirmed UI works
- No regressions detected

Ready for review."
comm_type: "report"
interrupt_priority: "none"
```

#### Example 4: Blocked on Unclear Requirements
```
summary: "Issue #789 - BLOCKED: Unclear error handling requirements"
content: "Issue #789 asks to 'improve error handling' but doesn't specify:
1. Which errors need improvement?
2. What's the desired behavior?
3. Should this apply to all endpoints or specific ones?

Current state: Analyzed codebase, ready to implement once requirements are clear.

Need: Clarification on error handling scope and desired behavior."
comm_type: "question"
interrupt_priority: "none"
```

### Bad Examples (Don't Do This)

#### Bad Example 1: Too Frequent
```
summary: "Issue #123 - Reading file auth.py"
content: "About to read auth.py to understand current implementation."
# Too granular, work autonomously
```

#### Bad Example 2: Missing Details
```
summary: "Issue #456 done"
content: "Finished the work."
# Missing PR number, changes description, testing info
```

#### Bad Example 3: Not Using Question for Blockers
```
summary: "Issue #789 - Not sure what to do"
content: "Requirements are unclear."
comm_type: "report"
# Should use comm_type="question" for blockers
```

## Summary

This communication protocol ensures:
- Visibility into all worker progress
- Immediate escalation of blockers
- User informed when PRs ready
- Autonomous work on clear tasks
- Efficient coordination across fleet

Minions work autonomously but communicate at key milestones. Orchestrator monitors the fleet and escalates to user when needed.
