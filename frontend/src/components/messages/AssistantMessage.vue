<template>
  <div class="card assistant-message bg-white border-start border-success border-3">
    <div class="card-body">
      <!-- Header -->
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="d-flex align-items-center gap-2">
          <span class="message-icon">🤖</span>
          <strong class="message-role">Assistant</strong>
          <small v-if="modelName" class="text-muted">{{ modelName }}</small>
        </div>
        <small class="text-muted">{{ formattedTimestamp }}</small>
      </div>

      <!-- Thinking Block (collapsible) -->
      <div v-if="hasThinking" class="thinking-block mb-3">
        <ThinkingBlock :thinking="thinkingContent" />
      </div>

      <!-- Content -->
      <div v-if="hasContent" class="message-content" v-html="renderedContent"></div>

      <!-- Tool Calls (embedded inline) -->
      <div v-if="hasToolUses" class="tool-calls mt-3">
        <ToolCallCard
          v-for="toolCall in enrichedToolCalls"
          :key="toolCall.id"
          :toolCall="toolCall"
        />
      </div>
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
import ToolCallCard from './ToolCallCard.vue'

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

const hasContent = computed(() => {
  const content = props.message.content || ''
  return content.trim().length > 0 && content !== 'Assistant response'
})

const renderedContent = computed(() => {
  const content = props.message.content || ''
  // Render markdown and sanitize
  const html = marked.parse(content)
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
.assistant-message {
  max-width: 100%;
}

.message-icon {
  font-size: 1.2rem;
}

.message-content {
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.6;
}

.message-content :deep(pre) {
  background: #f8f9fa;
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.message-content :deep(code) {
  background: #e9ecef;
  padding: 0.2rem 0.4rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.message-content :deep(pre code) {
  background: transparent;
  padding: 0;
}

.message-content :deep(p) {
  margin-bottom: 0.5rem;
}

.message-content :deep(p:last-child) {
  margin-bottom: 0;
}

.message-content :deep(ul),
.message-content :deep(ol) {
  margin-bottom: 0.5rem;
  padding-left: 1.5rem;
}

.message-content :deep(blockquote) {
  border-left: 3px solid #dee2e6;
  padding-left: 1rem;
  margin-left: 0;
  color: #6c757d;
}
</style>
