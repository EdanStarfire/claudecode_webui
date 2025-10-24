# Tool Handler System - Vue 3 Developer Guide

## Quick Navigation
- [Overview](#overview) - System architecture and purpose
- [Getting Started](#getting-started) - Create your first handler in 5 minutes
- [Handler API](#handler-api-reference) - Component props, methods, and lifecycle
- [Existing Handlers](#existing-tool-handlers-reference) - Complete catalog with examples
- [Best Practices](#best-practices) - Coding standards and patterns
- [Advanced Topics](#advanced-topics) - Dynamic handlers, shared logic, performance
- [Debugging](#debugging-tool-handlers) - Common issues and solutions

## Overview

The Tool Handler System provides customizable Vue 3 components for displaying Claude Agent SDK tool calls. Each tool (Read, Edit, Write, Bash, etc.) can have a dedicated component that controls:

- **Parameter Display** - How tool inputs are shown before execution
- **Result Display** - How tool outputs are rendered after execution
- **Status Indication** - Visual feedback for tool lifecycle states
- **Error Handling** - Custom error message formatting

**Architecture**: Component-based with dynamic registration in `MessageItem.vue`

```
MessageItem.vue (Router Component)
    ↓
Checks tool name → looks up handler component
    ↓
<component :is="handlerComponent" :tool-call="toolCall" />
    ↓
ReadToolHandler.vue / EditToolHandler.vue / etc.
```

## File Locations

### Core System Files
```
frontend/src/
├── components/
│   └── messages/
│       ├── MessageItem.vue          # Router: selects handler based on tool name
│       ├── ToolCallCard.vue         # Container: lifecycle, expand/collapse, status
│       └── tools/                   # Tool handler components
│           ├── BaseToolHandler.vue           # Fallback for unknown tools
│           ├── ReadToolHandler.vue           # File reading
│           ├── EditToolHandler.vue           # File editing (diff view)
│           ├── WriteToolHandler.vue          # File creation
│           ├── BashToolHandler.vue           # Shell commands
│           ├── SearchToolHandler.vue         # Grep/Glob results
│           ├── WebToolHandler.vue            # WebFetch/WebSearch
│           ├── TodoToolHandler.vue           # Task checklists
│           ├── TaskToolHandler.vue           # Agent task delegation
│           ├── NotebookEditToolHandler.vue   # Jupyter notebooks
│           ├── ExitPlanModeToolHandler.vue   # Plan mode transitions
│           ├── ShellToolHandler.vue          # Generic shell operations
│           └── CommandToolHandler.vue        # Generic command display
└── stores/
    └── message.js                   # Tool call state management (Pinia)
```

### Styling
```
frontend/src/assets/
└── styles.css                       # Tool handler styles, diff views, status indicators
```

## Getting Started

### Create Your First Tool Handler (5-Minute Quickstart)

**Step 1: Create component file** (`frontend/src/components/messages/tools/MyToolHandler.vue`)

```vue
<template>
  <div class="tool-handler-mytool">
    <!-- Parameters Section -->
    <div class="tool-parameters">
      <div class="param-row">
        <strong>My Parameter:</strong>
        <span>{{ toolCall.input.my_parameter }}</span>
      </div>
    </div>

    <!-- Result Section (only if result exists) -->
    <div v-if="toolCall.result" class="tool-result" :class="resultClass">
      <strong>Result:</strong>
      <pre>{{ toolCall.result.content || toolCall.result.message }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const resultClass = computed(() => {
  return toolCall.result?.error ? 'tool-result-error' : 'tool-result-success'
})
</script>

<style scoped>
.tool-handler-mytool {
  padding: 0.75rem;
}

.tool-parameters {
  background: #f0f8ff;
  border: 1px solid #d0e8ff;
  border-radius: 6px;
  padding: 1rem;
  margin-bottom: 0.75rem;
}

.param-row {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.param-row:last-child {
  margin-bottom: 0;
}

.tool-result {
  border-radius: 6px;
  padding: 1rem;
  margin-top: 0.75rem;
}

.tool-result-success {
  background: #f0fff0;
  border: 1px solid #d0ffd0;
}

.tool-result-error {
  background: #fff0f0;
  border: 1px solid #ffd0d0;
}

.tool-result pre {
  margin: 0.5rem 0 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Courier New', monospace;
  font-size: 0.9rem;
}
</style>
```

**Step 2: Register in `MessageItem.vue`**

Open `frontend/src/components/messages/MessageItem.vue` and add your handler to the component import map:

```vue
<script setup>
// ... existing imports ...
import MyToolHandler from './tools/MyToolHandler.vue'

// Add to the toolHandlerMap
const toolHandlerMap = {
  // ... existing handlers ...
  'MyTool': MyToolHandler,
  'MyOtherTool': MyToolHandler, // Can reuse for similar tools
}
</script>
```

**Step 3: Test your handler**

1. Start dev server: `npm run dev`
2. Trigger your tool in a Claude session
3. Observe rendering in browser
4. Adjust styling/layout as needed

**That's it!** Your handler is now live with hot module replacement.

## Handler API Reference

### Component Props

Every tool handler component receives:

```typescript
props: {
  toolCall: {
    type: Object,
    required: true,
    // Structure:
    {
      id: string,                    // Unique tool use ID (e.g., "tool_use_123")
      name: string,                  // Tool name (e.g., "Read", "Edit")
      input: Object,                 // Tool parameters (varies by tool)
      status: string,                // Lifecycle state (see below)
      result: Object | null,         // Tool result (null until completed)
      permissionRequestId: string,   // Permission request ID (if applicable)
      permissionDecision: string,    // User decision: "allow" | "deny" (if applicable)
      explanation: string,           // Assistant's explanation for tool use
      isOrphaned: boolean,           // True if tool cancelled due to session event
    }
  }
}
```

### Tool Call Status Values

```typescript
type Status =
  | 'pending'              // Tool use message received, waiting to execute
  | 'permission_required'  // Awaiting user approval
  | 'executing'            // Currently running
  | 'completed'            // Finished successfully
  | 'error'                // Failed with error
```

### Tool Result Structure

```typescript
interface ToolResult {
  content?: string         // Main result content
  message?: string         // Alternative to content
  error?: boolean          // True if tool failed
  metadata?: Object        // Additional result data (varies by tool)
}
```

### Common Input Patterns

Different tools have different input structures:

```typescript
// File operations
{ file_path: string, offset?: number, limit?: number }

// Edit operations
{ file_path: string, old_string: string, new_string: string, replace_all?: boolean }

// Multi-edit
{ file_path: string, edits: Array<{ old_string, new_string }> }

// Search operations
{ pattern: string, path?: string, glob?: string, output_mode?: string }

// Bash commands
{ command: string, timeout?: number, run_in_background?: boolean }

// Web operations
{ url: string, prompt?: string, query?: string }
```

### Computed Properties Pattern

Use Vue computed properties for derived state:

```vue
<script setup>
import { computed } from 'vue'

const props = defineProps({ toolCall: Object })

// Compute result class based on error state
const resultClass = computed(() => {
  if (!props.toolCall.result) return ''
  return props.toolCall.result.error ? 'tool-result-error' : 'tool-result-success'
})

// Extract filename from path
const fileName = computed(() => {
  const path = props.toolCall.input.file_path || ''
  return path.split('/').pop() || path.split('\\').pop() || path
})

// Count lines in content
const lineCount = computed(() => {
  const content = props.toolCall.result?.content || ''
  return content.split('\n').length
})
</script>
```

### Conditional Rendering

Always check for data existence before rendering:

```vue
<template>
  <div class="tool-handler">
    <!-- Parameters always shown -->
    <div class="tool-parameters">
      <!-- Use optional chaining and fallbacks -->
      <span>{{ toolCall.input?.file_path || 'Unknown file' }}</span>
    </div>

    <!-- Result only if exists -->
    <div v-if="toolCall.result" class="tool-result">
      <!-- Error state -->
      <div v-if="toolCall.result.error" class="error-message">
        {{ toolCall.result.message || 'Unknown error' }}
      </div>

      <!-- Success state -->
      <div v-else class="success-message">
        {{ toolCall.result.content }}
      </div>
    </div>

    <!-- Orphaned state -->
    <div v-if="toolCall.isOrphaned" class="orphaned-banner">
      ⚠️ Tool execution cancelled due to session event
    </div>
  </div>
</template>
```

## Existing Tool Handlers Reference

### File Operations

#### ReadToolHandler.vue
**Tool Name**: `Read`

**Features**:
- File path display with 📄 icon
- Line range indication (offset/limit)
- Content preview (first 20 lines) with line numbers
- Scrollable container with "..." indicator if truncated
- Line count display in result

**Example Input**:
```javascript
{ file_path: "/path/to/file.txt", offset: 0, limit: 50 }
```

**Styling**: Blue theme, syntax-highlighted monospace font

---

#### EditToolHandler.vue
**Tool Name**: `Edit`

**Features**:
- File path with ✏️ icon
- "Replace All" badge if applicable
- Diff view with line-by-line changes:
  - Red background + `-` for removed lines
  - Green background + `+` for added lines
  - Gray background + ` ` for context lines
- Success/error result display

**Example Input**:
```javascript
{
  file_path: "/path/to/file.txt",
  old_string: "const foo = 'bar'",
  new_string: "const foo = 'baz'",
  replace_all: false
}
```

**Styling**: Diff colors (red/green), monospace font

---

#### WriteToolHandler.vue
**Tool Name**: `Write`

**Features**:
- File path with 📝 icon
- "Writing new file" label
- Content preview (first 20 lines)
- Line count header
- Scrollable monospace container
- Green theme for new file creation

**Example Input**:
```javascript
{ file_path: "/path/to/new-file.txt", content: "file contents..." }
```

**Styling**: Green theme, success-oriented

---

### Search Operations

#### SearchToolHandler.vue
**Tool Names**: `Grep`, `Glob`

**Features**:
- Search pattern display with 🔍 icon
- Match count badge
- File/path filters display
- Result preview (first 10 lines/files)
- Scrollable result container
- "..." indicator if truncated

**Example Input (Grep)**:
```javascript
{ pattern: "function\\s+\\w+", path: "/src", glob: "*.js", output_mode: "content" }
```

**Example Input (Glob)**:
```javascript
{ pattern: "**/*.vue", path: "/frontend/src" }
```

**Styling**: Purple theme, code formatting for results

---

### Shell Operations

#### BashToolHandler.vue
**Tool Names**: `Bash`, `BashOutput`, `KillShell`

**Features**:
- Command display with 💻 icon
- Timeout and background execution indicators
- Output preview (first 20 lines) with STDOUT/STDERR separation
- Exit code display (success: green, error: red)
- Scrollable output with monospace font

**Example Input**:
```javascript
{ command: "npm run build", timeout: 60000, run_in_background: false }
```

**Styling**: Dark terminal theme, monospace font

---

### Web Operations

#### WebToolHandler.vue
**Tool Names**: `WebFetch`, `WebSearch`

**Features**:
- URL/query display with 🌐 icon
- Search query and domain filter badges
- Prompt display (for WebFetch)
- Content preview (first 20 lines)
- Result count badge
- Scrollable result container

**Example Input (WebFetch)**:
```javascript
{ url: "https://example.com", prompt: "Extract pricing information" }
```

**Example Input (WebSearch)**:
```javascript
{ query: "Vue 3 composition API", allowed_domains: ["vuejs.org"] }
```

**Styling**: Blue web theme

---

### Task Management

#### TodoToolHandler.vue
**Tool Name**: `TodoWrite`

**Features**:
- Clipboard icon 📋 with "Task List" header
- Summary badges (completed/in-progress/pending counts)
- Checklist view with status indicators:
  - ☐ Empty checkbox for `pending` (gray)
  - ◐ Half-filled for `in_progress` (orange border, bold)
  - ☑ Checked for `completed` (green border, strikethrough)
- Hover effects on todo items
- Orange/amber theme

**Example Input**:
```javascript
{
  todos: [
    { content: "Write tests", status: "completed", activeForm: "Writing tests" },
    { content: "Deploy to prod", status: "in_progress", activeForm: "Deploying to prod" },
    { content: "Update docs", status: "pending", activeForm: "Updating docs" }
  ]
}
```

**Styling**: Amber theme, interactive checkboxes

---

#### TaskToolHandler.vue
**Tool Name**: `Task`

**Features**:
- Delegation icon 🤖
- Agent task description display
- Subagent type badge
- Prompt display with formatting

**Styling**: Agent-themed styling

---

#### ExitPlanModeToolHandler.vue
**Tool Name**: `ExitPlanMode`

**Features**:
- Mode transition indicator
- Plan summary display
- Automatic permission mode reset notification

**Styling**: Planning mode theme

---

### Jupyter Notebooks

#### NotebookEditToolHandler.vue
**Tool Name**: `NotebookEdit`

**Features**:
- Notebook path with 📓 icon
- Cell number display
- Cell type badge (code/markdown)
- Edit mode indicator (replace/insert/delete)
- Source code diff view (for replace mode)
- New source preview

**Example Input**:
```javascript
{
  notebook_path: "/path/to/notebook.ipynb",
  cell_id: "abc123",
  cell_type: "code",
  edit_mode: "replace",
  new_source: "print('hello world')"
}
```

**Styling**: Jupyter orange theme

---

### Fallback Handler

#### BaseToolHandler.vue
**Tool Names**: *(any unregistered tool)*

**Features**:
- Generic parameter display (JSON formatted)
- Plain text result display
- Handles unknown tools gracefully
- Supports all standard statuses

**Styling**: Neutral gray theme

---

## Best Practices

### 1. Always Use Scoped Styles

```vue
<style scoped>
/* Scoped styles prevent global namespace pollution */
.tool-parameters {
  /* Styles only apply to this component */
}
</style>
```

### 2. Handle Missing Data Gracefully

```vue
<template>
  <!-- Use optional chaining -->
  <div>{{ toolCall.input?.file_path || 'Unknown' }}</div>

  <!-- Use v-if for conditional sections -->
  <div v-if="toolCall.result">
    {{ toolCall.result.content }}
  </div>
</template>
```

### 3. Use Computed Properties for Derived State

```vue
<script setup>
import { computed } from 'vue'

const props = defineProps({ toolCall: Object })

// Good: Computed property (reactive, cached)
const lineCount = computed(() => {
  return (props.toolCall.result?.content || '').split('\n').length
})

// Bad: Direct computation in template (not cached)
// {{ toolCall.result?.content.split('\n').length }}
</script>
```

### 4. Limit Preview Sizes

```vue
<script setup>
const MAX_PREVIEW_LINES = 20

const previewContent = computed(() => {
  const content = props.toolCall.result?.content || ''
  const lines = content.split('\n')

  if (lines.length > MAX_PREVIEW_LINES) {
    return lines.slice(0, MAX_PREVIEW_LINES).join('\n') + '\n...'
  }

  return content
})

const hasMoreContent = computed(() => {
  const lines = (props.toolCall.result?.content || '').split('\n')
  return lines.length > MAX_PREVIEW_LINES
})
</script>

<template>
  <pre>{{ previewContent }}</pre>
  <div v-if="hasMoreContent" class="truncation-indicator">
    ... ({{ lineCount - MAX_PREVIEW_LINES }} more lines)
  </div>
</template>
```

### 5. Use Semantic HTML

```vue
<template>
  <!-- Good: Semantic structure -->
  <article class="tool-handler">
    <header class="tool-parameters">
      <h3>Parameters</h3>
      <dl>
        <dt>File Path:</dt>
        <dd>{{ toolCall.input.file_path }}</dd>
      </dl>
    </header>

    <section v-if="toolCall.result" class="tool-result">
      <h3>Result</h3>
      <pre><code>{{ toolCall.result.content }}</code></pre>
    </section>
  </article>
</template>
```

### 6. Consistent Status Indication

```vue
<script setup>
const statusConfig = {
  pending: { icon: '⏳', label: 'Pending', class: 'status-pending' },
  permission_required: { icon: '🔒', label: 'Awaiting Permission', class: 'status-permission' },
  executing: { icon: '⚙️', label: 'Executing', class: 'status-executing' },
  completed: { icon: '✅', label: 'Completed', class: 'status-completed' },
  error: { icon: '💥', label: 'Error', class: 'status-error' }
}

const currentStatus = computed(() => {
  return statusConfig[props.toolCall.status] || statusConfig.pending
})
</script>

<template>
  <div :class="['status-indicator', currentStatus.class]">
    <span class="status-icon">{{ currentStatus.icon }}</span>
    <span class="status-label">{{ currentStatus.label }}</span>
  </div>
</template>
```

### 7. Escape HTML When Rendering User Content

Vue automatically escapes text content in `{{ }}`, but be careful with `v-html`:

```vue
<template>
  <!-- Safe: Automatic escaping -->
  <div>{{ toolCall.result.content }}</div>

  <!-- UNSAFE: Don't use v-html with untrusted content -->
  <!-- <div v-html="toolCall.result.content"></div> -->

  <!-- If you must render HTML, sanitize first -->
  <div v-html="sanitizedContent"></div>
</template>

<script setup>
import DOMPurify from 'dompurify'

const sanitizedContent = computed(() => {
  return DOMPurify.sanitize(props.toolCall.result?.content || '')
})
</script>
```

### 8. Performance: Avoid Heavy Computations

```vue
<script setup>
// Bad: Heavy computation on every render
const processedContent = computed(() => {
  // Avoid syntax highlighting 10,000 lines
  const lines = props.toolCall.result?.content.split('\n') || []
  return lines.map(line => highlightSyntax(line)) // Too slow!
})

// Good: Limit processing scope
const processedContent = computed(() => {
  const content = props.toolCall.result?.content || ''
  const lines = content.split('\n')

  // Only process first N lines
  const previewLines = lines.slice(0, 20)
  return previewLines.map(line => highlightSyntax(line))
})
</script>
```

## Advanced Topics

### Pattern-Based Handler Registration

Register one handler for multiple similar tools:

```vue
<!-- frontend/src/components/messages/MessageItem.vue -->
<script setup>
import McpToolHandler from './tools/McpToolHandler.vue'

const toolHandlerMap = {
  // Exact matches
  'Read': ReadToolHandler,
  'Edit': EditToolHandler,

  // Multiple tools → same handler
  'Grep': SearchToolHandler,
  'Glob': SearchToolHandler,

  // MCP tools (all start with mcp__)
  'mcp__legion__send_comm': McpToolHandler,
  'mcp__legion__spawn_minion': McpToolHandler,
  'mcp__custom__my_tool': McpToolHandler,
}

// Dynamic lookup with fallback
const getHandler = (toolName) => {
  // 1. Exact match
  if (toolHandlerMap[toolName]) {
    return toolHandlerMap[toolName]
  }

  // 2. Pattern match (e.g., all mcp__ tools)
  if (toolName.startsWith('mcp__')) {
    return McpToolHandler
  }

  // 3. Fallback
  return BaseToolHandler
}
</script>
```

### Shared Utility Functions

Create composables for reusable logic:

```javascript
// frontend/src/composables/useToolFormatting.js
import { computed } from 'vue'

export function useToolFormatting(toolCall) {
  const fileName = computed(() => {
    const path = toolCall.value.input?.file_path || ''
    return path.split('/').pop() || path.split('\\').pop() || path
  })

  const lineCount = computed(() => {
    const content = toolCall.value.result?.content || ''
    return content.split('\n').length
  })

  const previewContent = computed(() => {
    const content = toolCall.value.result?.content || ''
    const lines = content.split('\n')

    if (lines.length > 20) {
      return lines.slice(0, 20).join('\n') + '\n...'
    }

    return content
  })

  return {
    fileName,
    lineCount,
    previewContent
  }
}
```

Use in components:

```vue
<script setup>
import { useToolFormatting } from '@/composables/useToolFormatting'

const props = defineProps({ toolCall: Object })

const { fileName, lineCount, previewContent } = useToolFormatting(toRef(props, 'toolCall'))
</script>

<template>
  <div>
    <strong>File:</strong> {{ fileName }}
    <pre>{{ previewContent }}</pre>
    <div>{{ lineCount }} lines</div>
  </div>
</template>
```

### Dynamic Status Indicators with Transitions

```vue
<template>
  <transition name="fade">
    <div :key="toolCall.status" class="status-badge" :class="statusClass">
      {{ statusText }}
    </div>
  </transition>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ toolCall: Object })

const statusClass = computed(() => {
  return `status-${props.toolCall.status}`
})

const statusText = computed(() => {
  const labels = {
    pending: 'Pending',
    permission_required: 'Awaiting Permission',
    executing: 'Running...',
    completed: 'Done',
    error: 'Failed'
  }
  return labels[props.toolCall.status] || 'Unknown'
})
</script>

<style scoped>
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

.status-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.85rem;
  font-weight: 600;
}

.status-pending { background: #ffc107; color: #000; }
.status-executing { background: #17a2b8; color: #fff; }
.status-completed { background: #28a745; color: #fff; }
.status-error { background: #dc3545; color: #fff; }
.status-permission_required { background: #6c757d; color: #fff; }
</style>
```

### Diff View Component (Reusable)

Create a reusable diff component:

```vue
<!-- frontend/src/components/common/DiffView.vue -->
<template>
  <div class="diff-view">
    <div
      v-for="(line, index) in diffLines"
      :key="index"
      class="diff-line"
      :class="`diff-line-${line.type}`"
    >
      <span class="diff-marker">{{ line.marker }}</span>
      <span class="diff-content">{{ line.content }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  oldContent: String,
  newContent: String
})

const diffLines = computed(() => {
  const oldLines = props.oldContent.split('\n')
  const newLines = props.newContent.split('\n')

  const lines = []

  // Simple diff (for complex diff, use a library like 'diff')
  oldLines.forEach(line => {
    lines.push({ type: 'removed', marker: '-', content: line })
  })

  newLines.forEach(line => {
    lines.push({ type: 'added', marker: '+', content: line })
  })

  return lines
})
</script>

<style scoped>
.diff-view {
  font-family: 'Courier New', monospace;
  font-size: 0.9rem;
  border: 1px solid #ddd;
  border-radius: 6px;
  overflow: auto;
  max-height: 400px;
}

.diff-line {
  display: flex;
  padding: 0.25rem 0.5rem;
}

.diff-line-removed {
  background: #ffecec;
  color: #d73a49;
}

.diff-line-added {
  background: #e6ffec;
  color: #22863a;
}

.diff-line-context {
  background: #f6f8fa;
  color: #586069;
}

.diff-marker {
  width: 1.5rem;
  flex-shrink: 0;
  font-weight: bold;
}

.diff-content {
  flex: 1;
  white-space: pre;
}
</style>
```

Use in EditToolHandler:

```vue
<template>
  <div class="edit-tool-handler">
    <div class="tool-parameters">
      <strong>📝 Editing:</strong> {{ fileName }}
      <DiffView
        :old-content="toolCall.input.old_string"
        :new-content="toolCall.input.new_string"
      />
    </div>
  </div>
</template>

<script setup>
import DiffView from '@/components/common/DiffView.vue'
// ... rest of component
</script>
```

## Performance Optimization

### 1. Virtualized Rendering for Large Results

For extremely large tool results (e.g., 10,000 lines), use virtual scrolling:

```vue
<template>
  <virtual-scroller
    :items="resultLines"
    :item-height="20"
    :height="400"
  >
    <template #default="{ item }">
      <div class="result-line">{{ item }}</div>
    </template>
  </virtual-scroller>
</template>

<script setup>
import { computed } from 'vue'

const resultLines = computed(() => {
  const content = props.toolCall.result?.content || ''
  return content.split('\n')
})
</script>
```

### 2. Lazy Loading Heavy Components

```vue
<script setup>
import { defineAsyncComponent } from 'vue'

// Lazy load syntax highlighter only when needed
const SyntaxHighlighter = defineAsyncComponent(() =>
  import('./SyntaxHighlighter.vue')
)
</script>

<template>
  <Suspense>
    <template #default>
      <SyntaxHighlighter :code="toolCall.result.content" />
    </template>
    <template #fallback>
      <div>Loading syntax highlighter...</div>
    </template>
  </Suspense>
</template>
```

### 3. Debounce Expensive Operations

```vue
<script setup>
import { ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'

const processedContent = ref('')

const processContent = useDebounceFn((content) => {
  // Expensive operation (syntax highlighting, etc.)
  processedContent.value = expensiveProcessing(content)
}, 300)

watch(() => props.toolCall.result?.content, (newContent) => {
  if (newContent) {
    processContent(newContent)
  }
})
</script>
```

## Future Enhancements

Ideas for additional handlers and features:

- **Syntax highlighting** - Use `highlight.js` or `Prism` for code display
- **Image previews** - Show thumbnails for image file operations
- **JSON viewer** - Collapsible tree for structured data (use `vue-json-viewer`)
- **Table rendering** - Display CSV/structured data in tables
- **Code execution visualization** - Step-through debugger view
- **Performance metrics** - Show execution time, memory usage for tools
- **Collaborative editing** - Real-time diff view for multi-user scenarios
- **Export functionality** - Download tool results as files
- **Search within results** - Filter/search large tool outputs
- **Comparison view** - Side-by-side comparison for multiple tool executions

## Reference Links

- **Vue 3 Documentation**: https://vuejs.org/guide/introduction.html
- **Composition API**: https://vuejs.org/api/composition-api-setup.html
- **Pinia Store**: https://pinia.vuejs.org/
- **Vue DevTools**: https://devtools.vuejs.org/
- **VueUse Utilities**: https://vueuse.org/

---
