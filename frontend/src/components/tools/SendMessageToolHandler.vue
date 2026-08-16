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
        <span v-if="senderName" class="outbound-comm-sender" :style="{ color: senderColor.accent }">
          {{ senderName }}
        </span>
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
import { useMessageStore } from '@/stores/message'
import MarkdownView from '@/components/common/MarkdownView.vue'

// Issue #1746 (stage: subagents) follow-up (user feedback): the SDK's own `SendMessage` tool
// (used to resume a background subagent, or for a subagent to report back to main — distinct
// from Legion's `mcp__legion__send_comm`) fell through to BaseToolHandler's plain <pre> blocks
// with no background at all when it appeared in the MAIN session's own timeline (not nested in
// a subagent's transcript, where SubagentAnchorRow/MessageList already handle it). Reuses the
// same sender->recipient gradient bubble mechanics as SendCommToolHandler.vue, trimmed to
// SendMessage's actual input shape (to/message/summary — no comm_type/interrupt_priority/
// attachments, which are Legion-specific fields SendMessage doesn't have).
//
// Issue #1746 follow-up (user feedback): direction genuinely varies — main->agent (resuming a
// subagent), agent->main (reporting back), and agent->agent (sending to a different subagent by
// name) all use this same tool. The "sender is always self" assumption only holds when THIS
// call has no parent_tool_use_id (main's own top-level turn); a call nested inside a subagent's
// own transcript (parent_tool_use_id set) was actually made BY that subagent, not by main.
const props = defineProps({
  toolCall: { type: Object, required: true }
})

const messageStore = useMessageStore()

const recipientName = computed(() => props.toolCall.input?.to || props.toolCall.input?.recipient || 'unknown')
const contentForRender = computed(() => props.toolCall.input?.message || props.toolCall.input?.summary || props.toolCall.input?.content || '')
const formattedTimestamp = computed(() => formatTimestamp(props.toolCall.timestamp))

// 'main' is a well-known special identity (the orchestrating session itself), not just another
// named agent — resolves to the same fixed "self" color/treatment regardless of which side of
// the gradient it's on, matching the #1755 convention comm rows already follow.
function identityColor(name) {
  if (!name) return getAgentColor(null)
  if (name.toLowerCase() === 'main') return getAssistantRowColor()
  return getAgentColor(slugifyAgentName(name))
}

const recipientColor = computed(() => identityColor(recipientName.value))

// The subagent leg that owns this call, if it's nested inside one — parent_tool_use_id stays
// pinned to the very FIRST leg's own launch tool_use_id for ALL of a subagent's activity
// (original run and every resume), so this resolves correctly regardless of which leg is
// currently active. Colored by task_id (not by name) to match the SAME technique the gutter/
// main-timeline "pushed" signal already use for this exact subagent, so a given agent's color
// is consistent across every surface it's shown on.
const senderTaskId = computed(() => {
  const parentId = props.toolCall.parent_tool_use_id
  return parentId ? messageStore.getTaskIdForLaunchToolUse(parentId) : null
})
const senderName = computed(() => {
  if (!senderTaskId.value) return null // main's own top-level call — no "from" label needed
  // getTaskLegEntry() returns the plain frontend mirror {task_id, session_id, legs} — unlike
  // the backend's TaskLegEntry dataclass, it has no top-level `.description` computed property;
  // only each individual leg does. Use the latest leg's, matching the "latest_leg" convention
  // used elsewhere (e.g. task_registry.py's TaskLegEntry.description).
  const legs = messageStore.getTaskLegEntry(senderTaskId.value)?.legs
  return legs?.[legs.length - 1]?.description || 'Agent'
})
const senderColor = computed(() => senderTaskId.value
  ? getAgentColor(slugifyAgentName(senderTaskId.value))
  : getAssistantRowColor())

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

.outbound-comm-sender,
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
