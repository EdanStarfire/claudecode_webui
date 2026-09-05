---
name: custom-cleanup-process
description: Project-specific cleanup process after issue completion. Stops test servers (Frontend API + its auto-started Backend) and cleans project-specific artifacts for claudecode_webui.
allowed-tools: [Bash, Skill]
---

# Custom Cleanup Process

## Purpose

This is a **project-specific custom skill** called by the `approve_issue` workflow to clean up project-specific resources.
It handles stopping test servers and cleaning artifacts specific to this project (claudecode_webui).

Generic workflow skills invoke this skill if it exists; if absent, the cleanup step is skipped (only generic cleanup like worktree removal runs).

## Architecture note (issue #498)

The Frontend API auto-starts a Backend child process on its own dynamically-
allocated port (`src/backend_supervisor.py`). A graceful SIGTERM to the
Frontend API cascades to Backend (Frontend's shutdown lifespan calls
`backend_supervisor.stop()`, which sends Backend SIGTERM, waits, then SIGKILL
if needed). This normally means killing only the Frontend API port is enough —
but the cleanup steps below explicitly verify no `backend.main` process
survives anyway, since Backend's port isn't predictable/lsof-able in advance
and a SIGKILL fallback on Frontend (if plain `kill` doesn't work) would skip
the graceful cascade entirely and orphan it.

## Input

- `issue_number` (from $1 argument): The issue number being cleaned up

## Cleanup Steps

### 1. Calculate Ports

- Frontend API Port = 8000 + (issue_number % 1000)
- Vite Port = 5000 + (issue_number % 1000)
- Backend Port: not calculated — dynamically allocated per run, not
  lsof-able by formula. Verified by process name instead (step 3).

### 2. Stop Test Servers

Use the `process-manager` skill pattern - find and kill by PID. Kill the
Frontend API gracefully first (SIGTERM, default) so its shutdown lifespan has
a chance to cleanly stop its auto-started Backend child:

```bash
# Find and kill the Frontend API (graceful SIGTERM — lets it cascade-stop Backend)
lsof -ti :${FRONTEND_API_PORT} | xargs -r kill 2>/dev/null

# Give the graceful shutdown cascade a moment to complete (Frontend waits up to
# 10s for Backend to exit before SIGKILLing it — src/backend_supervisor.py)
sleep 12

# Find and kill vite server
lsof -ti :${VITE_PORT} | xargs -r kill 2>/dev/null
```

### 3. Verify No Orphaned Backend Process

**Required, not optional** — Backend's port can't be predicted in advance, so
the only reliable check is by process name. If the graceful cascade in step 2
didn't work (e.g. Frontend had to be force-killed), Backend can be left running
with no Frontend left to clean it up:

```bash
ORPHANED_BACKEND=$(ps aux | grep "backend\.main" | grep -v grep)
if [ -n "$ORPHANED_BACKEND" ]; then
    echo "WARNING: orphaned Backend process(es) found after Frontend shutdown:"
    echo "$ORPHANED_BACKEND"
    echo "$ORPHANED_BACKEND" | awk '{print $2}' | xargs -r kill -9
fi
```

### 4. Verify Servers Stopped

```bash
lsof -i :${FRONTEND_API_PORT} 2>/dev/null
lsof -i :${VITE_PORT} 2>/dev/null
ps aux | grep "backend\.main" | grep -v grep
```

All three should return no output.

### 5. Error Handling

- If servers are not found on expected ports, warn but continue (servers may have already been stopped)
- If the Frontend API kill fails, try `kill -9` as fallback — but if this path is taken, step 3's orphan check
  is not optional, since a SIGKILL skips the graceful Backend-stop cascade entirely
- Do NOT fail the overall cleanup if server stop fails

## Usage by Generic Skills

The `approve_issue` workflow calls this skill like:

```
Invoke custom-cleanup-process skill with issue_number=$1
```

The skill derives port numbers from the issue number and handles all project-specific cleanup.
It may use the `process-manager` skill internally for process management.
If this skill does not exist, the generic workflow proceeds with only generic cleanup (worktree removal, branch cleanup, etc.).
