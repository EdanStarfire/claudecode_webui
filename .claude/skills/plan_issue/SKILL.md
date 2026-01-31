---
name: plan_issue
description: Start planning phase for a GitHub issue by spawning a Planner minion
disable-model-invocation: true
argument-hint: <issue_number>
allowed-tools: [Bash, Skill, Task, mcp__legion__spawn_minion, mcp__legion__list_templates, mcp__legion__send_comm]
---

## Plan Issue

This skill creates an isolated git worktree for a GitHub issue and spawns a Planner minion to collaborate with the user on requirements and design.

### Workflow

You are starting the planning phase for GitHub issue #$1. Follow these steps:

#### 1. Validate Issue Number

Ensure the issue number is valid:
- Must be a positive integer
- Issue should exist in the repository

**Invoke the `github-issue-reader` skill** to verify issue exists and fetch details.

#### 2. Calculate Ports

Calculate test ports for this issue:
- Backend Port = 8000 + ($1 % 1000)
- Vite Port = 5000 + ($1 % 1000)

Example: Issue #372 → Backend: 8372, Vite: 5372

Store these for minion initialization context.

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
  You are Planner-$1, responsible for planning GitHub issue #$1.

  Working Directory: [worktree path]
  Issue: #$1

  Your mission:
  1. Fetch issue details using github-issue-reader skill
  2. Explore current implementation using Task tool with Explore subagent
  3. Build user stories from requirements
  4. Create design artifacts (diagrams, flows) as appropriate
  5. Present to user and iterate based on feedback
  6. When user approves, finalize and post plan to GitHub
  7. Add `ready-to-build` label to issue
  8. Send comm to Orchestrator: "Plan ready for issue #$1"

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

  Port Configuration (for plan documentation):
  - Backend Port: [calculated]
  - Vite Port: [calculated]

  IMPORTANT: You are READ-ONLY by default. Do not modify files unless the user
  explicitly requests it. Focus on research, analysis, and user collaboration.

  When the user is satisfied with the plan:
  1. Post final plan as GitHub comment
  2. Add ready-to-build label
  3. Send completion comm to Orchestrator

  The Orchestrator will then dispose you and spawn a Builder to implement the plan.
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
✅ Planning started for issue #$1
- Planner: Planner-$1
- Worktree: worktrees/issue-$1/
- Branch: feature/issue-$1

The Planner will:
1. Fetch and analyze the issue
2. Build user stories from requirements
3. Create design artifacts as needed
4. Collaborate with you on requirements
5. Post approved plan to GitHub

Speak directly with the Planner to refine the plan.
When satisfied, the Planner will signal completion and a Builder will be spawned.
```

### Skills Used

- **github-issue-reader** - Fetch and validate issue
- **worktree-manager** - Create isolated worktree
- **mcp__legion__spawn_minion** - Create Planner minion
- **mcp__legion__send_comm** - Start Planner working

### Subagent Exploration

The Planner uses Task tool with Explore subagent for codebase investigation. This:
- Prevents context overflow from large file reads
- Leverages specialized search capabilities
- Returns focused summaries for planning decisions

### Port Convention

- Backend: 8000 + (issue_number % 1000)
- Vite: 5000 + (issue_number % 1000)

Examples:
- Issue #42 → Backend: 8042, Vite: 5042
- Issue #372 → Backend: 8372, Vite: 5372
- Issue #1234 → Backend: 8234, Vite: 5234

### Important Notes

- Each issue gets its own isolated worktree
- Planner is READ-ONLY by default
- User collaborates directly with Planner
- Plan is posted to GitHub for Builder to consume
- Worktree cleaned up after issue is merged
