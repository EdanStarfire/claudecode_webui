---
name: ui-project-create
description: Create a new project in the Claude Code WebUI via the UI. Use when creating a project, adding a project, opening the new project modal, or testing project creation flow.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__fill
  - mcp__chrome_devtools__type_text
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_screenshot
---

# ui-project-create

Create a new project via the WebUI project creation modal.

## Inputs

- `name` (required) — Project display name
- `directory` (required) — Working directory path for the project

## Prerequisites

The app must be loaded and connected. Run `ui-navigate` first if needed.

## Steps

### 1. Open the Create Project Modal

Click the "+" add button in the project pill bar:
```
wait_for(selector=".project-pill-bar", timeout=5000)
click(selector=".pill-add-btn")
```

Wait for the modal to appear:
```
wait_for(selector="#createProjectModal.show, #createProjectModal[aria-modal='true']", timeout=5000)
```

Alternative selector if Bootstrap modal uses display style:
```javascript
// Check modal is visible
const modal = document.querySelector('#createProjectModal');
modal && (modal.classList.contains('show') || window.getComputedStyle(modal).display !== 'none')
```

If modal doesn't open within 5s: take a screenshot and report "Create project modal did not open. Check `.pill-add-btn` is clickable."

### 2. Fill the Project Name

Wait for the name field and fill it:
```
wait_for(selector="#projectName", timeout=3000)
fill(selector="#projectName", value=<name>)
```

### 3. Fill the Working Directory

Fill the directory field:
```
fill(selector="#workingDirectory", value=<directory>)
```

### 4. Submit the Form

Verify the submit button is enabled (not disabled):
```
wait_for(selector="#createProjectModal .btn.btn-primary:not([disabled])", timeout=3000)
```

Click the "Create Project" button:
```
click(selector="#createProjectModal .btn.btn-primary")
```

The button text changes to "Creating..." while loading. Do NOT click again.

### 5. Wait for Modal to Close

Poll for the modal to dismiss, indicating success:
```
wait_for(selector="#createProjectModal:not(.show)", timeout=10000)
```

Alternative check — wait for modal to have aria-hidden:
```javascript
const modal = document.querySelector('#createProjectModal');
!modal || !modal.classList.contains('show')
```

If the modal does not close within 10s:
- Check for an error alert: `.alert.alert-danger[role="alert"]`
- Take a snapshot and report the error text

### 6. Verify Project Appears in Sidebar

Wait for the new project pill to appear in the project pill bar:
```javascript
// Poll for pill with matching text
const pills = document.querySelectorAll('.project-pill-bar .project-pill, .project-pill-bar [role="button"]');
Array.from(pills).some(p => p.textContent.trim().includes('<name>'))
```

Use `wait_for` with the evaluate approach or take a snapshot and verify manually.

If the project pill does not appear within 5s: take a snapshot and report "Project created but not visible in pill bar. May need page refresh."

## Error Handling

- **Submit button stays disabled**: The form may have validation errors. Check that `#projectName` and `#workingDirectory` are both filled.
- **Error alert shown**: Read `.alert.alert-danger` text and report it (e.g., "Directory does not exist", "Name already taken").
- **Modal closes but project missing**: Take a full snapshot of the pill bar. The project may have been created with a truncated name.

## Notes

- The modal also has a "Create session" checkbox (`#createSession`). This skill leaves it at its default value. If you want to create an initial session simultaneously, click the checkbox before submitting.
- The directory field accepts any string — the server validates existence. For mock-sdk testing, use a valid directory that exists on the server.
