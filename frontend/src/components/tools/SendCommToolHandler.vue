<template>
  <div class="outbound-comm-wrapper">
    <div class="outbound-comm-meta">
      <span class="outbound-comm-recipient" :style="{ color: recipientColor.accent }">
        → {{ recipientName }}
      </span>
      <span v-if="commType" class="outbound-comm-type-badge" :class="'comm-type-' + commType">
        {{ commType }}
      </span>
      <span v-if="interruptPriority && interruptPriority !== 'none'" class="outbound-comm-priority">
        {{ interruptPriority }}
      </span>
      <span v-if="attachments.length > 0" class="outbound-comm-attach-badge">
        📎 {{ attachments.length }}
      </span>
    </div>
    <div
      class="outbound-comm-bubble"
      :style="{
        background: recipientColor.bg,
        borderColor: recipientColor.border,
        borderLeftWidth: '3px',
        borderLeftStyle: 'solid',
      }"
    >
      <div class="outbound-comm-content" ref="contentRef" v-html="renderedContent"></div>
      <div v-if="attachments.length > 0" class="outbound-comm-attachments">
        <div v-for="(name, idx) in attachments" :key="idx" class="outbound-comm-attachment-item">
          <span class="outbound-comm-attachment-icon">{{ getFileIcon(name) }}</span>
          <span class="outbound-comm-attachment-name">{{ name }}</span>
        </div>
      </div>
    </div>
    <!-- Result indicator -->
    <div v-if="hasResult" class="outbound-comm-result" :class="isError ? 'result-error' : 'result-success'">
      <span>{{ isError ? '✗ Failed' : '✓ Delivered' }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, toRef } from 'vue'
import { renderMarkdown } from '@/composables/useMarkdown'
import { useMermaid } from '@/composables/useMermaid'
import { useResourceImages } from '@/composables/useResourceImages'
import { useToolResult } from '@/composables/useToolResult'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

// Extract parameters
const recipientName = computed(() => props.toolCall.input?.to_minion_name || 'unknown')
const content = computed(() => props.toolCall.input?.content || '')
const summaryText = computed(() => props.toolCall.input?.summary || '')
const commType = computed(() => props.toolCall.input?.comm_type || '')
const interruptPriority = computed(() => props.toolCall.input?.interrupt_priority || '')

const attachments = computed(() => {
  const raw = props.toolCall.input?.attachments
  if (!raw) return []
  try {
    const paths = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Array.isArray(paths) ? paths.map(p => p.split('/').pop()) : []
  } catch {
    // single path string (not JSON array)
    return [String(raw).split('/').pop()]
  }
})

function getFileIcon(name) {
  const ext = name.split('.').pop()?.toLowerCase()
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) return '\u{1F5BC}'
  if (['py', 'js', 'ts', 'vue', 'go', 'rs', 'java', 'cpp', 'c', 'h'].includes(ext)) return '\u{1F4DD}'
  if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini'].includes(ext)) return '\u{2699}'
  return '\u{1F4CE}'
}

// Agent color from recipient name
const recipientColor = computed(() => getAgentColor(slugifyAgentName(recipientName.value)))

// Render content as markdown
const renderedContent = computed(() => renderMarkdown(content.value || summaryText.value))

const sessionStore = useSessionStore()

// Mermaid diagram rendering
const contentRef = ref(null)
useMermaid(contentRef)

// Inline resource image click-to-open
const currentSessionId = computed(() => sessionStore.currentSessionId)
useResourceImages(contentRef, currentSessionId)

// Result handling
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Expose for TimelineDetail
const summary = computed(() => `→ ${recipientName.value}: ${summaryText.value || commType.value || 'message'}`)
const params = computed(() => ({
  to_minion_name: recipientName.value,
  comm_type: commType.value,
  summary: summaryText.value,
}))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.outbound-comm-wrapper {
  padding: 4px 0;
}

.outbound-comm-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
  padding: 0 4px;
}

.outbound-comm-recipient {
  font-size: 12px;
  font-weight: 600;
}

.outbound-comm-type-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.comm-type-task {
  background: #dbeafe;
  color: #1e40af;
}

.comm-type-question {
  background: #fef3c7;
  color: #92400e;
}

.comm-type-report {
  background: #d1fae5;
  color: #065f46;
}

.comm-type-info {
  background: #f1f5f9;
  color: #475569;
}

.outbound-comm-priority {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 8px;
  background: #fee2e2;
  color: #991b1b;
  text-transform: uppercase;
}

.outbound-comm-bubble {
  border: 1px solid;
  border-radius: 12px;
  border-top-left-radius: 4px;
  padding: 10px 14px;
  max-width: 85%;
  min-width: 60px;
}

.outbound-comm-content {
  font-size: 14px;
  line-height: 1.5;
  color: #1e293b;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* Markdown styling */
.outbound-comm-content :deep(*) {
  margin-bottom: 0;
}

.outbound-comm-content :deep(p) {
  margin-bottom: 0;
}

.outbound-comm-content :deep(p + p) {
  margin-top: 0.5em;
}

.outbound-comm-content :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.outbound-comm-content :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.outbound-comm-content :deep(pre code) {
  background: transparent;
  padding: 0;
}

/* Result indicator */
.outbound-comm-result {
  font-size: 11px;
  padding: 2px 4px;
  margin-top: 2px;
}

.outbound-comm-result.result-success {
  color: #16a34a;
}

.outbound-comm-result.result-error {
  color: #dc2626;
}

.outbound-comm-attach-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  background: #e0f2fe;
  color: #0369a1;
}

.outbound-comm-attachments {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #cbd5e1;
}

.outbound-comm-attachment-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
}

.outbound-comm-attachment-name {
  font-family: 'Courier New', monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
