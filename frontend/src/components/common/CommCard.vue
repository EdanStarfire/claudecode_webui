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
            <strong>
              {{ recipientName }}
            </strong>
          </template>
        </div>

        <!-- Middle: Badge + Summary -->
        <div class="comm-badge-summary">
          <span class="badge" :class="commTypeBadgeClass" :title="tooltipText">
            {{ comm.comm_type }}
          </span>
          <span v-if="attachmentCount > 0" class="badge bg-warning text-dark ms-1" :title="attachmentCount + ' file(s) attached'">
            &#x1F4CE; {{ attachmentCount }}
          </span>
          <span class="comm-summary-text text-muted ms-2">
            {{ summaryOrPreview }}
          </span>
        </div>

        <!-- Right: Copy + Expand/Collapse -->
        <div class="comm-header-actions">
          <button
            v-if="rawContent"
            class="copy-markdown-btn"
            @click.stop="copyMarkdown"
            :title="copyFeedback ? 'Copied!' : 'Copy markdown'"
            aria-label="Copy raw markdown"
          >
            <svg v-if="copyFeedback" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2 2v1"></path>
            </svg>
          </button>
          <div class="expand-icon" :aria-label="isExpanded ? 'Collapse' : 'Expand'">
            {{ isExpanded ? '▾' : '▸' }}
          </div>
        </div>
      </div>

      <!-- Collapsible Content -->
      <div v-if="isExpanded" class="comm-content-body">
        <div class="message-text" ref="contentRef" v-html="renderedContent"></div>

        <!-- File Attachments -->
        <div v-if="attachmentCount > 0" class="comm-attachments">
          <div class="comm-attachments-label text-muted">Attachments</div>
          <div class="comm-attachment-list">
            <AttachmentChip
              v-for="(att, idx) in comm.attachments"
              :key="idx"
              :filename="basename(att.name)"
              :size="att.size"
              :mime-type="att.mime_type"
              :resource-id="att.resource_id"
              :session-id="att.session_id"
              :download-url="getDownloadUrl(att)"
              @preview="openAttachmentPreview(att)"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'
import { useMermaid } from '@/composables/useMermaid'
import { useResourceStore } from '@/stores/resource'
import AttachmentChip from '@/components/common/AttachmentChip.vue'

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

const resourceStore = useResourceStore()

// System minion ID constant (matches backend)
const SYSTEM_MINION_ID = 'ffffffff-ffff-ffff-ffff-ffffffffffff'

// Expanded state
const isExpanded = ref(false)

// Mermaid diagram rendering
const contentRef = ref(null)
useMermaid(contentRef)

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
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
const rawContent = computed(() => props.comm.content || '')
const { renderedHtml: renderedContent } = useMarkdown(rawContent)

// Attachment count for header badge
const attachmentCount = computed(() => {
  return props.comm.attachments ? props.comm.attachments.length : 0
})

function getDownloadUrl(att) {
  return resourceStore.getDownloadUrl(att.session_id, att.resource_id)
}

function basename(name) {
  if (!name) return ''
  return name.split('/').pop() || name
}

function openAttachmentPreview(att) {
  resourceStore.openFullViewById(att.resource_id, att.session_id)
}
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

/* Column 3: Copy + Expand */
.comm-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.copy-markdown-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px;
  color: var(--bs-secondary);
  opacity: 0.4;
  transition: opacity 0.15s;
  line-height: 1;
}

.copy-markdown-btn:hover {
  opacity: 1;
}

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

/* File Attachments */
.comm-attachments {
  margin-top: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px dashed rgba(0, 0, 0, 0.1);
}

.comm-attachments-label {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.25rem;
}

.comm-attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

</style>
