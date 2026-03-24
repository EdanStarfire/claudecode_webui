---
name: ui-session-create
description: Create a new session within the currently selected project in the Claude Code WebUI. Use when adding a new session, creating an agent, testing session creation, or setting up a new conversation.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__fill
  - mcp__chrome_devtools__type_text
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__take_screenshot
---

# ui-session-create

Create a new session within the currently selected project.

## Inputs

- `name` (optional) — Session name. If omitted, the server assigns a default name.

## Prerequisites

A project must be selected (visible in the project pill bar with the agent strip showing). Run `ui-navigate` with the `project` argument first.

## Steps

### 1. Find the "Add Agent" Button

Wait for the agent strip to be visible:
```
wait_for(selector=".agent-strip", timeout=5000)
```

Click the add-session button (dashed "+" button at the right end of the agent strip):
```
wait_for(selector=".strip-add-btn", timeout=3000)
click(selector=".strip-add-btn")
```

### 2. Handle Session Name Input (if required)

The UI may show a prompt for the session name, or it may create the session immediately and route to it. Check which behavior applies:

a. **If a name input dialog appears** (modal or inline prompt):
```javascript
// Check for any visible input that appeared after clicking
const newInput = document.querySelector('[placeholder*="session"], [placeholder*="name"], [placeholder*="agent"]');
if (newInput) newInput.value = '<name>';
```

b. **If session is created immediately** (no name prompt): the new chip will appear in the agent strip. If `name` was provided, skip to the rename step or accept the default name.

### 3. Wait for the New Session Chip to Appear

Poll for a new session chip to appear in the agent strip:
```
wait_for(selector=".agent-chip", timeout=10000)
```

Use `evaluate_script` to count chips before and after to detect the new one:
```javascript
document.querySelectorAll('.agent-chip').length
```

### 4. Verify Session is Created and Starting

The new session should appear with `.ac-status-sq.created` or `.ac-status-sq.starting` state initially.

Wait for the new chip to become active (selected):
```
wait_for(selector=".agent-chip.active", timeout=5000)
```

Read the session name from the active chip:
```javascript
document.querySelector('.agent-chip.active .ac-name')?.textContent?.trim()
```

### 5. Wait for Session to be Ready (Optional but Recommended)

If the session transitions through `starting` state, wait for it to reach `active`:
```
wait_for(selector=".agent-chip.active .ac-status-sq.active, .agent-chip.active .ac-status-sq.created", timeout=15000)
```

For a freshly created session that has not been started, the state will be `created` (gray dot).

## Idempotency

If a session with the given name already exists, this skill creates a second session with the same name (the UI allows duplicate names). To avoid this, first check with `ui-navigate` or `ui-verify-snapshot` whether the session already exists.

## Error Handling

- **Add button not found**: Agent strip may not be visible. Ensure a project is selected. Take a screenshot.
- **No new chip appears after 10s**: Server may have returned an error. Check browser console messages with `list_console_messages`.
- **Session stuck in `starting` state**: The server may be launching the SDK. Wait up to 30s before reporting.
- **Session enters `error` state**: Red dot (`.ac-status-sq.error`). Take a snapshot and check console for errors.

## Notes

- In mock-sdk mode (`--mock-sdk`), sessions are created with a fixture-based name. The session name corresponds to the fixture directory used.
- The new session starts in `created` state and remains idle until a message is sent or it is manually started.
