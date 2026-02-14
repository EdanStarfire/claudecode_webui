<template>
  <div class="msg-wrapper msg-system">
    <div class="msg-pill pill-compaction" @click="toggleCollapse" style="cursor: pointer;">
      <span class="pill-icon">&#x1F5DC;&#xFE0F;</span>
      <span class="pill-text">Context Compaction Event</span>
      <span class="pill-badge">{{ formatTokenCount(preTokens) }}</span>
      <span class="pill-sep">&middot;</span>
      <span class="pill-time">{{ formattedTimestamp }}</span>
      <span class="pill-chevron" :class="{ expanded: isOpen }">&#x25B6;</span>
    </div>

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

      <!-- Continuation Message -->
      <div class="continuation-section">
        <div class="detail-label">Continuation Message</div>
        <pre class="continuation-text">{{ continuationContent }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  messages: {
    type: Array,
    required: true,
    validator: (messages) => messages.length === 4 || messages.length === 5
  }
})

const isOpen = ref(false)

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
