<template>
  <div class="schedule-item" :class="schedule.status">
    <!-- Header -->
    <div class="schedule-header">
      <template v-if="!editingName">
        <span
          class="schedule-name"
          :class="{ editable: schedule.status !== 'cancelled' }"
          :title="schedule.status !== 'cancelled' ? 'Click to edit name' : ''"
          @click.stop="schedule.status !== 'cancelled' && startEditName()"
        >{{ schedule.name }}</span>
      </template>
      <template v-else>
        <input
          ref="nameInput"
          v-model="editNameText"
          class="inline-edit-input name-input"
          @keydown.enter.stop="saveName"
          @keydown.escape.stop="cancelEditName"
          @blur="saveName"
        />
      </template>
      <span class="status-badge" :class="schedule.status">{{ schedule.status }}</span>
    </div>

    <!-- Cron + next run -->
    <div class="schedule-meta">
      <template v-if="!editingCron">
        <span
          class="cron-desc"
          :class="{ editable: schedule.status !== 'cancelled' }"
          :title="schedule.status !== 'cancelled' ? `${schedule.cron_expression} — click to edit` : schedule.cron_expression"
          @click.stop="schedule.status !== 'cancelled' && startEditCron()"
        >{{ cronDescription }}</span>
      </template>
      <template v-else>
        <div class="cron-edit-row">
          <input
            ref="cronInput"
            v-model="editCronText"
            class="inline-edit-input cron-input"
            placeholder="e.g. */5 * * * *"
            @keydown.enter.stop="saveCron"
            @keydown.escape.stop="cancelEditCron"
          />
          <button class="ctrl-btn save" @click.stop="saveCron" :disabled="!isCronValid">Save</button>
          <button class="ctrl-btn" @click.stop="cancelEditCron">Cancel</button>
        </div>
        <div class="cron-preview" :class="{ invalid: !isCronValid }">
          {{ cronEditPreview }}
        </div>
      </template>
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

    <!-- Prompt viewer/editor -->
    <div class="prompt-section">
      <div class="prompt-toggle" @click.stop="showPrompt = !showPrompt">
        <span class="prompt-label">Prompt</span>
        <span class="prompt-arrow">{{ showPrompt ? '▾' : '▸' }}</span>
      </div>
      <div v-if="showPrompt" class="prompt-body">
        <template v-if="!editingPrompt">
          <pre class="prompt-text">{{ schedule.prompt }}</pre>
          <button
            v-if="schedule.status !== 'cancelled'"
            class="ctrl-btn edit-prompt"
            @click.stop="startEditPrompt"
          >Edit</button>
        </template>
        <template v-else>
          <textarea
            ref="promptInput"
            v-model="editPromptText"
            class="prompt-editor"
            rows="5"
            @keydown.escape.stop="cancelEditPrompt"
          ></textarea>
          <div class="prompt-edit-controls">
            <button class="ctrl-btn save" @click.stop="savePrompt" :disabled="saving">
              {{ saving ? 'Saving...' : 'Save' }}
            </button>
            <button class="ctrl-btn" @click.stop="cancelEditPrompt">Cancel</button>
          </div>
        </template>
      </div>
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
import { ref, computed, nextTick } from 'vue'
import { useScheduleStore } from '@/stores/schedule'
import cronstrue from 'cronstrue'

const props = defineProps({
  schedule: { type: Object, required: true },
  legionId: { type: String, required: true },
})

const scheduleStore = useScheduleStore()
const expanded = ref(false)
const history = ref([])
const showPrompt = ref(false)
const editingPrompt = ref(false)
const editPromptText = ref('')
const saving = ref(false)
const promptInput = ref(null)
const editingName = ref(false)
const editNameText = ref('')
const nameInput = ref(null)
const editingCron = ref(false)
const editCronText = ref('')
const cronInput = ref(null)

const cronDescription = computed(() => {
  try {
    return cronstrue.toString(props.schedule.cron_expression)
  } catch {
    return props.schedule.cron_expression
  }
})

const cronEditPreview = computed(() => {
  if (!editCronText.value.trim()) return 'Enter a cron expression'
  try {
    return cronstrue.toString(editCronText.value.trim())
  } catch {
    return 'Invalid cron expression'
  }
})

const isCronValid = computed(() => {
  if (!editCronText.value.trim()) return false
  try {
    cronstrue.toString(editCronText.value.trim())
    return true
  } catch {
    return false
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

function startEditName() {
  editNameText.value = props.schedule.name
  editingName.value = true
  nextTick(() => {
    if (nameInput.value) {
      nameInput.value.focus()
      nameInput.value.select()
    }
  })
}

function cancelEditName() {
  editingName.value = false
  editNameText.value = ''
}

async function saveName() {
  const trimmed = editNameText.value.trim()
  if (!trimmed || trimmed === props.schedule.name) {
    cancelEditName()
    return
  }
  try {
    await scheduleStore.updateSchedule(
      props.legionId,
      props.schedule.schedule_id,
      { name: trimmed }
    )
  } catch (e) {
    console.error('Failed to update name:', e)
  }
  editingName.value = false
  editNameText.value = ''
}

function startEditCron() {
  editCronText.value = props.schedule.cron_expression
  editingCron.value = true
  nextTick(() => {
    if (cronInput.value) {
      cronInput.value.focus()
      cronInput.value.select()
    }
  })
}

function cancelEditCron() {
  editingCron.value = false
  editCronText.value = ''
}

async function saveCron() {
  const trimmed = editCronText.value.trim()
  if (!trimmed || trimmed === props.schedule.cron_expression || !isCronValid.value) {
    cancelEditCron()
    return
  }
  try {
    await scheduleStore.updateSchedule(
      props.legionId,
      props.schedule.schedule_id,
      { cron_expression: trimmed }
    )
  } catch (e) {
    console.error('Failed to update cron expression:', e)
  }
  editingCron.value = false
  editCronText.value = ''
}

function startEditPrompt() {
  editPromptText.value = props.schedule.prompt
  editingPrompt.value = true
  nextTick(() => {
    if (promptInput.value) promptInput.value.focus()
  })
}

function cancelEditPrompt() {
  editingPrompt.value = false
  editPromptText.value = ''
}

async function savePrompt() {
  const trimmed = editPromptText.value.trim()
  if (!trimmed || trimmed === props.schedule.prompt) {
    cancelEditPrompt()
    return
  }
  saving.value = true
  try {
    await scheduleStore.updateSchedule(
      props.legionId,
      props.schedule.schedule_id,
      { prompt: trimmed }
    )
    editingPrompt.value = false
    editPromptText.value = ''
  } catch (e) {
    console.error('Failed to update prompt:', e)
  } finally {
    saving.value = false
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

.schedule-name.editable,
.cron-desc.editable {
  cursor: pointer;
  border-bottom: 1px dashed transparent;
}

.schedule-name.editable:hover,
.cron-desc.editable:hover {
  border-bottom-color: #94a3b8;
}

.inline-edit-input {
  border: 1px solid #6366f1;
  border-radius: 4px;
  font-size: inherit;
  font-family: inherit;
  color: #334155;
  padding: 1px 4px;
  outline: none;
  box-sizing: border-box;
}

.inline-edit-input:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
}

.name-input {
  font-weight: 600;
  font-size: 12px;
  flex: 1;
  min-width: 0;
}

.cron-input {
  font-size: 11px;
  flex: 1;
  min-width: 0;
  font-family: monospace;
}

.cron-edit-row {
  display: flex;
  gap: 4px;
  align-items: center;
}

.cron-preview {
  font-size: 10px;
  color: #16a34a;
  margin-top: 2px;
}

.cron-preview.invalid {
  color: #dc2626;
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

.prompt-section {
  margin-bottom: 6px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.prompt-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 8px;
  cursor: pointer;
  background: #f1f5f9;
  user-select: none;
}

.prompt-toggle:hover {
  background: #e2e8f0;
}

.prompt-label {
  font-size: 11px;
  font-weight: 500;
  color: #475569;
}

.prompt-arrow {
  font-size: 10px;
  color: #94a3b8;
}

.prompt-body {
  padding: 6px 8px;
}

.prompt-text {
  margin: 0 0 4px 0;
  padding: 4px 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  font-size: 11px;
  font-family: inherit;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 150px;
  overflow-y: auto;
}

.prompt-editor {
  width: 100%;
  padding: 6px;
  border: 1px solid #6366f1;
  border-radius: 4px;
  font-size: 11px;
  font-family: inherit;
  color: #334155;
  resize: vertical;
  min-height: 60px;
  outline: none;
  box-sizing: border-box;
}

.prompt-editor:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
}

.prompt-edit-controls {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.ctrl-btn.edit-prompt {
  border-color: #c7d2fe;
  color: #4338ca;
}

.ctrl-btn.save {
  border-color: #86efac;
  color: #166534;
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
