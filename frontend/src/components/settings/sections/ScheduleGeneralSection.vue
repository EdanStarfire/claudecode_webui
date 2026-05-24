<template>
  <div class="settings-section">
    <SettingsToolbar
      title="General"
      :show-save-cancel="isDirty"
      :saving="saving"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <!-- ── NEW mode ──────────────────────────────────────────────── -->
    <div v-if="isNew" class="section-body">
      <!-- Validation error banner -->
      <div v-if="saveError" class="validation-banner">{{ saveError }}</div>

      <!-- Schedule Name -->
      <div class="field-row">
        <label class="field-label">Schedule Name</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :class="{ 'is-invalid': touched && !newDraftName.trim() }"
            :value="newDraftName"
            placeholder="Daily status report"
            @input="handleField('name', $event.target.value)"
          />
          <div v-if="touched && !newDraftName.trim()" class="field-error">Name is required</div>
        </div>
      </div>

      <!-- Legion (read-only if query param; else select) -->
      <div class="field-row">
        <label class="field-label">Legion</label>
        <div class="field-control">
          <template v-if="legionIdFromQuery">
            <span class="field-value-readonly">{{ legionName }}</span>
          </template>
          <template v-else>
            <select
              class="field-input field-select"
              :class="{ 'is-invalid': touched && !effectiveLegionId }"
              :value="newDraftLegionId"
              @change="handleField('legion_id', $event.target.value)"
            >
              <option value="" disabled>Select a legion…</option>
              <option
                v-for="p in allLegions"
                :key="p.project_id"
                :value="p.project_id"
              >{{ p.name }}</option>
            </select>
            <div v-if="touched && !effectiveLegionId" class="field-error">Select a legion</div>
          </template>
        </div>
      </div>

      <!-- Kind -->
      <div class="field-row">
        <label class="field-label">Kind</label>
        <div class="field-control">
          <div class="seg-btns">
            <button
              type="button"
              class="seg-btn"
              :class="{ active: newDraftKind === 'ephemeral' }"
              @click="setKind('ephemeral')"
            >Ephemeral</button>
            <button
              type="button"
              class="seg-btn"
              :class="{ active: newDraftKind === 'permanent' }"
              @click="setKind('permanent')"
            >Permanent</button>
          </div>
          <div class="field-helper">
            {{ newDraftKind === 'permanent'
              ? 'Schedule fires into an existing agent session.'
              : 'A dedicated ephemeral agent is created for this schedule.' }}
          </div>
        </div>
      </div>

      <!-- Cron Expression -->
      <div class="field-row">
        <label class="field-label">Cron Expression</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :class="{ 'is-invalid': touched && cronError }"
            :value="draft?.cron_expression ?? ''"
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
          <div v-if="touched && cronError" class="field-error">Valid cron expression required</div>
        </div>
      </div>

      <!-- Schedule Type -->
      <div class="field-row">
        <label class="field-label">Type</label>
        <div class="field-control">
          <div class="seg-btns">
            <button
              type="button"
              class="seg-btn"
              :class="{ active: newDraftScheduleType === 'prompt' }"
              @click="handleField('schedule_type', 'prompt')"
            >Prompt</button>
            <button
              type="button"
              class="seg-btn"
              :class="{ active: newDraftScheduleType === 'script' }"
              @click="handleField('schedule_type', 'script')"
            >Script</button>
          </div>
        </div>
      </div>

      <!-- Prompt (prompt type only) -->
      <div v-if="newDraftScheduleType === 'prompt'" class="field-row">
        <label class="field-label">Prompt</label>
        <div class="field-control">
          <textarea
            class="field-input field-textarea"
            :class="{ 'is-invalid': touched && !newDraftPrompt.trim() }"
            :value="newDraftPrompt"
            rows="5"
            placeholder="What should the agent do when this schedule fires?"
            @input="handleField('prompt', $event.target.value)"
          />
          <div v-if="touched && !newDraftPrompt.trim()" class="field-error">Prompt is required</div>
        </div>
      </div>

      <!-- Script fields (script type only) -->
      <template v-if="newDraftScheduleType === 'script'">
        <div class="field-row">
          <label class="field-label">Command</label>
          <div class="field-control">
            <textarea
              class="field-input field-textarea field-textarea--mono"
              :class="{ 'is-invalid': touched && !newDraftScriptCommand.trim() }"
              :value="newDraftScriptCommand"
              rows="2"
              placeholder="/path/to/script.sh {working_dir}"
              @input="handleField('script_command', $event.target.value)"
            />
            <div class="field-helper">
              Available: <code>&#123;session_id&#125;</code>, <code>&#123;working_dir&#125;</code>, <code>&#123;session_data&#125;</code>.
              stdout sent to agent on exit 0 + non-empty.
            </div>
            <div v-if="touched && !newDraftScriptCommand.trim()" class="field-error">Script command is required</div>
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
              :value="newDraftScriptTimeout"
              @input="handleField('script_timeout_seconds', Number($event.target.value))"
            />
          </div>
        </div>
      </template>

      <!-- Repeat count -->
      <div class="field-row">
        <label class="field-label">Repeat</label>
        <div class="field-control">
          <div class="repeat-editor">
            <input
              type="number"
              class="field-input field-input--narrow"
              min="1"
              :value="newDraftRepeatCount"
              :disabled="newDraftIsUnlimited"
              @input="handleField('repeat_count', parseInt($event.target.value, 10) || 1)"
            />
            <label class="toggle-label">
              <input
                type="checkbox"
                :checked="newDraftIsUnlimited"
                @change="handleUnlimitedToggleNew($event.target.checked)"
              />
              <span>Unlimited</span>
            </label>
          </div>
          <div class="field-helper">Fires N times then self-deletes. Manual runs also consume the count.</div>
        </div>
      </div>

      <!-- Reset Session (permanent only) -->
      <div v-if="newDraftKind === 'permanent'" class="field-row">
        <label class="field-label">Reset Session</label>
        <div class="field-control">
          <label class="toggle-label">
            <input
              type="checkbox"
              :checked="newDraftResetSession"
              @change="handleField('reset_session', $event.target.checked)"
            />
            <span>Reset session before each execution</span>
          </label>
          <div class="field-helper">When enabled, the agent's context is cleared before each run.</div>
        </div>
      </div>

      <!-- ── Permanent: agent selector ──────────────────────── -->
      <div v-if="newDraftKind === 'permanent'" class="field-row">
        <label class="field-label">Agent</label>
        <div class="field-control">
          <select
            class="field-input field-select"
            :class="{ 'is-invalid': touched && !newDraftMinionId }"
            :value="newDraftMinionId"
            @change="handleField('minion_id', $event.target.value)"
          >
            <option value="" disabled>Select an agent…</option>
            <option
              v-for="s in projectSessions"
              :key="s.session_id"
              :value="s.session_id"
            >{{ s.name || s.session_id.substring(0, 8) }}</option>
          </select>
          <div v-if="touched && !newDraftMinionId" class="field-error">Select an agent</div>
        </div>
      </div>

      <!-- ── Ephemeral: config source ────────────────────────── -->
      <template v-if="newDraftKind === 'ephemeral'">
        <div class="field-row">
          <label class="field-label">Config Source</label>
          <div class="field-control">
            <div class="seg-btns">
              <button
                type="button"
                class="seg-btn seg-btn--sm"
                :class="{ active: newDraftConfigSource === 'capture' }"
                @click="handleField('config_source', 'capture')"
              >Capture from Session</button>
              <button
                type="button"
                class="seg-btn seg-btn--sm"
                :class="{ active: newDraftConfigSource === 'template' }"
                @click="handleField('config_source', 'template')"
              >Start from Template</button>
              <button
                type="button"
                class="seg-btn seg-btn--sm"
                :class="{ active: newDraftConfigSource === 'blank' }"
                @click="applyBlank"
              >Start blank</button>
            </div>
          </div>
        </div>

        <!-- Capture from session -->
        <div v-if="newDraftConfigSource === 'capture'" class="field-row">
          <label class="field-label">Source Session</label>
          <div class="field-control">
            <div class="capture-row">
              <select
                class="field-input field-select"
                :value="captureSessionId"
                @change="captureSessionId = $event.target.value"
              >
                <option value="" disabled>Select a session…</option>
                <option
                  v-for="s in projectSessions"
                  :key="s.session_id"
                  :value="s.session_id"
                >{{ s.name || s.session_id.substring(0, 8) }}</option>
              </select>
              <button
                type="button"
                class="btn-capture"
                :disabled="!captureSessionId"
                @click="captureConfig"
              >Capture</button>
            </div>
            <div v-if="newDraftSessionConfig" class="capture-success">
              Configuration captured{{ capturedSessionName ? ` from "${capturedSessionName}"` : '' }}
            </div>
          </div>
        </div>

        <!-- Start from template -->
        <div v-if="newDraftConfigSource === 'template'" class="field-row">
          <label class="field-label">Template</label>
          <div class="field-control">
            <select
              class="field-input field-select"
              :value="selectedTemplateId"
              @change="selectedTemplateId = $event.target.value; applyTemplate()"
            >
              <option value="" disabled>Select a template…</option>
              <option
                v-for="tmpl in templateStore.templateList"
                :key="tmpl.template_id"
                :value="tmpl.template_id"
              >{{ tmpl.name }}</option>
            </select>
            <div v-if="newDraftSessionConfig && newDraftConfigSource === 'template'" class="capture-success">
              Template applied
            </div>
          </div>
        </div>

        <!-- Config summary card -->
        <div v-if="newDraftSessionConfig" class="field-row">
          <label class="field-label">Config</label>
          <div class="field-control">
            <div class="config-summary">
              <div class="config-summary-line">
                <span class="config-label">Working Dir:</span>
                <span class="config-value">{{ newDraftSessionConfig.working_directory || '(default)' }}</span>
              </div>
              <div class="config-summary-line">
                <span class="config-label">Model:</span>
                <span class="config-value">{{ newDraftSessionConfig.model || 'default' }}</span>
              </div>
              <div class="config-summary-line">
                <span class="config-label">Permissions:</span>
                <span class="config-value">{{ newDraftSessionConfig.permission_mode || 'default' }}</span>
              </div>
              <div class="config-hint">Further configuration available in Schedule Settings after creation.</div>
            </div>
            <div v-if="touched && !newDraftSessionConfig?.working_directory" class="field-error">
              Working directory is required. Choose a different config source or ensure the legion has a working directory set.
            </div>
          </div>
        </div>
        <div v-else-if="touched" class="field-row">
          <label class="field-label"></label>
          <div class="field-control">
            <div class="field-error">Session configuration is required. Use Capture, Template, or Start blank.</div>
          </div>
        </div>
      </template>
    </div>

    <!-- ── EDIT mode: loading ─────────────────────────────────── -->
    <div v-else-if="!entity" class="section-loading">Loading…</div>

    <!-- ── EDIT mode: form ────────────────────────────────────── -->
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

      <!-- Repeat count (issue #1538) -->
      <div class="field-row">
        <label class="field-label">Repeat</label>
        <div class="field-control">
          <div class="repeat-editor">
            <input
              type="number"
              class="field-input field-input--narrow"
              min="1"
              :value="repeatCountValue"
              :disabled="isUnlimited"
              @input="handleRepeatCountInput($event.target.value)"
            />
            <label class="toggle-label">
              <input
                type="checkbox"
                :checked="isUnlimited"
                @change="handleUnlimitedToggle($event.target.checked)"
              />
              <span>Unlimited</span>
            </label>
          </div>
          <div class="field-helper">
            Fires N times then self-deletes. Manual runs also consume the count.
            Currently: {{ fireCountLabel }}.
          </div>
        </div>
      </div>

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
              <template v-if="entity.repeat_count != null">
                {{ entity.fire_count ?? 0 }} / {{ entity.repeat_count }} fires
              </template>
              <template v-else>
                {{ entity.fire_count ?? 0 }} fires
              </template>
              ({{ entity.execution_count ?? 0 }} total dispatches)
            </span>
          </div>
        </div>

        <div class="field-row">
          <label class="field-label">Consecutive errors</label>
          <div class="field-control">
            <span class="field-value-readonly" title="Resets to 0 after a successful run">{{ entity.failure_count ?? 0 }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useProjectStore } from '@/stores/project'
import { useScheduleStore } from '@/stores/schedule'
import { useSessionStore } from '@/stores/session'
import { useTemplateStore } from '@/stores/template'
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
const templateStore = useTemplateStore()

const entityId = computed(() => route.params.scheduleId || '')
const isNew    = computed(() => entityId.value === '__new__')
const areaKey  = computed(() => `schedule:${entityId.value}:${SECTION_KEY}`)
const entity   = computed(() => scheduleStore.getSchedule(entityId.value))

// ── __new__ mode query params ─────────────────────────────────────────────

const legionIdFromQuery  = computed(() => route.query.legion_id || '')
const minionIdFromQuery  = computed(() => route.query.minion_id || '')

// ── __new__ mode local UI state (not persisted in draft) ─────────────────

const captureSessionId  = ref('')
const capturedSessionName = ref('')
const selectedTemplateId = ref('')
const touched = ref(false)
const saveError = ref('')

// ── Draft accessors ───────────────────────────────────────────────────────

const draft    = computed(() => settingsStore.getDraft(areaKey.value))
const isDirty  = computed(() => isNew.value || settingsStore.dirtyAreas.has(areaKey.value))
const saving   = ref(false)

// New-mode draft field helpers
const newDraftName         = computed(() => draft.value?.name ?? '')
const newDraftLegionId     = computed(() => draft.value?.legion_id ?? '')
const newDraftKind         = computed(() => draft.value?.kind ?? (minionIdFromQuery.value ? 'permanent' : 'ephemeral'))
const newDraftScheduleType = computed(() => draft.value?.schedule_type ?? 'prompt')
const newDraftPrompt       = computed(() => draft.value?.prompt ?? '')
const newDraftScriptCommand  = computed(() => draft.value?.script_command ?? '')
const newDraftScriptTimeout  = computed(() => draft.value?.script_timeout_seconds ?? 60)
const newDraftRepeatCount    = computed(() => draft.value?.is_unlimited ? 1 : (draft.value?.repeat_count ?? 1))
const newDraftIsUnlimited    = computed(() => draft.value?.is_unlimited ?? false)
const newDraftResetSession   = computed(() => draft.value?.reset_session ?? false)
const newDraftMinionId       = computed(() => draft.value?.minion_id ?? minionIdFromQuery.value ?? '')
const newDraftConfigSource   = computed(() => draft.value?.config_source ?? 'capture')
const newDraftSessionConfig  = computed(() => draft.value?.session_config ?? null)

const effectiveLegionId = computed(() => legionIdFromQuery.value || newDraftLegionId.value)

// Legion display name
const legionName = computed(() => {
  if (!effectiveLegionId.value) return ''
  return projectStore.getProject(effectiveLegionId.value)?.name || effectiveLegionId.value
})

// All multi-agent legions for the legion selector
const allLegions = computed(() => {
  return [...projectStore.projects.values()].filter(p => p.is_multi_agent)
})

// Sessions in the selected legion (non-ephemeral only)
const projectSessions = computed(() => {
  if (!effectiveLegionId.value) return []
  return sessionStore.sessionsInProject(effectiveLegionId.value).value.filter(s => !s.is_ephemeral)
})

// ── Edit-mode repeat count helpers (issue #1538) ──────────────────────────

const draftRepeatCount    = computed(() => draft.value?.repeat_count)
const draftRepeatCountSet = computed(() => 'repeat_count' in (draft.value || {}))
const effectiveRepeatCount = computed(() => {
  if (draftRepeatCountSet.value) return draftRepeatCount.value
  return entity.value?.repeat_count ?? null
})
const isUnlimited      = computed(() => effectiveRepeatCount.value === null)
const repeatCountValue = computed(() => effectiveRepeatCount.value ?? 1)

const fireCountLabel = computed(() => {
  const fc = entity.value?.fire_count ?? 0
  const rc = effectiveRepeatCount.value
  if (rc != null) return `${fc} / ${rc} fires`
  return `${fc} fires`
})

function handleRepeatCountInput(raw) {
  const v = parseInt(raw, 10)
  if (!isNaN(v) && v >= 1) {
    settingsStore.setField(areaKey.value, 'repeat_count', v)
    settingsStore.setField(areaKey.value, 'repeat_count_set', true)
  }
}

function handleUnlimitedToggle(checked) {
  settingsStore.setField(areaKey.value, 'repeat_count', checked ? null : 1)
  settingsStore.setField(areaKey.value, 'repeat_count_set', true)
}

// New-mode unlimited toggle (simpler — uses is_unlimited flag)
function handleUnlimitedToggleNew(checked) {
  handleField('is_unlimited', checked)
  if (!checked) handleField('repeat_count', 1)
}

// ── Cron preview (shared edit + new mode) ────────────────────────────────

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

// ── Edit-mode bound entity names ─────────────────────────────────────────

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

// ── Field helpers ─────────────────────────────────────────────────────────

function handleField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

function setCron(expr) {
  handleField('cron_expression', expr)
}

// ── __new__ mode: kind change ─────────────────────────────────────────────

function setKind(val) {
  handleField('kind', val)
  // Clear stale cross-branch fields
  if (val === 'permanent') {
    handleField('session_config', null)
  } else {
    handleField('minion_id', '')
    handleField('reset_session', false)
  }
}

// ── __new__ mode: session config builders ─────────────────────────────────

function captureConfig() {
  const session = projectSessions.value.find(s => s.session_id === captureSessionId.value)
  if (!session) return
  const cfg = {
    working_directory: session.working_directory || '',
    model: session.config?.model || '',
    permission_mode: session.current_permission_mode || 'acceptEdits',
    system_prompt: session.config?.system_prompt || '',
    override_system_prompt: session.config?.override_system_prompt || false,
    sandbox_enabled: session.config?.sandbox_enabled || false,
  }
  if (session.config?.allowed_tools)      cfg.allowed_tools = [...session.config.allowed_tools]
  if (session.config?.disallowed_tools)   cfg.disallowed_tools = [...session.config.disallowed_tools]
  if (session.config?.setting_sources)    cfg.setting_sources = [...session.config.setting_sources]
  if (session.config?.sandbox_config)     cfg.sandbox_config = { ...session.config.sandbox_config }
  if (session.config?.docker_enabled) {
    cfg.docker_enabled = true
    cfg.docker_image = session.config.docker_image || null
    cfg.docker_extra_mounts = session.config.docker_extra_mounts || null
  }
  if (session.config?.thinking_mode) {
    cfg.thinking_mode = session.config.thinking_mode
    cfg.thinking_budget_tokens = session.config.thinking_budget_tokens || null
  }
  if (session.config?.effort)    cfg.effort = session.config.effort
  if (session.config?.cli_path)  cfg.cli_path = session.config.cli_path

  handleField('session_config', cfg)
  capturedSessionName.value = session.name || session.session_id.substring(0, 8)
}

function applyTemplate() {
  const tmpl = templateStore.getTemplate(selectedTemplateId.value)
  if (!tmpl) return
  const project = projectStore.getProject(effectiveLegionId.value)
  const cfg = {
    permission_mode: tmpl.permission_mode || 'default',
    model: tmpl.model || '',
    system_prompt: tmpl.system_prompt || '',
    override_system_prompt: tmpl.override_system_prompt || false,
    sandbox_enabled: tmpl.sandbox_enabled || false,
    working_directory: project?.working_directory || '',
  }
  if (tmpl.allowed_tools?.length)   cfg.allowed_tools = [...tmpl.allowed_tools]
  if (tmpl.disallowed_tools?.length) cfg.disallowed_tools = [...tmpl.disallowed_tools]
  if (tmpl.sandbox_config)          cfg.sandbox_config = { ...tmpl.sandbox_config }
  if (tmpl.cli_path)                cfg.cli_path = tmpl.cli_path
  if (tmpl.docker_enabled) {
    cfg.docker_enabled = true
    cfg.docker_image = tmpl.docker_image || null
    cfg.docker_extra_mounts = tmpl.docker_extra_mounts || null
  }
  if (tmpl.thinking_mode) {
    cfg.thinking_mode = tmpl.thinking_mode
    cfg.thinking_budget_tokens = tmpl.thinking_budget_tokens || null
  }
  if (tmpl.effort) cfg.effort = tmpl.effort

  handleField('session_config', cfg)
}

function applyBlank() {
  handleField('config_source', 'blank')
  const project = projectStore.getProject(effectiveLegionId.value)
  handleField('session_config', { working_directory: project?.working_directory || '' })
}

function buildSessionConfigPayload(cfg) {
  const result = { ...cfg }
  for (const key of Object.keys(result)) {
    if (result[key] === '' || result[key] === null) delete result[key]
  }
  return result
}

// ── New-mode validation ───────────────────────────────────────────────────

function validateNew() {
  const d = draft.value || {}
  const kind = d.kind ?? (minionIdFromQuery.value ? 'permanent' : 'ephemeral')
  const stype = d.schedule_type ?? 'prompt'
  const errors = []

  if (!(d.name || '').trim())            errors.push('Name is required')
  if (!d.cron_expression || cronError.value) errors.push('Valid cron expression required')
  if (stype === 'prompt' && !(d.prompt || '').trim()) errors.push('Prompt is required')
  if (stype === 'script' && !(d.script_command || '').trim()) errors.push('Script command is required')
  if (kind === 'permanent' && !d.minion_id && !minionIdFromQuery.value) errors.push('Select an agent')
  if (kind === 'ephemeral') {
    if (!d.session_config) errors.push('Session configuration is required')
    else if (!d.session_config.working_directory) errors.push('Working directory is required in the session config')
  }
  if (!effectiveLegionId.value) errors.push('Select a legion')

  return errors
}

// ── Save / Cancel ─────────────────────────────────────────────────────────

async function handleSave() {
  if (isNew.value) {
    touched.value = true
    saveError.value = ''
    const errors = validateNew()
    if (errors.length > 0) {
      saveError.value = errors.join(' · ')
      return
    }

    saving.value = true
    try {
      const d = draft.value || {}
      const kind = d.kind ?? (minionIdFromQuery.value ? 'permanent' : 'ephemeral')
      const stype = d.schedule_type ?? 'prompt'
      const legionId = effectiveLegionId.value

      const payload = {
        name: (d.name || '').trim() || 'New Schedule',
        cron_expression: d.cron_expression || '',
        prompt: stype === 'prompt' ? (d.prompt || '') : '',
        schedule_type: stype,
        script_command: stype === 'script' ? (d.script_command || null) : null,
        script_timeout_seconds: d.script_timeout_seconds ?? 60,
        repeat_count: d.is_unlimited ? null : (d.repeat_count ?? 1),
        reset_session: kind === 'permanent' ? !!d.reset_session : false,
      }

      if (kind === 'permanent') {
        payload.minion_id = d.minion_id || minionIdFromQuery.value
      } else {
        payload.session_config = buildSessionConfigPayload(d.session_config || {})
      }

      const schedule = await scheduleStore.createSchedule(legionId, payload)
      if (!schedule?.schedule_id) throw new Error('Unexpected response from server')
      settingsStore.discardDraft(areaKey.value)
      router.replace(`/settings/schedule/${schedule.schedule_id}/general`)
    } catch (err) {
      saveError.value = err.message || 'Failed to create schedule'
    } finally {
      saving.value = false
    }
    return
  }

  // Edit mode
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
    if (d.repeat_count_set) {
      updates.repeat_count = d.repeat_count
      updates.repeat_count_set = true
    }
    const result = await scheduleStore.updateSchedule(entity.value.legion_id, entity.value.schedule_id, updates)
    settingsStore.markClean(areaKey.value)
    if (result?.deleted) {
      router.push('/settings/library/schedules')
    }
  } catch (err) {
    console.error('Save failed:', err)
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  if (isNew.value) {
    settingsStore.discardDraft(areaKey.value)
    router.push('/settings/schedules')
    return
  }
  settingsStore.discardDraft(areaKey.value)
}

defineExpose({ save: handleSave, cancel: handleCancel })

// Navigate away when schedule is deleted while this page is open (issue #1538 R5)
watch(entity, (val, oldVal) => {
  if (!isNew.value && oldVal && !val) {
    router.push('/settings/library/schedules')
  }
})

onMounted(async () => {
  sessionStore.fetchSessions?.()
  await projectStore.fetchProjects()
  await scheduleStore.loadAllSchedules()

  if (isNew.value) {
    await templateStore.fetchTemplates()
    // Seed defaults into draft only if it has no fields yet
    const d = draft.value
    if (!d || Object.keys(d).length === 0) {
      if (minionIdFromQuery.value) {
        handleField('kind', 'permanent')
        handleField('minion_id', minionIdFromQuery.value)
      } else {
        handleField('kind', 'ephemeral')
      }
      handleField('schedule_type', 'prompt')
      handleField('config_source', 'capture')
    }
  }
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

.field-select {
  appearance: auto;
  cursor: pointer;
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

.field-error {
  font-size: 11px;
  color: #f87171;
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

/* ── Segmented buttons ───────────────────────────────────── */
.seg-btns {
  display: flex;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  overflow: hidden;
}

.seg-btn {
  flex: 1;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  border: none;
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.seg-btn:not(:last-child) {
  border-right: 1px solid var(--bs-border-color);
}

.seg-btn.active {
  background: #7c3aed;
  color: #fff;
}

.seg-btn--sm {
  font-size: 11px;
  padding: 5px 10px;
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

.repeat-editor {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* ── Capture / config source ─────────────────────────────── */
.capture-row {
  display: flex;
  gap: 6px;
}

.capture-row .field-input {
  flex: 1;
}

.btn-capture {
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid #7c3aed;
  border-radius: 6px;
  background: rgba(124, 58, 237, 0.1);
  color: #7c3aed;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s;
}

.btn-capture:hover:not(:disabled) {
  background: rgba(124, 58, 237, 0.2);
}

.btn-capture:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.capture-success {
  font-size: 11px;
  color: var(--bs-success);
  padding: 3px 6px;
  background: rgba(25, 135, 84, 0.12);
  border-radius: 4px;
}

/* ── Config summary card ─────────────────────────────────── */
.config-summary {
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--bs-secondary-bg);
}

.config-summary-line {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 2px 0;
}

.config-label {
  color: var(--bs-secondary-color);
  font-weight: 500;
}

.config-value {
  color: var(--bs-body-color);
  font-family: monospace;
  font-size: 11px;
}

.config-hint {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  margin-top: 6px;
  border-top: 1px solid var(--bs-border-color);
  padding-top: 6px;
}

/* ── Validation banner ───────────────────────────────────── */
.validation-banner {
  margin-bottom: 12px;
  padding: 10px 14px;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.35);
  border-radius: 6px;
  color: #f87171;
  font-size: 12px;
}
</style>
