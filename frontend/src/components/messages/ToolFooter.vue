<template>
  <div class="tool-footer-container">
    <!-- Footer Summary Bar (always visible if tools exist) -->
    <div
      class="tool-footer-summary"
      :class="footerClass"
      @click="toggleExpanded"
      role="button"
      :aria-label="isExpanded ? 'Collapse tools' : 'Expand tools'"
      tabindex="0"
      @keydown.enter="toggleExpanded"
      @keydown.space.prevent="toggleExpanded"
    >
      <div class="footer-left">
        <span class="footer-text">Used: {{ toolTypeSummary }}</span>
      </div>
      <div class="footer-middle">
        <span class="footer-separator">|</span>
        <span class="footer-text">{{ toolCountSummary }}</span>
      </div>
      <div class="footer-right">
        <span class="expand-icon">{{ expandIcon }}</span>
      </div>
    </div>

    <!-- Expanded Footer (chronological ToolCallCards) - Completed tools shown first -->
    <div v-if="isExpanded" class="tool-footer-expanded">
      <ToolCallCard
        v-for="tool in completedTools"
        :key="tool.id"
        :toolCall="tool"
      />
    </div>

    <!-- Active Tools Area (conditional - only executing or permission_required) - Shown last for chronological order -->
    <div v-if="activeTools.length > 0" class="active-tools-area">
      <ToolCallCard
        v-for="tool in activeTools"
        :key="tool.id"
        :toolCall="tool"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import ToolCallCard from './ToolCallCard.vue'

const props = defineProps({
  tools: {
    type: Array,
    required: true,
    default: () => []
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()

// ========== STATE ==========
const isExpanded = ref(false)

// ========== COMPUTED PROPERTIES ==========

/**
 * Sort tools by timestamp (chronological order)
 * Primary: timestamp, Fallback: original array index
 */
function sortToolsChronologically(tools) {
  return tools
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
}

/**
 * Active tools: Only executing or permission_required
 * These appear in the active tools area above the footer
 * Sorted chronologically by timestamp
 */
const activeTools = computed(() => {
  const filtered = props.tools.filter(tool =>
    tool.status === 'executing' || tool.status === 'permission_required'
  )
  return sortToolsChronologically(filtered)
})

/**
 * Completed tools: All tools that are NOT executing or permission_required
 * These appear in the expanded footer
 * Sorted chronologically by timestamp
 */
const completedTools = computed(() => {
  const filtered = props.tools.filter(tool =>
    tool.status !== 'executing' && tool.status !== 'permission_required'
  )
  return sortToolsChronologically(filtered)
})

/**
 * Tool type summary: "Read (3), Edit (2), Bash (1)"
 * Shows top 5 tool types, then "+N more"
 */
const toolTypeSummary = computed(() => {
  const typeCounts = new Map()

  // Count all tools (not just completed)
  props.tools.forEach(tool => {
    const count = typeCounts.get(tool.name) || 0
    typeCounts.set(tool.name, count + 1)
  })

  // Sort by count descending
  const sorted = Array.from(typeCounts.entries()).sort((a, b) => b[1] - a[1])

  // Take top 5
  const top5 = sorted.slice(0, 5)
  const remaining = sorted.length - top5.length

  // Format: "Read (3), Edit (2)"
  const summary = top5.map(([name, count]) => `${name} (${count})`).join(', ')

  if (remaining > 0) {
    return `${summary}, +${remaining} more`
  }

  return summary
})

/**
 * Tool count summary: "5 tools: 4 ✅ 1 ❌"
 * Shows total count and status breakdown
 */
const toolCountSummary = computed(() => {
  const total = props.tools.length
  let successCount = 0
  let errorCount = 0
  let orphanedCount = 0

  props.tools.forEach(tool => {
    const sessionId = sessionStore.currentSessionId
    const isOrphaned = sessionId ? messageStore.isToolUseOrphaned(sessionId, tool.id) : false

    if (isOrphaned) {
      orphanedCount++
    } else if (tool.status === 'completed' && !tool.result?.error && tool.permissionDecision !== 'deny') {
      successCount++
    } else if (tool.status === 'error' || tool.result?.error || tool.permissionDecision === 'deny') {
      errorCount++
    }
  })

  // Build status string
  let statusParts = []
  if (successCount > 0) {
    statusParts.push(`${successCount} ✅`)
  }
  if (errorCount > 0) {
    statusParts.push(`${errorCount} ❌`)
  }
  if (orphanedCount > 0) {
    statusParts.push(`${orphanedCount} ⏹️`)
  }

  const toolLabel = total === 1 ? 'tool' : 'tools'
  const statusStr = statusParts.length > 0 ? ': ' + statusParts.join(' ') : ''

  return `${total} ${toolLabel}${statusStr}`
})

/**
 * Expand/collapse icon
 */
const expandIcon = computed(() => {
  return isExpanded.value ? '▲' : '▼'
})

/**
 * Footer CSS class based on overall status
 * Determines background tint color
 */
const footerClass = computed(() => {
  const sessionId = sessionStore.currentSessionId

  const hasErrors = props.tools.some(tool =>
    tool.status === 'error' || tool.result?.error || tool.permissionDecision === 'deny'
  )
  const hasOrphaned = props.tools.some(tool =>
    sessionId ? messageStore.isToolUseOrphaned(sessionId, tool.id) : false
  )
  const hasInProgress = activeTools.value.length > 0
  const allSuccess = props.tools.every(tool => {
    const isOrphaned = sessionId ? messageStore.isToolUseOrphaned(sessionId, tool.id) : false
    return !isOrphaned && tool.status === 'completed' && !tool.result?.error && tool.permissionDecision !== 'deny'
  })

  // Priority: error > orphaned > in-progress > success
  if (hasErrors) {
    return 'footer-status-error'
  } else if (hasOrphaned) {
    return 'footer-status-orphaned'
  } else if (hasInProgress) {
    return 'footer-status-in-progress'
  } else if (allSuccess) {
    return 'footer-status-success'
  }

  return ''
})

// ========== METHODS ==========

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
/* Container */
.tool-footer-container {
  /* No margin - footer sits directly after message content */
}

/* Active Tools Area (conditional) */
.active-tools-area {
  margin-top: 0.5em;
}

.active-tools-area .tool-call-card {
  margin-bottom: 0.5em;
  transition: all 0.2s ease-out;
}

.active-tools-area .tool-call-card:last-child {
  margin-bottom: 0;
}

/* Footer Summary Bar */
.tool-footer-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 1.5em; /* ~24px with default font */
  padding: 0.2em 1em;
  border-top: 1px dotted #dee2e6;
  border-bottom: 1px dotted #dee2e6;
  cursor: pointer;
  user-select: none;
  font-size: 0.85rem; /* Smaller font to emphasize message content */
  transition: background-color 0.2s ease;
}

.tool-footer-summary:hover {
  background-color: rgba(0, 0, 0, 0.03);
}

.tool-footer-summary:focus {
  outline: none; /* Remove blue focus outline */
}

/* Footer sections */
.footer-left {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.footer-middle {
  display: flex;
  align-items: center;
  gap: 0.5em;
  flex-shrink: 0;
}

.footer-right {
  flex-shrink: 0;
  margin-left: 0.5em;
}

.footer-text {
  color: #6c757d;
  font-weight: 400;
}

.footer-separator {
  color: #dee2e6;
  margin: 0 0.5em;
}

.expand-icon {
  color: #495057;
  font-size: 0.9em;
}

/* Status-based background colors (subtle tints) */
.footer-status-success {
  background-color: #f0f9f4; /* Very light green */
  border-left: 3px solid #198754;
}

.footer-status-success .footer-text {
  color: #198754;
}

.footer-status-error {
  background-color: #fff4f4; /* Very light red */
  border-left: 3px solid #dc3545;
}

.footer-status-error .footer-text {
  color: #dc3545;
}

.footer-status-orphaned {
  background-color: #f8f9fa; /* Light gray */
  border-left: 3px solid #6c757d;
}

.footer-status-orphaned .footer-text {
  color: #6c757d;
}

.footer-status-in-progress {
  background-color: #f0f6ff; /* Very light blue */
  border-left: 3px solid #0d6efd;
}

.footer-status-in-progress .footer-text {
  color: #0d6efd;
}

/* Expanded Footer (chronological ToolCallCards) */
.tool-footer-expanded {
  max-height: 25em; /* ~400px with default font */
  overflow-y: auto;
  border: 1px solid #dee2e6;
  border-top: none; /* Connect to summary bar */
  transition: max-height 0.2s ease-out;
}

.tool-footer-expanded .tool-call-card {
  border-radius: 0; /* Square corners when in footer */
  border-left: none;
  border-right: none;
  border-top: none;
  border-bottom: 1px solid #dee2e6;
}

.tool-footer-expanded .tool-call-card:last-child {
  border-bottom: none;
}

/* Match ToolCallCard header styling to footer */
.tool-footer-expanded :deep(.card-header) {
  font-size: 0.85rem; /* Match footer font size */
  font-weight: 400; /* Remove bold from header itself */
}

/* Mobile Responsiveness */
@media (max-width: 768px) {
  .tool-footer-summary {
    font-size: 0.8rem; /* Smaller on mobile */
    flex-wrap: wrap; /* Allow wrapping */
    height: auto;
    min-height: 1.5em;
    padding: 0.3em 0.75em;
  }

  .footer-left {
    flex-basis: 100%;
  }

  .footer-middle {
    flex: 1;
    margin-top: 0.2em;
  }

  .footer-right {
    margin-top: 0.2em;
  }
}
</style>
