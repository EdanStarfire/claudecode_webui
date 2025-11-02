<template>
  <div class="message-row message-row-comm" :class="{ 'system-comm': isSystemComm }">
    <!-- Single column: Header + Content -->
    <div class="message-content-column-full">
      <!-- Accordion Header (always visible, clickable) -->
      <div
        class="comm-header-accordion"
        @click="toggleExpanded"
        role="button"
        :aria-expanded="isExpanded"
      >
        <!-- Left: Sender → Recipient -->
        <div class="comm-sender-recipient">
          <strong :class="{ 'system-sender': isSystemComm }">
            {{ senderName }}
          </strong>
          <template v-if="recipientName">
            <span class="text-muted mx-1">→</span>
            <strong :class="{ 'recipient-channel': comm.to_channel_id }">
              {{ recipientName }}
            </strong>
          </template>
        </div>

        <!-- Middle: Badge + Summary -->
        <div class="comm-badge-summary">
          <span class="badge" :class="commTypeBadgeClass" :title="tooltipText">
            {{ comm.comm_type }}
          </span>
          <span class="comm-summary-text text-muted ms-2">
            {{ summaryOrPreview }}
          </span>
        </div>

        <!-- Right: Expand/Collapse Icon -->
        <div class="expand-icon">
          <i :class="isExpanded ? 'bi bi-chevron-up' : 'bi bi-chevron-down'"></i>
        </div>
      </div>

      <!-- Collapsible Content -->
      <div v-if="isExpanded" class="comm-content-body">
        <div class="message-text" v-html="renderedContent"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps({
  comm: {
    type: Object,
    required: true
  },
  senderName: {
    type: String,
    required: true
  },
  recipientName: {
    type: String,
    required: true
  }
})

// System minion ID constant (matches backend)
const SYSTEM_MINION_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff'

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true
})

// Expanded state
const isExpanded = ref(false)

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}

// Check if comm is from system
const isSystemComm = computed(() => {
  return props.comm.from_minion_id === SYSTEM_MINION_ID
})

// Format timestamp - convert Unix timestamp (seconds) to local time
const formattedTimestamp = computed(() => {
  // Backend sends Unix timestamp in seconds, convert to milliseconds
  const date = new Date(props.comm.timestamp * 1000)
  return date.toLocaleTimeString()
})

// Tooltip text
const tooltipText = computed(() => {
  const date = new Date(props.comm.timestamp * 1000)
  return `${props.senderName} → ${props.recipientName}\n${date.toLocaleString()}`
})

// Get badge class for comm type
const commTypeBadgeClass = computed(() => {
  const badgeMap = {
    task: 'bg-primary',
    question: 'bg-info',
    info: 'bg-secondary',
    report: 'bg-success',
    system: 'bg-dark'
  }
  return badgeMap[props.comm.comm_type] || 'bg-secondary'
})

// Summary or preview text - use summary if available, else truncate content
const summaryOrPreview = computed(() => {
  if (props.comm.summary) {
    return props.comm.summary
  }
  // Fallback to truncated content (first 100 chars)
  const content = props.comm.content || ''
  if (content.length > 100) {
    return content.substring(0, 100) + '...'
  }
  return content
})

// Render content with markdown
const renderedContent = computed(() => {
  const content = props.comm.content || ''
  // Render markdown and sanitize
  let html = marked.parse(content)
  // Remove newlines before HTML tags to reduce whitespace
  html = html.replace(/\n</g, '<')
  // Trim trailing newlines
  html = html.replace(/\n+$/, '')
  return DOMPurify.sanitize(html)
})
</script>

<style scoped>
/* Single-column full-width layout */
.message-row {
  width: 100%;
  min-height: 1.2rem;
  padding: 0.5rem 1rem;
  line-height: 1.2rem;
}

.message-row-comm {
  background-color: #E8F5E9; /* Light green for comms */
}

/* System comm styling - muted background */
.system-comm {
  background-color: #f5f5f5;
  opacity: 0.9;
}

/* Content column (full width) */
.message-content-column-full {
  width: 100%;
  overflow-wrap: break-word;
}

/* Accordion Header (clickable) - Three column layout */
.comm-header-accordion {
  display: grid;
  grid-template-columns: minmax(100px, 15%) 1fr auto;
  gap: 1rem;
  align-items: center;
  cursor: pointer;
  user-select: none;
  padding: 0.25rem 0;
}

.comm-header-accordion:hover {
  opacity: 0.8;
}

/* Column 1: Sender → Recipient */
.comm-sender-recipient {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.comm-sender-recipient strong {
  font-size: 0.95rem;
}

/* Column 2: Badge + Summary */
.comm-badge-summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  overflow: hidden;
}

.comm-badge-summary .badge {
  font-size: 0.75rem;
  padding: 0.25em 0.5em;
  flex-shrink: 0;
}

.comm-summary-text {
  font-size: 0.875rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Column 3: Expand icon */
.expand-icon {
  flex-shrink: 0;
  color: #6c757d;
  font-size: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
}

/* System sender styling */
.system-sender {
  color: #6c757d !important;
  font-style: italic;
}

/* Channel recipient styling */
.recipient-channel {
  color: #0d6efd !important;
  font-weight: 500 !important;
}

/* Collapsible content body */
.comm-content-body {
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid rgba(0, 0, 0, 0.1);
}

/* Content text (reusing Session message styles) */
.message-text {
  line-height: 1.2rem;
  white-space: pre-wrap;
  word-wrap: break-word;
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

</style>
