<template>
  <div class="outbound-comm-wrapper">
    <div class="outbound-comm-meta">
      <span class="outbound-comm-recipient" :style="{ color: recipientColor.accent }">
        → {{ recipientName }}
      </span>
      <span v-if="commType" class="badge outbound-comm-type-badge" :class="commTypeBadgeClass">
        {{ commType }}
      </span>
      <span v-if="interruptPriority && interruptPriority !== 'none'" class="badge bg-danger outbound-comm-priority">
        {{ interruptPriority }}
      </span>
      <span v-if="attachments.length > 0" class="badge text-bg-info outbound-comm-attach-badge">
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
      <MarkdownView class="outbound-comm-content" ref="contentRef" :content="contentForRender" />
      <div v-if="attachments.length > 0" class="outbound-comm-attachments">
        <AttachmentChip
          v-for="(att, idx) in attachments"
          :key="idx"
          :filename="att.filename"
          :resource-id="att.resourceId"
          :session-id="att.sessionId"
          :size="att.size"
          :mime-type="att.mimeType"
          @preview="openAttachmentPreview(att)"
        />
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
import { useResourceImages } from '@/composables/useResourceImages'
import { useToolResult } from '@/composables/useToolResult'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import AttachmentChip from '@/components/common/AttachmentChip.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

// Extract parameters
const recipientName = computed(() => props.toolCall.input?.to_minion_name || 'unknown')
const content = computed(() => props.toolCall.input?.content || '')
const summaryText = computed(() => props.toolCall.input?.summary || '')
const commType = computed(() => props.toolCall.input?.comm_type || '')
const interruptPriority = computed(() => props.toolCall.input?.interrupt_priority || '')

const commTypeBadgeClass = computed(() => {
  const map = {
    task: 'bg-primary',
    question: 'bg-info',
    info: 'bg-secondary',
    report: 'bg-success',
    system: 'bg-dark',
  }
  return map[commType.value] || 'bg-secondary'
})

const sessionStore = useSessionStore()
const resourceStore = useResourceStore()

const attachments = computed(() => {
  // Issue #1593: prefer backend-resolved sender_attachments (reliable resource_id)
  // over the filename-based store lookup (which fails on timing or format mismatch).
  const senderAttachments = props.toolCall.senderAttachments
  if (senderAttachments && senderAttachments.length > 0) {
    const sid = props.toolCall.session_id || sessionStore.currentSessionId
    return senderAttachments.map(a => ({
      filename: a.name,
      resourceId: a.resource_id || null,
      sessionId: a.resource_id ? sid : null,
      size: a.size || null,
      mimeType: a.mime_type || null,
    }))
  }

  // Fallback: filename-based store lookup for older tool calls without sender_attachments.
  const raw = props.toolCall.input?.attachments
  if (!raw) return []
  try {
    const paths = typeof raw === 'string' ? JSON.parse(raw) : raw
    const list = Array.isArray(paths) ? paths : [String(raw)]
    const sid = props.toolCall.session_id || sessionStore.currentSessionId
    const resources = resourceStore.resourcesBySession.get(sid) || []
    return list.map(p => {
      const filename = p.split('/').pop()
      const resource = resources.find(
        r => r.title === filename || r.original_name === filename
      )
      return {
        filename,
        resourceId: resource?.resource_id || null,
        sessionId: resource ? sid : null,
        size: resource?.size_bytes || null,
        mimeType: resource?.mime_type || null,
      }
    })
  } catch {
    return [{ filename: String(raw).split('/').pop(), resourceId: null, sessionId: null, size: null, mimeType: null }]
  }
})

function openAttachmentPreview(att) {
  if (att.resourceId) {
    resourceStore.openFullViewById(att.resourceId, att.sessionId)
  }
}

// Agent color from recipient name
const recipientColor = computed(() => getAgentColor(slugifyAgentName(recipientName.value)))

const contentForRender = computed(() => content.value || summaryText.value)

// Inline resource image click-to-open
const contentRef = ref(null)
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

.outbound-comm-priority {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 8px;
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
  color: var(--bs-body-color);
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
  background: var(--bs-tertiary-bg);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.outbound-comm-content :deep(code) {
  background: var(--bs-secondary-bg);
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
  color: var(--bs-success);
}

.outbound-comm-result.result-error {
  color: var(--bs-danger);
}

.outbound-comm-attach-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
}

.outbound-comm-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--bs-border-color-translucent);
}
</style>
