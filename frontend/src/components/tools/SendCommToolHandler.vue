<template>
  <div class="outbound-comm-wrapper">
    <CommCard
      direction="outbound"
      :participant-name="recipientName"
      :participant-session-id="recipientSessionId"
      :comm-type="commType"
      :interrupt-priority="interruptPriority"
      :summary="summaryText"
      :content="content"
      :timestamp="toolCall.timestamp"
      :attachments="attachments"
      :expanded="messageStore.isCommExpanded(toolCall.id)"
      :self-agent-id="senderSessionId"
      :has-result="hasResult"
      :is-error="isError"
      :result-content="resultContent"
      :formatted-input="formattedInput"
      @toggle-expand="messageStore.toggleCommExpanded(toolCall.id)"
    />
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'
import { resolveAgentByIdentifier } from '@/utils/agentMentions'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import { useMessageStore } from '@/stores/message'
import CommCard from '@/components/common/CommCard.vue'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

// Extract parameters
const recipientName = computed(() => props.toolCall.input?.to_minion_name || 'unknown')
const content = computed(() => props.toolCall.input?.content || '')
const summaryText = computed(() => props.toolCall.input?.summary || '')
const commType = computed(() => props.toolCall.input?.comm_type || '')
const interruptPriority = computed(() => props.toolCall.input?.interrupt_priority || '')

const sessionStore = useSessionStore()
const resourceStore = useResourceStore()
const messageStore = useMessageStore()

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

// Issue #1714: resolve the recipient's session id (slug-first, then display-name
// fallback) scoped to the sender's project, so the recipient name can jump-link.
const senderSessionId = computed(() => props.toolCall.session_id || sessionStore.currentSessionId)
const projectAgents = computed(() => {
  const sender = senderSessionId.value ? sessionStore.sessions.get(senderSessionId.value) : null
  const projectId = sender?.project_id
  if (!projectId) return []
  return sessionStore.sessionsInProject(projectId).value.map(s => ({
    id: s.session_id,
    slug: s.slug,
    name: s.name,
  }))
})
const recipientSessionId = computed(() => resolveAgentByIdentifier(recipientName.value, projectAgents.value)?.id || null)

// Result handling
const { hasResult, isError, resultContent, formattedInput } = useToolResult(toRef(props, 'toolCall'))

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
/* Bubble/meta/content/attachment styling now lives in CommCard.vue (issue #1843) —
   this wrapper only provides the flex-column alignment context CommCard is dropped into. */
.outbound-comm-wrapper {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  align-self: stretch;
  padding: 4px 0;
}
</style>
