# Claude Code WebUI — Browser Automation Skills

Composable Chrome DevTools MCP skills for UI testing of the Claude Code WebUI.

## Skills

| Skill | Purpose |
|-------|---------|
| `ui-navigate` | Navigate to the app, verify connection, select project/session |
| `ui-project-create` | Create a new project via the creation modal |
| `ui-project-delete` | Delete a project via the edit modal |
| `ui-session-create` | Create a new session within the selected project |
| `ui-session-reset` | Reset a session via the manage modal |
| `ui-send-message` | Send a message and optionally wait for idle |
| `ui-permission-respond` | Detect and respond to permission prompts |
| `ui-verify-snapshot` | Assert expected content, report pass/fail |

## Design Principles

1. **No fixed sleeps** — all wait steps use `mcp__chrome_devtools__wait_for` polling
2. **Long-poll aware** — connection is HTTP long-poll (`/api/poll/ui`, `/api/poll/session/{id}`), not WebSocket. Connection check uses `.header-indicator.connected`
3. **Idempotent** — handle "already in desired state" without error
4. **Clear errors** — specific failure messages with current UI state context
5. **Composable** — no side effects that break subsequent skills
6. **Mock-SDK compatible** — works against `--mock-sdk --fixtures-dir <dir>` servers

## Key UI Selectors Reference

### Connection State
- `.header-indicator.connected` — long-poll active
- `.header-indicator.disconnected` — connection lost

### Session Chip States
- `.agent-chip.active` — currently selected session
- `.ac-status-sq.active` — session idle (green)
- `.ac-status-sq.active-processing` — session responding (purple)
- `.ac-status-sq.paused` — awaiting permission (amber)
- `.ac-status-sq.error` — error state (red)
- `.ac-alert.permission` — permission badge (`?`)

### Message Input
- `#message-input` — message textarea
- `.input-container .btn.btn-primary` — Send button (text: "Send")
- `.input-container .btn.btn-warning` — Stop button (text: "Stop")
- `.input-container .btn.btn-info` — Queue button (text: "Queue")

### Modals
- `#createProjectModal` — create project modal
- `#editProjectModal` — edit/delete project modal
- `#manageSessionModal` — manage/reset/delete session modal

### Permission Prompt
- `.permission-section` — permission prompt container
- `.btn-timeline.btn-approve` — Approve & Apply suggestions
- `.btn-timeline.btn-approve-outline` — Approve Only
- `.btn-timeline.btn-deny` — Deny

## Composition Patterns

### Basic test flow
```
1. /ui-navigate url=http://localhost:8000 project="My Project" session="main"
2. /ui-send-message message="Hello" wait_for_idle=true
3. /ui-verify-snapshot expected="Hello" screenshot=true
```

### Create and test a new project
```
1. /ui-navigate url=http://localhost:8000
2. /ui-project-create name="Test Project" directory="/tmp/test"
3. /ui-navigate project="Test Project"
4. /ui-session-create name="test-session"
5. /ui-send-message message="echo hello"
6. /ui-verify-snapshot expected="hello"
7. /ui-project-delete name="Test Project"
```

### Handle permission during message
```
1. /ui-navigate project="My Project" session="main"
2. /ui-send-message message="Edit /etc/hosts" wait_for_idle=false
3. /ui-permission-respond action=deny
4. /ui-verify-snapshot expected=".agent-chip.active .ac-status-sq.active"
```

### Reset and verify reconnect
```
1. /ui-navigate project="My Project" session="main"
2. /ui-session-reset
3. /ui-verify-snapshot expected="#message-input:not([disabled])"
```

## Running Against Mock SDK

Start the server with mock SDK:
```bash
uv run python main.py --port 8000 --mock-sdk --fixtures-dir path/to/fixtures
```

Then run skills normally. The session name passed to `ui-navigate` resolves to a fixture directory name, so mock responses are deterministic.

## Timeout Reference

| Operation | Default Timeout |
|-----------|----------------|
| App shell load | 15s |
| Connection established | 20s |
| Modal open/close | 5–10s |
| Session active after reset | 30s |
| Message response (wait_for_idle) | 120s |
| Permission prompt appears | 30s |
