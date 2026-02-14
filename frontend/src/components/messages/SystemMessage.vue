<template>
  <div class="msg-wrapper msg-system">
    <div class="msg-pill" :class="{ 'pill-compaction': isCompactionStatus }">
      <span v-if="isCompactionStatus" class="pill-icon">&#x1F5DC;&#xFE0F;</span>
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

const displayContent = computed(() => {
  if (isCompactionStatus.value) {
    return 'Context compaction in progress...'
  }
  const content = props.message.content || ''
  // Truncate long system messages for the pill display
  if (content.length > 80) {
    return content.substring(0, 77) + '...'
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
