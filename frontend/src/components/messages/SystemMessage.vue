<template>
  <!-- Special handling for compaction status messages -->
  <div v-if="isCompactionStatus" class="message-row message-row-compaction">
    <div class="message-speaker" :title="tooltipText">
      <span class="speaker-label">system</span>
    </div>
    <div class="message-content-column">
      <div class="compaction-status-content">
        <span class="me-2">🗜️</span>
        <strong class="me-2">Context compaction in progress...</strong>
        <small class="text-muted ms-auto">{{ formattedTimestamp }}</small>
      </div>
    </div>
  </div>

  <!-- Regular system message -->
  <div v-else class="message-row message-row-system">
    <div class="message-speaker" :title="tooltipText">
      <span class="speaker-label">system</span>
    </div>
    <div class="message-content-column">
      <span class="system-text">{{ message.content }}</span>
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

const tooltipText = computed(() => {
  return `system\n${formattedTimestamp.value}`
})

// Check if this is a compaction status message
const isCompactionStatus = computed(() => {
  return props.message.metadata?.subtype === 'status' &&
         props.message.metadata?.init_data?.status === 'compacting'
})
</script>

<style scoped>
/* Two-column row layout */
.message-row {
  display: flex;
  width: 100%;
  min-height: 1.2rem;
  padding: 0.2rem 0;
  line-height: 1.2rem;
  margin: 0;
}

.message-row-system {
  background-color: #F5F5F5; /* Light grey */
}

.message-row-compaction {
  background-color: #fffbea; /* Light yellow - matches template field highlight */
  padding: 0.2rem 0;
  margin: 0;
}

/* Compaction status content */
.compaction-status-content {
  display: flex;
  align-items: center;
  font-size: 0.9rem;
}

/* Speaker column (left) */
.message-speaker {
  width: 8em;
  padding: 0 1rem;
  flex-shrink: 0;
  text-align: right;
  cursor: help;
  font-weight: 500;
  color: #495057;
}

.speaker-label {
  font-size: 0.9rem;
  text-transform: lowercase;
}

/* Content column (right) */
.message-content-column {
  flex: 1;
  padding: 0 1rem 0 0.5rem;
  overflow-wrap: break-word;
}

.system-text {
  font-size: 0.9rem;
  color: #6c757d;
}

/* Mobile responsive: stack speaker above content */
@media (max-width: 768px) {
  .message-row {
    flex-direction: column;
  }

  .message-speaker {
    width: 100%;
    text-align: left;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  }

  .message-content-column {
    padding: 0.5rem 1rem;
  }
}
</style>
