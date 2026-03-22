<template>
  <!-- Issue #195: Hide assistant bubble entirely when nothing to render
       (e.g. content-less messages whose only tools were Task calls moved to SubagentTimeline) -->
  <div v-if="hasAnythingToShow" class="msg-wrapper msg-assistant">
    <div class="msg-meta">
      <span class="msg-role">assistant</span>
      <span class="msg-time">{{ formattedTimestamp }}</span>
    </div>
    <div class="msg-bubble msg-bubble-assistant" :class="{ 'has-permission-prompt': hasActivePermission, 'tts-playing': isTTSPlaying }">
      <!-- Thinking Block (collapsible) -->
      <div v-if="hasThinking" class="thinking-block mb-2">
        <ThinkingBlock :thinking="thinkingContent" />
      </div>

      <!-- Content -->
      <div v-if="hasContent" class="msg-content-row">
        <div class="msg-text" ref="contentRef" v-html="renderedContent"></div>
        <button
          v-if="tts"
          class="tts-play-icon"
          @click.stop="onPlayClick"
          aria-label="Read aloud from this message"
          title="Read aloud"
        >&#x1F50A;</button>
        <button
          class="copy-markdown-btn"
          @click.stop="copyMarkdown"
          :title="copyFeedback ? 'Copied!' : 'Copy markdown'"
          :style="{ right: tts ? '16px' : '-8px' }"
          aria-label="Copy raw markdown"
        >
          <svg v-if="copyFeedback" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2 2v1"></path>
          </svg>
        </button>
      </div>

      <!-- Activity Timeline (compact dot timeline) — excludes Task tools and child tools -->
      <ActivityTimeline
        v-if="mainTimelineTools.length > 0"
        :tools="mainTimelineTools"
        :messageId="message.id"
      />

      <!-- Subagent bubbles (one per Task tool call) -->
      <SubagentTimeline
        v-for="task in taskToolCalls"
        :key="task.id"
        :taskToolCall="task"
      />
    </div>

    <!-- Outbound comm bubbles (extracted from timeline, rendered as message-level items) -->
    <SendCommToolHandler
      v-for="comm in sendCommToolCalls"
      :key="comm.id"
      :toolCall="comm"
    />
  </div>
</template>

<script setup>
import { computed, inject, onUnmounted, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'
import { useMarkdown } from '@/composables/useMarkdown'
import { useMermaid } from '@/composables/useMermaid'
import { useResourceImages } from '@/composables/useResourceImages'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import ThinkingBlock from './ThinkingBlock.vue'
import ActivityTimeline from './tools/ActivityTimeline.vue'
import SubagentTimeline from './SubagentTimeline.vue'
import SendCommToolHandler from '@/components/tools/SendCommToolHandler.vue'

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

// TTS Read Aloud (provided by MessageList)
const tts = inject('ttsReadAloud', null)
const allMessages = inject('allMessages', null)

const isTTSPlaying = computed(() => {
  if (!tts) return false
  // Match by timestamp since messages lack unique IDs
  return tts.currentMessageId.value === props.message.timestamp
})

function onPlayClick() {
  if (!tts || !allMessages) return
  tts.playMessage(props.message, allMessages.value)
}

const copyFeedback = ref(false)
let copyTimer = null

async function copyMarkdown() {
  await navigator.clipboard.writeText(rawContent.value)
  copyFeedback.value = true
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copyFeedback.value = false }, 2000)
}

onUnmounted(() => clearTimeout(copyTimer))

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

const rawContent = computed(() => props.message.content || '')
const { renderedHtml: renderedContent } = useMarkdown(rawContent)

// Mermaid diagram rendering
const contentRef = ref(null)
useMermaid(contentRef)

// Inline resource image click-to-open
const currentSessionId = computed(() => sessionStore.currentSessionId)
useResourceImages(contentRef, currentSessionId)

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

/**
 * Issue #195: Main timeline tools — excludes Task tools and child tools (those with parent_tool_use_id)
 */
const mainTimelineTools = computed(() => {
  return enrichedToolCalls.value.filter(tc =>
    tc.name !== 'Task' && tc.name !== 'Agent' &&
    tc.name !== 'mcp__legion__send_comm' &&
    !tc.parent_tool_use_id
  )
})

/**
 * Issue #652: send_comm tool calls — extracted for standalone bubble rendering
 */
const sendCommToolCalls = computed(() => {
  return enrichedToolCalls.value.filter(tc => tc.name === 'mcp__legion__send_comm')
})

/**
 * Issue #195: Task tool calls — only Task tools for SubagentTimeline bubbles
 */
const taskToolCalls = computed(() => {
  return enrichedToolCalls.value.filter(tc => tc.name === 'Task' || tc.name === 'Agent')
})

/**
 * Issue #195: Hide the entire assistant bubble when there's nothing to render.
 * This handles content-less messages whose only tools were Task calls
 * (now rendered as SubagentTimeline on the parent message).
 */
/**
 * Issue #716: Expand bubble to full width when a permission prompt is active
 */
const hasActivePermission = computed(() => {
  return enrichedToolCalls.value.some(tc =>
    getEffectiveStatusForTool(tc) === 'permission_required'
  )
})

const hasAnythingToShow = computed(() => {
  return hasContent.value ||
    hasThinking.value ||
    mainTimelineTools.value.length > 0 ||
    taskToolCalls.value.length > 0 ||
    sendCommToolCalls.value.length > 0
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
  border: 1px solid #94a3b8;
  border-left: 3px solid #94a3b8;
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

/* TTS content row layout */
.msg-content-row {
  position: relative;
}

.copy-markdown-btn,
.tts-play-icon {
  position: absolute;
  top: 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  opacity: 0;
  transition: opacity 0.15s;
  padding: 2px;
  line-height: 1;
}

.tts-play-icon {
  right: -8px;
}

.msg-content-row:hover .copy-markdown-btn,
.msg-content-row:hover .tts-play-icon {
  opacity: 0.6;
}

.copy-markdown-btn:hover,
.tts-play-icon:hover {
  opacity: 1 !important;
}

/* Issue #716: Force bubble to full allowed width when permission prompt is active */
.msg-bubble.has-permission-prompt {
  width: 85%;
}

/* Mobile */
@media (max-width: 768px) {
  .msg-bubble {
    max-width: 95%;
  }

  .msg-bubble.has-permission-prompt {
    width: 95%;
  }

  .copy-markdown-btn,
  .tts-play-icon {
    opacity: 0.5;
  }
}
</style>
