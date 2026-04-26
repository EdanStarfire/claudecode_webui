<template>
  <div class="turn-card card mb-2" :class="{ 'border-primary': turn.in_progress }">
    <div class="card-header d-flex align-items-center gap-2 py-2 px-3" role="button" @click="toggle">
      <!-- Status indicator -->
      <span class="turn-status-dot flex-shrink-0" :class="statusDotClass">
        <span v-if="turn.in_progress" class="spinner-grow spinner-grow-sm" role="status"></span>
      </span>
      <!-- Session name + turn id -->
      <div class="flex-grow-1 min-w-0">
        <span class="fw-semibold">{{ turn.session_name || turn.session_id?.slice(0, 8) }}</span>
        <span class="text-muted small ms-2">{{ turnLabel }}</span>
      </div>
      <!-- Counts -->
      <div class="d-flex gap-2 flex-shrink-0 text-muted small align-items-center">
        <span v-if="turn.tool_count" title="tool calls">🔧 {{ turn.tool_count }}</span>
        <span v-if="turn.error_count" class="text-danger" title="errors">✕ {{ turn.error_count }}</span>
        <span v-if="turn.denial_count" class="text-warning" title="denied">⊘ {{ turn.denial_count }}</span>
        <span v-if="turn.permission_count" class="text-info" title="permissions">🔑 {{ turn.permission_count }}</span>
        <span class="text-muted" style="font-size:0.7rem">{{ formatTime(turn.started_at) }}</span>
        <span class="chevron">{{ expanded ? '▲' : '▼' }}</span>
      </div>
    </div>

    <!-- Sparkline -->
    <div v-if="turn.sparkline?.length" class="sparkline px-3 py-1 d-flex gap-1 flex-wrap">
      <span
        v-for="(s, i) in turn.sparkline"
        :key="i"
        class="sparkline-dot"
        :class="sparklineClass(s)"
        :title="s"
      ></span>
    </div>

    <!-- Expanded event list -->
    <div v-if="expanded" class="card-body p-0">
      <div v-if="loadingEvents" class="text-center py-3 text-muted small">
        <div class="spinner-border spinner-border-sm me-2"></div>Loading events…
      </div>
      <div v-else-if="turnEvents.length === 0" class="text-muted text-center py-2 small">No events</div>
      <EventRow v-for="ev in turnEvents" :key="ev.id" :event="ev" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAuditStore } from '../../stores/audit.js'
import EventRow from './EventRow.vue'

const props = defineProps({
  turn: { type: Object, required: true },
})

const auditStore = useAuditStore()
const expanded = ref(false)

const turnId = computed(() => props.turn.turn_id)
const turnEvents = computed(() => auditStore.expandedTurnEvents.get(turnId.value) || [])
const loadingEvents = computed(() => auditStore.expandedTurnLoading.has(turnId.value))

const turnLabel = computed(() => {
  const parts = (props.turn.turn_id || '').split(':')
  return `turn #${parts[parts.length - 1] || '?'}`
})

const statusDotClass = computed(() => {
  if (props.turn.in_progress) return 'bg-primary'
  if (props.turn.status === 'errored') return 'bg-danger'
  return 'bg-success'
})

function toggle() {
  expanded.value = !expanded.value
  if (expanded.value && !auditStore.expandedTurnEvents.has(turnId.value)) {
    auditStore.loadTurnEvents(turnId.value, props.turn.session_id)
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function sparklineClass(s) {
  if (s === 'error') return 'bg-danger'
  if (s === 'denied') return 'bg-warning'
  if (s === 'perm') return 'bg-info'
  if (s === 'tool') return 'bg-success'
  if (s === 'result') return 'bg-secondary'
  return 'bg-light'
}
</script>

<style scoped>
.turn-card { cursor: default; }
.turn-card .card-header { cursor: pointer; }
.turn-status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.sparkline { background: rgba(0,0,0,0.05); }
.sparkline-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.chevron { font-size: 0.7rem; color: var(--bs-secondary); }
</style>
