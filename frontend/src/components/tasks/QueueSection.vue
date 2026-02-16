<template>
  <div class="queue-section" v-if="items.length > 0 || isPaused">
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

      <!-- Pause / Resume toggle -->
      <button
        v-if="isPaused"
        class="btn btn-sm btn-outline-success ms-auto"
        title="Resume queue"
        @click.stop="resumeQueue"
      >
        Resume
      </button>
      <button
        v-else-if="pendingCount > 0"
        class="btn btn-sm btn-outline-warning ms-auto"
        title="Pause queue"
        @click.stop="doPause"
      >
        Pause
      </button>
    </div>

    <!-- Item List -->
    <div v-if="!collapsed" class="queue-items">
      <div
        v-for="item in items"
        :key="item.queue_id"
        class="queue-item d-flex align-items-center gap-2 px-3 py-1"
        :class="'status-' + item.status"
      >
        <!-- Status icon -->
        <span class="status-icon">{{ statusIcon(item.status) }}</span>

        <!-- Content preview -->
        <span class="item-content flex-grow-1" :title="item.content">
          {{ truncate(item.content, 60) }}
        </span>

        <!-- Action buttons -->
        <button
          v-if="item.status === 'pending'"
          class="btn btn-sm btn-link text-danger p-0"
          title="Cancel"
          @click="doCancel(item.queue_id)"
        >
          &times;
        </button>
        <button
          v-if="item.status === 'sent' || item.status === 'failed'"
          class="btn btn-sm btn-link text-primary p-0"
          title="Re-queue"
          @click="doRequeue(item.queue_id)"
        >
          &#8635;
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useQueueStore } from '@/stores/queue'
import { useSessionStore } from '@/stores/session'

const queueStore = useQueueStore()
const sessionStore = useSessionStore()

const collapsed = ref(false)

const sessionId = computed(() => sessionStore.currentSessionId)

// Fetch queue when session changes
watch(sessionId, (newId) => {
  if (newId) {
    queueStore.fetchQueue(newId)
  }
}, { immediate: true })

const items = computed(() => {
  if (!sessionId.value) return []
  return queueStore.getItems(sessionId.value)
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

function statusIcon(status) {
  switch (status) {
    case 'sent': return '\u2709'     // envelope
    case 'pending': return '\u25CB'  // circle
    case 'failed': return '\u26A0'   // warning
    case 'cancelled': return '\u2715' // X mark
    default: return '\u25CB'
  }
}

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
  max-height: 200px;
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
</style>
