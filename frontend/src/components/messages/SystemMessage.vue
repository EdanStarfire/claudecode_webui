<template>
  <div class="msg-wrapper msg-system">
    <div class="msg-pill" :class="{ 'pill-compaction': isCompactionStatus, 'pill-stderr': isStderr }">
      <span v-if="isStderr" class="pill-icon">&#x26A0;&#xFE0F;</span>
      <span v-else-if="isCompactionStatus" class="pill-icon">&#x1F5DC;&#xFE0F;</span>
      <span class="pill-text">{{ displayContent }}</span>
      <span class="pill-sep">&middot;</span>
      <span class="pill-time">{{ formattedTimestamp }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

// Check if this is a compaction status message
const isCompactionStatus = computed(() => {
  return props.message.metadata?.subtype === 'status' &&
         props.message.metadata?.init_data?.status === 'compacting'
})

// Check if this is an error-class message (stderr or session failure)
const isStderr = computed(() => {
  const subtype = props.message.metadata?.subtype
  return subtype === 'stderr' || subtype === 'session_failed'
})

const displayContent = computed(() => {
  if (isCompactionStatus.value) {
    return 'Context compaction in progress...'
  }
  const content = props.message.content || ''
  // Allow longer display for stderr messages since they often contain important error details
  const maxLen = isStderr.value ? 200 : 80
  if (content.length > maxLen) {
    return content.substring(0, maxLen - 3) + '...'
  }
  return content
})
</script>

<style scoped>
.msg-wrapper {
  padding: 8px 16px;
}

.msg-system {
  display: flex;
  justify-content: center;
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

.pill-stderr {
  background: #fef2f2;
  border-color: #fecaca;
}

.pill-stderr .pill-text {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  color: #991b1b;
  white-space: normal;
  word-break: break-word;
}

.pill-icon {
  font-size: 12px;
}

.pill-text {
  font-size: 12px;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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
</style>
