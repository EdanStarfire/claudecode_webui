<template>
  <div class="quick-panel">
    <!-- Template Selection (create-session and configure-ephemeral only) -->
    <div v-if="isCreateSession || isConfigureEphemeral" class="mb-3">
      <label for="template-select" class="form-label">
        Template
        <button v-if="isCreateSession" type="button" @click="$emit('open-template-manager')" class="btn btn-link btn-sm p-0 ms-2" style="text-transform: none; letter-spacing: normal;">
          Manage Templates
        </button>
      </label>
      <select
        id="template-select"
        :value="selectedTemplateId"
        @change="$emit('update:selected-template-id', $event.target.value || null)"
        class="form-select form-select-sm"
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
        class="form-control form-control-sm"
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
        class="form-control form-control-sm"
        :value="formData.description"
        @input="$emit('update:form-data', 'description', $event.target.value)"
        rows="2"
        placeholder="Brief description of this template's purpose"
      ></textarea>
    </div>

    <!-- Profile selectors (template mode only) -->
    <div v-if="isTemplateMode" class="mb-3">
      <label class="form-label">Configuration Profiles</label>
      <div class="profile-selectors">
        <div class="mb-2">
          <label class="form-label small text-muted mb-1">Model area</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('model')"
            @change="setProfileForArea('model', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('model')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
        <div>
          <label class="form-label small text-muted mb-1">Permissions area</label>
          <select
            class="form-select form-select-sm"
            :value="getProfileForArea('permissions')"
            @change="setProfileForArea('permissions', $event.target.value || null)"
          >
            <option value="">(no profile)</option>
            <option v-for="p in profilesForArea('permissions')" :key="p.profile_id" :value="p.profile_id">
              {{ p.name }}
            </option>
          </select>
        </div>
      </div>
      <div class="form-text">Assign reusable defaults per area. Override in Advanced Settings.</div>
    </div>

    <!-- Permission Mode (button group) -->
    <div class="mb-3">
      <label class="form-label">
        Permission Mode
        <span v-if="fieldStates.permission_mode === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
        <span v-if="fieldStates.permission_mode === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">&lt;</span>
        <span v-if="fieldStates.permission_mode === 'modified'" class="field-indicator modified" title="Modified from template">*</span>
      </label>
      <div class="perm-btn-group">
        <button
          type="button"
          class="perm-btn"
          :class="{ active: formData.permission_mode === 'default', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('default')"
        >Default</button>
        <button
          type="button"
          class="perm-btn perm-accept"
          :class="{ active: formData.permission_mode === 'acceptEdits', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('acceptEdits')"
        >Accept Edits</button>
        <button
          type="button"
          class="perm-btn perm-plan"
          :class="{ active: formData.permission_mode === 'plan', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('plan')"
        >Plan</button>
        <button
          type="button"
          class="perm-btn perm-dontask"
          :class="{ active: formData.permission_mode === 'dontAsk', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('dontAsk')"
          title="No permission prompts — agent limited to pre-approved tools only"
        >Don't Ask</button>
        <button
          type="button"
          class="perm-btn perm-auto"
          :class="{ active: formData.permission_mode === 'auto', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('auto')"
          title="Autonomous mode — agent makes its own tool decisions"
        >Auto</button>
        <button
          v-if="!isEditSession || canUseBypassPermissions"
          type="button"
          class="perm-btn perm-bypass"
          :class="{ active: formData.permission_mode === 'bypassPermissions', disabled: permDisabled }"
          :disabled="permDisabled"
          @click="setPermissionMode('bypassPermissions')"
        >Bypass</button>
      </div>
    </div>

    <!-- Role -->
    <div class="mb-3">
      <label for="config-role" class="form-label">
        {{ isTemplateMode ? 'Default Role' : 'Role' }}
        <span v-if="fieldStates.role === 'profile'" class="field-indicator profile" title="Value from profile">P</span>
        <span v-if="fieldStates.role === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">&lt;</span>
        <span v-if="fieldStates.role === 'modified'" class="field-indicator modified" title="Modified from template">*</span>
      </label>
      <input
        id="config-role"
        type="text"
        class="form-control form-control-sm"
        :value="formData.role"
        @input="$emit('update:form-data', 'role', $event.target.value)"
        :placeholder="isTemplateMode ? 'e.g., Code review specialist' : 'Optional role description'"
      />
    </div>

    <!-- Working Directory (session modes only) -->
    <div v-if="isSessionMode" class="mb-3">
      <label class="form-label">Working Directory</label>
      <div class="workdir-display">
        <div v-if="isEditSession" class="workdir-path">{{ formData.working_directory || '(inherited from project)' }}</div>
        <template v-else>
          <input
            type="text"
            class="form-control form-control-sm"
            :value="formData.working_directory"
            @input="$emit('update:form-data', 'working_directory', $event.target.value)"
            placeholder="Leave empty to inherit from project"
            style="flex: 1;"
          />
          <button
            type="button"
            class="btn btn-sm btn-outline-secondary"
            @click="$emit('open-folder-browser')"
            title="Browse"
          >
            &#x1F4C2;
          </button>
        </template>
      </div>
    </div>

    <!-- Current State (edit-session only) -->
    <div v-if="isEditSession && session" class="mb-3">
      <label class="form-label">Current State</label>
      <div>
        <span class="badge" :class="getStateBadgeClass(session.state)">
          {{ session.state }}
        </span>
      </div>
    </div>

    <!-- Start Immediately (create-session only) -->
    <div v-if="isCreateSession" class="form-check mb-2">
      <input
        class="form-check-input"
        type="checkbox"
        id="config-start-immediately"
        :checked="formData.startImmediately"
        @change="$emit('update:form-data', 'startImmediately', $event.target.checked)"
      />
      <label class="form-check-label" for="config-start-immediately" style="text-transform: none; letter-spacing: normal;">
        Start session immediately after creation
      </label>
    </div>

    <!-- Advanced Settings link -->
    <div class="advanced-link" @click="$emit('show-advanced')">
      <span>Advanced Settings</span>
      &rarr;
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useProfileStore } from '@/stores/profile'

const profileStore = useProfileStore()

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
      role: 'normal',
      permission_mode: 'normal'
    })
  },
  profileIds: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits([
  'update:form-data',
  'update:profile-ids',
  'update:selected-template-id',
  'open-folder-browser',
  'open-template-manager',
  'show-advanced'
])

const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
const isCreateSession = computed(() => props.mode === 'create-session')
const isEditSession = computed(() => props.mode === 'edit-session')
const isConfigureEphemeral = computed(() => props.mode === 'configure-ephemeral')

const isSessionActive = computed(() => {
  return props.session?.state === 'active' || props.session?.state === 'starting'
})

const permDisabled = computed(() => false)

const canUseBypassPermissions = computed(() => {
  return props.session?.initial_permission_mode === 'bypassPermissions'
})

const selectedTemplate = computed(() => {
  if (!props.selectedTemplateId) return null
  return props.templates.find(t => t.template_id === props.selectedTemplateId)
})

function profilesForArea(area) {
  return profileStore.profilesForArea(area)
}

function getProfileForArea(area) {
  return props.profileIds?.[area] || null
}

function setProfileForArea(area, profileId) {
  const updated = { ...(props.profileIds || {}) }
  if (profileId) {
    updated[area] = profileId
  } else {
    delete updated[area]
  }
  emit('update:profile-ids', updated)
}

onMounted(() => {
  profileStore.fetchProfiles()
})

function setPermissionMode(mode) {
  if (permDisabled.value) return
  emit('update:form-data', 'permission_mode', mode)
}

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
.field-indicator {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
  text-transform: none;
  letter-spacing: normal;
}

.field-indicator.autofilled {
  color: #856404;
}

.field-indicator.modified {
  color: #cc5500;
}

.profile-selectors {
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
  padding: 8px;
  border: 1px solid var(--bs-border-color);
}
</style>
