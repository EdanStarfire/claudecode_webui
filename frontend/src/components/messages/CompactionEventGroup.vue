<template>
  <div class="msg-wrapper msg-system">
    <div class="msg-pill pill-compaction" @click="toggleCollapse" style="cursor: pointer;">
      <span class="pill-icon">&#x1F5DC;&#xFE0F;</span>
      <span class="pill-text">Context Compaction Event</span>
      <span class="pill-badge">{{ formatTokenCount(preTokens) }}</span>
      <span class="pill-sep">&middot;</span>
      <span class="pill-time">{{ formattedTimestamp }}</span>
      <!-- Issue #1628: Summary sub-pill (hidden when disabled) -->
      <span
        v-if="summaryStatus !== 'disabled'"
        class="pill-summary-status"
        :class="summaryStatus"
        @click.stop="summaryStatus === 'ready' ? openSummary($event) : undefined"
      >
        <span v-if="summaryStatus === 'pending'" class="spinner"></span>
        <span v-if="summaryStatus === 'pending'"> Distilling&hellip;</span>
        <span v-else-if="summaryStatus === 'ready'">&#x1F4CB; View summary</span>
        <span v-else-if="summaryStatus === 'failed'">&#x26A0; Summary unavailable</span>
      </span>
      <span class="pill-chevron" :class="{ expanded: isOpen }">&#x25B6;</span>
    </div>

    <!-- PreCompact hook indicators (Issue #1350) -->
    <HookPillStrip v-if="preCompactHooks.length" :hooks="preCompactHooks" align="center" />

    <!-- Expanded detail -->
    <div v-if="isOpen" class="compaction-detail">
      <!-- Compaction Metadata -->
      <div class="detail-section">
        <div class="detail-label">Trigger</div>
        <div class="detail-value">{{ compactMetadata.trigger || 'auto' }}</div>
      </div>
      <div class="detail-section">
        <div class="detail-label">Pre-compaction</div>
        <div class="detail-value">{{ formatTokenCount(preTokens) }}</div>
      </div>
      <div v-if="compactMetadata.post_tokens" class="detail-section">
        <div class="detail-label">Post-compaction</div>
        <div class="detail-value">{{ formatTokenCount(compactMetadata.post_tokens) }}</div>
      </div>

      <!-- Issue #1628: Pre-compaction summary section -->
      <div v-if="summaryStatus !== 'disabled'" class="summary-link-section">
        <div class="detail-label">Pre-compaction summary</div>
        <a
          v-if="summaryStatus === 'ready'"
          class="summary-link"
          @click.stop="openSummary($event)"
        >&#x1F4CB; View distilled summary</a>
        <div v-else-if="summaryStatus === 'pending'" class="summary-empty">
          <span class="spinner"></span>&nbsp;Distillation in progress — will appear here when ready.
        </div>
        <div v-else-if="summaryStatus === 'failed'" class="summary-failed">
          Distillation failed; see server logs. Session continues normally.
        </div>
      </div>

      <!-- Continuation Message -->
      <div class="continuation-section">
        <div class="detail-label">Continuation Message</div>
        <pre class="continuation-text">{{ continuationContent }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'
import { useResourceStore } from '@/stores/resource'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import HookPillStrip from './HookPillStrip.vue'

const props = defineProps({
  messages: {
    type: Array,
    required: true,
    validator: (messages) => messages.length === 4 || messages.length === 5
  },
  compactionGroupIndex: {
    type: Number,
    default: -1,
  },
})

const resourceStore = useResourceStore()
const sessionStore = useSessionStore()
const messageStore = useMessageStore()

// Injected per-instance session id (provided by SessionView via MessageList).
const viewSessionId = inject('viewSessionId', ref(null))

// Issue #1350: PreCompact hooks attached to this compaction group
const preCompactHooks = computed(() => {
  const sid = viewSessionId.value
  if (!sid || props.compactionGroupIndex < 0) return []
  return messageStore.hooksForCompaction(sid, props.compactionGroupIndex)
})

const isOpen = ref(false)
// Set to true after the 30s pending→failed window elapses with no resource.
const isFailed = ref(false)
let _failTimer = null

function toggleCollapse() {
  isOpen.value = !isOpen.value
}

// Determine if this is a 5-message pattern (with init)
const hasInitMessage = computed(() => {
  return props.messages.length === 5 &&
    props.messages[2]?.type === 'system' &&
    props.messages[2]?.metadata?.subtype === 'init'
})

// Extract compact metadata from the boundary message
const boundaryMessage = computed(() => {
  return hasInitMessage.value ? props.messages[3] : props.messages[2]
})

const compactMetadata = computed(() => {
  return boundaryMessage.value?.metadata?.init_data?.compact_metadata || {}
})

const preTokens = computed(() => {
  return compactMetadata.value.pre_tokens || 0
})

// Extract continuation content from the user message
const continuationMessage = computed(() => {
  return hasInitMessage.value ? props.messages[4] : props.messages[3]
})

const continuationContent = computed(() => {
  return continuationMessage.value?.content || ''
})

// Use timestamp from the start message (message 1)
const formattedTimestamp = computed(() => {
  const timestamp = props.messages[0]?.timestamp
  return timestamp ? formatTimestamp(timestamp) : ''
})

// Issue #1628: Timestamp of the compact_boundary message, used for resource correlation.
const boundaryTimestamp = computed(() => {
  return boundaryMessage.value?.timestamp ?? null
})

// Issue #1628: Find the distilled summary resource for this compaction boundary.
// The backend registers it with description="compaction:{int(boundary_ts)}".
const summaryResource = computed(() => {
  const sessionId = viewSessionId.value || sessionStore.currentSessionId
  const bts = boundaryTimestamp.value
  if (!sessionId || bts == null) return null
  const target = `compaction:${Math.floor(bts)}`
  return resourceStore.resourcesBySession.get(sessionId)?.find(r => r.description === target) ?? null
})

// Issue #1628: Whether distillation is disabled in the session's effective config.
const distillationDisabled = computed(() => {
  const sessionId = viewSessionId.value || sessionStore.currentSessionId
  const eff = sessionStore.effectiveConfigBySession?.get(sessionId)
  // If effective config is not yet loaded, assume enabled (default true).
  if (eff == null) return false
  return eff.history_distillation_enabled === false
})

// Issue #1628: State machine for summary affordance display.
// 'disabled' → distillation off, render nothing
// 'ready'    → summary resource is present, show green pill + view link
// 'pending'  → no resource yet and within 30s window, show spinner
// 'failed'   → no resource after 30s, show muted indicator
const summaryStatus = computed(() => {
  if (distillationDisabled.value) return 'disabled'
  if (summaryResource.value) return 'ready'
  if (isFailed.value) return 'failed'
  return 'pending'
})

// Issue #1628: Open the summary in ResourceFullView by resource ID.
function openSummary(event) {
  if (event) event.stopPropagation()
  const res = summaryResource.value
  if (!res) return
  const sessionId = viewSessionId.value || sessionStore.currentSessionId
  resourceStore.openFullViewById(res.resource_id, sessionId)
}

// Issue #1628: Start the 30s pending→failed timer on mount if no resource exists yet.
onMounted(() => {
  if (summaryResource.value || distillationDisabled.value) return
  const bts = boundaryTimestamp.value
  if (bts == null) {
    isFailed.value = true
    return
  }
  const elapsed = Date.now() / 1000 - bts
  if (elapsed >= 30) {
    isFailed.value = true
  } else {
    const remaining = (30 - elapsed) * 1000
    _failTimer = setTimeout(() => { isFailed.value = true }, remaining)
  }
})

onUnmounted(() => {
  if (_failTimer != null) {
    clearTimeout(_failTimer)
    _failTimer = null
  }
})

// Format token count with commas
function formatTokenCount(tokens) {
  if (!tokens) return '0 tokens'
  return `${tokens.toLocaleString()} tokens`
}
</script>

<style scoped>
.msg-wrapper {
  padding: 8px 16px;
}

.msg-system {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.msg-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  max-width: 80%;
}

.pill-compaction {
  background: #fffbea;
  border-color: #fde68a;
}

.pill-icon {
  font-size: 12px;
}

.pill-text {
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
}

.pill-badge {
  font-size: 10px;
  font-weight: 600;
  background: #fbbf24;
  color: #78350f;
  padding: 1px 6px;
  border-radius: 8px;
}

.pill-sep {
  font-size: 12px;
  color: #94a3b8;
}

.pill-time {
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
}

.pill-chevron {
  font-size: 8px;
  color: #94a3b8;
  transition: transform 0.2s;
}

.pill-chevron.expanded {
  transform: rotate(90deg);
}

/* Issue #1628: Summary sub-pill */
.pill-summary-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 600;
}

.pill-summary-status.ready {
  background: #16a34a;
  color: white;
  cursor: pointer;
}

.pill-summary-status.ready:hover {
  background: #15803d;
}

.pill-summary-status.pending {
  background: #e2e8f0;
  color: #475569;
}

.pill-summary-status.failed {
  background: #fecaca;
  color: #991b1b;
}

/* Spinner */
.spinner {
  display: inline-block;
  width: 8px;
  height: 8px;
  border: 1.5px solid rgba(71, 85, 105, 0.3);
  border-top-color: #475569;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Expanded detail panel */
.compaction-detail {
  margin-top: 8px;
  padding: 12px 16px;
  background: #fffbea;
  border: 1px solid #fde68a;
  border-radius: 8px;
  max-width: 600px;
  width: 100%;
  font-size: 13px;
}

.detail-section {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px solid rgba(253, 230, 138, 0.5);
}

.detail-section:last-of-type {
  border-bottom: none;
}

.detail-label {
  font-weight: 600;
  color: #78350f;
  font-size: 12px;
}

.detail-value {
  color: #92400e;
  font-size: 12px;
}

/* Issue #1628: Summary link section */
.summary-link-section {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(253, 230, 138, 0.5);
}

.summary-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  margin-top: 4px;
  background: #fef3c7;
  border: 1px solid #d97706;
  border-radius: 6px;
  color: #78350f;
  text-decoration: none;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.summary-link:hover {
  background: #fde68a;
}

.summary-empty,
.summary-failed {
  margin-top: 4px;
  padding: 8px 12px;
  background: white;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
  color: #64748b;
  font-size: 12px;
}

.summary-failed {
  border-color: #fca5a5;
  background: #fef2f2;
  color: #991b1b;
}

.continuation-section {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(253, 230, 138, 0.5);
}

.continuation-text {
  margin-top: 4px;
  padding: 8px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  max-height: 300px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-size: 12px;
  line-height: 1.4;
  font-family: 'Courier New', monospace;
}
</style>
