<template>
  <div class="lib-section">
    <SettingsToolbar title="Schedules" />

    <div v-if="loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>

    <div v-else class="section-body">
      <!-- Empty state -->
      <div v-if="groupedSchedules.length === 0" class="empty-state">
        No schedules yet. Create one from a Legion project's schedule panel.
      </div>

      <!-- Grouped by legion -->
      <div
        v-for="group in groupedSchedules"
        :key="group.legionId"
        class="category-block"
      >
        <div class="category-header">
          <span class="category-label">{{ group.legionName }}</span>
          <button
            class="category-add-btn"
            :title="`New schedule in ${group.legionName}`"
            @click="openCreateModal(group.legionId)"
          >+</button>
        </div>

        <div v-if="group.schedules.length === 0" class="empty-state">
          No schedules in this legion yet.
        </div>

        <div
          v-for="s in group.schedules"
          :key="s.schedule_id"
          class="schedule-row"
          :class="{ 'schedule-row--confirm': pendingDeleteId === s.schedule_id }"
          role="button"
          tabindex="0"
          @click="pendingDeleteId !== s.schedule_id && openEdit(s)"
          @keydown.enter.prevent="pendingDeleteId !== s.schedule_id && openEdit(s)"
          @keydown.space.prevent="pendingDeleteId !== s.schedule_id && openEdit(s)"
        >
          <!-- Confirm delete state -->
          <template v-if="pendingDeleteId === s.schedule_id">
            <div class="confirm-col">
              <span class="confirm-text">Delete "{{ s.name }}"?</span>
              <label v-if="isEphemeral(s)" class="confirm-agent-check">
                <input
                  type="checkbox"
                  :checked="pendingDeleteAgent"
                  @click.stop
                  @change.stop="pendingDeleteAgent = $event.target.checked"
                />
                Also delete bound ephemeral agent
              </label>
            </div>
            <div class="confirm-btns">
              <button
                class="btn-confirm-delete"
                :disabled="deleting"
                @click.stop="executeDelete(s)"
              >{{ deleting ? '…' : 'Delete' }}</button>
              <button class="btn-confirm-cancel" @click.stop="cancelDelete">Cancel</button>
            </div>
          </template>

          <!-- Normal state -->
          <template v-else>
            <div class="row-left">
              <span class="schedule-name">{{ s.name }}</span>
              <div class="row-chips">
                <span class="kind-chip" :class="isEphemeral(s) ? 'ephemeral' : 'permanent'">
                  {{ isEphemeral(s) ? 'ephemeral' : 'permanent' }}
                </span>
                <span class="status-chip" :class="s.status">{{ s.status }}</span>
              </div>
              <span class="cron-text">{{ humanizeCron(s.cron_expression) }}</span>
            </div>
            <div class="row-right">
              <span v-if="s.next_run" class="next-run">Next: {{ formatRelativeTime(s.next_run) }}</span>
              <span
                v-if="s.monitor_error"
                class="monitor-error-icon"
                :title="s.monitor_error"
              >⚠</span>
              <button
                class="row-delete-btn"
                title="Delete schedule"
                @click.stop="startDelete(s)"
              >✕</button>
              <span class="row-chevron" aria-hidden="true">›</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <ScheduleCreateModal
      v-if="createModalLegionId"
      :legion-id="createModalLegionId"
      @close="createModalLegionId = null"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useScheduleStore } from '@/stores/schedule'
import { useProjectStore } from '@/stores/project'
import SettingsToolbar from '../SettingsToolbar.vue'
import ScheduleCreateModal from '../../schedules/ScheduleCreateModal.vue'
import cronstrue from 'cronstrue'

const router = useRouter()
const scheduleStore = useScheduleStore()
const projectStore  = useProjectStore()

const loading = ref(true)
const createModalLegionId = ref(null)
const pendingDeleteId    = ref(null)
const pendingDeleteAgent = ref(true)
const deleting           = ref(false)

const groupedSchedules = computed(() => {
  const legions = [...projectStore.projects.values()]
  return legions
    .map(legion => ({
      legionId: legion.project_id,
      legionName: legion.name,
      schedules: (scheduleStore.getSchedules(legion.project_id) || [])
        .slice()
        .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    }))
    .sort((a, b) => a.legionName.localeCompare(b.legionName))
})

function isEphemeral(schedule) {
  return !!schedule.session_config || !!schedule.ephemeral_agent_id
}

function humanizeCron(expr) {
  if (!expr) return ''
  try { return cronstrue.toString(expr) } catch { return expr }
}

function formatRelativeTime(ts) {
  if (!ts) return ''
  const now = Date.now() / 1000
  const diff = ts - now
  const absDiff = Math.abs(diff)
  const prefix = diff > 0 ? 'in ' : ''
  const suffix = diff < 0 ? ' ago' : ''
  if (absDiff < 60) return `${prefix}<1m${suffix}`
  if (absDiff < 3600) return `${prefix}${Math.ceil(absDiff / 60)}m${suffix}`
  if (absDiff < 86400) return `${prefix}${Math.ceil(absDiff / 3600)}h${suffix}`
  return `${prefix}${Math.ceil(absDiff / 86400)}d${suffix}`
}

function openEdit(schedule) {
  router.push(`/settings/schedule/${schedule.schedule_id}/general`)
}

function openCreateModal(legionId) {
  createModalLegionId.value = legionId
}

function startDelete(schedule) {
  pendingDeleteId.value = schedule.schedule_id
  pendingDeleteAgent.value = isEphemeral(schedule)
}

function cancelDelete() {
  pendingDeleteId.value = null
}

async function executeDelete(schedule) {
  if (deleting.value) return
  deleting.value = true
  try {
    await scheduleStore.deleteSchedule(schedule.legion_id, schedule.schedule_id, {
      delete_agent: pendingDeleteAgent.value,
    })
    pendingDeleteId.value = null
  } catch (err) {
    console.error('Delete schedule failed:', err)
  } finally {
    deleting.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await projectStore.fetchProjects()
    await scheduleStore.loadAllSchedules()
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-status {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.status-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Category ──────────────────────────────────────────── */
.category-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 4px;
  border-bottom: 1px solid var(--bs-border-color);
}

.category-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-secondary-color);
}

.category-add-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.category-add-btn:hover {
  background: #7c3aed;
  border-color: #7c3aed;
  color: #fff;
}

/* ── Empty state ─────────────────────────────────────────── */
.empty-state {
  padding: 14px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

/* ── Schedule rows ────────────────────────────────────────── */
.schedule-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 7px;
  border: 1px solid var(--bs-border-color);
  cursor: pointer;
  background: var(--bs-body-bg);
  transition: background 0.12s, border-color 0.12s;
  min-width: 0;
}

.schedule-row:hover {
  background: var(--bs-tertiary-bg);
  border-color: #7c3aed;
}

.schedule-row:hover .row-delete-btn {
  opacity: 1;
}

.schedule-row:focus-visible {
  outline: 2px solid #7c3aed;
  outline-offset: 1px;
}

.schedule-row--confirm {
  border-color: rgba(248, 113, 113, 0.5);
  background: rgba(248, 113, 113, 0.06);
  cursor: default;
}

.schedule-row--confirm:hover {
  border-color: rgba(248, 113, 113, 0.7);
  background: rgba(248, 113, 113, 0.06);
}

.row-left {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.schedule-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-chips {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}

.kind-chip, .status-chip {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: lowercase;
}

.kind-chip.ephemeral { background: rgba(124, 58, 237, 0.15); color: #7c3aed; }
.kind-chip.permanent { background: rgba(88, 166, 255, 0.15); color: #58a6ff; }

.status-chip.active   { background: rgba(63, 185, 80, 0.2);  color: #3fb950; }
.status-chip.paused   { background: rgba(210, 153, 34, 0.2); color: #d29922; }
.status-chip.cancelled{ background: rgba(139, 148, 158, 0.2); color: #8b949e; }

.cron-text {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.next-run {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
}

.monitor-error-icon {
  color: #f87171;
  font-size: 14px;
  cursor: help;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  line-height: 1;
}

.row-delete-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.row-delete-btn:hover {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}

/* ── Inline confirm ────────────────────────────────────────── */
.confirm-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.confirm-text {
  font-size: 12px;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.confirm-agent-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--bs-secondary-color);
  cursor: pointer;
}

.confirm-btns {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.btn-confirm-delete,
.btn-confirm-cancel {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-confirm-delete {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}

.btn-confirm-delete:hover:not(:disabled) {
  background: #f87171;
  border-color: #f87171;
  color: #fff;
}

.btn-confirm-delete:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-confirm-cancel {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-confirm-cancel:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
