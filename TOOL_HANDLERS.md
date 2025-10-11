# Tool Handler System - Complete Reference

## Quick Navigation
- [Overview](#overview) - System purpose and architecture
- [File Locations](#file-locations) - Where each handler lives
- [Adding New Handlers](#how-to-add-a-new-tool-handler) - Step-by-step guide
- [Existing Handlers](#existing-tool-handlers-reference) - Complete handler catalog
- [Handler API](#handler-methods) - Required and optional methods
- [Best Practices](#best-practices) - Coding guidelines
- [Debugging](#debugging-tool-handlers) - Common issues and solutions

## Overview
The Tool Handler System provides customizable display logic for Claude Agent SDK tools. Each tool (Read, Edit, Write, Bash, etc.) can have a custom handler that controls:
- **Parameter Display**: How tool inputs are shown before execution
- **Result Display**: How tool outputs are rendered after execution
- **Collapsed Summary**: One-line summary when tool card is collapsed
- **Error Handling**: Custom error message formatting

**Architecture Pattern**: Registry + Strategy Pattern
1. **ToolHandlerRegistry** ([static/tools/tool-handler-registry.js](./static/tools/tool-handler-registry.js)) - Central registry for tool handlers
2. **DefaultToolHandler** ([static/tools/handlers/base-handler.js](./static/tools/handlers/base-handler.js)) - Fallback handler for tools without custom implementations
3. **Tool-specific handlers** ([static/tools/handlers/*.js](./static/tools/handlers/)) - Custom display logic per tool

## File Locations

### Core System Files
- **Registry**: [static/tools/tool-handler-registry.js](./static/tools/tool-handler-registry.js) - Manages handler lookup and registration
- **Base Handler**: [static/tools/handlers/base-handler.js](./static/tools/handlers/base-handler.js) - `DefaultToolHandler` class
- **Registration Point**: [static/app.js](./static/app.js) - `ClaudeWebUI.initializeToolHandlers()` method (around line 550)

### Handler Implementation Files
All handlers in [static/tools/handlers/](./static/tools/handlers/):
- [base-handler.js](./static/tools/handlers/base-handler.js) - `DefaultToolHandler` (fallback)
- [read-handler.js](./static/tools/handlers/read-handler.js) - `ReadToolHandler` (file reading with preview)
- [edit-handlers.js](./static/tools/handlers/edit-handlers.js) - `EditToolHandler`, `MultiEditToolHandler` (diff views)
- [write-handler.js](./static/tools/handlers/write-handler.js) - `WriteToolHandler` (file creation preview)
- [todo-handler.js](./static/tools/handlers/todo-handler.js) - `TodoWriteToolHandler` (task checklist)
- [search-handlers.js](./static/tools/handlers/search-handlers.js) - `GrepToolHandler`, `GlobToolHandler` (search results)
- [web-handlers.js](./static/tools/handlers/web-handlers.js) - `WebFetchToolHandler`, `WebSearchToolHandler`
- [bash-handlers.js](./static/tools/handlers/bash-handlers.js) - `BashToolHandler`, `BashOutputToolHandler`, `KillShellToolHandler`
- [misc-handlers.js](./static/tools/handlers/misc-handlers.js) - `TaskToolHandler`, `ExitPlanModeToolHandler`

### CSS Styling
- **Shared Styles**: [static/styles.css](./static/styles.css) - Tool card styling, diff views, status indicators
- **Custom Overrides**: [static/custom.css](./static/custom.css) - User customizations (optional)

### Loading Order (CRITICAL)
Defined in [static/index.html](./static/index.html):
```html
<!-- Core infrastructure (no dependencies) -->
<script src="/static/core/logger.js"></script>
<script src="/static/core/constants.js"></script>
<script src="/static/core/api-client.js"></script>
<script src="/static/core/project-manager.js"></script>

<!-- Tool system -->
<script src="/static/tools/tool-call-manager.js"></script>
<script src="/static/tools/tool-handler-registry.js"></script>

<!-- Tool handlers (must load before app.js) -->
<script src="/static/tools/handlers/base-handler.js"></script>
<script src="/static/tools/handlers/read-handler.js"></script>
<script src="/static/tools/handlers/edit-handlers.js"></script>
<script src="/static/tools/handlers/write-handler.js"></script>
<script src="/static/tools/handlers/todo-handler.js"></script>
<script src="/static/tools/handlers/search-handlers.js"></script>
<script src="/static/tools/handlers/web-handlers.js"></script>
<script src="/static/tools/handlers/bash-handlers.js"></script>
<script src="/static/tools/handlers/misc-handlers.js"></script>

<!-- Main application (depends on all above) -->
<script src="/static/app.js"></script>
```

## Existing Tool Handlers Reference

### File Operations
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `ReadToolHandler` | [read-handler.js](./static/tools/handlers/read-handler.js) | `Read` | File preview (first 20 lines), line count display, scrollable content |
| `EditToolHandler` | [edit-handlers.js](./static/tools/handlers/edit-handlers.js) | `Edit` | Diff view with +/- line highlighting, "Replace All" badge |
| `MultiEditToolHandler` | [edit-handlers.js](./static/tools/handlers/edit-handlers.js) | `MultiEdit` | Multiple diffs with numbered sections, edit count badge |
| `WriteToolHandler` | [write-handler.js](./static/tools/handlers/write-handler.js) | `Write` | Content preview (first 20 lines), line count, green theme |

### Task & Project Management
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `TodoWriteToolHandler` | [todo-handler.js](./static/tools/handlers/todo-handler.js) | `TodoWrite` | Checklist with ☐/◐/☑ indicators, status badges (completed/in-progress/pending) |
| `TaskToolHandler` | [misc-handlers.js](./static/tools/handlers/misc-handlers.js) | `Task` | Agent task display, delegation icon |
| `ExitPlanModeToolHandler` | [misc-handlers.js](./static/tools/handlers/misc-handlers.js) | `ExitPlanMode` | Plan summary display, mode transition indicator |

### Search & Discovery
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `GrepToolHandler` | [search-handlers.js](./static/tools/handlers/search-handlers.js) | `Grep` | Pattern display, match count, result preview (10 lines) |
| `GlobToolHandler` | [search-handlers.js](./static/tools/handlers/search-handlers.js) | `Glob` | File pattern display, match count, file list preview (10 files) |

### Shell Operations
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `BashToolHandler` | [bash-handlers.js](./static/tools/handlers/bash-handlers.js) | `Bash` | Command display with icon, output preview (20 lines), exit code indicator |
| `BashOutputToolHandler` | [bash-handlers.js](./static/tools/handlers/bash-handlers.js) | `BashOutput` | Shell ID display, output streaming preview |
| `KillShellToolHandler` | [bash-handlers.js](./static/tools/handlers/bash-handlers.js) | `KillShell` | Shell termination indicator, process ID |

### Web Operations
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `WebFetchToolHandler` | [web-handlers.js](./static/tools/handlers/web-handlers.js) | `WebFetch` | URL display with icon, prompt display, content preview (20 lines) |
| `WebSearchToolHandler` | [web-handlers.js](./static/tools/handlers/web-handlers.js) | `WebSearch` | Search query display, domain filters, result count |

### Fallback
| Handler | File | Tool Names | Key Features |
|---------|------|------------|--------------|
| `DefaultToolHandler` | [base-handler.js](./static/tools/handlers/base-handler.js) | *(any unregistered tool)* | Generic JSON parameter display, plain text result |

## How to Add a New Tool Handler

### Method 1: Create Standalone Handler File (Recommended for New Tools)

#### Step 1: Create new file in `static/tools/handlers/`
```bash
# Example: static/tools/handlers/my-tool-handler.js
```

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

#### Step 2: Add script tag to `index.html`
Add before `<script src="/static/app.js"></script>`:
```html
<script src="/static/tools/handlers/my-tool-handler.js"></script>
```

#### Step 3: Register handler in `app.js`
In `ClaudeWebUI.initializeToolHandlers()` method (around line 550):
```javascript
initializeToolHandlers() {
    // ... existing handlers ...
    this.toolHandlerRegistry.registerHandler('MyTool', new MyToolHandler());

    // For pattern matching (e.g., all MCP tools)
    this.toolHandlerRegistry.registerPatternHandler('mcp__*', new MyToolHandler());
}
```

#### Step 4: Add CSS styling in `styles.css`

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

### Method 2: Add to Existing Handler File (For Related Tools)
If your tool is similar to existing tools, add to the appropriate file:
- File operations → [edit-handlers.js](./static/tools/handlers/edit-handlers.js) or [write-handler.js](./static/tools/handlers/write-handler.js)
- Search tools → [search-handlers.js](./static/tools/handlers/search-handlers.js)
- Shell tools → [bash-handlers.js](./static/tools/handlers/bash-handlers.js)
- Web tools → [web-handlers.js](./static/tools/handlers/web-handlers.js)

Then register in `app.js` as shown above.

## Debugging Tool Handlers

### Common Issues

**Problem**: Handler not being used (falls back to DefaultToolHandler)
→ **Solution**: Check handler is registered in `app.js` `initializeToolHandlers()` with exact tool name (case-sensitive!)

**Problem**: "Handler class not defined" error in browser console
→ **Solution**: Verify script tag is in `index.html` and loads BEFORE `app.js`

**Problem**: Tool card not rendering correctly
→ **Solution**: Check browser console for JavaScript errors, ensure `renderParameters()` and `renderResult()` return valid HTML strings

**Problem**: Collapsed summary not updating
→ **Solution**: Return string from `getCollapsedSummary()`, not `null` (null uses default)

**Problem**: CSS styles not applying
→ **Solution**: Check CSS selectors match rendered HTML class names, verify styles are in `styles.css` or `custom.css`

### Debug Workflow
1. Open browser DevTools (F12) → Console tab
2. Check for JavaScript errors when tool card renders
3. Use `Logger.debug('TOOL_HANDLER', 'message', data)` for logging
4. Inspect HTML elements to verify class names match CSS
5. Test with simple tool first (e.g., Read) before complex tools

### Testing New Handlers
```javascript
// In browser console:
const handler = new MyToolHandler();
const mockToolCall = {
    id: 'test_123',
    name: 'MyTool',
    input: { my_param: 'test value' },
    status: 'completed',
    result: { content: 'test result' }
};
console.log(handler.renderParameters(mockToolCall, (s) => s));
console.log(handler.renderResult(mockToolCall, (s) => s));
```

## Handler Data Flow

### 1. Tool Use Message Arrives
```
SDK message (AssistantMessage with ToolUseBlock)
    ↓
ToolCallManager.handleToolUse() extracts tool data
    ↓
Creates toolCall object: { id, name, input, status: 'pending', ... }
    ↓
Stores in ToolCallManager._toolCalls map
    ↓
Triggers UI update
```

### 2. Tool Card Rendering
```
ClaudeWebUI.renderToolUseMessage() called
    ↓
Looks up handler: toolHandlerRegistry.getHandler(toolName)
    ↓
Calls handler.renderParameters(toolCall, escapeHtml)
    ↓
Returns HTML string
    ↓
Inserts into tool-use-content div
```

### 3. Tool Result Arrives
```
SDK message (UserMessage with ToolResultBlock)
    ↓
ToolCallManager.handleToolResult() updates toolCall
    ↓
Updates: toolCall.result = { content, error }
         toolCall.status = 'completed' or 'error'
    ↓
Triggers UI update
    ↓
Calls handler.renderResult(toolCall, escapeHtml)
    ↓
Updates tool-result-content div
```

### 4. Card Collapse/Expand
```
User clicks tool card header
    ↓
Toggles 'collapsed' class on tool-use-item
    ↓
If collapsed: shows getCollapsedSummary() text
    ↓
If expanded: shows full renderParameters() + renderResult()
```

## Performance Considerations

### Keep Rendering Fast
- **Limit previews**: Don't render entire files (use first N lines)
- **Avoid heavy computation**: No syntax highlighting on 10,000-line files
- **Use CSS for effects**: Prefer CSS transitions over JS animations
- **Debounce updates**: If tool streams data, throttle re-renders

### Memory Management
- **Don't store large data**: Keep only references to data, not copies
- **Clean up on unmount**: No global state that persists after tool card removed
- **Use WeakMap for metadata**: Garbage collection friendly

## CSS Class Reference

### Tool Card Structure
```html
<div class="tool-use-item" data-tool-id="tool_123" data-tool-name="Read">
  <div class="tool-use-header">
    <span class="tool-use-icon">📄</span>
    <span class="tool-use-name">Read</span>
    <span class="tool-use-status-badge">completed</span>
  </div>
  <div class="tool-use-content">
    <!-- renderParameters() output here -->
  </div>
  <div class="tool-result-content">
    <!-- renderResult() output here -->
  </div>
</div>
```

### Common CSS Classes
| Class | Purpose | Defined In |
|-------|---------|------------|
| `.tool-use-item` | Tool card container | styles.css |
| `.tool-use-item.collapsed` | Collapsed state | styles.css |
| `.tool-use-header` | Card header (icon + name + status) | styles.css |
| `.tool-use-content` | Parameters section | styles.css |
| `.tool-result-content` | Results section | styles.css |
| `.tool-parameters` | Generic parameter display | styles.css |
| `.tool-result-success` | Success result styling | styles.css |
| `.tool-result-error` | Error result styling | styles.css |
| `.diff-view` | Diff container (Edit/MultiEdit) | styles.css |
| `.diff-line` | Single line in diff | styles.css |
| `.diff-line-removed` | Removed line (red) | styles.css |
| `.diff-line-added` | Added line (green) | styles.css |
| `.todo-list` | Todo checklist container | styles.css |
| `.todo-item` | Single todo item | styles.css |
| `.todo-item-pending` | Pending task styling | styles.css |
| `.todo-item-in-progress` | In-progress task styling | styles.css |
| `.todo-item-completed` | Completed task styling | styles.css |

## Advanced Patterns

### Pattern Handlers (for Tool Families)
```javascript
// Register handler for all tools matching pattern
this.toolHandlerRegistry.registerPatternHandler('mcp__*', new McpToolHandler());

// In ToolHandlerRegistry.getHandler():
// 1. Check exact match first
// 2. Fall back to pattern match
// 3. Fall back to DefaultToolHandler
```

### Shared Rendering Logic
```javascript
// Reusable helper function
function renderFilePath(filePath) {
    const fileName = filePath.split('/').pop();
    return `<strong>File:</strong> <code>${escapeHtml(filePath)}</code>`;
}

// Use in multiple handlers
class ReadToolHandler {
    renderParameters(toolCall, escapeHtml) {
        return `<div>${renderFilePath(toolCall.input.file_path)}</div>`;
    }
}
```

### Dynamic Status Indicators
```javascript
getCollapsedSummary(toolCall) {
    const icons = {
        'pending': '⏳',
        'permission_required': '🔒',
        'executing': '⚙️',
        'completed': '✅',
        'error': '💥'
    };
    const icon = icons[toolCall.status] || '🔧';
    return `${icon} ${toolCall.name} - ${this.getStatusText(toolCall)}`;
}
```

## Future Enhancements

Ideas for additional handlers:
- **Syntax Highlighting** - Use Prism.js or Highlight.js for code display
- **Image Preview** - Show thumbnails for image file operations
- **JSON Viewer** - Collapsible JSON tree for structured data
- **Table Rendering** - Tabular display for CSV/structured data
- **MCP Generic Handler** - Detect and display MCP server metadata