<template>
  <div class="tool-timeline-bar" v-if="sortedTools.length > 0">
    <div class="timeline-container">
      <div
        v-for="(tool, index) in sortedTools"
        :key="tool.id"
        class="timeline-segment"
        :class="getSegmentClass(tool)"
        :style="{ flex: 1 }"
        :title="getToolTooltip(tool)"
        @click="handleSegmentClick(tool, index)"
        role="button"
        tabindex="0"
        @keydown.enter="handleSegmentClick(tool, index)"
        @keydown.space.prevent="handleSegmentClick(tool, index)"
      >
        <span class="segment-inner" :class="{ 'executing-pulse': isExecuting(tool) }"></span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { generateShortToolSummary } from '@/utils/toolSummary'

const props = defineProps({
  tools: {
    type: Array,
    required: true,
    default: () => []
  }
})

const emit = defineEmits(['segment-click'])

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

/**
 * Sort tools by timestamp (chronological order)
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

/**
 * Get effective status for a tool (check backend state first)
 */
function getEffectiveStatus(tool) {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return tool.status

  // Check backend state first
  const backendState = messageStore.getBackendToolState(sessionId, tool.id)
  if (backendState) {
    const stateToStatus = {
      'pending': 'pending',
      'permission_required': 'permission_required',
      'executing': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'orphaned': 'orphaned'
    }
    return stateToStatus[backendState.state] || backendState.state
  }

  // Check orphaned status
  if (messageStore.isToolUseOrphaned(sessionId, tool.id)) {
    return 'orphaned'
  }

  return tool.status
}

/**
 * Check if tool has error
 */
function hasError(tool) {
  return tool.result?.error || tool.status === 'error' || tool.permissionDecision === 'deny'
}

/**
 * Check if tool is executing
 */
function isExecuting(tool) {
  const status = getEffectiveStatus(tool)
  return status === 'executing'
}

/**
 * Get CSS class for segment based on tool status
 */
function getSegmentClass(tool) {
  const status = getEffectiveStatus(tool)

  switch (status) {
    case 'completed':
      if (hasError(tool)) {
        return 'segment-failed'
      }
      return 'segment-success'
    case 'error':
      return 'segment-failed'
    case 'executing':
      return 'segment-executing'
    case 'permission_required':
      return 'segment-permission'
    case 'orphaned':
      return 'segment-orphaned'
    case 'pending':
    default:
      return 'segment-pending'
  }
}

/**
 * Generate tooltip for segment
 */
function getToolTooltip(tool) {
  const summary = generateShortToolSummary(tool)
  const status = getEffectiveStatus(tool)
  const statusLabel = {
    'completed': hasError(tool) ? 'Failed' : 'Success',
    'error': 'Failed',
    'executing': 'Executing...',
    'permission_required': 'Awaiting permission',
    'orphaned': 'Cancelled',
    'pending': 'Pending'
  }[status] || status

  return `${summary} [${statusLabel}]`
}

/**
 * Handle segment click - emit event to expand footer and specific tool
 */
function handleSegmentClick(tool, index) {
  emit('segment-click', { tool, index })
}
</script>

<style scoped>
.tool-timeline-bar {
  margin-bottom: 0.25em;
}

.timeline-container {
  display: flex;
  height: 6px;
  gap: 1px;
  border-radius: 3px;
  overflow: hidden;
  background-color: #e9ecef;
}

.timeline-segment {
  position: relative;
  cursor: pointer;
  transition: transform 0.1s ease, opacity 0.1s ease;
}

.timeline-segment:hover {
  transform: scaleY(1.5);
  z-index: 1;
}

.timeline-segment:focus {
  outline: none;
  transform: scaleY(1.5);
}

.segment-inner {
  display: block;
  width: 100%;
  height: 100%;
}

/* Status colors */
.segment-success .segment-inner {
  background-color: #198754; /* Bootstrap success green */
}

.segment-failed .segment-inner {
  background-color: #dc3545; /* Bootstrap danger red */
}

.segment-executing .segment-inner {
  background-color: #0d6efd; /* Bootstrap primary blue */
}

.segment-permission .segment-inner {
  background-color: #ffc107; /* Bootstrap warning yellow */
}

.segment-orphaned .segment-inner {
  background-color: #6c757d; /* Bootstrap secondary gray */
}

.segment-pending .segment-inner {
  background-color: #adb5bd; /* Light gray */
}

/* Executing pulse animation */
.executing-pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* Mobile responsiveness */
@media (max-width: 768px) {
  .timeline-container {
    height: 8px; /* Slightly taller on mobile for easier tapping */
  }
}
</style>
