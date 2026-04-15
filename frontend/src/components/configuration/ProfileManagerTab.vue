<template>
  <div class="profile-manager-tab">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h6 class="mb-0">Configuration Profiles</h6>
      <button class="btn btn-primary btn-sm" @click="openCreate">+ New Profile</button>
    </div>

    <!-- Loading / error -->
    <div v-if="profileStore.loading" class="text-muted small py-2">Loading profiles...</div>
    <div v-if="profileStore.error" class="alert alert-danger py-2 small">{{ profileStore.error }}</div>

    <!-- Empty state -->
    <div v-if="!profileStore.loading && profileStore.allProfiles.length === 0" class="alert alert-info py-2 small">
      No profiles yet. Create your first profile to define reusable configuration defaults.
    </div>

    <!-- Profiles grouped by area -->
    <div v-for="(area, areaKey) in AREA_META" :key="areaKey" class="mb-3">
      <div
        v-if="profileStore.profilesForArea(areaKey).length > 0"
      >
        <div class="area-header small fw-semibold text-muted mb-1">{{ area.label }}</div>
        <div
          v-for="profile in profileStore.profilesForArea(areaKey)"
          :key="profile.profile_id"
          class="profile-card card mb-1"
        >
          <div class="card-body py-2 px-3">
            <div class="d-flex justify-content-between align-items-center">
              <div class="flex-grow-1 me-2">
                <span class="fw-medium small">{{ profile.name }}</span>
                <span class="ms-2 badge bg-secondary-subtle text-secondary-emphasis">{{ areaKey }}</span>
                <div class="text-muted small mt-1 font-monospace config-preview">
                  {{ configPreview(profile.config) }}
                </div>
              </div>
              <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary" @click="openEdit(profile)" title="Edit">Edit</button>
                <button class="btn btn-outline-danger" @click="confirmDelete(profile)" title="Delete">×</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create / Edit Form -->
    <div v-if="showForm" class="profile-form card mt-3">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <strong class="small">{{ editingProfile ? 'Edit Profile' : 'New Profile' }}</strong>
        <button type="button" class="btn-close btn-close-sm" @click="closeForm"></button>
      </div>
      <div class="card-body">
        <div class="mb-3">
          <label class="form-label small">Name <span class="text-danger">*</span></label>
          <input
            v-model="form.name"
            type="text"
            class="form-control form-control-sm"
            placeholder="e.g. Fast Model"
          />
        </div>

        <div v-if="!editingProfile" class="mb-3">
          <label class="form-label small">Area <span class="text-danger">*</span></label>
          <select v-model="form.area" class="form-select form-select-sm">
            <option value="">Select area...</option>
            <option v-for="(meta, key) in AREA_META" :key="key" :value="key">
              {{ meta.label }}
            </option>
          </select>
          <div class="form-text small">{{ form.area ? AREA_META[form.area]?.description : '' }}</div>
        </div>

        <!-- Config fields for selected area -->
        <div v-if="currentAreaFields.length > 0" class="mb-3">
          <label class="form-label small">Config Values (optional)</label>
          <div class="config-fields">
            <div v-for="field in currentAreaFields" :key="field" class="mb-2">
              <label class="form-label small text-muted mb-1">{{ field }}</label>
              <input
                v-if="fieldType(field) === 'string'"
                v-model="form.config[field]"
                type="text"
                class="form-control form-control-sm font-monospace"
                :placeholder="`${field} value`"
              />
              <input
                v-else-if="fieldType(field) === 'number'"
                v-model.number="form.config[field]"
                type="number"
                class="form-control form-control-sm"
              />
              <select
                v-else-if="fieldType(field) === 'boolean'"
                v-model="form.config[field]"
                class="form-select form-select-sm"
              >
                <option :value="undefined">(not set)</option>
                <option :value="true">true</option>
                <option :value="false">false</option>
              </select>
              <input
                v-else
                v-model="form.config[field]"
                type="text"
                class="form-control form-control-sm font-monospace"
                :placeholder="`${field} value`"
              />
            </div>
          </div>
        </div>

        <div v-if="formError" class="alert alert-danger py-2 small">{{ formError }}</div>

        <div class="d-flex justify-content-end gap-2">
          <button class="btn btn-secondary btn-sm" @click="closeForm">Cancel</button>
          <button class="btn btn-primary btn-sm" :disabled="formSubmitting" @click="submitForm">
            {{ formSubmitting ? 'Saving...' : (editingProfile ? 'Save Changes' : 'Create Profile') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation -->
    <div v-if="deleteTarget" class="alert alert-danger mt-3">
      <div class="mb-2 small">
        <strong>Delete profile "{{ deleteTarget.name }}"?</strong>
        <span v-if="deleteBlockers.length > 0" class="d-block mt-1">
          Cannot delete — referenced by: {{ deleteBlockers.join(', ') }}
        </span>
      </div>
      <div class="d-flex gap-2">
        <button class="btn btn-sm btn-outline-secondary" @click="deleteTarget = null">Cancel</button>
        <button
          v-if="deleteBlockers.length === 0"
          class="btn btn-sm btn-danger"
          :disabled="deleteSubmitting"
          @click="executeDelete"
        >
          {{ deleteSubmitting ? 'Deleting...' : 'Confirm Delete' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useProfileStore } from '@/stores/profile'

const profileStore = useProfileStore()

// ---- Area metadata ----
const AREA_META = {
  model: {
    label: 'Model',
    description: 'Model selection, thinking mode, and effort level',
    fields: ['model', 'thinking_mode', 'thinking_budget_tokens', 'effort'],
  },
  permissions: {
    label: 'Permissions',
    description: 'Permission mode, allowed/disallowed tools, and directories',
    fields: ['permission_mode', 'allowed_tools', 'disallowed_tools', 'additional_directories', 'setting_sources'],
  },
  system_prompt: {
    label: 'System Prompt',
    description: 'System prompt content and override behavior',
    fields: ['system_prompt', 'override_system_prompt'],
  },
  mcp: {
    label: 'MCP',
    description: 'MCP server IDs and configuration toggles',
    fields: ['mcp_server_ids', 'enable_claudeai_mcp_servers', 'strict_mcp_config'],
  },
  isolation: {
    label: 'Isolation',
    description: 'CLI path, sandbox, Docker, and environment settings',
    fields: [
      'cli_path', 'sandbox_enabled', 'sandbox_config',
      'docker_enabled', 'docker_image', 'docker_extra_mounts',
      'docker_home_directory', 'docker_proxy_enabled', 'docker_proxy_image',
      'docker_proxy_credentials', 'bare_mode', 'env_scrub_enabled',
    ],
  },
  features: {
    label: 'Features',
    description: 'History distillation, auto-memory, and skill creation',
    fields: ['history_distillation_enabled', 'auto_memory_mode', 'auto_memory_directory', 'skill_creating_enabled'],
  },
}

// Fields that are boolean type
const BOOLEAN_FIELDS = new Set([
  'override_system_prompt', 'sandbox_enabled', 'docker_enabled',
  'docker_proxy_enabled', 'history_distillation_enabled', 'skill_creating_enabled',
  'bare_mode', 'env_scrub_enabled', 'enable_claudeai_mcp_servers', 'strict_mcp_config',
])

// Fields that are numeric type
const NUMBER_FIELDS = new Set(['thinking_budget_tokens'])

function fieldType(field) {
  if (BOOLEAN_FIELDS.has(field)) return 'boolean'
  if (NUMBER_FIELDS.has(field)) return 'number'
  return 'string'
}

// ---- Form state ----
const showForm = ref(false)
const editingProfile = ref(null)
const form = reactive({ name: '', area: '', config: {} })
const formError = ref('')
const formSubmitting = ref(false)

const currentAreaFields = computed(() => {
  const area = editingProfile.value?.area || form.area
  return AREA_META[area]?.fields || []
})

function openCreate() {
  editingProfile.value = null
  form.name = ''
  form.area = ''
  form.config = {}
  formError.value = ''
  showForm.value = true
}

function openEdit(profile) {
  editingProfile.value = profile
  form.name = profile.name
  form.area = profile.area
  form.config = { ...profile.config }
  formError.value = ''
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingProfile.value = null
  formError.value = ''
}

async function submitForm() {
  formError.value = ''
  if (!form.name.trim()) {
    formError.value = 'Name is required'
    return
  }
  if (!editingProfile.value && !form.area) {
    formError.value = 'Area is required'
    return
  }

  // Strip undefined/empty string values from config
  const cleanConfig = {}
  for (const [k, v] of Object.entries(form.config)) {
    if (v !== undefined && v !== '') cleanConfig[k] = v
  }

  formSubmitting.value = true
  try {
    if (editingProfile.value) {
      await profileStore.updateProfile(editingProfile.value.profile_id, {
        name: form.name.trim(),
        config: cleanConfig,
      })
    } else {
      await profileStore.createProfile(form.name.trim(), form.area, cleanConfig)
    }
    closeForm()
  } catch (err) {
    formError.value = err.message || 'Failed to save profile'
  } finally {
    formSubmitting.value = false
  }
}

// ---- Delete state ----
const deleteTarget = ref(null)
const deleteBlockers = ref([])
const deleteSubmitting = ref(false)

function confirmDelete(profile) {
  deleteTarget.value = profile
  deleteBlockers.value = []
}

async function executeDelete() {
  if (!deleteTarget.value) return
  deleteSubmitting.value = true
  try {
    const result = await profileStore.deleteProfile(deleteTarget.value.profile_id)
    if (result?.error === 'profile_in_use') {
      deleteBlockers.value = result.blocking_templates?.map(t => t.name) || []
    } else {
      deleteTarget.value = null
    }
  } catch (err) {
    // Check if the error body contains blocker info
    if (err.data?.error === 'profile_in_use') {
      deleteBlockers.value = err.data.blocking_templates?.map(t => t.name) || []
    } else {
      console.error('Failed to delete profile:', err)
    }
  } finally {
    deleteSubmitting.value = false
  }
}

// ---- Helpers ----
function configPreview(config) {
  if (!config || Object.keys(config).length === 0) return '(empty — inheritable defaults)'
  return Object.entries(config)
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join(', ') + (Object.keys(config).length > 3 ? ` +${Object.keys(config).length - 3} more` : '')
}

onMounted(() => {
  profileStore.fetchProfiles()
})
</script>

<style scoped>
.profile-manager-tab {
  min-height: 200px;
}

.area-header {
  border-bottom: 1px solid var(--bs-border-color);
  padding-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.7rem;
}

.profile-card {
  border-color: var(--bs-border-color);
}

.config-preview {
  font-size: 0.7rem;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

.config-fields {
  max-height: 300px;
  overflow-y: auto;
  padding: 4px;
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
}
</style>
