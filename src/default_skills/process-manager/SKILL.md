---
name: process-manager
description: Safely start, monitor, and stop processes by PID. Use when managing background servers, finding running processes, or ensuring clean process termination.
allowed-tools:
  - Bash(~/.claude/skills/process-manager/scripts/find-port.sh :*)
  - Bash(~/.claude/skills/process-manager/scripts/kill-pid.sh :*)
  - Bash(~/.claude/skills/process-manager/scripts/check-pid.sh :*)
---

# Process Manager

## Instructions

### CRITICAL RULES

- **ALWAYS** kill processes by PID — never by name (`pkill`, `killall`)
- **ALWAYS** verify the process identity before killing (is it yours or production?)
- **ALWAYS** confirm termination after killing

### Scripts

#### Find process on a port

```bash
~/.claude/skills/process-manager/scripts/find-port.sh <port>
```

| Field | Meaning |
|---|---|
| `PROC_STATUS` | `free` (port available), `in_use` (process found), `error` |
| `PROCESS_COUNT` | Number of processes on port |
| `PROCESSES` | One line per process: `PID=<pid> CMD=<cmd> USER=<user>` |

**Exit 0** — process(es) found. Present the process info to the user before taking action.
**Exit 1** — port is free.

#### Kill a process

```bash
~/.claude/skills/process-manager/scripts/kill-pid.sh <PID>
~/.claude/skills/process-manager/scripts/kill-pid.sh <PID> --force              # Skip SIGTERM, go straight to SIGKILL
~/.claude/skills/process-manager/scripts/kill-pid.sh <PID> --timeout 10          # Wait 10s before force-killing (default: 5)
```

| Field | Meaning |
|---|---|
| `KILL_STATUS` | `terminated`, `failed`, `not_found`, `permission_denied` |
| `KILL_METHOD` | `SIGTERM`, `SIGKILL`, `SIGKILL_fallback` |
| `PROCESS_CMD` | Command name of the killed process |

**Exit 0** — process terminated.
**Exit 1** — still running after all attempts. Alert user.
**Exit 3** — permission denied. Suggest `sudo` or ask user.
**Exit 4** — PID not found (already dead). Inform user, no action needed.

#### Check if a process is running

```bash
~/.claude/skills/process-manager/scripts/check-pid.sh <PID>
```

| Field | Meaning |
|---|---|
| `PROC_STATUS` | `running` or `not_running` |
| `PROCESS_CMD` | Command name |
| `PROCESS_USER` | Owner |
| `PROCESS_CPU` | CPU usage % |
| `PROCESS_MEM` | Memory usage % |
| `PROCESS_ELAPSED` | Time since start |
| `PROCESS_FULL_CMD` | Full command line |

**Exit 0** — running. **Exit 1** — not running.

### Decision Guidance

#### Port conflict ("Address already in use")

1. Run `~/.claude/skills/process-manager/scripts/find-port.sh <port>` to identify what's there
2. Present process info to user and ask:
   - **Kill it** — if it's a leftover test server, run `~/.claude/skills/process-manager/scripts/kill-pid.sh <PID>`
   - **Use different port** — if it's a production service or unrelated process
   - **Investigate** — if unclear, run `~/.claude/skills/process-manager/scripts/check-pid.sh <PID>` for more detail

#### Permission denied

The process is owned by another user or requires elevated privileges.
- Suggest running with `sudo` if appropriate
- Otherwise suggest using a different port

#### Process won't die (exit 1 from kill-pid.sh)

The process survived both SIGTERM and SIGKILL. This is rare.
- Alert the user — may need manual intervention
- Could be a zombie process (parent needs to reap it)
- Could be stuck in uninterruptible I/O (disk/NFS issue)
