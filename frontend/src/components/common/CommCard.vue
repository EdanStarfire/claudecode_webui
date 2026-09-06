<template>
  <div
    ref="cardRef"
    class="comm-card"
    :class="{ 'no-body': !hasContent }"
    :style="{ background: gradientBg, borderLeftColor: senderColor.accent }"
  >
    <div class="comm-card-header" @click="handleHeaderClick">
      <div class="comm-meta-row">
        <span class="chevron" :class="{ expanded }">▶</span>
        <span class="direction-arrow" :style="{ color: otherAgentColor.accent }">{{ direction === 'outbound' ? '→' : '←' }}</span>
        <a
          v-if="participantSessionId"
          :href="`#/session/${participantSessionId}`"
          class="participant-name"
          :style="{ color: otherAgentColor.accent }"
          @click.stop
        >{{ participantName }}</a>
        <span v-else class="participant-name" :style="{ color: otherAgentColor.accent }">{{ participantName }}</span>
        <span v-if="commType" class="badge comm-type-badge" :class="commTypeBadgeClass">{{ commType }}</span>
        <span v-if="interruptPriority && interruptPriority !== 'none'" class="badge bg-danger comm-priority-badge">
          {{ interruptPriority }}
        </span>
        <span v-if="attachments.length > 0" class="badge text-bg-info comm-attach-badge">
          📎 {{ attachments.length }}
        </span>
        <span class="comm-card-time">{{ formattedTimestamp }}</span>
        <span v-if="hasResult" class="comm-card-result" :class="isError ? 'result-error' : 'result-success'">
          {{ isError ? '✗ Failed' : '✓ Delivered' }}
        </span>
      </div>
      <div class="comm-summary-row" :class="{ 'is-fallback': summaryInfo.isFallback, 'is-empty-state': summaryInfo.isEmpty }">
        {{ summaryInfo.isEmpty ? summaryInfo.text : `"${summaryInfo.text}"` }}
      </div>
      <div v-if="attachments.length > 0" class="comm-attachments-row" @click.stop>
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
    <div v-if="expanded && hasContent" class="comm-card-body">
      <MarkdownView :content="resolvedBodyContent" :self-agent-id="selfAgentId" />
    </div>
    <div v-if="hasResult && isError" class="comm-card-failure-detail">
      <div class="tool-section">
        <div class="tool-label">Raw Input:</div>
        <div class="tool-code-block">
          <pre class="tool-code">{{ formattedInput }}</pre>
        </div>
      </div>
      <div class="tool-section">
        <div class="tool-label">Error:</div>
        <div class="tool-code-block tool-error">
          <pre class="tool-code">{{ resultContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { getAgentColor, getAssistantRowColor, slugifyAgentName } from '@/composables/useAgentColor'
import { useResourceImages } from '@/composables/useResourceImages'
import { useResourceStore } from '@/stores/resource'
import { useSessionStore } from '@/stores/session'
import { formatTimestamp } from '@/utils/time'
import AttachmentChip from './AttachmentChip.vue'
import MarkdownView from './MarkdownView.vue'

const props = defineProps({
  direction: { type: String, required: true }, // 'outbound' | 'inbound'
  participantName: { type: String, required: true },
  participantSessionId: { type: String, default: null },
  commType: { type: String, default: '' },
  interruptPriority: { type: String, default: null },
  summary: { type: String, default: '' },
  content: { type: String, default: '' },
  // Issue #1843 Q2 (Option A): inbound wants the body pane to render the raw, verbatim
  // message.content actually delivered to the model (zero reconstruction risk), while
  // `content` itself (comm.content) still drives hasContent/AC8-fallback below. Outbound
  // has no such wrapper, so it never needs to set this — it defaults to `content`.
  bodyContent: { type: String, default: null },
  timestamp: { type: [Number, String], default: null },
  attachments: { type: Array, default: () => [] },
  expanded: { type: Boolean, default: false },
  selfAgentId: { type: String, default: null },
  hasResult: { type: Boolean, default: false },
  isError: { type: Boolean, default: false },
  resultContent: { type: String, default: '' },
  formattedInput: { type: String, default: '' },
})

const emit = defineEmits(['toggle-expand'])

const formattedTimestamp = computed(() => formatTimestamp(props.timestamp))

const COMM_TYPE_BADGE_CLASS = {
  task: 'bg-primary',
  question: 'bg-info',
  info: 'bg-secondary',
  report: 'bg-success',
  system: 'bg-dark',
}
const commTypeBadgeClass = computed(() => COMM_TYPE_BADGE_CLASS[props.commType] || 'bg-secondary')

// Issue #1755 gradient formula, preserved exactly: "sender" is always whichever side is
// actually sending (self for outbound, the other agent for inbound); "recipient" is the
// other side. The border-left accent is always the sender's color; the gradient always
// runs sender 0% -> recipient 30%-100%.
const otherAgentColor = computed(() => getAgentColor(slugifyAgentName(props.participantName)))
const selfColor = computed(() => getAssistantRowColor())
const senderColor = computed(() => (props.direction === 'outbound' ? selfColor.value : otherAgentColor.value))
const recipientColor = computed(() => (props.direction === 'outbound' ? otherAgentColor.value : selfColor.value))
const gradientBg = computed(() => `linear-gradient(to right, ${senderColor.value.bg} 0%, ${recipientColor.value.bg} 30%, ${recipientColor.value.bg} 100%)`)

const hasContent = computed(() => !!(props.content && props.content.trim()))
const resolvedBodyContent = computed(() => props.bodyContent ?? props.content)

// AC8: when summary is empty, fall back to a truncated content preview; when both are
// empty, show an explicit empty-state string rather than a blank header.
const summaryInfo = computed(() => {
  const summary = (props.summary || '').trim()
  if (summary) return { text: summary, isFallback: false, isEmpty: false }
  const content = (props.content || '').trim()
  if (content) {
    const truncated = content.length > 50 ? `${content.slice(0, 50)}…` : content
    return { text: truncated, isFallback: true, isEmpty: false }
  }
  return { text: '(no summary)', isFallback: true, isEmpty: true }
})

function handleHeaderClick() {
  if (!hasContent.value) return
  emit('toggle-expand')
}

const resourceStore = useResourceStore()
function openAttachmentPreview(att) {
  if (att.resourceId) {
    resourceStore.openFullViewById(att.resourceId, att.sessionId)
  }
}

// Inline resource image click-to-open. Attached to the always-mounted root (not the
// MarkdownView itself, which is behind v-if="expanded && hasContent") since a card that
// mounts collapsed would otherwise register this listener against a still-null ref —
// click events on <img> inside .comm-card-body still bubble up to this root once expanded.
const cardRef = ref(null)
const sessionStore = useSessionStore()
const currentSessionId = computed(() => sessionStore.currentSessionId)
useResourceImages(cardRef, currentSessionId)
</script>

<style scoped>
.comm-card {
  align-self: stretch;
  margin: 0 -16px;
  padding: 9px 16px;
  border-left: 4px solid;
}

.comm-card-header {
  cursor: pointer;
  user-select: none;
}

.comm-card.no-body .comm-card-header {
  cursor: default;
}

.comm-meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  font-size: 10px;
  color: var(--bs-secondary-color);
  transition: transform 0.15s ease;
  flex-shrink: 0;
}

.chevron.expanded {
  transform: rotate(90deg);
}

.comm-card.no-body .chevron {
  opacity: 0.25;
}

.direction-arrow {
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}

.participant-name {
  font-size: 12px;
  font-weight: 600;
  color: inherit;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
}

.participant-name:hover {
  text-decoration-style: solid;
}

.comm-type-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.comm-priority-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 8px;
  text-transform: uppercase;
}

.comm-attach-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
}

.comm-card-time {
  font-size: 11px;
  color: var(--bs-secondary-color);
  margin-left: auto;
  flex-shrink: 0;
}

.comm-card-result {
  font-size: 11px;
  font-weight: 600;
}

.comm-card-result.result-success {
  color: var(--bs-success);
}

.comm-card-result.result-error {
  color: var(--bs-danger);
}

.comm-summary-row {
  font-size: 13px;
  color: var(--bs-body-color);
  line-height: 1.4;
  padding-left: 18px;
  margin-top: 4px;
  overflow-wrap: anywhere;
}

.comm-summary-row.is-fallback {
  font-style: italic;
  color: var(--bs-secondary-color);
}

.comm-summary-row.is-empty-state {
  color: var(--bs-secondary-color);
}

.comm-attachments-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 18px;
  margin-top: 6px;
}

.comm-card-body {
  font-size: 14px;
  line-height: 1.5;
  color: var(--bs-body-color);
  white-space: pre-wrap;
  word-wrap: break-word;
  padding-left: 18px;
  padding-top: 8px;
  margin-top: 6px;
  border-top: 1px solid var(--bs-border-color-translucent);
}

.comm-card-body :deep(*) {
  margin-bottom: 0;
}

.comm-card-body :deep(p) {
  margin-bottom: 0;
}

.comm-card-body :deep(p + p) {
  margin-top: 0.5em;
}

.comm-card-body :deep(pre) {
  background: var(--bs-tertiary-bg);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.comm-card-body :deep(code) {
  background: var(--bs-tertiary-bg);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.comm-card-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.comm-card-body :deep(ul),
.comm-card-body :deep(ol) {
  padding-left: 1.5rem;
}

.comm-card-body :deep(blockquote) {
  border-left: 3px solid var(--bs-border-color);
  padding-left: 1rem;
  margin-left: 0;
  color: var(--bs-secondary-color);
}

.comm-card-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
}

.comm-card-body :deep(table th),
.comm-card-body :deep(table td) {
  border: 1px solid var(--bs-border-color);
  padding: 0.5rem;
  text-align: left;
}

.comm-card-body :deep(table th) {
  background-color: var(--table-header-bg);
  font-weight: 600;
}

.comm-card-body :deep(table tr:nth-child(even)) {
  background: var(--table-stripe-bg);
}

.comm-card-failure-detail {
  margin-top: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--bs-border-color-translucent);
}

/* Mobile: tighter row padding (16px -> 12px per spec §4.5), mirrors UserMessage.vue */
@media (max-width: 768px) {
  .comm-card {
    padding: 9px 12px;
    margin: 0 -12px;
  }
}
</style>
