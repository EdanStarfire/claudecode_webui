# Tool Handler System

## Overview
The WebUI now supports custom display handlers for specific tools. Each tool can have a custom handler that controls how its parameters, results, and collapsed summary are displayed.

## Architecture

### Components
1. **ToolHandlerRegistry** - Central registry for tool handlers
2. **DefaultToolHandler** - Fallback handler for tools without custom implementations
3. **Tool-specific handlers** (e.g., `ReadToolHandler`) - Custom display logic per tool

## How to Add a New Tool Handler

### 1. Create Handler Class (in app.js)

```javascript
class MyToolHandler {
    /**
     * Render tool parameters (required)
     * @param {Object} toolCall - Tool call state object
     * @param {Function} escapeHtmlFn - HTML escaping function
     * @returns {string} HTML string for parameters display
     */
    renderParameters(toolCall, escapeHtmlFn) {
        // Access parameters from toolCall.input
        const myParam = toolCall.input.my_parameter;

        return `
            <div class="tool-parameters tool-mytool-params">
                <strong>My Parameter:</strong>
                <span>${escapeHtmlFn(myParam)}</span>
            </div>
        `;
    }

    /**
     * Render tool result (required)
     * @param {Object} toolCall - Tool call state object
     * @param {Function} escapeHtmlFn - HTML escaping function
     * @returns {string} HTML string for result display
     */
    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';
        const content = toolCall.result.content || toolCall.result.message;

        return `
            <div class="tool-result ${resultClass}">
                <strong>Result:</strong>
                <pre>${escapeHtmlFn(content)}</pre>
            </div>
        `;
    }

    /**
     * Generate custom collapsed summary (optional)
     * @param {Object} toolCall - Tool call state object
     * @returns {string|null} Custom summary string, or null to use default
     */
    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'completed': '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        return `${statusIcon} MyTool - Custom Summary`;
    }
}
```

### 2. Register Handler (in ClaudeWebUI.initializeToolHandlers())

```javascript
initializeToolHandlers() {
    // Register built-in tool handlers
    this.toolHandlerRegistry.registerHandler('Read', new ReadToolHandler());
    this.toolHandlerRegistry.registerHandler('MyTool', new MyToolHandler());

    // Register pattern handlers for MCP tools
    this.toolHandlerRegistry.registerPatternHandler('mcp__*', new McpToolHandler());
}
```

### 3. Add CSS Styling (in styles.css)

```css
/* MyTool Handler Styles */
.tool-mytool-params {
    background: #f0fff0;
    border: 1px solid #d0ffd0;
    border-radius: 6px;
    padding: 1rem;
}

/* Add more custom styles as needed */
```

## Example: Read Tool Handler

The `ReadToolHandler` demonstrates custom rendering:

### Parameters Display
- Shows file path prominently with icon 📄
- Displays line range if offset/limit specified
- Uses blue background to distinguish from other tools

### Result Display
- Shows line count at top
- Previews first 20 lines with scrollable container
- Indicates if more content exists with "..." indicator
- Syntax-highlighted monospace font

### Collapsed Summary
- Shows filename (extracted from path)
- Shows line count when completed successfully
- Format: `✅ Read filename.txt - 150 lines`

## Pattern Matching

Use `registerPatternHandler()` for tools matching a pattern:

```javascript
// Match all MCP tools (mcp__*)
this.toolHandlerRegistry.registerPatternHandler('mcp__*', new McpToolHandler());

// Use regex for complex patterns
this.toolHandlerRegistry.registerPatternHandler(/^custom_/, new CustomToolHandler());
```

## Handler Methods

### Required Methods
- `renderParameters(toolCall, escapeHtmlFn)` - Display tool parameters
- `renderResult(toolCall, escapeHtmlFn)` - Display tool result

### Optional Methods
- `getCollapsedSummary(toolCall)` - Custom collapsed view
  - Return `null` to use default summary
  - Return string for custom summary

## Accessing Tool Data

### toolCall Object Structure
```javascript
{
    id: "tool_use_123",
    name: "Read",
    input: { file_path: "/path/to/file.txt", ... },
    status: "completed", // pending, permission_required, executing, completed, error
    result: {
        error: false,
        content: "file contents..."
    },
    permissionRequestId: "req_123",
    permissionDecision: "allow", // or "deny"
    explanation: "Assistant's explanation text"
}
```

## Best Practices

1. **Always escape HTML** - Use the provided `escapeHtmlFn` for user-controlled content
2. **Handle missing data** - Check for undefined/null values in toolCall.input and toolCall.result
3. **Error states** - Provide clear error displays when toolCall.result.error is true
4. **Consistent styling** - Use existing CSS classes where possible
5. **Performance** - Keep rendering logic lightweight for smooth UI updates
6. **Accessibility** - Include appropriate ARIA labels and semantic HTML

## Example: Edit Tool Handler

The `EditToolHandler` demonstrates diff-style rendering:

### Parameters Display
- Shows file path with edit icon ✏️
- Displays "Replace All" badge if applicable
- **Diff view** showing line-by-line changes:
  - Red background with `-` for removed lines
  - Green background with `+` for added lines
  - White background with space for unchanged lines
- Monospace font for code readability

### Result Display
- Success: Shows checkmark with "File edited successfully"
- Error: Displays error message in red-tinted box

### Collapsed Summary
- Shows filename (extracted from path)
- Shows line count changed
- Format: `✅ Edit filename.txt - 4 lines changed`

## Example: MultiEdit Tool Handler

The `MultiEditToolHandler` demonstrates multiple diff rendering:

### Parameters Display
- Shows file path with edit icon ✏️
- Displays purple badge showing total edit count
- **Multiple diff sections** - each edit shown separately:
  - Purple header: "Edit 1 of 3", "Edit 2 of 3", etc.
  - Each section uses same diff view as Edit tool
  - Red/green highlighting for removed/added lines
- **Reuses diff CSS classes** - all diff styling (`.diff-view`, `.diff-line`, `.diff-marker`, etc.) is shared between Edit and MultiEdit for consistency

### Result Display
- Success: Shows checkmark with count "3 edits applied successfully"
- Error: Displays error message in red-tinted box

### Collapsed Summary
- Shows filename (extracted from path)
- Shows edit count
- Format: `✅ MultiEdit filename.txt - 3 edits`

## Example: Write Tool Handler

The `WriteToolHandler` demonstrates new file creation display:

### Parameters Display
- Shows file path with write icon 📝
- Displays "Writing new file:" label
- **Content preview** showing file content:
  - Line count displayed in header
  - Preview of first 20 lines
  - Scrollable monospace view
  - "..." indicator if more content exists
- Green background to indicate new file creation

### Result Display
- Success: Shows checkmark with "File created successfully (X lines written)"
- Error: Displays error message in red-tinted box

### Collapsed Summary
- Shows filename (extracted from path)
- Shows line count written
- Format: `✅ Write filename.txt - 25 lines`

## Example: TodoWrite Tool Handler

The `TodoWriteToolHandler` demonstrates task tracking display:

### Parameters Display
- Shows clipboard icon 📋 with "Task List:" header
- **Summary badges** showing completed/in-progress/pending counts (right-aligned)
- **Checklist view** with visual status indicators:
  - ☐ Empty checkbox for `pending` tasks (gray text)
  - ◐ Half-filled checkbox for `in_progress` tasks (orange left border, bold text)
  - ☑ Checked checkbox for `completed` tasks (green left border, strikethrough)
- Orange/amber background theme
- Hover effects on todo items

### Result Display
- Success: Shows checkmark with "Task list updated (X tasks)"
- Error: Displays error message in red-tinted box

### Collapsed Summary
- Shows completed/pending counts and full text of in-progress tasks
- Format: `✅ TodoWrite - 2 completed, 1 pending | ◐ Task description here`
- Multiple in-progress tasks separated by ` | `
- Allows seeing current work without expanding the tool card

## Future Enhancements

Ideas for additional handlers:
- **Bash Tool** - Syntax highlight commands, format output
- **Grep Tool** - Highlight matching patterns in results
- **MCP Tools** - Generic handler with server identification