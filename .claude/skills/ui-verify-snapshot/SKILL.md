---
name: ui-verify-snapshot
description: Take a snapshot of the Claude Code WebUI and verify expected content is present. Use when asserting UI state, checking that text or elements are visible, verifying a test condition, taking a screenshot for review, or reporting pass/fail on UI expectations.
allowed-tools:
  - mcp__chrome_devtools__take_snapshot
  - mcp__chrome_devtools__take_screenshot
  - mcp__chrome_devtools__evaluate_script
  - mcp__chrome_devtools__wait_for
---

# ui-verify-snapshot

Take an accessibility snapshot and verify expected content is present. Report pass/fail with details.

## Inputs

- `expected` (required) — Text string or CSS selector to verify is present
- `screenshot` (optional, default: `true`) — Whether to also take a screenshot

## Steps

### 1. Take Screenshot (if enabled)

If `screenshot=true`:
```
take_screenshot()
```

This captures the visual state for review.

### 2. Take Accessibility Snapshot

```
take_snapshot()
```

The snapshot returns the full accessibility tree and visible text content of the page.

### 3. Evaluate Expected Content

Determine the type of `expected`:

**If `expected` looks like a CSS selector** (starts with `.`, `#`, `[`, or is a tag name):
```javascript
// Check for element presence
const el = document.querySelector('<expected>');
el !== null  // true = found, false = not found
```

Use `evaluate_script` to run this check. Report:
- PASS: "Element '<expected>' found — `<tagName>`, text: `<element.textContent.trim().substring(0, 100)>`"
- FAIL: "Element '<expected>' NOT found. Page title: `<document.title>`. URL: `<location.href>`"

**If `expected` is plain text** (not a selector):
Check for the text in the snapshot or via DOM:
```javascript
// Check body text content
document.body.textContent.includes('<expected>')
```

Report:
- PASS: "Text '<expected>' found on page."
- FAIL: "Text '<expected>' NOT found on page."

### 4. Additional Context on Failure

If verification fails, gather additional context:

```javascript
// Current URL
location.href

// Page title
document.title

// Connection state
document.querySelector('.header-indicator')?.className

// Active session info
{
  chip: document.querySelector('.agent-chip.active')?.textContent?.trim(),
  state: document.querySelector('.agent-chip.active .ac-status-sq')?.className,
  permission: !!document.querySelector('.agent-chip.active .ac-alert.permission')
}
```

Report all of this in the failure output.

### 5. Report Result

Output a clear pass/fail result:

**PASS format:**
```
✓ PASS: [expected content] is present.
  - Element: <selector or text>
  - Page: <URL>
  - Session state: <state class>
```

**FAIL format:**
```
✗ FAIL: [expected content] is NOT present.
  - Expected: <expected>
  - Page: <URL>
  - Page title: <title>
  - Connection: <header-indicator class>
  - Active session: <chip text / state>
  - Suggestion: <what to check>
```

## Common Verification Patterns

### Check app is loaded and connected
```
expected: ".header-indicator.connected"
```

### Check project is visible
```
expected: "My Project"  (text in pill bar)
```

### Check session is idle (ready for input)
```
expected: ".agent-chip.active .ac-status-sq.active"
```

### Check session is processing
```
expected: ".agent-chip.active .ac-status-sq.active-processing"
```

### Check session has permission prompt
```
expected: ".agent-chip.active .ac-alert.permission"
```

### Check message text appeared
```
expected: "Hello, world"  (text in messages area)
```

### Check input is enabled
```
expected: "#message-input:not([disabled])"
```

### Check modal is open
```
expected: "#createProjectModal.show"
```

## Error Handling

- If `take_snapshot` fails: report "Snapshot failed — page may be in a transient state" and try again once.
- If `evaluate_script` throws: report the JavaScript error and fall back to text search in the snapshot.
- If `expected` is ambiguous (could be text or selector): try as selector first, then as text.

## Composability

This skill is designed to be the final step in test flows. Typical composition:
1. `ui-navigate` → navigate and connect
2. `ui-project-create` → create project
3. `ui-verify-snapshot` with `expected: "My New Project"` → confirm success

Always use this skill to assert final state rather than relying on previous skills' output alone.
