---
name: sync_main
description: Pull latest changes from main branch in preparation for new issues
allowed-tools: [Bash, Skill]
---

## Sync Main Branch

This skill updates the main branch with the latest changes from remote, preparing for new issue work.

### Workflow

#### 1. Verify Repository State

**Invoke the `git-state-validator` skill** to check:
- Current branch
- Uncommitted changes
- Working directory status

#### 2. Switch to Main Branch

If not on main:
```bash
git checkout main
```

If uncommitted changes exist, ask user how to handle (stash, commit, or abort).

#### 3. Pull Latest Changes

**Invoke the `git-sync` skill** to update main:
- Fetch from remote
- Pull latest changes
- Verify no conflicts

The skill will:
```bash
git fetch origin
git pull origin main
```

#### 4. Verify Sync Success

**Invoke the `git-state-validator` skill** again to confirm:
- On main branch
- No uncommitted changes
- Up to date with remote

#### 5. Report Status

Inform user:
```
✅ Main branch synchronized
- Latest changes pulled from remote
- Ready to spawn workers for new issues

Current commit: <commit hash>
Ready to process new issues.
```

### Skills Used

- **git-state-validator** - Verify repository state
- **git-sync** - Update main branch from remote

### Important Notes

- Run this periodically to stay up to date
- Run before spawning workers for new batch of issues
- Ensures all worktrees are based on latest main
- Should have no active worktrees when running
