<template>
  <div class="lib-section">
    <SettingsToolbar title="Schedules" />

    <!-- Group-by controls -->
    <div class="controls">
      <span class="control-label">Group by</span>
      <div class="seg">
        <button :class="{ on: groupBy === 'legion' }"   @click="setGroupBy('legion')">By Legion</button>
        <button :class="{ on: groupBy === 'state' }"    @click="setGroupBy('state')">By State</button>
        <button :class="{ on: groupBy === 'instance' }" @click="setGroupBy('instance')">By Instance</button>
      </div>
      <span class="control-label" style="margin-left: 8px;">Sort</span>
      <span class="sort-pill">A → Z</span>
    </div>

    <div v-if="loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>

    <div v-else class="section-body">
      <!-- Whole-library empty state -->
      <div v-if="displayGroups.length === 0" class="empty-state">
        No schedules yet. Create one from a Legion project's schedule panel.
      </div>

      <!-- By Legion or By State: flat list of groups -->
      <template v-if="groupBy !== 'instance'">
        <div
          v-for="group in displayGroups"
          :key="group.key"
          class="group-block"
        >
          <!-- Group header -->
          <div class="group-header">
            <div
              class="group-header-left"
              @click="toggleCollapse(group.key)"
            >
              <span class="chev" :class="{ open: !isCollapsed(group.key) }">›</span>
              <span
                class="group-label"
                :class="{
                  'state-active': group.bulkState === 'active',
                  'state-paused': group.bulkState === 'paused',
                }"
              >{{ group.label }}</span>
              <span class="group-count">{{ group.schedules.length }}</span>
            </div>
            <div class="group-actions">
              <!-- Bulk confirm strip (state groups only) -->
              <template v-if="group.bulkState && pendingBulkKey === group.key">
                <div class="group-confirm">
                  <span class="group-confirm-text">
                    {{ group.bulkState === 'active' ? 'Pause' : 'Resume' }}
                    {{ pendingBulkCount }} schedules?
                  </span>
                  <div class="group-confirm-btns">
                    <button
                      class="btn-bulk-confirm"
                      :disabled="bulkBusy"
                      @click="executeBulk(group)"
                    >{{ bulkBusy ? '…' : (group.bulkState === 'active' ? 'Pause all' : 'Resume all') }}</button>
                    <button class="btn-bulk-cancel" @click="cancelBulk">Cancel</button>
                  </div>
                </div>
              </template>
              <!-- Bulk trigger button (state groups only) -->
              <template v-else-if="group.bulkState">
                <button class="group-bulk-btn" @click="startBulk(group)">
                  {{ group.bulkState === 'active' ? 'Pause all' : 'Resume all' }} ({{ group.schedules.length }})
                </button>
              </template>
              <!-- Add button (legion groups only) -->
              <template v-else-if="group.legionId">
                <button
                  class="group-add-btn"
                  :title="`New schedule in ${group.label}`"
                  @click="openCreate(group.legionId)"
                >+</button>
              </template>
            </div>
          </div>

          <!-- Group body -->
          <div v-show="!isCollapsed(group.key)" class="group-body">
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
              <template v-else>
                <div class="row-left">
                  <span class="schedule-name">{{ s.name }}</span>
                  <div class="row-chips">
                    <span class="kind-chip" :class="isEphemeral(s) ? 'ephemeral' : 'permanent'">
                      {{ isEphemeral(s) ? 'ephemeral' : 'permanent' }}
                    </span>
                    <span class="repeat-chip" :title="repeatChipTitle(s)">
                      {{ s.fire_count ?? 0 }} / {{ s.repeat_count != null ? s.repeat_count : '∞' }}
                    </span>
                  </div>
                  <span class="cron-text">{{ humanizeCron(s.cron_expression) }}</span>
                </div>
                <div class="row-right">
                  <span v-if="s.next_run" class="next-run">Next: {{ formatRelativeTime(s.next_run) }}</span>
                  <span v-if="s.monitor_error" class="monitor-error-icon" :title="s.monitor_error">⚠</span>
                  <button
                    class="row-state-btn row-state-btn--play"
                    :class="{ 'is-active': s.status === 'active' }"
                    :title="s.status === 'active' ? 'Active' : 'Activate'"
                    @click.stop="setActive(s)"
                  >▶</button>
                  <button
                    class="row-state-btn row-state-btn--pause"
                    :class="{ 'is-active': s.status === 'paused' }"
                    :title="s.status === 'paused' ? 'Paused' : 'Pause'"
                    @click.stop="setPaused(s)"
                  >⏸</button>
                  <button class="row-delete-btn" title="Delete schedule" @click.stop="startDelete(s)">✕</button>
                  <span class="row-chevron" aria-hidden="true">›</span>
                </div>
              </template>
            </div>
          </div>
        </div>
      </template>

      <!-- By Instance: nested legion → subgroup structure -->
      <template v-else>
        <div
          v-for="group in displayGroups"
          :key="group.key"
          class="group-block"
        >
          <!-- Legion-level (or Ephemeral top-level) header -->
          <div class="group-header">
            <div class="group-header-left" @click="toggleCollapse(group.key)">
              <span class="chev" :class="{ open: !isCollapsed(group.key) }">›</span>
              <span class="group-label" :class="{ 'ephemeral-label': group.isEphemeral }">{{ group.label }}</span>
              <span class="group-count">
                {{ group.isEphemeral ? group.schedules.length : (group.instanceCount + ' instance' + (group.instanceCount === 1 ? '' : 's')) }}
              </span>
            </div>
            <div class="group-actions">
              <button
                v-if="group.legionId"
                class="group-add-btn"
                :title="`New schedule in ${group.label}`"
                @click="openCreate(group.legionId)"
              >+</button>
            </div>
          </div>

          <!-- Ephemeral schedules (flat, no subgroups) -->
          <div v-if="group.isEphemeral" v-show="!isCollapsed(group.key)" class="group-body">
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
              <template v-if="pendingDeleteId === s.schedule_id">
                <div class="confirm-col">
                  <span class="confirm-text">Delete "{{ s.name }}"?</span>
                  <label v-if="isEphemeral(s)" class="confirm-agent-check">
                    <input type="checkbox" :checked="pendingDeleteAgent" @click.stop @change.stop="pendingDeleteAgent = $event.target.checked" />
                    Also delete bound ephemeral agent
                  </label>
                </div>
                <div class="confirm-btns">
                  <button class="btn-confirm-delete" :disabled="deleting" @click.stop="executeDelete(s)">{{ deleting ? '…' : 'Delete' }}</button>
                  <button class="btn-confirm-cancel" @click.stop="cancelDelete">Cancel</button>
                </div>
              </template>
              <template v-else>
                <div class="row-left">
                  <span class="schedule-name">{{ s.name }}</span>
                  <div class="row-chips">
                    <span class="kind-chip" :class="isEphemeral(s) ? 'ephemeral' : 'permanent'">{{ isEphemeral(s) ? 'ephemeral' : 'permanent' }}</span>
                    <span class="repeat-chip" :title="repeatChipTitle(s)">{{ s.fire_count ?? 0 }} / {{ s.repeat_count != null ? s.repeat_count : '∞' }}</span>
                  </div>
                  <span class="cron-text">{{ humanizeCron(s.cron_expression) }}</span>
                </div>
                <div class="row-right">
                  <span v-if="s.next_run" class="next-run">Next: {{ formatRelativeTime(s.next_run) }}</span>
                  <span v-if="s.monitor_error" class="monitor-error-icon" :title="s.monitor_error">⚠</span>
                  <button class="row-state-btn row-state-btn--play" :class="{ 'is-active': s.status === 'active' }" :title="s.status === 'active' ? 'Active' : 'Activate'" @click.stop="setActive(s)">▶</button>
                  <button class="row-state-btn row-state-btn--pause" :class="{ 'is-active': s.status === 'paused' }" :title="s.status === 'paused' ? 'Paused' : 'Pause'" @click.stop="setPaused(s)">⏸</button>
                  <button class="row-delete-btn" title="Delete schedule" @click.stop="startDelete(s)">✕</button>
                  <span class="row-chevron" aria-hidden="true">›</span>
                </div>
              </template>
            </div>
          </div>

          <!-- Bound schedules: per-instance subgroups -->
          <div v-else v-show="!isCollapsed(group.key)" class="group-body">
            <div
              v-for="sg in group.subgroups"
              :key="sg.key"
              class="subgroup"
            >
              <div class="subgroup-header" @click="toggleCollapse(sg.key)">
                <div class="subgroup-left">
                  <span class="chev" :class="{ open: !isCollapsed(sg.key) }">›</span>
                  <span class="subgroup-name" :title="sg.label">{{ sg.label }}</span>
                  <span class="group-count">{{ sg.schedules.length }}</span>
                </div>
              </div>
              <div v-show="!isCollapsed(sg.key)" class="group-body subgroup-body">
                <div
                  v-for="s in sg.schedules"
                  :key="s.schedule_id"
                  class="schedule-row"
                  :class="{ 'schedule-row--confirm': pendingDeleteId === s.schedule_id }"
                  role="button"
                  tabindex="0"
                  @click="pendingDeleteId !== s.schedule_id && openEdit(s)"
                  @keydown.enter.prevent="pendingDeleteId !== s.schedule_id && openEdit(s)"
                  @keydown.space.prevent="pendingDeleteId !== s.schedule_id && openEdit(s)"
                >
                  <template v-if="pendingDeleteId === s.schedule_id">
                    <div class="confirm-col">
                      <span class="confirm-text">Delete "{{ s.name }}"?</span>
                      <label v-if="isEphemeral(s)" class="confirm-agent-check">
                        <input type="checkbox" :checked="pendingDeleteAgent" @click.stop @change.stop="pendingDeleteAgent = $event.target.checked" />
                        Also delete bound ephemeral agent
                      </label>
                    </div>
                    <div class="confirm-btns">
                      <button class="btn-confirm-delete" :disabled="deleting" @click.stop="executeDelete(s)">{{ deleting ? '…' : 'Delete' }}</button>
                      <button class="btn-confirm-cancel" @click.stop="cancelDelete">Cancel</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="row-left">
                      <span class="schedule-name">{{ s.name }}</span>
                      <div class="row-chips">
                        <span class="kind-chip" :class="isEphemeral(s) ? 'ephemeral' : 'permanent'">{{ isEphemeral(s) ? 'ephemeral' : 'permanent' }}</span>
                        <span class="repeat-chip" :title="repeatChipTitle(s)">{{ s.fire_count ?? 0 }} / {{ s.repeat_count != null ? s.repeat_count : '∞' }}</span>
                      </div>
                      <span class="cron-text">{{ humanizeCron(s.cron_expression) }}</span>
                    </div>
                    <div class="row-right">
                      <span v-if="s.next_run" class="next-run">Next: {{ formatRelativeTime(s.next_run) }}</span>
                      <span v-if="s.monitor_error" class="monitor-error-icon" :title="s.monitor_error">⚠</span>
                      <button class="row-state-btn row-state-btn--play" :class="{ 'is-active': s.status === 'active' }" :title="s.status === 'active' ? 'Active' : 'Activate'" @click.stop="setActive(s)">▶</button>
                      <button class="row-state-btn row-state-btn--pause" :class="{ 'is-active': s.status === 'paused' }" :title="s.status === 'paused' ? 'Paused' : 'Pause'" @click.stop="setPaused(s)">⏸</button>
                      <button class="row-delete-btn" title="Delete schedule" @click.stop="startDelete(s)">✕</button>
                      <span class="row-chevron" aria-hidden="true">›</span>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useScheduleStore } from '@/stores/schedule'
import { useProjectStore } from '@/stores/project'
import { useUIStore } from '@/stores/ui'
import SettingsToolbar from '../SettingsToolbar.vue'
import cronstrue from 'cronstrue'

const router = useRouter()
const scheduleStore = useScheduleStore()
const projectStore  = useProjectStore()
const uiStore       = useUIStore()

const loading            = ref(true)
const pendingDeleteId    = ref(null)
const pendingDeleteAgent = ref(true)
const deleting           = ref(false)

// Bulk action state (state-group headers only)
const pendingBulkKey   = ref(null)
const pendingBulkCount = ref(0)
const bulkBusy         = ref(false)

// ── Grouping ─────────────────────────────────────────────

const groupBy = computed(() => uiStore.schedulesGroupBy)

function setGroupBy(mode) {
  uiStore.setSchedulesGroupBy(mode)
  pendingDeleteId.value   = null
  pendingBulkKey.value    = null
  pendingBulkCount.value  = 0
}

const isCollapsed  = (key) => uiStore.isScheduleGroupCollapsed(key)
const toggleCollapse = (key) => uiStore.toggleScheduleGroupCollapsed(key)

// Flat list of all schedules across all legions
const allSchedulesFlat = computed(() => {
  const result = []
  for (const legion of projectStore.projects.values()) {
    const items = scheduleStore.getSchedules(legion.project_id) || []
    for (const s of items) result.push({ ...s, _legionName: legion.name })
  }
  return result
})

// === By Legion ===
const groupsByLegion = computed(() => {
  return [...projectStore.projects.values()]
    .map(l => ({
      key:      `legion-${l.project_id}`,
      label:    l.name,
      legionId: l.project_id,
      schedules: (scheduleStore.getSchedules(l.project_id) || [])
        .slice()
        .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
    }))
    .filter(g => g.schedules.length > 0)
    .sort((a, b) => a.label.localeCompare(b.label))
})

// === By State ===
const groupsByState = computed(() => {
  const active = []
  const paused = []
  for (const s of allSchedulesFlat.value) {
    if (s.status === 'active') active.push(s); else paused.push(s)
  }
  const byName = (a, b) => (a.name || '').localeCompare(b.name || '')
  active.sort(byName)
  paused.sort(byName)
  const out = []
  if (active.length) out.push({ key: 'state-active', label: 'Active', schedules: active, bulkState: 'active' })
  if (paused.length) out.push({ key: 'state-paused', label: 'Paused', schedules: paused, bulkState: 'paused' })
  return out
})

// === By Instance ===
const groupsByInstance = computed(() => {
  const out = []
  const ephemeral = allSchedulesFlat.value
    .filter(s => !s.minion_id)
    .slice()
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
  if (ephemeral.length) {
    out.push({ key: 'ephemeral', label: 'Ephemeral', isEphemeral: true, schedules: ephemeral })
  }
  for (const legion of [...projectStore.projects.values()].sort((a, b) => a.name.localeCompare(b.name))) {
    const items = (scheduleStore.getSchedules(legion.project_id) || []).filter(s => s.minion_id)
    if (items.length === 0) continue
    const byMinion = new Map()
    for (const s of items) {
      if (!byMinion.has(s.minion_id)) {
        byMinion.set(s.minion_id, { minionId: s.minion_id, minionName: s.minion_name || s.minion_id, schedules: [] })
      }
      byMinion.get(s.minion_id).schedules.push(s)
    }
    const subgroups = [...byMinion.values()]
      .sort((a, b) => a.minionName.localeCompare(b.minionName))
      .map(sg => ({
        key:       `inst-${legion.project_id}-${sg.minionId}`,
        label:     sg.minionName,
        schedules: sg.schedules.slice().sort((a, b) => (a.name || '').localeCompare(b.name || '')),
      }))
    out.push({
      key:           `legion-${legion.project_id}`,
      label:         legion.name,
      legionId:      legion.project_id,
      instanceCount: subgroups.length,
      subgroups,
    })
  }
  return out
})

const displayGroups = computed(() => {
  if (groupBy.value === 'state')    return groupsByState.value
  if (groupBy.value === 'instance') return groupsByInstance.value
  return groupsByLegion.value
})

// ── Bulk action ───────────────────────────────────────────

function startBulk(group) {
  pendingBulkKey.value   = group.key
  pendingBulkCount.value = group.schedules.length
}

function cancelBulk() {
  pendingBulkKey.value   = null
  pendingBulkCount.value = 0
}

async function executeBulk(group) {
  // Snapshot schedules before async so reactive recomputes mid-op don't affect which items we act on
  const snapshot  = group.schedules.slice()
  const total     = snapshot.length
  bulkBusy.value  = true
  try {
    const result = group.bulkState === 'active'
      ? await scheduleStore.pauseSchedulesBulk(snapshot)
      : await scheduleStore.resumeSchedulesBulk(snapshot)
    if (result.failed > 0) {
      console.error(`Bulk action: ${result.failed} of ${total} failed`)
    }
  } finally {
    bulkBusy.value       = false
    pendingBulkKey.value = null
  }
}

// ── Row helpers ───────────────────────────────────────────

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
  const now     = Date.now() / 1000
  const diff    = ts - now
  const absDiff = Math.abs(diff)
  const prefix  = diff > 0 ? 'in ' : ''
  const suffix  = diff < 0 ? ' ago' : ''
  if (absDiff < 60)    return `${prefix}<1m${suffix}`
  if (absDiff < 3600)  return `${prefix}${Math.ceil(absDiff / 60)}m${suffix}`
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

function openEdit(schedule) {
  router.push(`/settings/schedule/${schedule.schedule_id}/general`)
}

function openCreate(legionId) {
  router.push(`/settings/schedule/__new__/general?legion_id=${legionId}`)
}

function startDelete(schedule) {
  pendingDeleteId.value    = schedule.schedule_id
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

/* ── Controls bar ─────────────────────────────────────── */
.controls {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px 11px;
  border-bottom: 1px solid var(--bs-border-color);
  flex-wrap: wrap;
}

.control-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-tertiary-color);
}

.seg {
  display: inline-flex;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  overflow: hidden;
}

.seg button {
  border: none;
  background: transparent;
  color: var(--bs-secondary-color);
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
  transition: background 0.1s, color 0.1s;
}

.seg button + button {
  border-left: 1px solid var(--bs-border-color);
}

.seg button.on {
  background: rgba(124, 58, 237, 0.15);
  color: #7c3aed;
}

.seg button:hover:not(.on) {
  color: var(--bs-emphasis-color);
}

.sort-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  color: var(--bs-secondary-color);
  font-size: 12px;
}

/* ── Section body ─────────────────────────────────────── */
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
  padding: 12px 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Group blocks ─────────────────────────────────────── */
.group-block {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 2px 6px;
  border-bottom: 1px solid var(--bs-border-color);
}

.group-header-left {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex: 1;
  cursor: pointer;
  user-select: none;
}

.chev {
  width: 14px;
  color: var(--bs-tertiary-color);
  font-size: 14px;
  line-height: 1;
  transition: transform 0.12s;
  flex-shrink: 0;
}

.chev.open {
  transform: rotate(90deg);
}

.group-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.group-label.state-active { color: #3fb950; }
.group-label.state-paused { color: #d29922; }
.group-label.ephemeral-label { color: #7c3aed; }

.group-count {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 9px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  color: var(--bs-secondary-color);
  font-size: 10.5px;
  font-weight: 600;
  flex-shrink: 0;
}

.group-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.group-bulk-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.1s, color 0.1s, border-color 0.1s;
}

.group-bulk-btn:hover {
  background: rgba(124, 58, 237, 0.12);
  color: #7c3aed;
  border-color: #7c3aed;
}

.group-add-btn {
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

.group-add-btn:hover {
  background: #7c3aed;
  border-color: #7c3aed;
  color: #fff;
}

/* Inline bulk confirm strip */
.group-confirm {
  background: rgba(210, 153, 34, 0.08);
  border: 1px solid rgba(210, 153, 34, 0.35);
  border-radius: 6px;
  padding: 5px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
}

.group-confirm-text {
  color: #d29922;
}

.group-confirm-btns {
  display: flex;
  gap: 6px;
}

.btn-bulk-confirm {
  padding: 3px 10px;
  border-radius: 5px;
  border: 1px solid #d29922;
  background: rgba(210, 153, 34, 0.15);
  color: #d29922;
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
  transition: background 0.1s, color 0.1s;
}

.btn-bulk-confirm:hover:not(:disabled) {
  background: #d29922;
  color: #fff;
}

.btn-bulk-confirm:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-bulk-cancel {
  padding: 3px 10px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: transparent;
  color: var(--bs-secondary-color);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
}

.btn-bulk-cancel:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

/* Group body */
.group-body {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* ── Subgroups (By Instance) ──────────────────────────── */
.subgroup {
  margin-left: 14px;
  border-left: 1px solid var(--bs-border-color);
  padding: 4px 0 4px 12px;
}

.subgroup-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 2px 4px 0;
  cursor: pointer;
  user-select: none;
}

.subgroup-left {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex: 1;
}

.subgroup-name {
  font-size: 12px;
  color: var(--bs-emphasis-color);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}

.subgroup-body {
  margin-top: 6px;
}

/* ── Empty state ─────────────────────────────────────── */
.empty-state {
  padding: 14px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

/* ── Schedule rows ───────────────────────────────────── */
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

.kind-chip {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: lowercase;
}

.kind-chip.ephemeral { background: rgba(124, 58, 237, 0.15); color: #7c3aed; }
.kind-chip.permanent { background: rgba(88, 166, 255, 0.15); color: #58a6ff; }

.repeat-chip {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
  font-family: monospace;
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  border: 1px solid var(--bs-border-color);
}

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

.row-state-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--bs-tertiary-color);
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.schedule-row:hover .row-state-btn {
  opacity: 1;
}

.row-state-btn.is-active {
  opacity: 1;
  color: #7c3aed;
}

.row-state-btn:hover {
  background: rgba(124, 58, 237, 0.12);
  color: #7c3aed;
}

/* ── Inline row delete confirm ───────────────────────── */
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
