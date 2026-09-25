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

**`lsof` is not reliably present in this environment — confirmed absent on multiple builder
containers.** Do NOT rely on `lsof -ti :PORT | xargs -r kill` as the primary mechanism: if
`lsof` is missing, that command silently produces no output, `xargs -r` sees empty input and
runs nothing, and the whole step becomes a **silent no-op with no error** — the Frontend never
actually gets killed, but nothing tells you that. (This exact failure mode happened in
practice: step 3's orphan check then found the still-running Backend and killed *it* directly,
out from under a still-alive Frontend — which triggered `backend_supervisor`'s
auto-restart-on-crash logic and spawned a brand-new Backend process instead of cleaning up.)

Find the Frontend API by matching its own command-line arguments via `ps`, which needs no
external tool and can't silently no-op the same way:

```bash
# Find the Frontend API by its own --port argument (robust regardless of lsof availability)
FRONTEND_PID=$(ps aux | grep -E "main\.py.*--port[= ]${FRONTEND_API_PORT}\b" | grep -v grep | awk '{print $2}')
if [ -n "$FRONTEND_PID" ]; then
    kill "$FRONTEND_PID"   # graceful SIGTERM — lets it cascade-stop Backend
else
    echo "No Frontend API process found on port ${FRONTEND_API_PORT} (may already be stopped)"
fi

# Give the graceful shutdown cascade a moment to complete (Frontend waits up to
# 10s for Backend to exit before SIGKILLing it — src/backend_supervisor.py)
sleep 12

# Same approach for vite
VITE_PID=$(ps aux | grep -E "vite.*--port[= ]${VITE_PORT}\b" | grep -v grep | awk '{print $2}')
if [ -n "$VITE_PID" ]; then
    kill "$VITE_PID"
fi
```

If `lsof` happens to be available, it's fine to use as an additional cross-check — but never as
the only mechanism, and never in a way where its absence fails silently instead of falling
through to the `ps`-based approach above.

### 3. Verify No Orphaned Backend Process

**Required, not optional, and order matters**: only run this *after* confirming step 2 actually
found and killed a Frontend PID (or confirmed none was running). If step 2 found no Frontend
process at all, do NOT immediately jump to killing anything matching `backend.main` — a Backend
process with no Frontend PID found could mean either "already cleanly stopped" or "step 2's
match pattern missed it while Frontend is still actually alive" — verify with a fresh `ps aux`
check for `main.py` generally (not just the port-matched pattern) before concluding it's a true
orphan. Backend's own port can't be predicted in advance, so process name is the only reliable
identifier for it specifically:

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
ps aux | grep -E "main\.py.*--port[= ]${FRONTEND_API_PORT}\b" | grep -v grep
ps aux | grep -E "vite.*--port[= ]${VITE_PORT}\b" | grep -v grep
ps aux | grep "backend\.main" | grep -v grep
# If curl is available, an additional live check:
curl -s -o /dev/null -w "%{http_code}\n" --max-time 2 http://127.0.0.1:${FRONTEND_API_PORT}/health 2>/dev/null || true
```

All three `ps` checks should return no output (the `curl` check, if run, should fail to connect
rather than return `200`).

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
