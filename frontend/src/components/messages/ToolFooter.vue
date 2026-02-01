<template>
  <div class="tool-footer-container">
    <!-- Tool Timeline Bar (visual status overview) -->
    <ToolTimelineBar
      :tools="tools"
      @segment-click="handleTimelineClick"
    />

    <!-- Tool Cards (flat chronological list) -->
    <div class="tool-cards-list">
      <ToolCallCard
        v-for="tool in sortedTools"
        :key="tool.id"
        :toolCall="tool"
        :data-tool-id="tool.id"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import ToolCallCard from './ToolCallCard.vue'
import ToolTimelineBar from './ToolTimelineBar.vue'

const props = defineProps({
  tools: {
    type: Array,
    required: true,
    default: () => []
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// ========== COMPUTED PROPERTIES ==========

/**
 * All tools sorted chronologically by timestamp
 * Primary: timestamp, Fallback: original array index
 */
const sortedTools = computed(() => {
  return props.tools
    .map((tool, index) => ({ tool, originalIndex: index }))
    .sort((a, b) => {
      // Primary sort: timestamp (if both have timestamps)
      if (a.tool.timestamp && b.tool.timestamp) {
        const timeA = new Date(a.tool.timestamp).getTime()
        const timeB = new Date(b.tool.timestamp).getTime()
        if (timeA !== timeB) {
          return timeA - timeB
        }
      }
      // Fallback: original array order (stable sort)
      return a.originalIndex - b.originalIndex
    })
    .map(({ tool }) => tool)
})

// ========== METHODS ==========

/**
 * Handle timeline segment click - toggle tool expansion and scroll to it
 */
async function handleTimelineClick({ tool }) {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return

  // Toggle the clicked tool's expansion in the message store
  messageStore.toggleToolExpansion(sessionId, tool.id)

  // Scroll to the tool card after a brief delay for DOM update
  await nextTick()
  const toolCard = document.querySelector(`[data-tool-id="${tool.id}"]`)
  if (toolCard) {
    toolCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}
</script>

<style scoped>
/* Container */
.tool-footer-container {
  /* No margin - footer sits directly after message content */
}

/* Tool Cards List (flat chronological display with connected cards) */
.tool-cards-list {
  margin-top: 0.25em;
  border: 1px solid #dee2e6;
  border-radius: 4px;
  overflow: hidden;
}

.tool-cards-list :deep(.tool-call-card) {
  border-radius: 0; /* Square corners for connected look */
  border-left: none;
  border-right: none;
  border-top: none;
  border-bottom: 1px solid #dee2e6;
  margin: 0;
}

.tool-cards-list :deep(.tool-call-card:last-child) {
  border-bottom: none;
}

/* Match ToolCallCard header styling */
.tool-cards-list :deep(.card-header) {
  font-size: 0.85rem;
  font-weight: 400;
}
</style>
