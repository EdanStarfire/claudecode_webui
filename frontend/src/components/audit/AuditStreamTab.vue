<template>
  <div class="audit-stream-tab d-flex flex-column h-100">
    <!-- Stream controls -->
    <div class="d-flex align-items-center gap-2 px-3 py-2 border-bottom">
      <button
        class="btn btn-sm"
        :class="auditStore.liveStreamEnabled ? 'btn-success' : 'btn-outline-secondary'"
        @click="auditStore.toggleLiveStream()"
      >
        <span v-if="auditStore.liveStreamEnabled">● Live</span>
        <span v-else>○ Paused</span>
      </button>
      <span class="text-muted small ms-1">
        <span v-if="auditStore.pollStatus === 'polling'" class="spinner-grow spinner-grow-sm me-1"></span>
        {{ statusLabel }}
      </span>
      <div class="form-check form-switch ms-auto mb-0">
        <input
          id="showAllTools"
          v-model="showAllTools"
          class="form-check-input"
          type="checkbox"
          role="switch"
        >
        <label class="form-check-label small" for="showAllTools">Show all tool calls</label>
      </div>
    </div>

    <!-- Event list -->
    <div class="stream-list flex-grow-1 overflow-auto p-2">
      <div v-if="auditStore.streamLoading && !filteredEvents.length" class="text-center py-4 text-muted">
        <div class="spinner-border mb-2"></div>
        <div>Loading…</div>
      </div>
      <div v-else-if="!filteredEvents.length" class="text-center text-muted py-4">
        No events. Waiting for activity…
      </div>
      <EventRow v-for="ev in filteredEvents" :key="ev.id" :event="ev" class="mb-1" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuditStore } from '../../stores/audit.js'
import EventRow from './EventRow.vue'

const auditStore = useAuditStore()
const showAllTools = ref(false)

const ROUTINE_TOOL_STATUSES = new Set(['ok', 'started'])

const filteredEvents = computed(() => {
  if (showAllTools.value) return auditStore.events
  return auditStore.events.filter(ev => {
    if (ev.event_type === 'tool_call') {
      return ev.status && !ROUTINE_TOOL_STATUSES.has(ev.status)
    }
    return true
  })
})

const statusLabel = computed(() => {
  if (auditStore.pollStatus === 'polling') return 'Connected'
  if (auditStore.pollStatus === 'error') return 'Reconnecting…'
  return 'Paused'
})

onMounted(async () => {
  await auditStore.fetchEvents()
  if (auditStore.liveStreamEnabled) {
    auditStore.startLivePoll()
  }
})

onUnmounted(() => {
  auditStore.stopLivePoll()
})
</script>

<style scoped>
.stream-list { min-height: 0; }
</style>
