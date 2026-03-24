---
name: ui-session-reset
description: Reset a session via the manage modal in the Claude Code WebUI. Use when resetting a session, clearing session history, restarting a Claude Code conversation, or waiting for session reconnect after reset.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__take_screenshot
---

# ui-session-reset

Reset the currently active session via the manage modal.

## Prerequisites

A session must be selected and active. Run `ui-navigate` with `project` and `session` arguments first if needed.

## Steps

### 1. Open the Session Manage Modal

Find and click the manage/settings button for the active session. This is typically accessible via a button in the session info bar or by right-clicking the session chip.

Check for a manage button in the session view:
```javascript
// Look for manage or settings button in session info bar
const manageBtn = document.querySelector(
  '.session-info-bar .btn[aria-label*="manage"], ' +
  '.session-info-bar .btn[aria-label*="settings"], ' +
  '[aria-label*="Manage session"], ' +
  '.session-manage-btn'
);
manageBtn?.click();
```

Alternatively, look for a clickable element that opens `#manageSessionModal`:
```javascript
document.querySelector('[data-bs-target="#manageSessionModal"]')?.click()
```

Wait for the modal to appear:
```
wait_for(selector="#manageSessionModal.show, #manageSessionModal[aria-modal='true']", timeout=5000)
```

If modal does not open: take a screenshot to identify the manage trigger element.

### 2. Click "Reset Session"

Inside the manage modal, click the Reset Session button:
```
wait_for(selector="#manageSessionModal .btn-outline-warning", timeout=3000)
click(selector="#manageSessionModal .btn-outline-warning")
```

The button has text "Reset Session". This reveals a confirmation view.

### 3. Confirm the Reset

Wait for the confirmation prompt to appear, then click the confirm button:
```
wait_for(selector="#manageSessionModal .btn.btn-warning", timeout=3000)
click(selector="#manageSessionModal .btn.btn-warning")
```

The confirm button has text "Confirm Reset". Do NOT click Cancel (`.btn-secondary` with text "Cancel").

### 4. Wait for Modal to Close

```
wait_for(selector="#manageSessionModal:not(.show)", timeout=10000)
```

If modal does not close: take a snapshot and report any error messages.

### 5. Wait for Session to Reconnect

After reset, the session goes through `starting` → `active` states. Poll for the session to be active and idle (not processing). Do NOT use a fixed sleep.

Wait for the active chip to show green (idle) state:
```
wait_for(selector=".agent-chip.active .ac-status-sq.active", timeout=30000)
```

While waiting, also check that the session is not in error state:
```javascript
const sq = document.querySelector('.agent-chip.active .ac-status-sq');
sq?.classList.contains('error') // if true, report error
```

If a "Claude Code Launched" system message appears in the message list, that is the canonical signal that the session has reconnected:
```javascript
const msgs = document.querySelectorAll('.messages-area .message-item');
Array.from(msgs).some(m => m.textContent.includes('Claude Code Launched') || m.textContent.includes('launched'))
```

### 6. Verify Reset Complete

Take a snapshot and confirm:
- Session chip shows green/active state
- No error indicators
- Input area is enabled and not disabled

```
wait_for(selector="#message-input:not([disabled])", timeout=10000)
```

## Error Handling

- **Manage modal doesn't open**: The trigger button may have a different selector. Take a screenshot and look for a gear icon or "manage" text near the session.
- **Reset button not found**: The modal may show a different view. Check for spinner or loading state.
- **Session enters error state after reset**: Red dot (`.ac-status-sq.error`). Report the error and suggest checking server logs.
- **Session never becomes active after 30s**: Session may be stuck in `starting`. Check browser console messages.

## Notes

- Reset clears the session's SDK conversation history and restarts the Claude Code process.
- The session messages in the UI are preserved (not deleted) — only the SDK conversation context is reset.
- After reset, the next message sent will start a fresh conversation.
- In mock-sdk mode, reset causes the mock SDK to reinitialize with the same fixture.
