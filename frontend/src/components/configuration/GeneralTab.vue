<template>
  <div class="general-tab">
    <!-- Template Selection (create-session only) -->
    <div v-if="isCreateSession" class="mb-3">
      <label for="template-select" class="form-label">
        Template
        <button type="button" @click="$emit('open-template-manager')" class="btn btn-link btn-sm p-0 ms-2">
          Manage Templates
        </button>
      </label>
      <select
        id="template-select"
        :value="selectedTemplateId"
        @change="$emit('update:selected-template-id', $event.target.value || null)"
        class="form-select"
      >
        <option :value="null">[None - Manual Configuration]</option>
        <option v-for="template in templates" :key="template.template_id" :value="template.template_id">
          {{ template.name }}
        </option>
      </select>
      <div v-if="selectedTemplate" class="form-text">
        {{ selectedTemplate.description }}
      </div>
    </div>

    <!-- Name -->
    <div class="mb-3">
      <label for="config-name" class="form-label">
        {{ isTemplateMode ? 'Template Name' : 'Session Name' }}
        <span v-if="mode !== 'configure-ephemeral'" class="text-danger">*</span>
      </label>
      <input
        id="config-name"
        type="text"
        class="form-control"
        :class="{ 'is-invalid': errors.name }"
        :value="formData.name"
        @input="$emit('update:form-data', 'name', $event.target.value)"
        :placeholder="mode === 'configure-ephemeral' ? '[Scheduled]' : (isTemplateMode ? 'e.g., Code Expert' : 'main')"
        :required="mode !== 'configure-ephemeral'"
      />
      <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
    </div>

    <!-- Description (template only) -->
    <div v-if="isTemplateMode" class="mb-3">
      <label for="config-description" class="form-label">Description</label>
      <textarea
        id="config-description"
        class="form-control"
        :value="formData.description"
        @input="$emit('update:form-data', 'description', $event.target.value)"
        rows="2"
        placeholder="Brief description of this template's purpose"
      ></textarea>
    </div>

    <!-- Model Selection -->
    <div class="mb-3">
      <label for="config-model" class="form-label">Model</label>
      <select
        id="config-model"
        class="form-select"
        :value="formData.model"
        @change="$emit('update:form-data', 'model', $event.target.value)"
      >
        <option value="sonnet">Sonnet (Recommended) - Coding & complex agents</option>
        <option value="opus">Opus - Complex reasoning tasks</option>
        <option value="haiku">Haiku - Fastest for simple tasks</option>
        <option value="opusplan">OpusPlan - Opus planning + Sonnet execution</option>
      </select>
      <div class="form-text">
        <span v-if="selectedModelInfo">
          {{ selectedModelInfo.description }}
        </span>
      </div>
    </div>

    <!-- Thinking Mode (issue #540) -->
    <div class="mb-3">
      <label for="config-thinking-mode" class="form-label">Thinking Mode</label>
      <select
        id="config-thinking-mode"
        class="form-select"
        :value="formData.thinking_mode"
        @change="$emit('update:form-data', 'thinking_mode', $event.target.value)"
      >
        <option value="">Default (SDK decides)</option>
        <option value="adaptive">Adaptive</option>
        <option value="enabled">Enabled</option>
        <option value="disabled">Disabled</option>
      </select>
      <div class="form-text">Controls whether the model uses extended thinking</div>
    </div>

    <!-- Budget Tokens slider (conditional on thinking_mode === 'enabled') (issue #540) -->
    <div v-if="formData.thinking_mode === 'enabled'" class="mb-3">
      <label for="config-thinking-budget" class="form-label">
        Thinking Budget: {{ formData.thinking_budget_tokens?.toLocaleString() || '10,240' }} tokens
      </label>
      <input
        id="config-thinking-budget"
        type="range"
        class="form-range"
        :value="formData.thinking_budget_tokens || 10240"
        @input="$emit('update:form-data', 'thinking_budget_tokens', parseInt($event.target.value))"
        min="1024"
        max="32768"
        step="1024"
      />
      <div class="form-text">Token budget for extended thinking (1,024 - 32,768)</div>
    </div>

    <!-- Effort (issue #540) -->
    <div class="mb-3">
      <label for="config-effort" class="form-label">Effort</label>
      <select
        id="config-effort"
        class="form-select"
        :value="formData.effort"
        @change="$emit('update:form-data', 'effort', $event.target.value)"
      >
        <option value="">Default (SDK decides)</option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
        <option value="max">Max</option>
      </select>
      <div class="form-text">Balance response quality against latency</div>
    </div>

    <!-- Permission Mode -->
    <div class="mb-3">
      <label for="config-permission-mode" class="form-label">
        Permission Mode
        <span v-if="fieldStates.permission_mode === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
          &lt;
        </span>
        <span v-if="fieldStates.permission_mode === 'modified'" class="field-indicator modified" title="Modified from template">
          *
        </span>
      </label>
      <select
        id="config-permission-mode"
        class="form-select"
        :class="permissionModeFieldClass"
        :value="formData.permission_mode"
        @change="$emit('update:form-data', 'permission_mode', $event.target.value)"
        :disabled="isEditSession && !isSessionActive"
      >
        <option value="default">Default (Prompt for tools not in settings)</option>
        <option value="acceptEdits">Accept Edits (Auto-approve Edit/Write)</option>
        <option value="plan">Plan Mode (Auto-resets after ExitPlanMode)</option>
        <option v-if="!isEditSession || canUseBypassPermissions" value="bypassPermissions">
          Bypass Permissions (No prompts - use with caution)
        </option>
      </select>
      <div class="form-text">
        <span v-if="isEditSession && !isSessionActive" class="text-warning">
          Session must be active to change permission mode. Start the session first.
        </span>
        <span v-else>Controls which tool actions require permission prompts</span>
      </div>
    </div>

    <!-- Default Role (template and session) -->
    <div class="mb-3">
      <label for="config-role" class="form-label">
        {{ isTemplateMode ? 'Default Role' : 'Role' }}
        <span v-if="fieldStates.default_role === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
          &lt;
        </span>
        <span v-if="fieldStates.default_role === 'modified'" class="field-indicator modified" title="Modified from template">
          *
        </span>
      </label>
      <input
        id="config-role"
        type="text"
        class="form-control"
        :class="roleFieldClass"
        :value="formData.default_role"
        @input="$emit('update:form-data', 'default_role', $event.target.value)"
        :placeholder="isTemplateMode ? 'e.g., Code review and refactoring specialist' : 'Optional role description'"
      />
      <div class="form-text">{{ isTemplateMode ? 'Default role for minions using this template' : 'Optional role description for the session' }}</div>
    </div>

    <!-- Working Directory (session only) -->
    <div v-if="isSessionMode" class="mb-3">
      <label for="config-working-dir" class="form-label">Working Directory</label>
      <div class="input-group">
        <input
          id="config-working-dir"
          type="text"
          class="form-control"
          :value="formData.working_directory"
          @input="$emit('update:form-data', 'working_directory', $event.target.value)"
          :placeholder="isEditSession ? '' : 'Leave empty to inherit from project'"
          :disabled="isEditSession"
          :readonly="isEditSession"
        />
        <button
          v-if="!isEditSession"
          type="button"
          class="btn btn-outline-secondary"
          @click="$emit('open-folder-browser')"
        >
          Browse
        </button>
      </div>
      <div class="form-text">
        {{ isEditSession ? 'Working directory is inherited from project' : 'Custom working directory for git worktrees or multi-repo workflows' }}
      </div>
    </div>

    <!-- Additional Directories (session and template modes) -->
    <div class="mb-3">
      <label class="form-label">Additional Directories</label>
      <div class="additional-dirs-editor">
        <div v-if="additionalDirsList.length > 0" class="dirs-list mb-2">
          <div
            v-for="(dir, index) in additionalDirsList"
            :key="index"
            class="dir-entry d-flex align-items-center mb-1"
          >
            <code class="flex-grow-1 small text-truncate" :title="dir">{{ dir }}</code>
            <button
              type="button"
              class="btn btn-sm btn-outline-danger ms-2 py-0 px-1"
              @click="removeDirectory(index)"
            >&times;</button>
          </div>
        </div>
        <div v-else class="text-muted small mb-2">No additional directories configured</div>
        <div class="input-group input-group-sm">
          <input
            type="text"
            class="form-control"
            :class="{ 'is-invalid': newDirError }"
            v-model="newDirectory"
            @keydown.enter.prevent="addDirectory"
            placeholder="/path/to/directory"
          />
          <button
            type="button"
            class="btn btn-outline-secondary"
            @click="$emit('browse-additional-dir')"
            title="Browse for directory"
          >Browse</button>
          <button
            type="button"
            class="btn btn-outline-primary"
            @click="addDirectory"
            :disabled="!newDirectory.trim()"
          >+</button>
        </div>
        <div v-if="newDirError" class="invalid-feedback d-block">{{ newDirError }}</div>
      </div>
      <div class="form-text">
        Extra directories the agent can access beyond the working directory. Requires session restart.
      </div>
    </div>

    <!-- Session State (edit-session only) -->
    <div v-if="isEditSession && session" class="mb-3">
      <label class="form-label">Current State</label>
      <div class="form-control-plaintext">
        <span class="badge" :class="getStateBadgeClass(session.state)">
          {{ session.state }}
        </span>
      </div>
    </div>

    <!-- MCP Servers (edit-session, active only) -->
    <div v-if="isEditSession && isSessionActive && mcpServers.length > 0" class="mb-3">
      <label class="form-label">MCP Servers</label>
      <div class="mcp-servers-list">
        <McpServerRow
          v-for="server in mcpServers"
          :key="server.name"
          :server="server"
          @toggle="handleMcpToggle"
          @reconnect="handleMcpReconnect"
        />
      </div>
    </div>

    <!-- Start Immediately (create-session only) -->
    <div v-if="isCreateSession" class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-start-immediately"
        :checked="formData.startImmediately"
        @change="$emit('update:form-data', 'startImmediately', $event.target.checked)"
      />
      <label class="form-check-label" for="config-start-immediately">
        Start session immediately after creation
      </label>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useMcpStore } from '../../stores/mcp'
import McpServerRow from './McpServerRow.vue'

const mcpStore = useMcpStore()

const props = defineProps({
  mode: {
    type: String,
    required: true
  },
  formData: {
    type: Object,
    required: true
  },
  errors: {
    type: Object,
    required: true
  },
  templates: {
    type: Array,
    default: () => []
  },
  selectedTemplateId: {
    type: String,
    default: null
  },
  session: {
    type: Object,
    default: null
  },
  fieldStates: {
    type: Object,
    default: () => ({
      default_role: 'normal',
      permission_mode: 'normal'
    })
  }
})

const emit = defineEmits([
  'update:form-data',
  'update:selected-template-id',
  'open-folder-browser',
  'browse-additional-dir',
  'open-template-manager'
])

// Computed
const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
const isCreateSession = computed(() => props.mode === 'create-session')
const isEditSession = computed(() => props.mode === 'edit-session')

const isSessionActive = computed(() => {
  return props.session?.state === 'active' || props.session?.state === 'starting'
})

// MCP servers for the current session
const sessionId = computed(() => props.session?.session_id)
const mcpServers = computed(() => {
  if (!sessionId.value) return []
  return mcpStore.mcpServers(sessionId.value)
})

// Fetch MCP status when session becomes active
watch([sessionId, isSessionActive], ([id, active]) => {
  if (id && active) {
    mcpStore.fetchMcpStatus(id)
  }
}, { immediate: true })

function handleMcpToggle(name, enabled) {
  if (sessionId.value) {
    mcpStore.toggleServer(sessionId.value, name, enabled)
  }
}

function handleMcpReconnect(name) {
  if (sessionId.value) {
    mcpStore.reconnectServer(sessionId.value, name)
  }
}

const canUseBypassPermissions = computed(() => {
  return props.session?.initial_permission_mode === 'bypassPermissions'
})

const selectedTemplate = computed(() => {
  if (!props.selectedTemplateId) return null
  return props.templates.find(t => t.template_id === props.selectedTemplateId)
})

const modelInfo = {
  sonnet: { description: 'Smart model for coding and complex agents - best balance of speed & capability' },
  opus: { description: 'Most capable model for complex reasoning, advanced analysis, and sophisticated tasks' },
  haiku: { description: 'Optimized for speed and cost-efficiency on straightforward tasks' },
  opusplan: { description: 'Uses Opus for planning phase, Sonnet for execution - best of both worlds' }
}

const selectedModelInfo = computed(() => {
  return modelInfo[props.formData.model] || null
})

// Field highlighting classes
const roleFieldClass = computed(() => ({
  'field-autofilled': props.fieldStates.default_role === 'autofilled',
  'field-modified': props.fieldStates.default_role === 'modified'
}))

const permissionModeFieldClass = computed(() => ({
  'field-autofilled': props.fieldStates.permission_mode === 'autofilled',
  'field-modified': props.fieldStates.permission_mode === 'modified'
}))

// Additional directories (issue #630)
const newDirectory = ref('')
const newDirError = ref('')

const additionalDirsList = computed(() => {
  const raw = props.formData.additional_directories || ''
  return raw.split('\n').map(d => d.trim()).filter(d => d)
})

function addDirectory() {
  const dir = newDirectory.value.trim()
  if (!dir) return

  if (!dir.startsWith('/')) {
    newDirError.value = 'Path must be absolute (start with /)'
    return
  }

  newDirError.value = ''
  addDirectoryPath(dir)
  newDirectory.value = ''
}

function addDirectoryPath(dir) {
  const current = additionalDirsList.value
  if (!current.includes(dir)) {
    current.push(dir)
    emit('update:form-data', 'additional_directories', current.join('\n'))
  }
}

defineExpose({ addDirectoryPath })

function removeDirectory(index) {
  const current = [...additionalDirsList.value]
  current.splice(index, 1)
  emit('update:form-data', 'additional_directories', current.join('\n'))
}

// Methods
function getStateBadgeClass(state) {
  const classMap = {
    created: 'bg-secondary',
    starting: 'bg-info',
    active: 'bg-success',
    paused: 'bg-warning',
    terminating: 'bg-warning',
    terminated: 'bg-secondary',
    error: 'bg-danger'
  }
  return classMap[state] || 'bg-secondary'
}
</script>

<style scoped>
.form-control-plaintext {
  padding-top: 0.375rem;
  padding-bottom: 0.375rem;
}

/* Field highlighting states */
.field-autofilled {
  background-color: #fffbea !important; /* Light yellow */
  transition: background-color 0.3s ease;
}

.field-modified {
  background-color: #ffe4cc !important; /* Darker orange */
  transition: background-color 0.3s ease;
}

/* Ensure text readability */
.field-autofilled,
.field-modified {
  color: #212529;
}

/* Ensure borders remain visible */
.form-control.field-autofilled,
.form-control.field-modified,
.form-select.field-autofilled,
.form-select.field-modified {
  border: 1px solid #dee2e6;
}

/* Focus states */
.form-control.field-autofilled:focus,
.form-control.field-modified:focus,
.form-select.field-autofilled:focus,
.form-select.field-modified:focus {
  border-color: #0d6efd;
  box-shadow: 0 0 0 0.25rem rgba(13, 110, 253, 0.25);
}

/* Field indicators */
.field-indicator {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
}

.field-indicator.autofilled {
  color: #856404; /* Dark yellow */
}

.field-indicator.modified {
  color: #cc5500; /* Dark orange */
}

/* Smooth transitions */
.form-control,
.form-select {
  transition: background-color 0.3s ease, border-color 0.3s ease;
}

/* Additional directories list */
.dirs-list .dir-entry {
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  background: var(--bs-gray-100, #f8f9fa);
}
</style>
