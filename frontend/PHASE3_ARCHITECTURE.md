# Phase 3 Architecture: Messages + Tool Handlers

## Overview

Phase 3 focuses on building a robust message and tool rendering system with:
1. **Single source of truth**: Backend handles ALL message sourcing (no frontend injection)
2. **Type safety**: Proper validation of backend message structures
3. **Component reusability**: Shared tool handler components
4. **Graceful degradation**: Handle unknown/malformed messages without breaking UI

## Backend Message Structure (Source of Truth)

### Message Flow
```
SDK → message_parser.py → prepare_for_websocket() → Frontend
SDK → message_parser.py → prepare_for_storage() → JSONL → Frontend (on reload)
```

### Standard Message Format
```typescript
interface Message {
  type: 'user' | 'assistant' | 'system' | 'result' | 'permission_request' | 'permission_response'
  content: string
  timestamp: number  // Unix timestamp or ISO string
  metadata: {
    // Common fields (all messages)
    session_id?: string

    // Tool-related fields
    has_tool_uses: boolean
    has_tool_results: boolean
    has_thinking: boolean
    tool_uses?: ToolUse[]
    tool_results?: ToolResult[]

    // Assistant-specific
    thinking_content?: string
    thinking_blocks?: ThinkingBlock[]
    model?: string
    role?: string

    // Result-specific
    subtype?: string  // 'completed', 'error', 'interrupted'
    is_error?: boolean
    error_type?: string
    duration_ms?: number
    usage?: object
  }
}

interface ToolUse {
  id: string
  name: string
  input: Record<string, any>
  timestamp: number
}

interface ToolResult {
  tool_use_id: string
  content: string | any  // Backend normalizes to string
  is_error: boolean
  timestamp: number
}
```

### Message Types Explained

1. **user**: User input or tool results sent to SDK
   - `metadata.tool_results[]`: Array of tool execution results
   - Backend broadcasts user messages via WebSocket (no frontend injection needed)

2. **assistant**: Claude's response
   - `metadata.tool_uses[]`: Tools Claude wants to execute
   - `metadata.thinking_blocks[]`: Claude's internal reasoning
   - `content`: Text response to user

3. **system**: System/SDK status messages
   - `metadata.subtype`: 'init', 'client_launched', etc.
   - Filtered from display (init messages hidden)

4. **result**: Conversation completion/error
   - `metadata.subtype`: 'completed', 'error', 'interrupted'
   - `metadata.is_error`: Whether this is an error result
   - Filtered from display (updates session state instead)

5. **permission_request**: Tool needs permission
   - Triggers permission modal (handled separately)

6. **permission_response**: User's permission decision
   - Filtered from display (part of tool lifecycle)

## Frontend Architecture

### Component Structure
```
components/messages/
├── MessageList.vue           # Main container (scroll, auto-scroll detection)
├── MessageItem.vue           # Individual message wrapper
├── UserMessage.vue           # User message display
├── AssistantMessage.vue      # Assistant message display
├── SystemMessage.vue         # System message display
├── ThinkingBlock.vue         # Thinking content display
├── ToolCallCard.vue          # Tool call container with lifecycle
└── tools/                    # Tool-specific renderers
    ├── BaseToolHandler.vue   # Fallback for unknown tools
    ├── ReadTool.vue          # Read file display
    ├── EditTool.vue          # Edit/MultiEdit with diff view
    ├── WriteTool.vue         # Write file display
    ├── BashTool.vue          # Bash command/output
    ├── TodoTool.vue          # TodoWrite checklist
    ├── SearchTool.vue        # Grep/Glob results
    ├── WebTool.vue           # WebFetch/WebSearch
    ├── TaskTool.vue          # Task agent
    └── McpTool.vue           # MCP tool calls
```

### Tool Call Lifecycle

Tool calls are managed separately from messages in the message store:

```javascript
// stores/message.js
const toolCalls = ref(new Map())  // Map<toolCallId, ToolCallState>

interface ToolCallState {
  id: string
  name: string
  status: 'pending' | 'permission_required' | 'executing' | 'completed' | 'error'
  input: Record<string, any>
  result?: any
  error?: string
  permissionDecision?: 'allow' | 'deny'
  permissionRequestId?: string
  timestamp: number
  resultTimestamp?: number
}
```

**Lifecycle Flow:**
1. **ToolUse appears** in assistant message → Create `pending` tool call
2. **Permission needed** → Update to `permission_required` status
3. **User responds** → Update with permission decision
4. **Execution starts** → Update to `executing` status
5. **ToolResult appears** in user message → Update to `completed` status + store result

**Benefits:**
- Tools update in real-time without re-rendering entire message history
- Tool state persists across navigation
- Easy to find and update specific tool calls by ID

### Message Rendering Strategy

```vue
<!-- MessageList.vue -->
<template>
  <div class="messages-area" ref="messagesArea">
    <MessageItem
      v-for="message in displayableMessages"
      :key="`msg-${message.timestamp}`"
      :message="message"
    />

    <!-- Tool calls rendered separately for real-time updates -->
    <ToolCallCard
      v-for="toolCall in currentToolCalls"
      :key="`tool-${toolCall.id}`"
      :toolCall="toolCall"
    />
  </div>
</template>
```

### Tool Handler Pattern

Each tool handler is a Vue component that receives:
- **Props**: `toolCall` (ToolCallState object)
- **Emits**: Events for user interactions (if needed)

```vue
<!-- tools/ReadTool.vue -->
<template>
  <div class="tool-read">
    <!-- Parameters Section -->
    <div class="tool-parameters">
      <span class="read-icon">📄</span>
      <strong>Reading:</strong>
      <code>{{ toolCall.input.file_path }}</code>
      <span v-if="hasRange">Lines {{ startLine }}-{{ endLine }}</span>
    </div>

    <!-- Result Section (when completed) -->
    <div v-if="toolCall.status === 'completed'" class="tool-result">
      <div v-if="toolCall.result?.error" class="error">
        {{ toolCall.result.content }}
      </div>
      <div v-else class="file-preview">
        <!-- Use diff view library or custom syntax highlighting -->
        <pre><code>{{ previewContent }}</code></pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

const hasRange = computed(() =>
  props.toolCall.input.offset !== undefined ||
  props.toolCall.input.limit !== undefined
)

// ... more computed properties
</script>
```

## Key Design Decisions

### 1. No Frontend Message Injection
**Problem**: Old code injected user messages in frontend, causing duplicates when backend also broadcast them.

**Solution**: Backend is sole source of messages. When user sends message:
```javascript
// ❌ OLD WAY
wsStore.sendMessage(content)
messageStore.addMessage({ type: 'user', content })  // Frontend injection

// ✅ NEW WAY
wsStore.sendMessage(content)
// Backend broadcasts user message via WebSocket - frontend receives and displays
```

### 2. Tool Call Separate from Messages
**Problem**: Tool calls need real-time updates as status changes, but messages are immutable.

**Solution**:
- Messages are append-only log
- Tool calls are mutable state tracked by ID
- Tool calls reference messages via tool_use_id

### 3. Graceful Degradation
**Problem**: Unknown message types or malformed data could crash UI.

**Solution**:
```javascript
// Message validation with defaults
function normalizeMessage(rawMessage) {
  return {
    type: rawMessage.type || 'unknown',
    content: rawMessage.content || '[No content]',
    timestamp: rawMessage.timestamp || Date.now(),
    metadata: {
      has_tool_uses: false,
      has_tool_results: false,
      has_thinking: false,
      ...rawMessage.metadata
    }
  }
}

// Tool handler fallback
<component
  :is="getToolComponent(toolCall.name)"
  :toolCall="toolCall"
  @fallback="() => showBaseHandler(toolCall)"
/>
```

### 4. Auto-Scroll with User Override
**Problem**: Auto-scroll when user has scrolled up is annoying.

**Solution**:
```javascript
// Detect user scroll vs programmatic scroll
const isNearBottom = computed(() => {
  if (!messagesArea.value) return true
  const { scrollTop, scrollHeight, clientHeight } = messagesArea.value
  return scrollHeight - scrollTop - clientHeight < 100  // 100px threshold
})

watch(() => messageStore.currentMessages.length, async () => {
  if (isNearBottom.value) {
    await nextTick()
    scrollToBottom()
  }
})
```

### 5. Markdown Rendering with Sanitization
**Libraries**:
- `marked`: Markdown parsing
- `DOMPurify`: XSS prevention
- `highlight.js`: Code syntax highlighting (optional)

```javascript
import marked from 'marked'
import DOMPurify from 'dompurify'

function renderMarkdown(text) {
  const rawHtml = marked.parse(text)
  return DOMPurify.sanitize(rawHtml)
}
```

## Data Validation Checklist

For each message type, validate:

### User Messages
- [ ] `content` is string or can be converted
- [ ] `metadata.tool_results` is array (default: [])
- [ ] Each tool result has: `tool_use_id`, `content`, `is_error`
- [ ] Tool result `content` is normalized to string (backend does this)

### Assistant Messages
- [ ] `content` is string or can be converted
- [ ] `metadata.tool_uses` is array (default: [])
- [ ] Each tool use has: `id`, `name`, `input`
- [ ] `metadata.thinking_blocks` is array (default: [])

### System Messages
- [ ] `metadata.subtype` exists for filtering
- [ ] Handle unknown subtypes gracefully

### Result Messages
- [ ] `metadata.subtype` exists ('completed', 'error', 'interrupted')
- [ ] `metadata.is_error` is boolean (default: false)
- [ ] Error messages have `content` with error text

## Implementation Phases

### Phase 3.1: Message Components (Days 11-12)
- [ ] Build MessageItem wrapper component
- [ ] Build UserMessage component (simple text + tool results)
- [ ] Build AssistantMessage component (text + tool uses + thinking)
- [ ] Build SystemMessage component (minimal display)
- [ ] Add markdown rendering with sanitization
- [ ] Add syntax highlighting for code blocks

### Phase 3.2: Tool Call System (Days 13-14)
- [ ] Build ToolCallCard component (lifecycle UI)
- [ ] Enhance message store with tool call tracking
- [ ] Implement tool call status updates via WebSocket
- [ ] Build BaseToolHandler (fallback component)

### Phase 3.3: Tool Handlers (Days 14-15)
- [ ] Port ReadTool.vue (file preview with diff)
- [ ] Port EditTool.vue (diff view with Diff2Html)
- [ ] Port WriteTool.vue (file creation preview)
- [ ] Port BashTool.vue (command + output)
- [ ] Port TodoTool.vue (checklist with progress)
- [ ] Port SearchTool.vue (Grep/Glob results)
- [ ] Port WebTool.vue (WebFetch/WebSearch)
- [ ] Port TaskTool.vue (agent output)
- [ ] Port McpTool.vue (MCP calls)

### Phase 3.4: Permission Modal (Day 15)
- [ ] Build PermissionModal component
- [ ] Handle permission suggestions from backend
- [ ] Wire up permission response to WebSocket
- [ ] Update tool call status after permission

### Phase 3.5: Polish (Day 15)
- [ ] Implement auto-scroll with user detection
- [ ] Add message timestamps
- [ ] Add thinking block expand/collapse
- [ ] Add tool call expand/collapse
- [ ] Test all message types
- [ ] Test all tool handlers
- [ ] Error boundary for malformed messages

## Testing Scenarios

### Message Display
- [ ] User message with plain text
- [ ] User message with tool results
- [ ] Assistant message with plain text
- [ ] Assistant message with tool uses
- [ ] Assistant message with thinking blocks
- [ ] Assistant message with mixed content
- [ ] System message (init - should be hidden)
- [ ] Result message (should be hidden)

### Tool Call Lifecycle
- [ ] Tool use appears → pending state
- [ ] Permission requested → permission_required state
- [ ] User allows → executing state
- [ ] Tool completes → completed state with result
- [ ] Tool errors → error state with message
- [ ] User denies → completed state with denial

### Edge Cases
- [ ] Message with missing fields → graceful defaults
- [ ] Unknown message type → shown with warning
- [ ] Unknown tool type → BaseToolHandler fallback
- [ ] Malformed tool result → error display
- [ ] Very long message content → scrollable
- [ ] Very large tool output → truncated preview
- [ ] Rapid message stream → no UI lag

## Dependencies to Install

```bash
cd frontend
npm install marked dompurify
npm install --save-dev @types/marked @types/dompurify
```

Optional for code highlighting:
```bash
npm install highlight.js
```

For diff views (EditTool, ReadTool):
```bash
npm install diff diff2html
```

## Success Criteria

Phase 3 complete when:
1. All message types render correctly
2. All tool handlers ported and working
3. Tool lifecycle updates in real-time
4. No frontend message injection (backend-only source)
5. Markdown rendering works with code blocks
6. Auto-scroll respects user scroll position
7. Permission flow works end-to-end
8. No console errors for any message type
9. Graceful handling of malformed data
10. Performance good even with 100+ messages

---

**Status**: Ready to implement
**Est. Time**: 4-5 days (Days 11-15)
