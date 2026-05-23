<template>
  <CollapsiblePanel
    id="schedules"
    title="Schedules"
    :expanded="expanded"
    :badge="badge"
    :flex-weight="flexWeight"
    :show-resize-handle="showResizeHandle"
    @update:expanded="$emit('update:expanded', $event)"
    @resize-start="$emit('resize-start', $event)"
    @resize-move="$emit('resize-move', $event)"
    @resize-end="$emit('resize-end', $event)"
  >
    <template #header-actions>
      <button
        class="new-btn"
        title="New schedule for this session"
        :disabled="!currentSession || currentSession.is_ephemeral"
        @click.stop="openCreateModal"
      >+</button>
    </template>

    <div class="schedules-body">
      <!-- No schedules for this session -->
      <div v-if="scopedSchedules.length === 0" class="empty-state">
        <div>No schedules for this session.</div>
        <button
          v-if="currentSession && !currentSession.is_ephemeral"
          class="empty-new-btn"
          @click="openCreateModal"
        >+ New Schedule</button>
      </div>

      <!-- Schedule rows -->
      <div v-else class="schedule-list">
        <div
          v-for="s in scopedSchedules"
          :key="s.schedule_id"
          class="schedule-row"
          :class="{ 'schedule-row--confirm': pendingDeleteId === s.schedule_id }"
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
            <div class="row-content">
              <div class="row-line1">
                <span
                  class="status-icon"
                  :class="statusClass(s)"
                  :title="statusTitle(s)"
                >{{ statusIcon(s) }}</span>
                <span class="row-name">{{ s.name }}</span>
                <span class="repeat-chip" :title="repeatChipTitle(s)">
                  {{ s.fire_count ?? 0 }} / {{ s.repeat_count != null ? s.repeat_count : '∞' }}
                </span>
              </div>
              <div class="row-line2">
                <span class="cron-text">{{ humanizeCron(s.cron_expression) }}</span>
                <span v-if="s.next_run" class="next-run">Next: {{ formatRelativeTime(s.next_run) }}</span>
              </div>
            </div>
            <div class="row-actions">
              <button
                class="action-btn play-btn"
                :class="{ 'is-active': s.status === 'active' }"
                :title="s.status === 'active' ? 'Active' : 'Activate'"
                @click.stop="setActive(s)"
              >▶</button>
              <button
                class="action-btn pause-btn"
                :class="{ 'is-active': s.status === 'paused' }"
                :title="s.status === 'paused' ? 'Paused' : 'Pause'"
                @click.stop="setPaused(s)"
              >⏸</button>
              <button
                class="action-btn edit-btn"
                title="Edit schedule"
                @click.stop="openEdit(s)"
              >✎</button>
              <button
                class="action-btn delete-btn"
                title="Delete schedule"
                @click.stop="startDelete(s)"
              >✕</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Create modal -->
    <ScheduleCreateModal
      v-if="createModalOpen && legionId && currentSession"
      :legion-id="legionId"
      :default-minion-id="currentSession.is_ephemeral ? '' : currentSession.session_id"
      @close="createModalOpen = false"
    />
  </CollapsiblePanel>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useScheduleStore } from '@/stores/schedule'
import { useSessionStore } from '@/stores/session'
import CollapsiblePanel from '@/components/layout/CollapsiblePanel.vue'
import ScheduleCreateModal from '@/components/schedules/ScheduleCreateModal.vue'
import cronstrue from 'cronstrue'

const props = defineProps({
  expanded: { type: Boolean, default: false },
  flexWeight: { type: Number, default: 1 },
  showResizeHandle: { type: Boolean, default: false },
  badge: { type: Number, default: null },
})

const emit = defineEmits(['update:expanded', 'resize-start', 'resize-move', 'resize-end'])

const router = useRouter()
const scheduleStore = useScheduleStore()
const sessionStore = useSessionStore()

const createModalOpen = ref(false)
const pendingDeleteId = ref(null)
const pendingDeleteAgent = ref(true)
const deleting = ref(false)

const currentSession = computed(() => sessionStore.currentSession)
const legionId = computed(() => currentSession.value?.project_id)

// Load schedules for this project if not yet cached
watch(
  legionId,
  (lid) => {
    if (lid && !scheduleStore.schedulesByLegion.has(lid)) {
      scheduleStore.loadSchedules(lid)
    }
  },
  { immediate: true }
)

const scopedSchedules = computed(() => {
  if (!legionId.value || !currentSession.value) return []
  const sid = currentSession.value.session_id
  const all = scheduleStore.getSchedules(legionId.value)
  return all
    .filter(s => s.minion_id === sid || s.ephemeral_agent_id === sid)
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
})

function statusIcon(s) {
  if (s.monitor_error) return '⚠'
  return s.status === 'paused' ? '⏸' : '▶'
}

function statusClass(s) {
  if (s.monitor_error) return 'status-error'
  return s.status === 'paused' ? 'status-paused' : 'status-active'
}

function statusTitle(s) {
  if (s.monitor_error) return `Error: ${s.monitor_error}`
  return s.status === 'paused' ? 'Paused' : 'Active'
}

function isEphemeral(schedule) {
  return !!schedule.session_config || !!schedule.ephemeral_agent_id
}

function repeatChipTitle(schedule) {
  const fc = schedule.fire_count ?? 0
  const rc = schedule.repeat_count
  if (rc != null) return `${fc} of ${rc} fires used`
  return `${fc} fires total (unlimited)`
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

async function setActive(s) {
  if (s.status === 'active') return
  await scheduleStore.resumeSchedule(s.legion_id, s.schedule_id)
}

async function setPaused(s) {
  if (s.status === 'paused') return
  await scheduleStore.pauseSchedule(s.legion_id, s.schedule_id)
}

function openEdit(s) {
  router.push(`/settings/schedule/${s.schedule_id}/general`)
}

function openCreateModal() {
  if (!currentSession.value || currentSession.value.is_ephemeral) return
  createModalOpen.value = true
}

function startDelete(s) {
  pendingDeleteId.value = s.schedule_id
  pendingDeleteAgent.value = isEphemeral(s)
}

function cancelDelete() {
  pendingDeleteId.value = null
}

async function executeDelete(s) {
  if (deleting.value) return
  deleting.value = true
  try {
    await scheduleStore.deleteSchedule(s.legion_id, s.schedule_id, {
      delete_agent: pendingDeleteAgent.value,
    })
    pendingDeleteId.value = null
  } catch (err) {
    console.error('Delete schedule failed:', err)
  } finally {
    deleting.value = false
  }
}
</script>

<style scoped>
.schedules-body {
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* ── Header new button ──────────────────────────────────────── */
.new-btn {
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

.new-btn:hover:not(:disabled) {
  background: #7c3aed;
  border-color: #7c3aed;
  color: #fff;
}

.new-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* ── Empty states ───────────────────────────────────────────── */
.empty-state {
  padding: 14px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
  margin: 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.empty-new-btn {
  padding: 4px 12px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.empty-new-btn:hover:not(:disabled) {
  background: #7c3aed;
  border-color: #7c3aed;
  color: #fff;
}

.empty-new-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* ── Schedule list ──────────────────────────────────────────── */
.schedule-list {
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  flex: 1;
}

.schedule-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--bs-border-color);
  min-width: 0;
  transition: background 0.12s;
}

.schedule-row:last-child {
  border-bottom: none;
}

.schedule-row:hover {
  background: var(--bs-tertiary-bg);
}

.schedule-row:hover .row-actions {
  opacity: 1;
}

.schedule-row--confirm {
  background: rgba(248, 113, 113, 0.06);
  border-color: rgba(248, 113, 113, 0.3);
}

.schedule-row--confirm:hover {
  background: rgba(248, 113, 113, 0.06);
}

/* ── Row content (2-line layout) ───────────────────────────── */
.row-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.row-line1 {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.row-line2 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* ── Status icon ────────────────────────────────────────────── */
.status-icon {
  font-size: 10px;
  flex-shrink: 0;
  line-height: 1;
}

.status-active  { color: #7c3aed; }
.status-paused  { color: var(--bs-secondary-color); }
.status-error   { color: #f87171; }

/* ── Row name ───────────────────────────────────────────────── */
.row-name {
  font-size: 12px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

/* ── Repeat chip ────────────────────────────────────────────── */
.repeat-chip {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
  font-family: monospace;
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  border: 1px solid var(--bs-border-color);
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Cron + next run ────────────────────────────────────────── */
.cron-text {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.next-run {
  font-size: 10px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Row actions (hover-revealed) ───────────────────────────── */
.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.12s;
}

.action-btn {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--bs-tertiary-color);
  font-size: 10px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.action-btn.is-active {
  opacity: 1;
  color: #7c3aed;
}

.play-btn:hover,
.pause-btn:hover,
.edit-btn:hover {
  background: rgba(124, 58, 237, 0.12);
  color: #7c3aed;
}

.delete-btn:hover {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}

/* ── Inline confirm ─────────────────────────────────────────── */
.confirm-col {
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1;
  min-width: 0;
}

.confirm-text {
  font-size: 11px;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.confirm-agent-check {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--bs-secondary-color);
  cursor: pointer;
}

.confirm-btns {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}

.btn-confirm-delete,
.btn-confirm-cancel {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
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
</style>
