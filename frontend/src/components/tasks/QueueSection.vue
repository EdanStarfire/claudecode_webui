<template>
  <div class="queue-section" v-if="allItems.length > 0 || isPaused" :style="sectionStyle">
    <!-- Section Header -->
    <div
      class="queue-header d-flex align-items-center gap-2 px-3 py-2"
      @click="collapsed = !collapsed"
    >
      <svg
        class="chevron-icon"
        :class="{ expanded: !collapsed }"
        width="12"
        height="12"
        viewBox="0 0 12 12"
      >
        <path
          d="M4.5 2L8.5 6L4.5 10"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          fill="none"
        />
      </svg>
      <span class="section-title">Message Queue</span>
      <span v-if="pendingCount > 0" class="badge bg-primary">{{ pendingCount }}</span>

      <div class="ms-auto d-flex align-items-center gap-1">
        <!-- Show history toggle -->
        <button
          v-if="terminalCount > 0"
          class="btn btn-sm btn-link text-muted p-0 history-toggle"
          :title="showHistory ? 'Hide history' : `Show history (${terminalCount})`"
          @click.stop="showHistory = !showHistory"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8.515 1.019A7 7 0 0 0 8 1V0a8 8 0 0 1 .589.022l-.074.997zm2.004.45a7.003 7.003 0 0 0-.985-.299l.219-.976c.383.086.76.2 1.126.342l-.36.933zm1.37.71a7.01 7.01 0 0 0-.439-.27l.493-.87a8.025 8.025 0 0 1 .979.654l-.615.789a6.996 6.996 0 0 0-.418-.302zm1.834 1.79a6.99 6.99 0 0 0-.653-.796l.724-.69c.27.285.52.59.747.91l-.818.576zm.744 1.352a7.08 7.08 0 0 0-.214-.468l.893-.45a7.976 7.976 0 0 1 .45 1.088l-.95.313a7.023 7.023 0 0 0-.179-.483zm.53 2.507a6.991 6.991 0 0 0-.1-1.025l.985-.17c.067.386.106.778.116 1.17l-1 .025zm-.131 1.538c.033-.17.06-.339.081-.51l.993.123a7.957 7.957 0 0 1-.23 1.155l-.964-.267c.046-.165.086-.332.12-.501zm-.952 2.379c.184-.29.346-.594.486-.908l.914.405c-.16.36-.345.706-.555 1.038l-.845-.535zm-.964 1.205c.122-.122.239-.248.35-.378l.758.653a8.073 8.073 0 0 1-.401.432l-.707-.707z"/>
            <path d="M8 1a7 7 0 1 0 4.95 11.95l.707.707A8.001 8.001 0 1 1 8 0v1z"/>
            <path d="M7.5 3a.5.5 0 0 1 .5.5v5.21l3.248 1.856a.5.5 0 0 1-.496.868l-3.5-2A.5.5 0 0 1 7 9V3.5a.5.5 0 0 1 .5-.5z"/>
          </svg>
          <span class="history-count">{{ terminalCount }}</span>
        </button>

        <!-- Pause / Resume toggle -->
        <button
          v-if="isPaused"
          class="btn btn-sm btn-outline-success"
          title="Resume queue"
          @click.stop="resumeQueue"
        >
          Resume
        </button>
        <button
          v-else-if="pendingCount > 0"
          class="btn btn-sm btn-outline-warning"
          title="Pause queue"
          @click.stop="doPause"
        >
          Pause
        </button>
      </div>
    </div>

    <!-- Item List -->
    <div v-if="!collapsed" class="queue-items">
      <div
        v-for="item in visibleItems"
        :key="item.queue_id"
        class="queue-item d-flex align-items-center gap-2 px-3 py-1"
        :class="'status-' + item.status"
      >
        <!-- Status icon -->
        <span class="status-icon" :class="'icon-' + item.status" :title="item.status">
          <!-- Sent: checkmark circle -->
          <svg v-if="item.status === 'sent'" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0zm3.78 4.97a.75.75 0 0 0-1.06 0L7 8.69 5.28 6.97a.75.75 0 0 0-1.06 1.06l2.25 2.25a.75.75 0 0 0 1.06 0l4.25-4.25a.75.75 0 0 0 0-1.06z"/>
          </svg>
          <!-- Pending: clock -->
          <svg v-else-if="item.status === 'pending'" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm.5 4.5v3.793l2.354 2.353a.5.5 0 0 1-.708.708l-2.5-2.5A.5.5 0 0 1 7.5 8.5V4.5a.5.5 0 0 1 1 0z"/>
          </svg>
          <!-- Failed: exclamation triangle -->
          <svg v-else-if="item.status === 'failed'" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/>
          </svg>
          <!-- Cancelled: X circle -->
          <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0zm3.354 4.646a.5.5 0 0 1 0 .708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 1 1 .708-.708L8 7.293l2.646-2.647a.5.5 0 0 1 .708 0z"/>
          </svg>
        </span>

        <!-- Content preview -->
        <span class="item-content flex-grow-1" :title="item.content">
          {{ truncate(item.content, 60) }}
        </span>

        <!-- Action buttons -->
        <button
          v-if="item.status === 'pending'"
          class="btn btn-sm btn-link text-danger p-0 action-btn"
          title="Cancel"
          @click="doCancel(item.queue_id)"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4.646 4.646a.5.5 0 0 1 .708 0L8 7.293l2.646-2.647a.5.5 0 0 1 .708.708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 0 1 0-.708z"/>
          </svg>
        </button>
        <button
          v-if="item.status === 'sent' || item.status === 'failed'"
          class="btn btn-sm btn-link text-primary p-0 action-btn"
          title="Re-queue"
          @click="doRequeue(item.queue_id)"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M11.534 7h3.932a.25.25 0 0 1 .192.41l-1.966 2.36a.25.25 0 0 1-.384 0l-1.966-2.36a.25.25 0 0 1 .192-.41zm-11 2h3.932a.25.25 0 0 0 .192-.41L2.692 6.23a.25.25 0 0 0-.384 0L.342 8.59A.25.25 0 0 0 .534 9z"/>
            <path fill-rule="evenodd" d="M8 3c-1.552 0-2.94.707-3.857 1.818a.5.5 0 1 1-.771-.636A6.002 6.002 0 0 1 13.917 7H12.9A5.002 5.002 0 0 0 8 3zM3.1 9a5.002 5.002 0 0 0 8.757 2.182.5.5 0 1 1 .771.636A6.002 6.002 0 0 1 2.083 9H3.1z"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useQueueStore } from '@/stores/queue'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  height: {
    type: Number,
    default: 200
  }
})

const queueStore = useQueueStore()
const sessionStore = useSessionStore()

const collapsed = ref(false)

const sectionStyle = computed(() => {
  if (collapsed.value) return {}
  return {
    flexShrink: 0,
    height: props.height + 'px',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden'
  }
})
const showHistory = ref(false)

const sessionId = computed(() => sessionStore.currentSessionId)

// Fetch queue when session changes
watch(sessionId, (newId) => {
  if (newId) {
    queueStore.fetchQueue(newId)
  }
}, { immediate: true })

const allItems = computed(() => {
  if (!sessionId.value) return []
  return queueStore.getItems(sessionId.value)
})

const terminalCount = computed(() => {
  return allItems.value.filter(i => i.status === 'sent' || i.status === 'failed' || i.status === 'cancelled').length
})

const visibleItems = computed(() => {
  if (showHistory.value) return allItems.value
  return allItems.value.filter(i => i.status === 'pending')
})

const pendingCount = computed(() => {
  if (!sessionId.value) return 0
  return queueStore.getPendingCount(sessionId.value)
})

const isPaused = computed(() => {
  if (!sessionId.value) return false
  // Check store state first, fall back to session data
  const storePaused = queueStore.isPaused(sessionId.value)
  if (storePaused) return true
  const session = sessionStore.sessions.get(sessionId.value)
  return session?.queue_paused || false
})

function truncate(text, maxLen) {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
}

async function doCancel(queueId) {
  if (!sessionId.value) return
  await queueStore.cancelItem(sessionId.value, queueId)
}

async function doRequeue(queueId) {
  if (!sessionId.value) return
  await queueStore.requeueItem(sessionId.value, queueId)
}

async function doPause() {
  if (!sessionId.value) return
  await queueStore.pauseQueue(sessionId.value, true)
}

async function resumeQueue() {
  if (!sessionId.value) return
  await queueStore.pauseQueue(sessionId.value, false)
}
</script>

<style scoped>
.queue-section {
  border-top: 1px solid #dee2e6;
}

.queue-header {
  cursor: pointer;
  user-select: none;
  font-size: 0.85rem;
  font-weight: 600;
  color: #495057;
}

.queue-header:hover {
  background-color: #f8f9fa;
}

.section-title {
  flex-shrink: 0;
}

.chevron-icon {
  color: #6c757d;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.chevron-icon.expanded {
  transform: rotate(90deg);
}

.queue-items {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.queue-item {
  font-size: 0.85rem;
  border-bottom: 1px solid #f0f0f0;
}

.queue-item:last-child {
  border-bottom: none;
}

.status-icon {
  flex-shrink: 0;
  width: 1.25rem;
  text-align: center;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.status-icon.icon-sent {
  color: #198754;
}

.status-icon.icon-pending {
  color: #0d6efd;
}

.status-icon.icon-failed {
  color: #dc3545;
}

.status-icon.icon-cancelled {
  color: #6c757d;
}

.action-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  line-height: 1;
}

.item-content {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #495057;
}

.status-sent .item-content {
  color: #6c757d;
}

.status-failed .item-content {
  color: #dc3545;
}

.status-cancelled {
  opacity: 0.5;
}

.btn-link {
  font-size: 1rem;
  line-height: 1;
  text-decoration: none;
}

.history-toggle {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 0.75rem;
}

.history-count {
  font-size: 0.7rem;
}
</style>
