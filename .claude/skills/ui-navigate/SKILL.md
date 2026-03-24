---
name: ui-navigate
description: Navigate to the Claude Code WebUI application, verify it loads, and optionally select a project and/or session by name. Use when starting any UI test, opening the app, verifying the app is loaded, switching projects, or selecting sessions.
allowed-tools:
  - mcp__chrome_devtools__navigate_page
  - mcp__chrome_devtools__list_pages
  - mcp__chrome_devtools__new_page
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__take_screenshot
---

# ui-navigate

Navigate to the WebUI app, verify it loads, and optionally select a project and/or session.

## Inputs

- `url` (optional, default: `http://localhost:8000`) — App URL to navigate to
- `project` (optional) — Project name to select after loading
- `session` (optional) — Session name to select after selecting the project

## Steps

### 1. Open the App

Check if a page is already open at the target URL. If not, navigate to it:

```
navigate_page(url=<url>)
```

### 2. Wait for Main Layout

Poll until the main layout is rendered. Do NOT use a fixed sleep.

Poll for `.app-shell` to be present in the DOM:
```
wait_for(selector=".app-shell", timeout=15000)
```

If timeout: report "App shell not found — server may not be running at <url>".

### 3. Wait for Connection

Poll for the connection indicator to show "Connected". The app uses HTTP long-poll (NOT WebSocket). Check for `.header-indicator.connected`.

```
wait_for(selector=".header-indicator.connected", timeout=20000)
```

If timeout: check for `.header-indicator.disconnected` — report "App loaded but not connected. Long-poll connection failed. Check that backend is running."

If connected: the `.header-indicator` element contains text "Connected" and has the `.connected` CSS class.

### 4. Select Project (optional)

If `project` argument is provided:

a. Poll for the project pill bar:
```
wait_for(selector=".project-pill-bar", timeout=5000)
```

b. Find the project pill with matching text. Use `evaluate_script` to find the pill by name:
```javascript
// Find project pill containing the target text
const pills = document.querySelectorAll('.project-pill-bar .project-pill, .project-pill-bar [role="button"]');
const target = Array.from(pills).find(p => p.textContent.trim().includes('<project name>'));
target?.click();
```

c. If not found: take a snapshot and report "Project '<name>' not found in project pill bar. Available: <list>".

d. Wait for the agent strip to update:
```
wait_for(selector=".agent-strip", timeout=5000)
```

### 5. Select Session (optional)

If `session` argument is provided (requires project to be selected first):

a. Poll for session chips to appear:
```
wait_for(selector=".agent-chip", timeout=5000)
```

b. Find the chip with the target session name using `evaluate_script`:
```javascript
const chips = document.querySelectorAll('.agent-chip');
const target = Array.from(chips).find(c => {
  const name = c.querySelector('.ac-name');
  return name && name.textContent.trim().includes('<session name>');
});
target?.click();
```

c. If not found: take a snapshot and report "Session '<name>' not found. Available: <list of .ac-name texts>".

d. Wait for the selected chip to gain the `.active` class:
```
wait_for(selector=".agent-chip.active", timeout=5000)
```

### 6. Verify

Take a snapshot to confirm the current state. Report:
- URL loaded
- Connection: Connected / Disconnected
- Project selected (if requested): name
- Session selected (if requested): name and state

## Connection State Values

The `.header-indicator` element:
- `.connected` — long-poll active, app working normally
- `.disconnected` — connection lost, app cannot receive updates

The session chip `.ac-status-sq` classes indicate session state:
- `.active` (green) — idle, ready
- `.active-processing` (purple) — responding
- `.paused` (amber) — awaiting permission
- `.error` (red) — error state

## Error Handling

- **Server not running**: `wait_for` times out on `.app-shell`. Report the URL and suggest starting the server.
- **Long-poll not connected**: `.header-indicator` does not get `.connected` class. Check backend health endpoint.
- **Project not found**: take snapshot, list available projects from `.project-pill-bar`.
- **Session not found**: take snapshot, list available sessions from `.agent-chip .ac-name` texts.

## Mock-SDK Compatibility

Skills work identically against `--mock-sdk` servers. The `session` name passed to `ui-navigate` corresponds to a fixture directory name when the server is started with `--mock-sdk --fixtures-dir <dir>`.
