<template>
  <div class="outbound-comm-wrapper">
    <div
      class="outbound-comm-bubble"
      :style="{
        background: gradientBg,
        borderLeftColor: senderColor.accent,
      }"
    >
      <div class="outbound-comm-meta">
        <span class="outbound-comm-recipient" :style="{ color: recipientColor.accent }">
          → {{ recipientName }}
        </span>
        <span class="outbound-comm-time">{{ formattedTimestamp }}</span>
        <span v-if="hasResult" class="outbound-comm-result" :class="isError ? 'result-error' : 'result-success'">
          {{ isError ? '✗ Failed' : '✓ Delivered' }}
        </span>
      </div>
      <MarkdownView class="outbound-comm-content" :content="contentForRender" />
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'
import { getAgentColor, getAssistantRowColor, slugifyAgentName } from '@/composables/useAgentColor'
import { formatTimestamp } from '@/utils/time'
import MarkdownView from '@/components/common/MarkdownView.vue'

// Issue #1746 (stage: subagents) follow-up (user feedback): the SDK's own `SendMessage` tool
// (used to resume a background subagent, or for a subagent to report back to main — distinct
// from Legion's `mcp__legion__send_comm`) fell through to BaseToolHandler's plain <pre> blocks
// with no background at all when it appeared in the MAIN session's own timeline (not nested in
// a subagent's transcript, where SubagentAnchorRow/MessageList already handle it). Reuses the
// same sender->recipient gradient bubble mechanics as SendCommToolHandler.vue, trimmed to
// SendMessage's actual input shape (to/message/summary — no comm_type/interrupt_priority/
// attachments, which are Legion-specific fields SendMessage doesn't have).
const props = defineProps({
  toolCall: { type: Object, required: true }
})

const recipientName = computed(() => props.toolCall.input?.to || props.toolCall.input?.recipient || 'unknown')
const contentForRender = computed(() => props.toolCall.input?.message || props.toolCall.input?.summary || props.toolCall.input?.content || '')
const formattedTimestamp = computed(() => formatTimestamp(props.toolCall.timestamp))

// Issue #1755 convention: the sending side is always "this session, as assistant" in its own
// transcript; the recipient's color comes from its hashed agent name, same as SendCommToolHandler.
const recipientColor = computed(() => getAgentColor(slugifyAgentName(recipientName.value)))
const senderColor = computed(() => getAssistantRowColor())
const gradientBg = computed(() => `linear-gradient(to right, ${senderColor.value.bg} 0%, ${recipientColor.value.bg} 30%, ${recipientColor.value.bg} 100%)`)

const { hasResult, isError } = useToolResult(toRef(props, 'toolCall'))

const summary = computed(() => `→ ${recipientName.value}: ${props.toolCall.input?.summary || 'message'}`)
const params = computed(() => ({ to: recipientName.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.outbound-comm-wrapper {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  align-self: stretch;
  padding: 4px 0;
}

.outbound-comm-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.outbound-comm-time {
  font-size: 11px;
  color: var(--bs-secondary-color);
}

.outbound-comm-recipient {
  font-size: 12px;
  font-weight: 600;
}

.outbound-comm-bubble {
  align-self: stretch;
  margin: 0 -16px;
  padding: 9px 16px;
  border-left: 4px solid;
}

.outbound-comm-content {
  font-size: 14px;
  line-height: 1.5;
  color: var(--bs-body-color);
  white-space: pre-wrap;
  word-wrap: break-word;
}

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

.outbound-comm-result {
  font-size: 11px;
  font-weight: 600;
  margin-left: auto;
}

.outbound-comm-result.result-success {
  color: var(--bs-success);
}

.outbound-comm-result.result-error {
  color: var(--bs-danger);
}

/* Mobile: tighter row padding (16px -> 12px per spec §4.5), mirrors SendCommToolHandler.vue */
@media (max-width: 768px) {
  .outbound-comm-bubble {
    padding: 9px 12px;
    margin: 0 -12px;
  }
}
</style>
