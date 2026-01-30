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
        <span class="text-danger">*</span>
      </label>
      <input
        id="config-name"
        type="text"
        class="form-control"
        :class="{ 'is-invalid': errors.name }"
        :value="formData.name"
        @input="$emit('update:form-data', 'name', $event.target.value)"
        :placeholder="isTemplateMode ? 'e.g., Code Expert' : 'main'"
        :pattern="isSessionMode ? '[^\\s]+' : undefined"
        :title="isSessionMode ? 'Session name must be a single word with no spaces' : undefined"
        required
      />
      <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
      <div v-if="isSessionMode" class="form-text">Must be a single word (no spaces) for #nametag matching</div>
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
        <option value="sonnet">Sonnet 4.5 (Recommended) - Coding & complex agents</option>
        <option value="opus">Opus 4.5 - Complex reasoning tasks</option>
        <option value="haiku">Haiku 4.5 - Fastest for simple tasks</option>
        <option value="opusplan">OpusPlan - Opus planning + Sonnet execution</option>
      </select>
      <div v-if="selectedModelInfo" class="form-text">
        {{ selectedModelInfo.description }}
      </div>
    </div>

    <!-- Permission Mode -->
    <div class="mb-3">
      <label for="config-permission-mode" class="form-label">Permission Mode</label>
      <select
        id="config-permission-mode"
        class="form-select"
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
      <label for="config-role" class="form-label">{{ isTemplateMode ? 'Default Role' : 'Role' }}</label>
      <input
        id="config-role"
        type="text"
        class="form-control"
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

    <!-- Session State (edit-session only) -->
    <div v-if="isEditSession && session" class="mb-3">
      <label class="form-label">Current State</label>
      <div class="form-control-plaintext">
        <span class="badge" :class="getStateBadgeClass(session.state)">
          {{ session.state }}
        </span>
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
import { computed } from 'vue'

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
  }
})

defineEmits([
  'update:form-data',
  'update:selected-template-id',
  'open-folder-browser',
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
</style>
