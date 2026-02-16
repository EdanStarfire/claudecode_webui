<template>
  <div class="schedule-panel">
    <!-- Toolbar -->
    <div class="schedule-toolbar">
      <select v-model="statusFilter" class="filter-select">
        <option value="">All</option>
        <option value="active">Active</option>
        <option value="paused">Paused</option>
        <option value="cancelled">Cancelled</option>
      </select>
      <button class="create-btn" @click="showCreate = true" title="Create Schedule">+ New</button>
    </div>

    <!-- Empty state -->
    <div v-if="filteredSchedules.length === 0" class="empty-placeholder">
      <span v-if="!projectId">Select a session to view schedules</span>
      <span v-else>No schedules found. Create one to automate recurring tasks.</span>
    </div>

    <!-- Schedule list -->
    <div v-else class="schedule-list">
      <ScheduleItem
        v-for="schedule in filteredSchedules"
        :key="schedule.schedule_id"
        :schedule="schedule"
        :legion-id="projectId"
      />
    </div>

    <!-- Create modal -->
    <ScheduleCreateModal
      v-if="showCreate"
      :legion-id="projectId"
      @close="showCreate = false"
      @created="showCreate = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useWebSocketStore } from '@/stores/websocket'
import ScheduleItem from './ScheduleItem.vue'
import ScheduleCreateModal from './ScheduleCreateModal.vue'

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()
const wsStore = useWebSocketStore()

const showCreate = ref(false)
const statusFilter = ref('')

const projectId = computed(() => sessionStore.currentSession?.project_id || null)

const filteredSchedules = computed(() => {
  if (!projectId.value) return []
  const all = scheduleStore.getSchedules(projectId.value)
  if (!statusFilter.value) return all
  return all.filter(s => s.status === statusFilter.value)
})

// Load schedules when project changes
watch(projectId, (newId) => {
  if (newId) {
    scheduleStore.loadSchedules(newId)
  }
}, { immediate: true })

// Reload when legion WebSocket reconnects
watch(() => wsStore.legionConnected, (connected) => {
  if (connected && projectId.value) {
    scheduleStore.loadSchedules(projectId.value)
  }
})
</script>

<style scoped>
.schedule-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.schedule-toolbar {
  display: flex;
  gap: 6px;
  padding: 8px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.filter-select {
  flex: 1;
  font-size: 11px;
  padding: 4px 6px;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  background: #fff;
  color: #334155;
}

.create-btn {
  font-size: 11px;
  padding: 4px 10px;
  border: 1px solid #6366f1;
  border-radius: 4px;
  background: #6366f1;
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
}

.create-btn:hover {
  background: #4f46e5;
}

.empty-placeholder {
  text-align: center;
  padding: 32px 16px;
  color: #94a3b8;
  font-size: 12px;
  font-style: italic;
}

.schedule-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
}
</style>
