---
name: custom-test-process
description: Project-specific test process. Starts the Frontend API (which auto-starts Backend), vite server, runs pytest, and verifies health/ready endpoints for claudecode_webui.
allowed-tools: [Bash, Skill]
---

# Custom Test Process

## Purpose

This is a **project-specific custom skill** called by the Builder workflow to run the full test cycle.
It handles starting servers, running tests, and verifying health endpoints specific to this project (claudecode_webui).

Generic workflow skills invoke this skill if it exists; if absent, the test step is skipped.

## Architecture note (issue #498)

The old single process split into two: a **Frontend API** (serves the browser,
relays everything to Backend) and a **Backend** (owns session execution).
Starting the Frontend API auto-starts Backend as a child process — there is no
separate "start Backend" step. Backend's port is allocated dynamically and
isn't predictable across runs; almost everything below should talk to the
Frontend API port, never Backend's, since that's the whole point of the relay.

## When Called

The Builder invokes this skill from its working directory (the worktree). Environment variables (ports) come from `custom-environment-setup`.

## Input

Environment from `custom-environment-setup`:
- `FRONTEND_API_PORT`: Frontend API server port (8000 + issue_number % 1000).
  Named `BACKEND_PORT` in older versions of this skill — that name was always
  this port, not a genuine backend port; see custom-environment-setup for why.
- `VITE_PORT`: Vite dev server port (5000 + issue_number % 1000)
- `TEST_AUTH_TOKEN`: Fixed auth token for test servers (default: `test`)
  - Pinned so the token survives restarts during testing
  - Included in all URLs reported to the user
  - This is the Frontend API's browser-facing token only — Backend's own
    bearer token is generated fresh internally and never needs to be known here

## Test Lifecycle

This skill owns the full test lifecycle in a single invocation:

### 1. Start the Frontend API (auto-starts Backend)

**CRITICAL:** Unset the `CLAUDECODE` environment variable before starting. The Claude Agent SDK includes an undocumented safety check that prevents it from running inside another Claude Code instance. Since the builder agent runs inside Claude Code, this env var is inherited by child processes — both the Frontend API and the Backend it spawns. Our application launches its own Claude Code SDK instances (inside Backend), so if `CLAUDECODE` is set, those SDK sessions will halt prematurely.

```bash
env -u CLAUDECODE uv run python main.py --host 0.0.0.0 --debug-all --port ${FRONTEND_API_PORT} --token ${TEST_AUTH_TOKEN:-test} &
```

No `--remote-backend-url`/`--remote-backend-token` flags — omitting them is what
triggers auto-start (`src/backend_supervisor.py`), matching the "no manual
configuration required" production default this issue delivers.

Wait for the Frontend API to be ready — **use `/ready`, not just `/health`**:
`/health` only proves the Frontend process is up; `/ready` is gated on the
auto-started Backend itself reporting ready (`src/backend_supervisor.py`'s
readiness-polling), which is what actually proves the two-process setup works,
not just that one process started.

```bash
# Wait up to 30 seconds for the Frontend API (and its auto-started Backend) to be ready
for i in $(seq 1 30); do
    ready=$(curl -s http://localhost:${FRONTEND_API_PORT}/ready 2>/dev/null | grep -o '"ready":true')
    [ -n "$ready" ] && break
    sleep 1
done
```

### 2. Start Frontend Dev Server (if frontend changed)

**CRITICAL:** Set `VITE_BACKEND_PORT` to the Frontend API port (not Backend's —
the browser always talks to the Frontend API, which relays internally; Vite's
env var is named `VITE_BACKEND_PORT` for historical reasons but its value must
be the Frontend API port):

```bash
cd frontend && VITE_BACKEND_PORT=${FRONTEND_API_PORT} npm run dev -- --port ${VITE_PORT} &
```

Without this, the frontend would proxy requests to port 8001 (the default) instead of the issue-specific Frontend API port.

### 3. Run Unit Tests

Both test suites — the split is real, they cover different processes:

```bash
uv run pytest backend/tests/ src/tests/ -v
```

### 4. Verify Health/Ready Endpoints

```bash
# Check Frontend API liveness (always true once the process is up)
curl -s http://localhost:${FRONTEND_API_PORT}/health

# Check Frontend API readiness (true only once auto-started Backend is ready too)
curl -s http://localhost:${FRONTEND_API_PORT}/ready

# Confirm two distinct real processes are actually running, not just one
ps aux | grep -E "main.py|backend.main" | grep -v grep

# Check frontend (if started)
curl -s http://localhost:${VITE_PORT}
```

### 5. Leave Servers Running

**CRITICAL:** Do NOT stop servers after testing. Leave them running for user review.
The `custom-cleanup-process` skill handles stopping servers later — it must stop
the Frontend API process, which will then cleanly shut down its auto-started
Backend child (SIGTERM, wait, then SIGKILL — `src/backend_supervisor.py`). Do
not try to separately find and kill the Backend process; Frontend owns its
lifecycle.

### 6. Report Server URLs to User

When reporting that servers are running, include the auth token in the URLs:

```
Frontend API: http://localhost:${FRONTEND_API_PORT}/?token=${TEST_AUTH_TOKEN:-test}
Frontend Dev: http://localhost:${VITE_PORT}/?token=${TEST_AUTH_TOKEN:-test}
```

This lets the user click the URL and authenticate automatically. Backend's port
is not reported — it's internal, varies per run, and nothing outside the
Frontend<->Backend hop should need it.

## Test Verification

- Frontend API starts without errors and its auto-started Backend becomes ready
  (confirmed via `/ready`, not just `/health` — see step 1)
- Two distinct real processes are actually running (`ps aux` check, step 4) —
  don't just trust a green health check; confirm the process split is real
- Both test suites pass (`backend/tests/` and `src/tests/`)
- No regressions introduced

## Usage by Generic Skills

The Builder workflow calls this skill like:

```
Invoke custom-test-process skill
```

The skill uses port information from the environment (provided by custom-environment-setup).
It may use the `process-manager` skill internally for process management.
If this skill does not exist, the generic workflow skips the test step.
