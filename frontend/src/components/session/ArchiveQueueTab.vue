<template>
  <div class="archive-queue-tab">
    <div v-if="archiveTabsStore.isLoadingTabs(sessionId)" class="empty-placeholder">
      <span class="icon">⏳</span>Loading…
    </div>
    <div v-else-if="items.length === 0" class="empty-placeholder">
      <span class="icon">🗒️</span>No queued messages were recorded for this session.
    </div>
    <template v-else>
      <div v-for="item in items" :key="item.queue_id" class="queue-row">
        <span class="ts">{{ formatQueueTimestamp(item.created_at) }}</span>
        <span class="status">
          <span class="status-pill" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span>
        </span>
        <span class="content">{{ item.content }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { formatQueueTimestamp } from '@/utils/time'

const props = defineProps({
  sessionId: { type: String, required: true },
  archiveId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const items = computed(() => archiveTabsStore.reconstructedQueueForSession(props.sessionId))

const STATUS_CLASS = {
  sent: 'status-delivered',
  pending: 'status-pending',
  cancelled: 'status-cancelled',
  failed: 'status-cancelled',
}
const STATUS_LABEL = {
  sent: 'delivered',
  pending: 'pending',
  cancelled: 'cancelled',
  failed: 'failed',
}

function statusClass(status) {
  return STATUS_CLASS[status] || 'status-pending'
}
function statusLabel(status) {
  return STATUS_LABEL[status] || status
}
</script>

<style scoped>
.empty-placeholder {
  text-align: center;
  color: var(--bs-secondary-color);
  font-style: italic;
  font-size: 12px;
  padding: 36px 10px;
}
.empty-placeholder .icon {
  font-size: 22px;
  display: block;
  margin-bottom: 6px;
  opacity: 0.6;
}

.queue-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--bs-border-color);
  font-size: 12.5px;
}
.queue-row:hover {
  background: var(--table-stripe-bg);
}
.queue-row .ts {
  font-family: monospace;
  color: var(--bs-secondary-color);
  flex: 0 0 150px;
}
.queue-row .status {
  flex: 0 0 90px;
}
.queue-row .content {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}
.status-pill {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.status-delivered {
  background: #d1fae5;
  color: #047857;
}
.status-cancelled {
  background: #fee2e2;
  color: #b91c1c;
}
.status-pending {
  background: #fef3c7;
  color: #92400e;
}
[data-bs-theme="dark"] .status-delivered {
  background: rgba(16, 185, 129, 0.18);
  color: #6ee7b7;
}
[data-bs-theme="dark"] .status-cancelled {
  background: rgba(239, 68, 68, 0.18);
  color: #fca5a5;
}
[data-bs-theme="dark"] .status-pending {
  background: rgba(245, 158, 11, 0.18);
  color: #fcd34d;
}
</style>
