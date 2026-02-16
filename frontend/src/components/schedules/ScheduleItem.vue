<template>
  <div class="schedule-item" :class="schedule.status">
    <!-- Header -->
    <div class="schedule-header">
      <span class="schedule-name">{{ schedule.name }}</span>
      <span class="status-badge" :class="schedule.status">{{ schedule.status }}</span>
    </div>

    <!-- Cron + next run -->
    <div class="schedule-meta">
      <span class="cron-desc" :title="schedule.cron_expression">{{ cronDescription }}</span>
    </div>
    <div class="schedule-timing">
      <span v-if="schedule.next_run" class="next-run">
        Next: {{ formatRelativeTime(schedule.next_run) }}
      </span>
      <span v-if="schedule.last_run" class="last-run">
        Last: {{ formatRelativeTime(schedule.last_run) }}
        <span v-if="schedule.last_status" class="last-status" :class="schedule.last_status">
          ({{ schedule.last_status }})
        </span>
      </span>
    </div>

    <!-- Stats -->
    <div class="schedule-stats">
      <span>{{ schedule.execution_count }} runs</span>
      <span v-if="schedule.failure_count > 0" class="failures">
        {{ schedule.failure_count }} consecutive failures
      </span>
    </div>

    <!-- Controls -->
    <div class="schedule-controls">
      <button
        v-if="schedule.status === 'active'"
        class="ctrl-btn pause"
        @click.stop="pause"
        title="Pause"
      >Pause</button>
      <button
        v-if="schedule.status === 'paused'"
        class="ctrl-btn resume"
        @click.stop="resume"
        title="Resume"
      >Resume</button>
      <button
        v-if="schedule.status !== 'cancelled'"
        class="ctrl-btn cancel"
        @click.stop="cancel"
        title="Cancel permanently"
      >Cancel</button>
      <button
        class="ctrl-btn delete"
        @click.stop="remove"
        title="Delete"
      >Delete</button>
    </div>

    <!-- Expandable history -->
    <div v-if="expanded" class="schedule-history">
      <div class="history-header">Execution History</div>
      <div v-if="history.length === 0" class="history-empty">No executions yet</div>
      <div v-for="exec in history" :key="exec.execution_id" class="history-item" :class="exec.status">
        <span class="history-time">{{ formatTimestamp(exec.actual_time) }}</span>
        <span class="history-status">{{ exec.status }}</span>
        <span v-if="exec.error_message" class="history-error">{{ exec.error_message }}</span>
      </div>
    </div>

    <!-- Toggle history -->
    <button class="toggle-history" @click.stop="toggleHistory">
      {{ expanded ? 'Hide history' : 'Show history' }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useScheduleStore } from '@/stores/schedule'
import cronstrue from 'cronstrue'

const props = defineProps({
  schedule: { type: Object, required: true },
  legionId: { type: String, required: true },
})

const scheduleStore = useScheduleStore()
const expanded = ref(false)
const history = ref([])

const cronDescription = computed(() => {
  try {
    return cronstrue.toString(props.schedule.cron_expression)
  } catch {
    return props.schedule.cron_expression
  }
})

function formatRelativeTime(timestamp) {
  if (!timestamp) return ''
  const now = Date.now() / 1000
  const diff = timestamp - now

  if (Math.abs(diff) < 60) return 'just now'

  const absDiff = Math.abs(diff)
  const prefix = diff > 0 ? 'in ' : ''
  const suffix = diff < 0 ? ' ago' : ''

  if (absDiff < 3600) {
    const mins = Math.round(absDiff / 60)
    return `${prefix}${mins}m${suffix}`
  }
  if (absDiff < 86400) {
    const hours = Math.round(absDiff / 3600)
    return `${prefix}${hours}h${suffix}`
  }
  const days = Math.round(absDiff / 86400)
  return `${prefix}${days}d${suffix}`
}

function formatTimestamp(timestamp) {
  if (!timestamp) return ''
  return new Date(timestamp * 1000).toLocaleString()
}

async function pause() {
  try {
    await scheduleStore.pauseSchedule(props.legionId, props.schedule.schedule_id)
  } catch (e) {
    console.error('Failed to pause schedule:', e)
  }
}

async function resume() {
  try {
    await scheduleStore.resumeSchedule(props.legionId, props.schedule.schedule_id)
  } catch (e) {
    console.error('Failed to resume schedule:', e)
  }
}

async function cancel() {
  if (!confirm(`Cancel schedule "${props.schedule.name}" permanently?`)) return
  try {
    await scheduleStore.cancelSchedule(props.legionId, props.schedule.schedule_id)
  } catch (e) {
    console.error('Failed to cancel schedule:', e)
  }
}

async function remove() {
  if (!confirm(`Delete schedule "${props.schedule.name}"?`)) return
  try {
    await scheduleStore.deleteSchedule(props.legionId, props.schedule.schedule_id)
  } catch (e) {
    console.error('Failed to delete schedule:', e)
  }
}

async function toggleHistory() {
  expanded.value = !expanded.value
  if (expanded.value && history.value.length === 0) {
    history.value = await scheduleStore.loadHistory(
      props.legionId, props.schedule.schedule_id
    )
  }
}
</script>

<style scoped>
.schedule-item {
  padding: 8px 10px;
  margin-bottom: 6px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  background: #f8fafc;
}

.schedule-item.paused {
  opacity: 0.7;
  border-color: #fde68a;
  background: #fffbeb;
}

.schedule-item.cancelled {
  opacity: 0.5;
  border-color: #fecaca;
  background: #fef2f2;
}

.schedule-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.schedule-name {
  font-weight: 600;
  color: #1e293b;
  font-size: 12px;
}

.status-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 500;
}

.status-badge.active {
  background: #dcfce7;
  color: #166534;
}

.status-badge.paused {
  background: #fef3c7;
  color: #92400e;
}

.status-badge.cancelled {
  background: #fee2e2;
  color: #991b1b;
}

.schedule-meta {
  margin-bottom: 2px;
}

.cron-desc {
  color: #64748b;
  font-size: 11px;
}

.schedule-timing {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.next-run, .last-run {
  color: #94a3b8;
  font-size: 10px;
}

.last-status.delivered {
  color: #16a34a;
}

.last-status.failed {
  color: #dc2626;
}

.schedule-stats {
  display: flex;
  gap: 8px;
  color: #94a3b8;
  font-size: 10px;
  margin-bottom: 6px;
}

.failures {
  color: #dc2626;
}

.schedule-controls {
  display: flex;
  gap: 4px;
}

.ctrl-btn {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
  background: #fff;
  cursor: pointer;
  color: #475569;
}

.ctrl-btn:hover {
  background: #f1f5f9;
}

.ctrl-btn.pause {
  border-color: #fde68a;
  color: #92400e;
}

.ctrl-btn.resume {
  border-color: #86efac;
  color: #166534;
}

.ctrl-btn.cancel {
  border-color: #fecaca;
  color: #991b1b;
}

.ctrl-btn.delete {
  border-color: #fecaca;
  color: #991b1b;
}

.toggle-history {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 2px;
  background: none;
  border: none;
  color: #6366f1;
  font-size: 10px;
  cursor: pointer;
  text-align: center;
}

.toggle-history:hover {
  text-decoration: underline;
}

.schedule-history {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid #e2e8f0;
}

.history-header {
  font-weight: 600;
  color: #334155;
  font-size: 11px;
  margin-bottom: 4px;
}

.history-empty {
  color: #94a3b8;
  font-size: 10px;
  font-style: italic;
}

.history-item {
  display: flex;
  gap: 6px;
  font-size: 10px;
  padding: 2px 0;
  align-items: baseline;
}

.history-time {
  color: #94a3b8;
  white-space: nowrap;
}

.history-status {
  font-weight: 500;
}

.history-item.delivered .history-status {
  color: #16a34a;
}

.history-item.failed .history-status,
.history-item.timeout .history-status {
  color: #dc2626;
}

.history-item.retry .history-status {
  color: #d97706;
}

.history-error {
  color: #dc2626;
  font-style: italic;
}
</style>
