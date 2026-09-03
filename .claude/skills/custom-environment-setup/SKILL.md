---
name: custom-environment-setup
description: Project-specific environment setup for issue workflows. Calculates test ports and provides initialization context for minions.
---

# Custom Environment Setup

## Purpose

This is a **project-specific custom skill** called by generic workflow skills at defined checkpoints.
It provides environment configuration specific to this project (claudecode_webui).

Generic workflow skills invoke this skill if it exists; if absent, the workflow continues without project-specific environment setup.

## Input

- `issue_number` (from $1 argument): The issue number being worked on

## Output

When invoked, this skill should set the following environment variables/context for the caller:

### Port Calculation

Issue #498 split the old single process into two: a **Frontend API** (serves the
browser, relays everything) and a **Backend** (owns session execution, spawned
by the Frontend). Only the Frontend API needs a predictable, reservable port —
Backend's real port is allocated automatically at runtime (an OS-assigned free
port via a throwaway socket, `src/backend_supervisor.py`), specifically to avoid
the fixed-offset collision problem (#1825) that a second per-issue formula would
reintroduce. Naming note: earlier versions of this skill called the formula
below "Backend Port" — that name was always this issue's Frontend API port, not
a genuine backend port; renamed here to avoid confusion during the transition.

- **Frontend API Port** = 8000 + (issue_number % 1000)
- **Vite Port** = 5000 + (issue_number % 1000)
- **Backend Port** = allocated dynamically at Frontend startup — do not compute
  or reserve one. Discover the actual value from the running Frontend process's
  logs (`data/logs/backend/backend.log` path, or the "Auto-starting Backend on
  127.0.0.1:<port>" startup log line) or via `lsof -i -P -n | grep backend.main`
  if you need it directly (e.g. to hit Backend's own API for debugging, bypassing
  the relay). Most testing should go through the Frontend API port and never
  need Backend's port at all — that's the point of the relay.

Examples:
- Issue #42 -> Frontend API: 8042, Vite: 5042, Backend: (auto-assigned, see above)
- Issue #372 -> Frontend API: 8372, Vite: 5372, Backend: (auto-assigned, see above)
- Issue #1234 -> Frontend API: 8234, Vite: 5234, Backend: (auto-assigned, see above)

### Auth Token

- **Test Auth Token** = `test` (fixed value for all test servers)
  - Passed to the Frontend API via `--token test` so it survives restarts
  - Included in URLs reported to the user for one-click access
  - Backend's own bearer token is generated fresh per Frontend startup
    (`secrets.token_urlsafe(32)`) and never needs to be known by testers —
    it's internal to the Frontend<->Backend hop, never exposed to the browser

### Initialization Context Fragment

Return this context fragment for inclusion in minion initialization:

```
Test Server Configuration:
- Frontend API Port: ${FRONTEND_API_PORT} (8000 + issue_number % 1000)
- Frontend API Host: 0.0.0.0 (required for network-accessible dev server)
- Backend Port: allocated dynamically by the Frontend at startup — do not
  hardcode a second fixed port (see custom-environment-setup skill for how
  to discover the actual value if needed for direct debugging)
- Vite Port: ${VITE_PORT} (5000 + issue_number % 1000)
- Auth Token: test (pinned for testing, Frontend-side only)
- Data Directory: Default (data/) - DO NOT use --data-dir flag
```

### Status Display Context

For `/status_workers`, provide:
- Port range info: Frontend API ports 8000-8999, Vite ports 5000-5999, Backend
  ports unpredictable (OS-assigned per run)
- How to check running servers:
  ```bash
  # Check Frontend API ports
  lsof -i :8000-8999 2>/dev/null | grep LISTEN

  # Check vite ports
  lsof -i :5000-5999 2>/dev/null | grep LISTEN

  # Check any running Backend processes (port varies per run)
  ps aux | grep "backend.main" | grep -v grep
  ```

## Usage by Generic Skills

Generic workflow skills call this skill like:

```
Invoke custom-environment-setup skill with issue_number=$1
```

The skill returns port configuration and any project-specific init context.
If this skill does not exist, the generic workflow proceeds without port/environment configuration.
