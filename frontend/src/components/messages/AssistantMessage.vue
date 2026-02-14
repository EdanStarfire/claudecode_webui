<template>
  <div class="msg-wrapper msg-assistant">
    <div class="msg-meta">
      <span class="msg-role">assistant</span>
      <span class="msg-time">{{ formattedTimestamp }}</span>
    </div>
    <div class="msg-bubble msg-bubble-assistant">
      <!-- Thinking Block (collapsible) -->
      <div v-if="hasThinking" class="thinking-block mb-2">
        <ThinkingBlock :thinking="thinkingContent" />
      </div>

      <!-- Content -->
      <div v-if="hasContent" class="msg-text" v-html="renderedContent"></div>

      <!-- Activity Timeline (compact dot timeline) -->
      <ActivityTimeline
        v-if="hasToolUses"
        :tools="enrichedToolCalls"
        :messageId="message.id"
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
import ActivityTimeline from './tools/ActivityTimeline.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  attachedTools: {
    type: Array,
    default: () => []
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
  const messageTools = props.message.metadata?.has_tool_uses && props.message.metadata?.tool_uses?.length > 0
  const hasAttached = props.attachedTools && props.attachedTools.length > 0
  return messageTools || hasAttached
})

const toolUses = computed(() => {
  const messageTools = props.message.metadata?.tool_uses || []
  const attachedTools = props.attachedTools || []
  // Combine message tools and attached tools, avoiding duplicates by ID
  const allTools = [...messageTools]
  const existingIds = new Set(messageTools.map(t => t.id))
  for (const tool of attachedTools) {
    if (!existingIds.has(tool.id)) {
      allTools.push(tool)
    }
  }
  return allTools
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
/* Left-aligned assistant bubble */
.msg-wrapper {
  padding: 4px 16px;
}

.msg-assistant {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.msg-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 2px;
  padding: 0 4px;
}

.msg-role {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
}

.msg-time {
  font-size: 11px;
  color: #94a3b8;
}

.msg-bubble {
  border-radius: 12px;
  padding: 10px 14px;
  max-width: 85%;
  min-width: 60px;
}

.msg-bubble-assistant {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-top-left-radius: 4px;
}

.msg-text {
  font-size: 14px;
  line-height: 1.5;
  color: #1e293b;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* Markdown styling */
.msg-text :deep(*) {
  margin-bottom: 0;
}

.msg-text :deep(p) {
  margin-bottom: 0;
}

.msg-text :deep(p + p) {
  margin-top: 0.5em;
}

.msg-text :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.msg-text :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.msg-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.msg-text :deep(ul),
.msg-text :deep(ol) {
  padding-left: 1.5rem;
}

.msg-text :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 1rem;
  margin-left: 0;
  color: #64748b;
}

.msg-text :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
}

.msg-text :deep(table th),
.msg-text :deep(table td) {
  border: 1px solid #94a3b8;
  padding: 0.5rem;
  text-align: left;
}

.msg-text :deep(table th) {
  background-color: rgba(0, 0, 0, 0.04);
  font-weight: 600;
}

/* Mobile */
@media (max-width: 768px) {
  .msg-bubble {
    max-width: 95%;
  }
}
</style>
