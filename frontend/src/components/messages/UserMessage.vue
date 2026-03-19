<template>
  <div class="msg-wrapper msg-user">
    <div class="msg-meta">
      <span class="msg-role" :style="isComm ? { color: commColor.accent } : {}">{{ isComm ? commSenderName : 'user' }}</span>
      <span class="msg-time">{{ formattedTimestamp }}</span>
    </div>
    <div
      class="msg-bubble"
      :class="isComm ? 'msg-bubble-comm' : 'msg-bubble-user'"
      :style="isComm ? { background: commColor.bg, borderColor: commColor.border, borderRightWidth: '3px', borderRightStyle: 'solid' } : {}"
    >
      <!-- Content (attachment section stripped from rendered markdown) -->
      <div class="msg-text" ref="contentRef" v-html="cleanRenderedContent"></div>

      <!-- Attachment chips (parsed from content; display only — content sent to Claude is unchanged) -->
      <!-- NOTE: Format contract with InputArea.vue: "\n\n---\nAttached files (use Read tool...)\n- name (X KB): path\n  Resource ID: uuid" -->
      <div v-if="attachmentItems.length > 0" class="msg-attachments">
        <span
          v-for="(item, idx) in attachmentItems"
          :key="idx"
          class="attachment-chip"
          :class="{ clickable: item.resourceId }"
          @click="item.resourceId ? openPreview(item) : null"
          :title="item.resourceId ? 'Click to preview' : item.filename"
        >
          <span class="file-icon">{{ item.icon }}</span>
          <span class="attachment-name">{{ item.filename }}</span>
        </span>
      </div>

      <!-- Tool Results (if any) -->
      <div v-if="hasToolResults" class="tool-results mt-2">
        <div class="tool-results-header text-muted small mb-2">
          Tool Results ({{ toolResults.length }})
        </div>
        <div
          v-for="(result, index) in toolResults"
          :key="index"
          class="tool-result-summary"
          :class="result.is_error ? 'result-error' : 'result-success'"
        >
          <div class="d-flex align-items-start gap-2">
            <span>{{ result.is_error ? '✗' : '✓' }}</span>
            <div class="flex-grow-1">
              <small class="text-muted">Tool: {{ result.tool_use_id }}</small>
              <div v-if="result.is_error" class="text-danger small">
                {{ truncate(result.content, 200) }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'
import { getAgentColor } from '@/composables/useAgentColor'
import { useMarkdown } from '@/composables/useMarkdown'
import { useResourceImages } from '@/composables/useResourceImages'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import { getFileIcon } from '@/utils/fileTypes'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

const isComm = computed(() => !!props.message.metadata?.comm)
const commColor = computed(() => isComm.value ? getAgentColor(props.message.metadata.comm.from_name) : null)
const commSenderName = computed(() => props.message.metadata?.comm?.from_display_name || 'agent')

const sessionStore = useSessionStore()
const resourceStore = useResourceStore()

// Separator used by InputArea.vue when appending the attachment block
const ATTACHMENT_SEPARATOR = '\n\n---\nAttached files (use Read tool to access, or embed via markdown URL):\n'

/**
 * Split raw content into [mainText, attachmentBlock | null].
 * Falls back gracefully if no attachment section is present.
 */
const splitContent = computed(() => {
  const raw = props.message.content || ''
  const idx = raw.indexOf(ATTACHMENT_SEPARATOR)
  if (idx === -1) return { main: raw, block: null }
  return {
    main: raw.slice(0, idx),
    block: raw.slice(idx + ATTACHMENT_SEPARATOR.length)
  }
})

// Content rendered without the attachment section
const cleanContent = computed(() => splitContent.value.main)
const { renderedHtml: cleanRenderedContent } = useMarkdown(cleanContent)

/**
 * Parse the attachment block into structured items.
 * Each line follows: "- <name> (<size> KB): <stored_path>"
 * Optionally followed by "  Resource ID: <uuid>"
 * Falls back to extracting uuid from stored_path URL for gallery resources.
 */
const attachmentItems = computed(() => {
  const block = splitContent.value.block
  if (!block) return []

  const items = []
  const lines = block.split('\n')
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // Match: "- filename.ext (10.0 KB): /some/path"
    const entryMatch = line.match(/^- (.+?) \([\d.,]+ [KMG]?B\): (.+)$/)
    if (entryMatch) {
      const filename = entryMatch[1].trim()
      const storedPath = entryMatch[2].trim()
      let resourceId = null

      // Check next line for explicit Resource ID
      if (i + 1 < lines.length) {
        const idMatch = lines[i + 1].match(/^\s+Resource ID:\s+([a-f0-9-]{36})/)
        if (idMatch) {
          resourceId = idMatch[1]
          i++ // consume the Resource ID line
        }
      }

      // Fallback: extract from /api/sessions/{sid}/resources/{uuid} URL
      if (!resourceId) {
        const urlMatch = storedPath.match(/\/resources\/([a-f0-9-]{36})/)
        if (urlMatch) resourceId = urlMatch[1]
      }

      items.push({
        filename,
        storedPath,
        resourceId,
        icon: getFileIcon(filename)
      })
    }
    i++
  }
  return items
})

function openPreview(item) {
  if (!item.resourceId) return
  resourceStore.openFullViewById(item.resourceId, sessionStore.currentSessionId)
}

// Inline resource image click-to-open
const contentRef = ref(null)
const currentSessionId = computed(() => sessionStore.currentSessionId)
useResourceImages(contentRef, currentSessionId)

const hasToolResults = computed(() => {
  return props.message.metadata?.has_tool_results &&
         props.message.metadata?.tool_results?.length > 0
})

const toolResults = computed(() => {
  return props.message.metadata?.tool_results || []
})

function truncate(text, maxLength) {
  if (!text) return ''
  if (text.length <= maxLength) return text
  return text.substring(0, maxLength) + '...'
}
</script>

<style scoped>
/* Right-aligned user bubble */
.msg-wrapper {
  padding: 4px 16px;
}

.msg-user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
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
  color: #3b82f6;
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

.msg-bubble-user {
  background: #eef2ff;
  border: 1px solid #a5b4fc;
  border-right: 3px solid #818cf8;
  border-top-right-radius: 4px;
}

/* Comm-injected messages: right-aligned with agent-colored right border */
.msg-bubble-comm {
  border: 1px solid;
  border-top-right-radius: 4px;
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

/* Attachment chips */
.msg-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed rgba(0, 0, 0, 0.12);
}

.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid #c7d2fe;
  border-radius: 12px;
  font-size: 12px;
  font-family: 'Courier New', monospace;
  color: #374151;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip.clickable {
  cursor: pointer;
  color: #3730a3;
}

.attachment-chip.clickable:hover {
  background: rgba(238, 242, 255, 0.9);
  border-color: #818cf8;
}

.attachment-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Tool results */
.tool-result-summary {
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 4px;
  font-size: 12px;
}

.result-error {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.15);
}

.result-success {
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.15);
}

/* Mobile */
@media (max-width: 768px) {
  .msg-bubble {
    max-width: 95%;
  }
}
</style>
