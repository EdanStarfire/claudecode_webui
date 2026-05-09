<template>
  <div class="settings-section">
    <SettingsToolbar
      title="General"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <div v-if="!entity" class="section-loading">Loading…</div>

    <div v-else class="section-body">
      <!-- Name -->
      <div class="field-row">
        <label class="field-label">Schedule Name</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :value="draft?.name ?? entity.name ?? ''"
            placeholder="Schedule name"
            @input="handleField('name', $event.target.value)"
          />
        </div>
      </div>

      <!-- Status (direct API call — not a draft field) -->
      <div class="field-row">
        <label class="field-label">Status</label>
        <div class="field-control">
          <div class="status-btn-group">
            <button
              class="status-btn"
              :class="{ active: entity.status === 'active' }"
              :disabled="entity.status === 'cancelled'"
              @click="setStatus('active')"
            >Active</button>
            <button
              class="status-btn"
              :class="{ active: entity.status === 'paused' }"
              :disabled="entity.status === 'cancelled'"
              @click="setStatus('paused')"
            >Paused</button>
            <button
              class="status-btn status-btn--cancel"
              :class="{ active: entity.status === 'cancelled' }"
              @click="setStatus('cancelled')"
            >Cancelled</button>
          </div>
          <div v-if="entity.status === 'cancelled'" class="field-helper">Cancelled schedules cannot be reactivated.</div>
        </div>
      </div>

      <!-- Cron expression -->
      <div class="field-row">
        <label class="field-label">Cron Expression</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :class="{ 'is-invalid': cronError }"
            :value="draft?.cron_expression ?? entity.cron_expression ?? ''"
            placeholder="0 8 * * 1-5"
            @input="handleField('cron_expression', $event.target.value)"
          />
          <div class="cron-preview" :class="{ 'cron-error': cronError }">{{ cronPreview }}</div>
          <div class="cron-presets">
            <button type="button" class="preset-btn" @click="setCron('0 * * * *')">Every hour</button>
            <button type="button" class="preset-btn" @click="setCron('0 8 * * 1-5')">Weekdays 8am</button>
            <button type="button" class="preset-btn" @click="setCron('0 9 * * *')">Daily 9am</button>
            <button type="button" class="preset-btn" @click="setCron('0 0 * * 0')">Weekly Sun</button>
          </div>
        </div>
      </div>

      <!-- Schedule Type (read-only — immutable post-create) -->
      <div class="field-row">
        <label class="field-label">Type</label>
        <div class="field-control">
          <span class="field-value-readonly">{{ entity.schedule_type === 'script' ? 'Script' : 'Prompt' }}</span>
          <div class="field-helper">Schedule type cannot be changed after creation.</div>
        </div>
      </div>

      <!-- Prompt (prompt type only) -->
      <div v-if="entity.schedule_type !== 'script'" class="field-row">
        <label class="field-label">Prompt</label>
        <div class="field-control">
          <textarea
            class="field-input field-textarea"
            :value="draft?.prompt ?? entity.prompt ?? ''"
            rows="5"
            placeholder="What should the agent do when this schedule fires?"
            @input="handleField('prompt', $event.target.value)"
          />
        </div>
      </div>

      <!-- Script command (script type only) -->
      <template v-if="entity.schedule_type === 'script'">
        <div class="field-row">
          <label class="field-label">Command</label>
          <div class="field-control">
            <textarea
              class="field-input field-textarea field-textarea--mono"
              :value="draft?.script_command ?? entity.script_command ?? ''"
              rows="2"
              placeholder="/path/to/script.sh {working_dir}"
              @input="handleField('script_command', $event.target.value)"
            />
            <div class="field-helper">
              Available template variables: <code>&#123;session_id&#125;</code>, <code>&#123;working_dir&#125;</code>,
              <code>&#123;session_data&#125;</code>. stdout is sent to the agent on exit 0 + non-empty.
            </div>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Script Timeout (s)</label>
          <div class="field-control">
            <input
              type="number"
              class="field-input field-input--narrow"
              min="1"
              max="3600"
              :value="draft?.script_timeout_seconds ?? entity.script_timeout_seconds ?? 60"
              @input="handleField('script_timeout_seconds', Number($event.target.value))"
            />
          </div>
        </div>
      </template>

      <!-- Reset session toggle -->
      <div class="field-row">
        <label class="field-label">Reset Session</label>
        <div class="field-control">
          <label class="toggle-label">
            <input
              type="checkbox"
              :checked="draft?.reset_session ?? entity.reset_session ?? false"
              @change="handleField('reset_session', $event.target.checked)"
            />
            <span>Reset session before each execution</span>
          </label>
          <div class="field-helper">When enabled, the agent's context is cleared before each run.</div>
        </div>
      </div>

      <!-- Max retries -->
      <div class="field-row">
        <label class="field-label">Max Retries</label>
        <div class="field-control">
          <input
            type="number"
            class="field-input field-input--narrow"
            min="0"
            max="10"
            :value="draft?.max_retries ?? entity.max_retries ?? 0"
            @input="handleField('max_retries', Number($event.target.value))"
          />
          <div class="field-helper">Consecutive failures before the schedule is paused automatically (0 = never pause).</div>
        </div>
      </div>

      <!-- Bound entity (permanent or ephemeral) -->
      <div v-if="entity.minion_id && !entity.session_config" class="field-row">
        <label class="field-label">Bound Session</label>
        <div class="field-control">
          <button
            class="profile-link-chip"
            :title="`Open session settings`"
            @click="router.push(`/settings/session/${entity.minion_id}/general`)"
          >
            {{ boundMinionName || entity.minion_id.substring(0, 8) }}
          </button>
          <div class="field-helper">Permanent schedule — fires into this session.</div>
        </div>
      </div>

      <div v-else-if="entity.session_config" class="field-row">
        <label class="field-label">Ephemeral Agent</label>
        <div class="field-control">
          <template v-if="entity.ephemeral_agent_id">
            <button
              class="profile-link-chip"
              :title="`Open ephemeral agent settings`"
              @click="router.push(`/settings/session/${entity.ephemeral_agent_id}/general`)"
            >
              {{ ephemeralAgentName || entity.ephemeral_agent_id.substring(0, 8) }}
            </button>
          </template>
          <span v-else class="field-value-muted">Not yet created (will spawn on first run)</span>
          <div class="field-helper">
            <span v-if="entity.session_config.working_directory">Dir: {{ entity.session_config.working_directory }} · </span>
            <span v-if="entity.session_config.model">Model: {{ entity.session_config.model }}</span>
          </div>
        </div>
      </div>

      <!-- Telemetry block (read-only) -->
      <div class="telemetry-block">
        <div class="telemetry-header">Telemetry</div>

        <div v-if="entity.monitor_error" class="monitor-error-banner">
          Monitor error: {{ entity.monitor_error }}
        </div>

        <div class="field-row">
          <label class="field-label">Next Run</label>
          <div class="field-control">
            <span class="field-value-readonly">{{ entity.next_run ? formatTimestamp(entity.next_run) : 'Not scheduled' }}</span>
          </div>
        </div>

        <div class="field-row">
          <label class="field-label">Last Run</label>
          <div class="field-control">
            <span class="field-value-readonly">
              {{ entity.last_run ? formatTimestamp(entity.last_run) : 'Never' }}
              <span v-if="entity.last_status" class="last-status-chip" :class="entity.last_status">
                {{ entity.last_status }}
              </span>
              <span v-if="entity.last_exit_code !== null && entity.last_exit_code !== undefined" class="last-exit-code">
                exit {{ entity.last_exit_code }}
              </span>
            </span>
            <details v-if="entity.last_stdout" class="stream-details">
              <summary>stdout ({{ entity.last_stdout.length }} bytes)</summary>
              <pre class="stream-pre">{{ entity.last_stdout }}</pre>
            </details>
            <details v-if="entity.last_stderr" class="stream-details">
              <summary>stderr ({{ entity.last_stderr.length }} bytes)</summary>
              <pre class="stream-pre">{{ entity.last_stderr }}</pre>
            </details>
          </div>
        </div>

        <div class="field-row">
          <label class="field-label">Executions</label>
          <div class="field-control">
            <span class="field-value-readonly">
              {{ entity.execution_count ?? 0 }} total,
              {{ entity.failure_count ?? 0 }} failures
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useProjectStore } from '@/stores/project'
import { useScheduleStore } from '@/stores/schedule'
import { useSessionStore } from '@/stores/session'
import SettingsToolbar from '../SettingsToolbar.vue'
import { FIELD_RESET } from '@/composables/fieldResetSentinel.js'
import cronstrue from 'cronstrue'

const SECTION_KEY = 'general'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const projectStore  = useProjectStore()
const scheduleStore = useScheduleStore()
const sessionStore  = useSessionStore()

const entityId = computed(() => route.params.scheduleId || '')
const areaKey  = computed(() => `schedule:${entityId.value}:${SECTION_KEY}`)
const entity   = computed(() => scheduleStore.getSchedule(entityId.value))

const draft    = computed(() => settingsStore.getDraft(areaKey.value))
const isDirty  = computed(() => settingsStore.dirtyAreas.has(areaKey.value))
const saving   = ref(false)

// Cron preview
const currentCron = computed(() => draft.value?.cron_expression ?? entity.value?.cron_expression ?? '')
const cronPreview = computed(() => {
  const expr = currentCron.value.trim()
  if (!expr) return 'Enter a cron expression'
  try { return cronstrue.toString(expr) } catch { return 'Invalid cron expression' }
})
const cronError = computed(() => {
  const expr = currentCron.value.trim()
  if (!expr) return false
  try { cronstrue.toString(expr); return false } catch { return true }
})

// Bound entity names
const boundMinionName = computed(() => {
  if (!entity.value?.minion_id) return ''
  return sessionStore.getSession(entity.value.minion_id)?.name || ''
})

const ephemeralAgentName = computed(() => {
  if (!entity.value?.ephemeral_agent_id) return ''
  return sessionStore.getSession(entity.value.ephemeral_agent_id)?.name || ''
})

function formatTimestamp(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString()
}

function handleField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

function setCron(expr) {
  handleField('cron_expression', expr)
}

async function setStatus(newStatus) {
  if (!entity.value || entity.value.status === newStatus) return
  try {
    if (newStatus === 'active') {
      await scheduleStore.resumeSchedule(entity.value.legion_id, entity.value.schedule_id)
    } else if (newStatus === 'paused') {
      await scheduleStore.pauseSchedule(entity.value.legion_id, entity.value.schedule_id)
    } else if (newStatus === 'cancelled') {
      await scheduleStore.cancelSchedule(entity.value.legion_id, entity.value.schedule_id)
    }
  } catch (err) {
    console.error('Status change failed:', err)
  }
}

async function handleSave() {
  if (!entity.value || !isDirty.value) return
  saving.value = true
  try {
    const d = { ...draft.value }
    const keysToDelete = Object.keys(d).filter(k => d[k] === FIELD_RESET)
    for (const k of keysToDelete) delete d[k]
    const updates = {}
    for (const k of ['name', 'cron_expression', 'prompt', 'reset_session', 'max_retries', 'script_command', 'script_timeout_seconds']) {
      if (k in d) updates[k] = d[k]
    }
    await scheduleStore.updateSchedule(entity.value.legion_id, entity.value.schedule_id, updates)
    settingsStore.markClean(areaKey.value)
  } catch (err) {
    console.error('Save failed:', err)
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  settingsStore.discardDraft(areaKey.value)
}

defineExpose({ save: handleSave, cancel: handleCancel })

onMounted(async () => {
  sessionStore.fetchSessions?.()
  await projectStore.fetchProjects()
  await scheduleStore.loadAllSchedules()
})
</script>

<style scoped>
.settings-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-loading {
  padding: 24px 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── Field rows ──────────────────────────────────────────── */
.field-row {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  align-items: start;
  padding: 12px 0;
  border-bottom: 1px solid var(--bs-border-color);
}

@container settings-area (max-width: 599px) {
  .field-row {
    grid-template-columns: 1fr;
  }
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  padding-top: 6px;
  white-space: nowrap;
}

.field-control {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  background: var(--bs-body-bg);
  color: var(--bs-body-color);
  font-size: 13px;
  outline: none;
  transition: border-color 0.12s;
}

.field-input:focus {
  border-color: #7c3aed;
}

.field-input.is-invalid {
  border-color: #f87171;
}

.field-input--narrow {
  max-width: 120px;
}

.field-textarea {
  resize: vertical;
  min-height: 100px;
  font-family: inherit;
}

.field-helper {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  line-height: 1.5;
}

.field-value-readonly {
  font-size: 13px;
  color: var(--bs-body-color);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.field-value-muted {
  font-size: 13px;
  color: var(--bs-tertiary-color);
  font-style: italic;
}

/* ── Status buttons ─────────────────────────────────────── */
.status-btn-group {
  display: flex;
  gap: 6px;
}

.status-btn {
  padding: 4px 14px;
  border-radius: 6px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.status-btn:hover:not(:disabled):not(.active) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

.status-btn.active {
  background: #7c3aed;
  border-color: #7c3aed;
  color: #fff;
}

.status-btn--cancel.active {
  background: rgba(248, 113, 113, 0.2);
  border-color: #f87171;
  color: #f87171;
}

.status-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* ── Cron ────────────────────────────────────────────────── */
.cron-preview {
  font-size: 12px;
  color: var(--bs-secondary-color);
}

.cron-error {
  color: #f87171;
}

.cron-presets {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.preset-btn {
  padding: 2px 10px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.preset-btn:hover {
  background: var(--mode-tint, var(--bs-secondary-bg));
  border-color: #7c3aed;
  color: #7c3aed;
}

/* ── Toggle ──────────────────────────────────────────────── */
.toggle-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}

/* ── Profile link chip ───────────────────────────────────── */
.profile-link-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-tertiary-bg);
  color: var(--bs-secondary-color);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.12s, color 0.12s;
}

.profile-link-chip:hover {
  border-color: #7c3aed;
  color: #7c3aed;
}

/* ── Telemetry ───────────────────────────────────────────── */
.telemetry-block {
  margin-top: 16px;
  border-top: 1px solid var(--bs-border-color);
  padding-top: 12px;
}

.telemetry-header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-tertiary-color);
  margin-bottom: 8px;
}

.monitor-error-banner {
  padding: 8px 12px;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 6px;
  color: #f87171;
  font-size: 12px;
  margin-bottom: 12px;
}

/* ── Last status chip ────────────────────────────────────── */
.last-status-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.last-status-chip.delivered { background: rgba(63, 185, 80, 0.2); color: #3fb950; }
.last-status-chip.failed    { background: rgba(248, 113, 113, 0.2); color: #f87171; }
.last-status-chip.queued    { background: rgba(210, 153, 34, 0.2); color: #d29922; }
.last-status-chip.discarded { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
.last-status-chip.error     { background: rgba(248, 113, 113, 0.2); color: #f87171; }

.last-exit-code {
  font-size: 11px;
  color: var(--bs-secondary-color);
  font-family: monospace;
}

.stream-details {
  margin-top: 6px;
  font-size: 12px;
}

.stream-details summary {
  cursor: pointer;
  color: var(--bs-secondary-color);
  user-select: none;
  padding: 2px 0;
}

.stream-details summary:hover {
  color: var(--bs-body-color);
}

.stream-pre {
  margin: 4px 0 0 0;
  padding: 8px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  font-size: 11px;
  font-family: monospace;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.field-textarea--mono {
  font-family: monospace;
  font-size: 12px;
}

.field-control code {
  font-family: monospace;
  background: var(--bs-secondary-bg);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
