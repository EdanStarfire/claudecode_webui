---
name: ui-permission-respond
description: Detect and respond to a permission prompt in the Claude Code WebUI. Use when approving or denying tool permissions, handling permission modals, responding to Claude asking for approval, or unblocking a paused session.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__take_screenshot
---

# ui-permission-respond

Detect and respond to a permission prompt in the active session.

## Inputs

- `action` (required) — `allow` or `deny`
- `timeout` (optional, default: 30000) — Milliseconds to wait for permission prompt to appear

## Prerequisites

A session must be selected and in a state where it's awaiting permission. The session chip will show an amber dot (`.ac-status-sq.paused`) and/or a `?` badge (`.ac-alert.permission`).

## Steps

### 1. Wait for Permission State

Poll for the session to enter paused/permission state. Do NOT use a fixed sleep.

Check for amber status indicator:
```
wait_for(selector=".agent-chip.active .ac-status-sq.paused", timeout=<timeout>)
```

Alternative: check for the permission alert badge:
```
wait_for(selector=".agent-chip.active .ac-alert.permission", timeout=<timeout>)
```

If timeout: take a snapshot and report "No permission prompt detected within <timeout>ms. Session may already be idle or processing."

### 2. Locate the Permission Section

The permission prompt appears inside the activity timeline in the session view. Find the permission section:

```
wait_for(selector=".permission-section", timeout=5000)
```

If `.permission-section` is not visible, check if the timeline needs to be scrolled or the tool node needs to be expanded:
```javascript
// Check for permission-related content
const perm = document.querySelector('.permission-section, .detail-banner-warning');
perm?.scrollIntoView({ behavior: 'smooth', block: 'center' });
```

### 3. Take a Snapshot for Verification

Before responding, take a snapshot to confirm the permission prompt is present and note what tool is requesting permission.

```
take_snapshot()
```

Look for:
- `.detail-banner.detail-banner-warning` with text "Permission Required"
- The tool name in the permission description
- Any suggested permission updates in `.suggestions-section`

### 4. Respond to the Permission

**To ALLOW:**

If there are suggestions and you want to approve with suggestions applied:
```
click(selector=".btn-timeline.btn-approve")
```
Button text: "Approve & Apply (N/M)" where N/M indicates suggestion counts.

If you want to approve without applying suggestions:
```
click(selector=".btn-timeline.btn-approve-outline")
```
Button text: "Approve Only"

**To DENY:**
```
click(selector=".btn-timeline.btn-deny")
```
Button text: "Deny"

### 5. Wait for Permission Modal to Dismiss

After responding, wait for the permission section to be replaced and the session to resume:

Poll for the session to leave paused state:
```
wait_for(selector=".agent-chip.active .ac-status-sq.active-processing, .agent-chip.active .ac-status-sq.active", timeout=10000)
```

The session should:
- On **allow**: return to `.active-processing` (purple) as Claude continues
- On **deny**: return to `.active` (green) as Claude handles the denial and stops the tool

### 6. Verify Permission Dismissed

Take a snapshot to confirm:
- The permission `?` badge is gone from the session chip: no `.ac-alert.permission`
- The session is back in active state
- If allowed: the tool continues execution

```javascript
// Confirm no permission badge
const badge = document.querySelector('.agent-chip.active .ac-alert.permission');
!badge // should be null
```

## AskUserQuestion Handling

For `AskUserQuestion` tool (different from standard permission):
- Banner text: "Claude is asking for your input"
- Class: `.detail-banner-info`
- Response: Fill the answer input and click "Submit Answers" (`.btn-timeline.btn-primary`)
- Skip: "Skip" button (`.btn-timeline.btn-secondary`)

This skill handles standard permission prompts. For AskUserQuestion, use `ui-send-message` or handle manually.

## Error Handling

- **No permission prompt within timeout**: Session may already have moved on. Take a snapshot to check current state.
- **Approve button not found**: The permission section may be collapsed inside a timeline node. Try clicking the timeline node to expand it.
- **Session stuck in paused state after response**: Take a screenshot. Check browser console for errors.
- **Multiple permission prompts**: This skill handles one at a time. After each allow/deny, check if another permission follows before proceeding.

## Notes

- The `.btn-approve` button (with suggestions) is preferred when suggestions are available, as it applies permission rules that prevent future prompts.
- If `action=allow` and `.btn-approve` is not present (no suggestions), use `.btn-approve-outline` instead.
- After a session reset, any in-flight permission requests are cancelled (shown as "Permission Request Cancelled").
