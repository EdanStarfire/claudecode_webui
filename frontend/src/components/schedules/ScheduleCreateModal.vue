<template>
  <div class="modal-overlay" @click.self="$emit('close')" role="dialog" aria-modal="true" aria-labelledby="schedule-create-title">
    <div class="modal-content">
      <div class="modal-header">
        <h3 id="schedule-create-title">Create Schedule</h3>
        <button class="close-btn" @click="$emit('close')">&times;</button>
      </div>

      <form @submit.prevent="submit">
        <!-- Session Mode Toggle (issue #578) -->
        <div class="form-group">
          <label>Session Mode</label>
          <div class="mode-toggle">
            <button
              type="button"
              class="mode-btn"
              :class="{ active: mode === 'permanent' }"
              @click="mode = 'permanent'"
            >Permanent Session</button>
            <button
              type="button"
              class="mode-btn"
              :class="{ active: mode === 'ephemeral' }"
              @click="mode = 'ephemeral'"
            >Ephemeral (Temporary)</button>
          </div>
          <div class="mode-hint">
            {{ mode === 'permanent'
              ? 'Schedule fires into an existing agent session.'
              : 'A dedicated agent is created for this schedule. Archives accumulate under it.' }}
          </div>
        </div>

        <!-- Permanent mode: Minion selector -->
        <div v-if="mode === 'permanent'" class="form-group">
          <label>Agent</label>
          <select v-model="form.minion_id" required>
            <option value="" disabled>Select an agent...</option>
            <option
              v-for="session in projectSessions"
              :key="session.session_id"
              :value="session.session_id"
            >{{ session.name || session.session_id.substring(0, 8) }}</option>
          </select>
        </div>

        <!-- Ephemeral mode: config source -->
        <template v-if="mode === 'ephemeral'">
          <div class="form-group">
            <label>Configuration Source</label>
            <div class="mode-toggle">
              <button
                type="button"
                class="mode-btn small"
                :class="{ active: configSource === 'capture' }"
                @click="configSource = 'capture'"
              >Capture from Session</button>
              <button
                type="button"
                class="mode-btn small"
                :class="{ active: configSource === 'template' }"
                @click="configSource = 'template'"
              >Use Template</button>
            </div>
          </div>

          <!-- Capture from existing session -->
          <div v-if="configSource === 'capture'" class="form-group">
            <label>Source Session</label>
            <div class="capture-row">
              <select v-model="captureSessionId">
                <option value="" disabled>Select a session...</option>
                <option
                  v-for="session in projectSessions"
                  :key="session.session_id"
                  :value="session.session_id"
                >{{ session.name || session.session_id.substring(0, 8) }}</option>
              </select>
              <button
                type="button"
                class="btn-capture"
                :disabled="!captureSessionId"
                @click="captureConfig"
              >Capture</button>
            </div>
            <div v-if="configCaptured" class="capture-success">
              Configuration captured from "{{ capturedSessionName }}"
            </div>
          </div>

          <!-- Use template -->
          <div v-if="configSource === 'template'" class="form-group">
            <label>Template</label>
            <select v-model="selectedTemplateId" @change="applyTemplate">
              <option value="" disabled>Select a template...</option>
              <option
                v-for="tmpl in templates"
                :key="tmpl.template_id"
                :value="tmpl.template_id"
              >{{ tmpl.name }}</option>
            </select>
            <div v-if="templateApplied" class="capture-success">
              Template "{{ appliedTemplateName }}" applied
            </div>
          </div>

          <!-- Config summary + Review button (shown after seeding) -->
          <div v-if="hasSessionConfig" class="config-summary">
            <div class="config-summary-line">
              <span class="config-label">Working Dir:</span>
              <span class="config-value">{{ sessionConfig.working_directory || '(default)' }}</span>
            </div>
            <div class="config-summary-line">
              <span class="config-label">Model:</span>
              <span class="config-value">{{ sessionConfig.model || 'default' }}</span>
            </div>
            <div class="config-summary-line">
              <span class="config-label">Permissions:</span>
              <span class="config-value">{{ sessionConfig.permission_mode || 'default' }}</span>
            </div>
            <button
              type="button"
              class="btn-review"
              @click="openConfigModal"
            >Review & Edit Settings</button>
          </div>
        </template>

        <!-- Name -->
        <div class="form-group">
          <label>Schedule Name</label>
          <input v-model="form.name" type="text" placeholder="Daily status report" required />
        </div>

        <!-- Cron expression -->
        <div class="form-group">
          <label>Cron Expression</label>
          <input
            v-model="form.cron_expression"
            type="text"
            placeholder="0 8 * * 1-5"
            required
          />
          <div class="cron-preview" :class="{ error: cronError }">
            {{ cronPreview }}
          </div>
          <div class="cron-presets">
            <button type="button" @click="setCron('0 * * * *')">Every hour</button>
            <button type="button" @click="setCron('0 8 * * 1-5')">Weekdays 8am</button>
            <button type="button" @click="setCron('0 9 * * *')">Daily 9am</button>
            <button type="button" @click="setCron('0 0 * * 0')">Weekly Sun</button>
          </div>
        </div>

        <!-- Prompt -->
        <div class="form-group">
          <label>Prompt</label>
          <textarea
            v-model="form.prompt"
            rows="4"
            placeholder="What should the agent do when this schedule fires?"
            required
          ></textarea>
        </div>

        <!-- Reset session toggle (only for permanent mode) -->
        <div v-if="mode === 'permanent'" class="form-group toggle-group">
          <label class="toggle-label">
            <input type="checkbox" v-model="form.reset_session" />
            <span>Reset session before each execution</span>
          </label>
          <div class="toggle-hint">When enabled, the agent's session is reset for a clean context each time the schedule fires.</div>
        </div>

        <!-- Error -->
        <div v-if="error" class="error-msg">{{ error }}</div>

        <!-- Actions -->
        <div class="modal-actions">
          <button type="button" class="btn-secondary" @click="$emit('close')">Cancel</button>
          <button type="submit" class="btn-primary" :disabled="submitting || cronError || !isValid">
            {{ submitting ? 'Creating...' : 'Create Schedule' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'
import cronstrue from 'cronstrue'

const props = defineProps({
  legionId: { type: String, required: true },
})

const emit = defineEmits(['close', 'created'])

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()
const uiStore = useUIStore()

const submitting = ref(false)
const error = ref('')
const mode = ref('permanent')  // 'permanent' | 'ephemeral'
const configSource = ref('capture')  // 'capture' | 'template'
const captureSessionId = ref('')
const configCaptured = ref(false)
const capturedSessionName = ref('')
const configReviewed = ref(false)

// Template support
const templates = ref([])
const selectedTemplateId = ref('')
const templateApplied = ref(false)
const appliedTemplateName = ref('')

const form = ref({
  minion_id: '',
  name: '',
  cron_expression: '',
  prompt: '',
  reset_session: false,
})

const sessionConfig = ref({})

const hasSessionConfig = computed(() => {
  return Object.keys(sessionConfig.value).length > 0
})

const projectSessions = computed(() => {
  return sessionStore.sessionsInProject(props.legionId).value.filter(s => !s.is_ephemeral)
})

const cronPreview = computed(() => {
  if (!form.value.cron_expression) return 'Enter a cron expression (min hour dom mon dow)'
  try {
    return cronstrue.toString(form.value.cron_expression)
  } catch {
    return 'Invalid cron expression'
  }
})

const cronError = computed(() => {
  if (!form.value.cron_expression) return false
  try {
    cronstrue.toString(form.value.cron_expression)
    return false
  } catch {
    return true
  }
})

const isValid = computed(() => {
  if (mode.value === 'permanent') {
    return !!form.value.minion_id
  }
  // Ephemeral: need session config with working_directory
  return hasSessionConfig.value && !!sessionConfig.value.working_directory
})

onMounted(async () => {
  try {
    const data = await api.get('/api/templates')
    templates.value = data || []
  } catch (e) {
    console.error('Failed to load templates:', e)
  }
})

function setCron(expr) {
  form.value.cron_expression = expr
}

function captureConfig() {
  const session = projectSessions.value.find(s => s.session_id === captureSessionId.value)
  if (!session) return

  sessionConfig.value = {
    working_directory: session.working_directory || '',
    model: session.model || '',
    permission_mode: session.current_permission_mode || 'acceptEdits',
    system_prompt: session.system_prompt || '',
    override_system_prompt: session.override_system_prompt || false,
    sandbox_enabled: session.sandbox_enabled || false,
  }
  if (session.allowed_tools) {
    sessionConfig.value.allowed_tools = [...session.allowed_tools]
  }
  if (session.disallowed_tools) {
    sessionConfig.value.disallowed_tools = [...session.disallowed_tools]
  }
  if (session.setting_sources) {
    sessionConfig.value.setting_sources = [...session.setting_sources]
  }
  if (session.sandbox_config) {
    sessionConfig.value.sandbox_config = { ...session.sandbox_config }
  }
  if (session.docker_enabled) {
    sessionConfig.value.docker_enabled = true
    sessionConfig.value.docker_image = session.docker_image || null
    sessionConfig.value.docker_extra_mounts = session.docker_extra_mounts || null
  }
  if (session.thinking_mode) {
    sessionConfig.value.thinking_mode = session.thinking_mode
    sessionConfig.value.thinking_budget_tokens = session.thinking_budget_tokens || null
  }
  if (session.effort) {
    sessionConfig.value.effort = session.effort
  }
  if (session.cli_path) {
    sessionConfig.value.cli_path = session.cli_path
  }

  configCaptured.value = true
  capturedSessionName.value = session.name || session.session_id.substring(0, 8)
}

function applyTemplate() {
  const tmpl = templates.value.find(t => t.template_id === selectedTemplateId.value)
  if (!tmpl) return

  sessionConfig.value = {
    permission_mode: tmpl.permission_mode || 'default',
    model: tmpl.model || '',
    system_prompt: tmpl.default_system_prompt || '',
    override_system_prompt: tmpl.override_system_prompt || false,
    sandbox_enabled: tmpl.sandbox_enabled || false,
    working_directory: '',
  }
  if (tmpl.allowed_tools?.length) {
    sessionConfig.value.allowed_tools = [...tmpl.allowed_tools]
  }
  if (tmpl.disallowed_tools?.length) {
    sessionConfig.value.disallowed_tools = [...tmpl.disallowed_tools]
  }
  if (tmpl.sandbox_config) {
    sessionConfig.value.sandbox_config = { ...tmpl.sandbox_config }
  }
  if (tmpl.cli_path) {
    sessionConfig.value.cli_path = tmpl.cli_path
  }
  if (tmpl.docker_enabled) {
    sessionConfig.value.docker_enabled = true
    sessionConfig.value.docker_image = tmpl.docker_image || null
    sessionConfig.value.docker_extra_mounts = tmpl.docker_extra_mounts || null
  }
  if (tmpl.thinking_mode) {
    sessionConfig.value.thinking_mode = tmpl.thinking_mode
    sessionConfig.value.thinking_budget_tokens = tmpl.thinking_budget_tokens || null
  }
  if (tmpl.effort) {
    sessionConfig.value.effort = tmpl.effort
  }

  templateApplied.value = true
  appliedTemplateName.value = tmpl.name
}

function openConfigModal() {
  uiStore.showModal('configuration', {
    mode: 'configure-ephemeral',
    seedConfig: { ...sessionConfig.value },
    onConfigured: (payload) => {
      sessionConfig.value = payload
      configReviewed.value = true
    }
  })
}

function buildSessionConfigPayload() {
  const cfg = { ...sessionConfig.value }
  // Remove empty/null values — let server use defaults
  for (const key of Object.keys(cfg)) {
    if (cfg[key] === '' || cfg[key] === null) {
      delete cfg[key]
    }
  }
  return cfg
}

async function submit() {
  if (submitting.value || cronError.value || !isValid.value) return
  error.value = ''
  submitting.value = true

  try {
    const payload = {
      name: form.value.name,
      cron_expression: form.value.cron_expression,
      prompt: form.value.prompt,
      reset_session: form.value.reset_session,
    }

    if (mode.value === 'permanent') {
      payload.minion_id = form.value.minion_id
    } else {
      payload.session_config = buildSessionConfigPayload()
    }

    const schedule = await scheduleStore.createSchedule(props.legionId, payload)
    emit('created', schedule)
  } catch (e) {
    error.value = e.message || 'Failed to create schedule'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1050;
}

.modal-content {
  background: #fff;
  border-radius: 12px;
  width: 480px;
  max-width: 95vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.modal-header h3 {
  margin: 0;
  font-size: 16px;
  color: #1e293b;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  color: #94a3b8;
  cursor: pointer;
  padding: 0 4px;
}

form {
  padding: 16px 20px;
}

.form-group {
  margin-bottom: 14px;
}

.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 4px;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  font-size: 13px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #1e293b;
  box-sizing: border-box;
}

.form-group textarea {
  resize: vertical;
  font-family: inherit;
}

.mode-toggle {
  display: flex;
  gap: 0;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.mode-btn {
  flex: 1;
  padding: 7px 12px;
  font-size: 12px;
  border: none;
  background: #f8fafc;
  color: #64748b;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.15s;
}

.mode-btn:not(:last-child) {
  border-right: 1px solid #e2e8f0;
}

.mode-btn.active {
  background: #6366f1;
  color: #fff;
}

.mode-btn.small {
  font-size: 11px;
  padding: 5px 10px;
}

.mode-hint {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
}

.capture-row {
  display: flex;
  gap: 6px;
}

.capture-row select {
  flex: 1;
}

.btn-capture {
  padding: 8px 14px;
  font-size: 12px;
  border: 1px solid #6366f1;
  border-radius: 6px;
  background: #eef2ff;
  color: #4338ca;
  cursor: pointer;
  white-space: nowrap;
  font-weight: 500;
}

.btn-capture:hover:not(:disabled) {
  background: #e0e7ff;
}

.btn-capture:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.capture-success {
  font-size: 11px;
  color: #16a34a;
  margin-top: 4px;
  padding: 4px 6px;
  background: #f0fdf4;
  border-radius: 4px;
}

.config-summary {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 14px;
  background: #f8fafc;
}

.config-summary-line {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 2px 0;
}

.config-label {
  color: #64748b;
  font-weight: 500;
}

.config-value {
  color: #1e293b;
  font-family: monospace;
  font-size: 11px;
}

.btn-review {
  display: block;
  width: 100%;
  margin-top: 8px;
  padding: 6px 12px;
  font-size: 12px;
  border: 1px solid #6366f1;
  border-radius: 6px;
  background: #eef2ff;
  color: #4338ca;
  cursor: pointer;
  font-weight: 500;
  text-align: center;
}

.btn-review:hover {
  background: #e0e7ff;
}

.cron-preview {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
  padding: 4px 6px;
  background: #f1f5f9;
  border-radius: 4px;
}

.cron-preview.error {
  color: #dc2626;
  background: #fef2f2;
}

.cron-presets {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.cron-presets button {
  font-size: 10px;
  padding: 3px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
  color: #475569;
  cursor: pointer;
}

.cron-presets button:hover {
  background: #e2e8f0;
}

.toggle-group {
  margin-bottom: 14px;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
}

.toggle-label input[type="checkbox"] {
  width: auto;
  margin: 0;
}

.toggle-hint {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
  padding-left: 24px;
}

.error-msg {
  color: #dc2626;
  font-size: 12px;
  margin-bottom: 10px;
  padding: 6px 8px;
  background: #fef2f2;
  border-radius: 4px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
}

.btn-secondary {
  font-size: 13px;
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #475569;
  cursor: pointer;
}

.btn-primary {
  font-size: 13px;
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  background: #6366f1;
  color: #fff;
  cursor: pointer;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary:hover:not(:disabled) {
  background: #4f46e5;
}
</style>
