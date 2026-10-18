<template>
  <div class="archive-schedules-tab">
    <div v-if="archiveTabsStore.isLoadingTabs(sessionId)" class="empty-placeholder">
      <span class="icon">⏳</span>Loading…
    </div>
    <div v-else-if="schedules.length === 0 && history.length === 0" class="empty-placeholder">
      <span class="icon">⏱️</span>No schedules were associated with this session.
    </div>
    <template v-else>
      <template v-if="schedules.length > 0">
        <h5 class="section-label">Schedule Config</h5>
        <div v-for="sched in schedules" :key="sched.schedule_id" class="sched-card">
          <span class="cron">{{ sched.cron_expression }}</span>
          <span class="flexname">{{ sched.name || sched.schedule_id }}</span>
          <span class="status-pill" :class="scheduleStatusClass(sched.status)">{{ sched.status || 'unknown' }}</span>
        </div>
      </template>

      <template v-if="history.length > 0">
        <h5 class="section-label">Execution History</h5>
        <table class="exec-history">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Schedule</th>
              <th>Status</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(exec, idx) in history" :key="exec.execution_id || idx">
              <td>{{ formatFullTimestamp(exec.actual_time || exec.scheduled_time) }}</td>
              <td>{{ scheduleNameById(exec.schedule_id) }}</td>
              <td>
                <span class="status-pill" :class="executionStatusClass(exec.status)">{{ exec.status || 'unknown' }}</span>
              </td>
              <td>{{ exec.error_message || exec.stdout || '' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { formatFullTimestamp } from '@/utils/time'

const props = defineProps({
  sessionId: { type: String, required: true },
  archiveId: { type: String, required: true },
})

const archiveTabsStore = useArchiveTabsStore()

const schedules = computed(() => archiveTabsStore.schedulesForSession(props.sessionId).schedules || [])
const history = computed(() => archiveTabsStore.schedulesForSession(props.sessionId).history || [])

// Schedule.status values (backend/models/schedule_models.py ScheduleStatus): "active" | "paused"
const SCHEDULE_STATUS_CLASS = {
  active: 'status-delivered',
  paused: 'status-pending',
}

// ScheduleExecution.status values: "queued" | "failed" | "timeout" | "retry" |
// "delivered" | "discarded" | "error"
const EXECUTION_STATUS_CLASS = {
  delivered: 'status-delivered',
  queued: 'status-pending',
  retry: 'status-pending',
  failed: 'status-cancelled',
  timeout: 'status-cancelled',
  discarded: 'status-cancelled',
  error: 'status-cancelled',
}

function scheduleStatusClass(status) {
  return SCHEDULE_STATUS_CLASS[status] || 'status-pending'
}

function executionStatusClass(status) {
  return EXECUTION_STATUS_CLASS[status] || 'status-pending'
}

// ScheduleExecution only carries schedule_id, not a denormalized name — look it
// up against the sibling schedules list (falls back to the raw id if the
// schedule itself was deleted and isn't in the current config list).
function scheduleNameById(scheduleId) {
  const match = schedules.value.find((s) => s.schedule_id === scheduleId)
  return match?.name || scheduleId
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

h5.section-label {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bs-secondary-color);
  margin: 18px 0 8px;
}
h5.section-label:first-child {
  margin-top: 0;
}

.sched-card {
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
.sched-card .cron {
  font-family: monospace;
  background: var(--bs-secondary-bg);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}
.sched-card .flexname {
  flex: 1;
}

table.exec-history {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  margin-top: 12px;
}
table.exec-history th {
  text-align: left;
  color: var(--bs-secondary-color);
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  border-bottom: 1px solid var(--bs-border-color);
  padding: 6px 8px;
}
table.exec-history td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--bs-border-color);
}
table.exec-history tr:nth-child(even) {
  background: var(--table-stripe-bg);
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
