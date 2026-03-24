---
name: ui-project-delete
description: Delete a project via the Claude Code WebUI. Use when deleting a project, removing a project, testing project deletion, or cleaning up test projects.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__take_screenshot
---

# ui-project-delete

Delete a project via the WebUI project edit modal.

## Inputs

- `name` (required) — Name of the project to delete

## Prerequisites

The app must be loaded and connected. Run `ui-navigate` first if needed.

## Steps

### 1. Select the Target Project

Find and click the project pill for the target project:
```javascript
// Find project pill by name
const pills = document.querySelectorAll('.project-pill-bar .project-pill, .project-pill-bar [role="button"]');
const target = Array.from(pills).find(p => p.textContent.trim().includes('<name>'));
if (target) target.click();
```

Use `evaluate_script` to run this. If `target` is null: take a snapshot and report "Project '<name>' not found in project pill bar."

### 2. Open the Project Edit Modal

After selecting the project, find the edit button. In the pill bar, look for an edit/settings icon button associated with the selected project pill:

```javascript
// Look for edit button in the active/selected project area
const editBtn = document.querySelector('.project-pill.active .pill-edit-btn, .project-pill-bar .btn-edit, [aria-label*="Edit project"], [aria-label*="edit"]');
editBtn?.click();
```

If no edit button is visible via direct click, check if hovering over the selected pill reveals it:
```
// Try clicking the gear/edit icon in pill bar
click(selector=".project-pill.active .pill-options-btn")
```

Wait for the edit modal to open:
```
wait_for(selector="#editProjectModal.show, #editProjectModal[aria-modal='true']", timeout=5000)
```

If modal does not open within 5s: take a screenshot to identify the correct edit button selector, then report the issue.

### 3. Initiate Delete

Scroll to the danger zone section and click the delete button. Look for a button with class `.btn-outline-danger` or text matching "Delete":

```
wait_for(selector="#editProjectModal .danger-zone .btn-outline-danger", timeout=3000)
click(selector="#editProjectModal .danger-zone .btn-outline-danger")
```

This reveals a confirmation prompt (`.alert.alert-warning`).

### 4. Confirm Deletion

Wait for the confirmation alert to appear, then click the "Yes, Delete Permanently" button:

```
wait_for(selector="#editProjectModal .btn.btn-danger", timeout=3000)
click(selector="#editProjectModal .btn.btn-danger")
```

The button shows text "Yes, Delete Permanently" (or "Deleting..." while in progress). Do NOT click again while it shows "Deleting...".

### 5. Wait for Modal to Close

Poll for the modal to dismiss:
```
wait_for(selector="#editProjectModal:not(.show)", timeout=10000)
```

If modal stays open after 10s: take a snapshot, check for error messages, and report.

### 6. Verify Project Removed from Pill Bar

Check that the project pill is gone:
```javascript
const pills = document.querySelectorAll('.project-pill-bar .project-pill, .project-pill-bar [role="button"]');
!Array.from(pills).some(p => p.textContent.trim().includes('<name>'))
```

If still present after 5s: take a snapshot and report "Project pill still visible after deletion. May be a display refresh issue."

## Error Handling

- **Project not found**: No pill matches the name. Check spelling. Take a snapshot to list available projects.
- **Edit modal doesn't open**: The edit button selector may vary. Take a screenshot to identify the correct trigger element.
- **Delete button not visible**: The danger zone section may need scrolling. Use `evaluate_script` to scroll: `document.querySelector('.danger-zone')?.scrollIntoView()`.
- **Confirmation shows error**: Read `.alert.alert-danger` text and report. The project may have active sessions preventing deletion.

## Notes

- This skill handles the two-step delete confirmation (click delete → click confirm).
- If the project has active sessions, deletion may fail. Terminate sessions first using `ui-session-reset` or by stopping sessions manually.
- The Cancel button (`.btn-secondary` text "Cancel") dismisses the confirmation without deleting.
