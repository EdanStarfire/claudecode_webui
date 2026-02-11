---
name: plan_issue
description: Start planning phase for an issue by spawning a Planner minion
disable-model-invocation: true
argument-hint: <issue_number>
allowed-tools:
  - Bash(ls:*)
  - Bash(git worktree:*)
  - Skill(custom-plan-manager)
  - Skill(custom-environment-setup)
  - Skill(worktree-manager)
  - Task
---

## Plan Issue

This skill creates an isolated git worktree for an issue and spawns a Planner minion to collaborate with the user on requirements and design.

### Workflow

You are starting the planning phase for issue #$1. Follow these steps:

#### 1. Fetch Issue Details

Check if `custom-plan-manager` skill exists:
```bash
ls .claude/skills/custom-plan-manager/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-plan-manager` skill** with operation=`fetch-issue` and issue_number=$1.

If it does not exist, **invoke the `github-issue-reader` skill** with issue number $1 to fetch details from GitHub.

#### 2. Get Environment Configuration (if custom skill exists)

Check if `custom-environment-setup` skill exists:
```bash
ls .claude/skills/custom-environment-setup/SKILL.md 2>/dev/null
```

If it exists, **invoke the `custom-environment-setup` skill** with issue_number=$1 to get:
- Port configuration for plan documentation
- Any project-specific initialization context

Store the returned environment info for minion initialization context.

If it does not exist, proceed without project-specific environment configuration.

#### 3. Create Git Worktree

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

#### 4. Spawn Planner Minion

**Invoke `mcp__legion__spawn_minion`** with:
- **name**: `Planner-$1`
- **template_name**: `Issue Planner`
- **working_directory**: Full absolute path to `worktrees/issue-$1/`
- **role**: `Issue Planner for #$1 - User story and design specialist`
- **initialization_context**:
  ```
  You are Planner-$1, responsible for planning issue #$1.

  Working Directory: [worktree path]
  Issue: #$1

  Your mission:
  1. Fetch issue details (use custom-plan-manager fetch-issue if available, else github-issue-reader)
  2. Explore current implementation using Task tool with Explore subagent
  3. Build user stories from requirements
  4. Create design artifacts (diagrams, flows) as appropriate
  5. Present to user and iterate based on feedback
  6. When user approves, post plan (use custom-plan-manager write-plan if available, else gh issue comment + ready-to-build label)
  7. Send comm to Orchestrator: "Plan posted to issue #$1, awaiting user approval"
     IMPORTANT: Your comm is informational only. The Orchestrator will NOT
     auto-transition to building. The user must explicitly invoke /approve_plan $1 when ready.

  CRITICAL - Codebase Exploration:
  When you need to understand the codebase structure, find relevant files, or
  investigate how existing features work, use the Task tool with subagent_type="Explore":

  Example:
  Task(
    description="Find auth implementation",
    prompt="Find files related to authentication and session management",
    subagent_type="Explore"
  )

  This delegates exploration to an Explore agent which has specialized search
  capabilities and returns a focused summary without consuming your context.

  [Include environment info from custom-environment-setup if available]

  IMPORTANT: You are READ-ONLY by default. Do not modify files unless the user
  explicitly requests it. Focus on research, analysis, and user collaboration.

  When the user is satisfied with the plan:
  1. Post the approved plan (use custom-plan-manager write-plan if available,
     else post as GitHub comment with gh issue comment and add ready-to-build label)
  2. Send completion comm to Orchestrator

  The Orchestrator will acknowledge the plan is posted. You remain active for
  potential user iteration. Only when the user explicitly invokes /approve_plan $1
  will the Orchestrator dispose you and spawn a Builder.
  ```

#### 5. Start Planner Working

**Invoke `mcp__legion__send_comm`** to the new Planner:
- to_minion_name: `Planner-$1`
- summary: "Begin planning issue #$1"
- content: "Start the planning workflow for issue #$1. Fetch the issue, analyze requirements, and begin user collaboration."
- comm_type: "task"
- interrupt_priority: "none"

#### 6. Confirm to User

Inform user:
```
Planning started for issue #$1
- Planner: Planner-$1
- Worktree: worktrees/issue-$1/
- Branch: feature/issue-$1

The Planner will:
1. Fetch and analyze the issue
2. Build user stories from requirements
3. Create design artifacts as needed
4. Collaborate with you on requirements
5. Post approved plan (via custom-plan-manager or GitHub)

Speak directly with the Planner to refine the plan.
When satisfied, use /approve_plan $1 to transition to the Building phase.
```

#### 7. Await Explicit Approval

**CRITICAL:** The Orchestrator must wait for the user to explicitly invoke `/approve_plan $1` before transitioning to the Building phase.

**Correct workflow:**
1. Planner posts plan to GitHub and sends informational comm
2. Orchestrator acknowledges: "Plan ready for issue #$1 - review and use /approve_plan $1 when ready"
3. User may iterate with Planner (ask for revisions, clarifications)
4. When satisfied, user invokes: `/approve_plan $1`
5. Only then: Orchestrator disposes Planner and spawns Builder

**Anti-pattern to avoid:**
- Planner: "Plan ready for issue #5"
- Orchestrator: *immediately disposes Planner and spawns Builder*
- User: "Wait, I wanted to change something!"

**Key principle:** Planner comms saying "plan ready" or "plan posted" are informational status updates, NOT approval signals. Only explicit `/approve_plan` invocation indicates user approval.

### Skills Used

- **custom-plan-manager** - Fetch issue details (if exists, replaces github-issue-reader)
- **github-issue-reader** - Fetch and validate issue (fallback when no custom-plan-manager)
- **custom-environment-setup** - Get project-specific environment config (if exists)
- **worktree-manager** - Create isolated worktree
- **mcp__legion__spawn_minion** - Create Planner minion
- **mcp__legion__send_comm** - Start Planner working

### Subagent Exploration

The Planner uses Task tool with Explore subagent for codebase investigation. This:
- Prevents context overflow from large file reads
- Leverages specialized search capabilities
- Returns focused summaries for planning decisions

### Important Notes

- Each issue gets its own isolated worktree
- Planner is READ-ONLY by default
- User collaborates directly with Planner
- Plan is posted via custom-plan-manager or GitHub for Builder to consume
- Worktree cleaned up after issue is merged
- Environment configuration is project-specific via custom-environment-setup
- Issue tracking is project-specific via custom-plan-manager (falls back to GitHub)
