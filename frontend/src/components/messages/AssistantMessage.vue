<template>
  <div class="message-row message-row-assistant">
    <div class="message-speaker" :title="tooltipText">
      <span class="speaker-label">assistant</span>
    </div>
    <div class="message-content-column">
      <!-- Thinking Block (collapsible) -->
      <div v-if="hasThinking" class="thinking-block mb-2">
        <ThinkingBlock :thinking="thinkingContent" />
      </div>

      <!-- Content -->
      <div v-if="hasContent" class="message-text" v-html="renderedContent"></div>

      <!-- Tool Footer (hybrid active area + collapsible summary) -->
      <ToolFooter
        v-if="hasToolUses"
        :tools="enrichedToolCalls"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { formatTimestamp } from '@/utils/time'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import ThinkingBlock from './ThinkingBlock.vue'
import ToolFooter from './ToolFooter.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

const modelName = computed(() => {
  return props.message.metadata?.model || null
})

const tooltipText = computed(() => {
  const time = formattedTimestamp.value
  const model = modelName.value ? `\nModel: ${modelName.value}` : ''
  return `assistant${model}\n${time}`
})

const hasContent = computed(() => {
  const content = props.message.content || ''
  return content.trim().length > 0 && content !== 'Assistant response'
})

const renderedContent = computed(() => {
  const content = props.message.content || ''
  // Render markdown and sanitize
  let html = marked.parse(content)
  // Remove newlines before HTML tags to reduce whitespace
  html = html.replace(/\n</g, '<')
  // Trim trailing newlines
  html = html.replace(/\n+$/, '')
  return DOMPurify.sanitize(html)
})

const hasThinking = computed(() => {
  return props.message.metadata?.has_thinking &&
         props.message.metadata?.thinking_content
})

const thinkingContent = computed(() => {
  return props.message.metadata?.thinking_content || ''
})

const hasToolUses = computed(() => {
  return props.message.metadata?.has_tool_uses &&
         props.message.metadata?.tool_uses?.length > 0
})

const toolUses = computed(() => {
  return props.message.metadata?.tool_uses || []
})

/**
 * Get enriched tool calls with live state from the store
 * This computed property ensures reactive updates when tool state changes
 */
const enrichedToolCalls = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return []

  const toolCalls = messageStore.toolCallsBySession.get(sessionId) || []

  return toolUses.value.map(toolUse => {
    // Find the live tool call state from the store
    const liveToolCall = toolCalls.find(tc => tc.id === toolUse.id)

    if (liveToolCall) {
      // Return live state (will update reactively)
      return liveToolCall
    }

    // Fallback: Create minimal tool call from message data
    return {
      id: toolUse.id,
      name: toolUse.name,
      input: toolUse.input,
      status: 'pending',
      result: null,
      timestamp: toolUse.timestamp || props.message.timestamp,
      isExpanded: true
    }
  })
})
</script>

<style scoped>
/* Two-column row layout */
.message-row {
  display: flex;
  width: 100%;
  min-height: 1.2rem;
  padding: 0.2rem 0;
  line-height: 1.2rem;
}

.message-row-assistant {
  background-color: #E3F2FD; /* Light blue */
}

/* Speaker column (left) */
.message-speaker {
  width: 8em;
  padding: 0 1rem;
  flex-shrink: 0;
  text-align: right;
  cursor: help;
  font-weight: 500;
  color: #495057;
}

.speaker-label {
  font-size: 0.9rem;
  text-transform: lowercase;
}

/* Content column (right) */
.message-content-column {
  flex: 1;
  padding: 0 1rem 0 0.5rem;
  overflow-wrap: break-word;
}

.message-text {
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.2rem;
}

/* Markdown/code styling - remove bottom margins by default */
.message-text :deep(*) {
  margin-bottom: 0;
}

.message-text :deep(pre) {
  background: #f8f9fa;
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.message-text :deep(code) {
  background: #e9ecef;
  padding: 0.2rem 0.4rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.message-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.message-text :deep(ul),
.message-text :deep(ol) {
  padding-left: 1.5rem;
}

.message-text :deep(blockquote) {
  border-left: 3px solid #dee2e6;
  padding-left: 1rem;
  margin-left: 0;
  color: #6c757d;
}

.message-text :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
}

.message-text :deep(table th),
.message-text :deep(table td) {
  border: 1px solid #6c757d;
  padding: 0.5rem;
  text-align: left;
}

.message-text :deep(table th) {
  background-color: rgba(0, 0, 0, 0.05);
  font-weight: 600;
}

/* Mobile responsive: stack speaker above content */
@media (max-width: 768px) {
  .message-row {
    flex-direction: column;
  }

  .message-speaker {
    width: 100%;
    text-align: left;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  }

  .message-content-column {
    padding: 0.5rem 1rem;
  }
}
</style>
