---
name: ui-send-message
description: Send a message in the active session of the Claude Code WebUI. Use when typing and sending a message to Claude, submitting a prompt, interacting with an active session, or waiting for the assistant to finish responding.
allowed-tools:
  - mcp__chrome_devtools__wait_for
  - mcp__chrome_devtools__click
  - mcp__chrome_devtools__type_text
  - mcp__chrome_devtools__press_key
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__take_screenshot
---

# ui-send-message

Send a message in the active session and optionally wait for the assistant to finish responding.

## Inputs

- `message` (required) — Text to send
- `wait_for_idle` (optional, default: `true`) — Wait for the assistant to finish before returning

## Prerequisites

A session must be active and selected. Run `ui-navigate` with the appropriate `project` and `session` arguments first if needed.

## Steps

### 1. Verify Session is Ready

Check that the active session chip exists and is NOT in processing state:
```
wait_for(selector=".agent-chip.active", timeout=5000)
```

Check the input area is enabled. Wait for the textarea to not be disabled:
```
wait_for(selector="#message-input:not([disabled])", timeout=10000)
```

If the input is still disabled after 10s: take a snapshot and report "Input area is disabled. Session may be processing, disconnected, or starting."

### 2. Click the Input Area

```
click(selector="#message-input")
```

### 3. Type the Message

Use `type_text` to enter the message content. This appends to whatever is in the field:
```
type_text(text=<message>)
```

If the input needs to be cleared first, use `evaluate_script`:
```javascript
const input = document.querySelector('#message-input');
input.value = '';
input.dispatchEvent(new Event('input', { bubbles: true }));
```

### 4. Send the Message

Press Enter to send (preferred method — matches normal user behavior):
```
press_key(key="Enter")
```

Alternative: click the Send button:
```
click(selector=".input-container .btn.btn-primary")
```

Note: The Send button shows text "Send" when idle, and is replaced by "Stop" when processing. Only click the Send button if it shows "Send" text.

### 5. Wait for Processing to Start

After sending, wait briefly for the session to enter processing state (purple dot):
```
wait_for(selector=".agent-chip.active .ac-status-sq.active-processing", timeout=5000)
```

If the processing indicator never appears within 5s, the message may have been sent to a queued session or the session started very fast. Continue to the idle check regardless.

### 6. Wait for Idle (if wait_for_idle is true)

Poll for the session to return to idle state (green dot, not processing). Do NOT use a fixed sleep.

```
wait_for(selector=".agent-chip.active .ac-status-sq.active:not(.active-processing)", timeout=120000)
```

Wait up to 120 seconds for the assistant to finish. If timeout: take a screenshot, take a snapshot, and report "Session did not return to idle within 120s. May still be processing. Check for permission prompts (`.ac-alert.permission`)."

**Permission detection during wait**: If the session enters `.paused` state while waiting:
```
// Check for paused state (amber dot)
.agent-chip.active .ac-status-sq.paused
// or permission alert badge
.agent-chip.active .ac-alert.permission
```

If paused, report "Session is paused awaiting permission. Use `ui-permission-respond` to handle the permission request before continuing."

### 7. Confirm Response Received

Take a snapshot to verify a response appeared in the message area:
```
wait_for(selector=".messages-area", timeout=5000)
```

Use `evaluate_script` to check the last message:
```javascript
const msgs = document.querySelectorAll('.messages-area .message-item');
const last = msgs[msgs.length - 1];
last?.textContent?.trim().substring(0, 100)
```

## Idle State Detection

The session is idle (ready for next message) when:
- `.agent-chip.active .ac-status-sq.active` exists (green dot)
- AND `.agent-chip.active .ac-status-sq.active-processing` does NOT exist

Use `evaluate_script` for explicit check:
```javascript
const chip = document.querySelector('.agent-chip.active');
const sq = chip?.querySelector('.ac-status-sq');
sq?.classList.contains('active') && !sq?.classList.contains('active-processing')
```

## Error Handling

- **Input disabled**: Session may be in `starting` or `terminating` state. Wait or run `ui-navigate` to select a valid active session.
- **Message not sent**: Verify `#message-input` received the text. Check for validation errors.
- **Session stuck in processing**: After 120s take a screenshot. Check for permission modal in the activity timeline.
- **Permission required**: Pause and invoke `ui-permission-respond` with appropriate `action`.

## Queue vs Send

If the session is processing when you call this skill and you want to queue the message rather than wait:
- Click the "Queue" button (`.btn.btn-info` with text "Queue") instead of Send
- Queued messages are delivered automatically when the session returns to idle
