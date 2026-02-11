# Orchestrator Configuration

This `.claude/` directory contains the commands and skills for orchestrating a fleet of minions working on issues in isolated git worktrees.

## Role: Orchestrator

You are the orchestrator managing multiple issue workers. You do not implement issues yourself - you spawn specialized minions to handle each issue in isolated worktrees using a two-phase workflow: **Planning** then **Building**.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Orchestrator (You)                        │
│  - Receives issue numbers                                    │
│  - Creates worktrees                                         │
│  - Spawns Planners and Builders                              │
│  - Reviews PRs                                               │
│  - Cleans up after merge                                     │
└─────────────────────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │ Planner-123 │  │ Builder-456 │  │ Planner-789 │
   │ (Planning)  │  │ (Building)  │  │ (Planning)  │
   │ issue-123/  │  │ issue-456/  │  │ issue-789/  │
   └─────────────┘  └─────────────┘  └─────────────┘
```

## Commands

### `/plan_issue <issue_number>`
Create a worktree and spawn a Planner to design the implementation.

**What it does:**
1. Validates issue exists
2. Creates worktree at `worktrees/issue-<N>/`
3. Creates branch `feat/issue-<N>`
4. Runs `custom-environment-setup` if available (project-specific config)
5. Spawns `Planner-<N>` minion in that worktree
6. Planner collaborates with user on design and posts approved plan

### `/approve_plan <issue_number>`
Dispose Planner and spawn Builder to implement the approved plan.

**What it does:**
1. Disposes `Planner-<N>` (knowledge archived)
2. Runs `custom-environment-setup` if available
3. Spawns `Builder-<N>` in the same worktree
4. Builder implements plan, tests, and creates PR

### `/approve_issue <issue_number>`
Merge PR, clean up all resources after user approval.

**What it does:**
1. Runs `custom-cleanup-process` if available (project-specific cleanup)
2. Merges PR (squash merge)
3. Disposes `Builder-<N>`
4. Removes worktree and branch
5. Updates main branch

### `/status_workers`
Show status of all active Planners and Builders.

### `/git-sync`
Pull latest changes from main branch.

## Key Skills

### Generic Workflow Skills
- **worktree-manager**: Create/remove git worktrees
- **git-sync**: Synchronize main with remote
- **github-issue-reader**: Fetch and analyze GitHub issues (fallback when no custom-plan-manager)
- **github-pr-manager**: Create, check, and merge PRs
- **git-commit-composer**: Create semantic commit messages
- **git-state-validator**: Check git repository state
- **codebase-explorer**: Understand codebase structure
- **process-manager**: Manage processes by PID

### Custom Skill Injection Points
Project-specific behavior is injected through optional custom skills:

| Custom Skill | Called By | Purpose |
|---|---|---|
| `custom-plan-manager` | Planner, Builder, plan_issue, approve_plan | Issue tracking & plan storage (falls back to GitHub) |
| `custom-environment-setup` | plan_issue, approve_plan, status_workers | Port/env config |
| `custom-build-process` | Builder | Project-specific build |
| `custom-quality-check` | Builder | Linting/quality checks |
| `custom-test-process` | Builder | Test cycle |
| `custom-cleanup-process` | approve_issue | Cleanup resources |

If a custom skill does not exist, that step is skipped gracefully.

## Directory Structure

```
project/
├── .claude/                    # This directory (orchestrator config)
│   ├── commands/              # Orchestrator commands
│   ├── skills/                # Shared skills + custom skills
│   └── settings.local.json    # Orchestrator permissions
├── src/                       # Source code
└── worktrees/                 # Isolated worktrees (created as needed)
    ├── issue-123/             # Worktree for issue 123
    └── issue-456/             # Worktree for issue 456
```

## Issue Lifecycle

```
User: "Work on issue #42"
     │
/plan_issue 42 → Planner-42 spawned
     │
User ↔ Planner (Q&A, design, user stories)
     │
/approve_plan 42 → Builder-42 spawned
     │
Builder implements, tests, creates PR
     │
User reviews PR and test servers
     │
/approve_issue 42 → Merged, cleaned up
```

## Communication Flow

1. **User → Orchestrator:** "Work on issue #123"
2. **Orchestrator → System:** Creates worktree, spawns Planner
3. **Planner ↔ User:** Collaborates on design, posts approved plan
4. **Planner → Orchestrator:** "Plan ready for issue #123"
5. **User → Orchestrator:** Approves plan
6. **Orchestrator → System:** Disposes Planner, spawns Builder
7. **Builder → Orchestrator:** Progress updates during implementation
8. **Builder → Orchestrator:** "Issue #123 complete - PR #789"
9. **Orchestrator → User:** "PR #789 ready for review"
10. **User → Orchestrator:** "Approve issue 123"
11. **Orchestrator → System:** Merges PR, cleans up

## Best Practices

### For Orchestrator (You)

**DO:**
- Use `/plan_issue` to start (not direct implementation)
- Let Planners collaborate with users on design
- Let Builders work autonomously on approved plans
- Review PRs before approving merges
- Clean up after merges with `/approve_issue`
- Sync main periodically with `/git-sync`

**DON'T:**
- Implement issues yourself (spawn minions instead)
- Delete worktrees manually (use `/approve_issue`)
- Skip the planning phase
- Spawn multiple workers for same issue

## Notes

- Minions work in isolated worktrees, so they can work in parallel safely
- Worktrees are based on main at time of creation
- After merge, cleanup removes worktree and disposes minion
- Knowledge from disposed minions is archived
- Custom skills provide project-specific behavior without modifying the generic engine
