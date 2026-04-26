<template>
  <div class="profile-manager-tab">
    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h6 class="mb-0">Configuration Profiles</h6>
      <button class="btn btn-primary btn-sm" @click="openCreate">+ New Profile</button>
    </div>

    <template v-if="!showForm">
      <!-- Loading / error -->
      <div v-if="profileStore.loading" class="text-muted small py-2">Loading profiles...</div>
      <div v-if="profileStore.error" class="alert alert-danger py-2 small">{{ profileStore.error }}</div>

      <!-- Empty state -->
      <div v-if="!profileStore.loading && profileStore.allProfiles.length === 0" class="alert alert-info py-2 small">
        No profiles yet. Create your first profile to define reusable configuration defaults.
      </div>

      <!-- Profiles grouped by area -->
      <div v-for="(area, areaKey) in AREA_META" :key="areaKey" class="mb-3">
        <div v-if="profileStore.profilesForArea(areaKey).length > 0">
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
    </template>

    <!-- Create / Edit Form -->
    <div v-if="showForm" class="profile-form card mt-3">
      <div class="card-header d-flex justify-content-between align-items-center py-2">
        <strong class="small">{{ editingProfile ? 'Edit Profile' : 'New Profile' }}</strong>
        <button type="button" class="btn-close btn-close-sm" @click="closeForm"></button>
      </div>
      <div class="card-body">
        <!-- Area (create only) -->
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

        <!-- Name -->
        <div class="mb-3">
          <label class="form-label small">Name <span class="text-danger">*</span></label>
          <input
            v-model="form.name"
            type="text"
            class="form-control form-control-sm"
            placeholder="e.g. Fast Model"
          />
        </div>

        <!-- Config fields -->
        <div v-if="currentArea" class="mb-3">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <label class="form-label small mb-0">Config Values</label>
            <span class="form-text small text-muted">Toggle fields to include them in this profile</span>
          </div>

          <div class="area-fields">

            <!-- MODEL, PERMISSIONS, SYSTEM_PROMPT, FEATURES areas use FieldSection -->
            <FieldSection
              v-if="currentArea === 'model'"
              :fields="FIELD_SCHEMAS.model"
              :config="form.config"
              :show-badges="false"
              :show-include-toggle="true"
              :field-states="{}"
              :included="included"
              @update:config="(key, val) => { form.config[key] = val }"
              @update:included="handleIncluded"
            />

            <FieldSection
              v-else-if="currentArea === 'permissions'"
              :fields="permissionsFieldsForPmt"
              :config="form.config"
              :show-badges="false"
              :show-include-toggle="true"
              :field-states="{}"
              :included="included"
              @update:config="(key, val) => { form.config[key] = val }"
              @update:included="handleIncluded"
            />

            <FieldSection
              v-else-if="currentArea === 'system_prompt'"
              :fields="FIELD_SCHEMAS.system_prompt"
              :config="form.config"
              :show-badges="false"
              :show-include-toggle="true"
              :field-states="{}"
              :included="included"
              @update:config="(key, val) => { form.config[key] = val }"
              @update:included="handleIncluded"
            />

            <!-- MCP AREA — kept as custom template (checkbox list + toggles) -->
            <template v-else-if="currentArea === 'mcp'">
              <!-- mcp_server_ids -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.mcp_server_ids" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.mcp_server_ids }">
                  <label class="form-label small mb-1">mcp_server_ids</label>
                  <div v-if="mcpConfigStore.configList().length === 0" class="text-muted small">
                    No MCP servers configured. Add them in Application Settings.
                  </div>
                  <div v-else class="mcp-server-list">
                    <div v-for="srv in mcpConfigStore.configList()" :key="srv.id" class="form-check">
                      <input class="form-check-input" type="checkbox" :id="'mcp-' + srv.id"
                        :checked="mcpServerIds.includes(srv.id)"
                        :disabled="!included.mcp_server_ids"
                        @change="toggleMcpServer(srv.id, $event.target.checked)"
                      />
                      <label class="form-check-label small" :for="'mcp-' + srv.id">{{ srv.name }}</label>
                    </div>
                  </div>
                </div>
              </div>
              <!-- enable_claudeai_mcp_servers -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.enable_claudeai_mcp_servers" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.enable_claudeai_mcp_servers }">
                  <label class="form-label small mb-1">enable_claudeai_mcp_servers</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-claudeai-mcp-val" v-model="form.config.enable_claudeai_mcp_servers" :disabled="!included.enable_claudeai_mcp_servers" />
                    <label class="form-check-label small" for="f-claudeai-mcp-val">Enable Claude.ai MCP servers</label>
                  </div>
                </div>
              </div>
              <!-- strict_mcp_config -->
              <div class="field-row">
                <div class="field-toggle">
                  <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included.strict_mcp_config" />
                  </div>
                </div>
                <div class="field-body" :class="{ 'field-disabled': !included.strict_mcp_config }">
                  <label class="form-label small mb-1">strict_mcp_config</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-strict-mcp-val" v-model="form.config.strict_mcp_config" :disabled="!included.strict_mcp_config" />
                    <label class="form-check-label small" for="f-strict-mcp-val">Strict MCP config (disable local servers)</label>
                  </div>
                </div>
              </div>
            </template>

            <!-- ISOLATION AREA -->
            <template v-else-if="currentArea === 'isolation'">
              <!-- cli_path -->
              <div class="field-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.cli_path" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.cli_path }">
                  <label class="form-label small mb-1">cli_path</label>
                  <input type="text" class="form-control form-control-sm font-monospace" v-model="form.config.cli_path" :disabled="!included.cli_path" placeholder="/path/to/claude-cli" />
                </div>
              </div>
              <!-- sandbox_enabled -->
              <div class="field-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.sandbox_enabled" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.sandbox_enabled }">
                  <label class="form-label small mb-1">sandbox_enabled</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-iso-sb-en" v-model="form.config.sandbox_enabled" :disabled="!included.sandbox_enabled" />
                    <label class="form-check-label small" for="f-iso-sb-en">Enable sandbox mode</label>
                  </div>
                </div>
              </div>
              <!-- sandbox_config (shown when sandbox_enabled is included and ON) -->
              <div v-if="included.sandbox_enabled && form.config.sandbox_enabled" class="field-row iso-sub-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.sandbox_config" @change="onSandboxConfigToggle" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.sandbox_config }">
                  <label class="form-label small mb-1">sandbox_config</label>
                  <SandboxSubSectionWidget
                    v-if="included.sandbox_config && form.config.sandbox_config"
                    :value="form.config.sandbox_config"
                    :disabled="false"
                    @update:value="form.config.sandbox_config = $event"
                  />
                </div>
              </div>
              <!-- docker_enabled -->
              <div class="field-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.docker_enabled" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.docker_enabled }">
                  <label class="form-label small mb-1">docker_enabled</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-iso-dk-en" v-model="form.config.docker_enabled" :disabled="!included.docker_enabled" />
                    <label class="form-check-label small" for="f-iso-dk-en">Docker isolation</label>
                  </div>
                </div>
              </div>
              <!-- Docker sub-fields -->
              <template v-for="isoField in dockerSubFields" :key="isoField.key">
                <div v-if="isIsoParentActive(isoField.dependsOn)" class="field-row iso-sub-row">
                  <div class="field-toggle"><div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" v-model="included[isoField.key]" />
                  </div></div>
                  <div class="field-body" :class="{ 'field-disabled': !included[isoField.key] }">
                    <label class="form-label small mb-1">{{ isoField.key }}</label>
                    <div v-if="isoField.type === 'toggle'" class="form-check form-switch">
                      <input class="form-check-input" type="checkbox" :id="'f-iso-' + isoField.key" v-model="form.config[isoField.key]" :disabled="!included[isoField.key]" />
                      <label class="form-check-label small" :for="'f-iso-' + isoField.key">{{ isoField.label }}</label>
                    </div>
                    <textarea v-else-if="isoField.type === 'textarea'" class="form-control form-control-sm font-monospace" rows="2" v-model="form.config[isoField.key]" :disabled="!included[isoField.key]" :placeholder="isoField.placeholder"></textarea>
                    <MultiSelectField
                      v-else-if="isoField.type === 'multi-select'"
                      :value="form.config[isoField.key]"
                      :disabled="!included[isoField.key]"
                      :options-from="isoField.optionsFrom || null"
                      :placeholder="isoField.placeholder || 'Select...'"
                      @update:value="form.config[isoField.key] = $event"
                    />
                    <TagListField
                      v-else-if="isoField.type === 'tag-list'"
                      :value="form.config[isoField.key]"
                      :disabled="!included[isoField.key]"
                      :placeholder="isoField.placeholder || 'Add...'"
                      @update:value="form.config[isoField.key] = $event"
                    />
                    <input v-else type="text" class="form-control form-control-sm font-monospace" v-model="form.config[isoField.key]" :disabled="!included[isoField.key]" :placeholder="isoField.placeholder" />
                  </div>
                </div>
              </template>
              <!-- bare_mode -->
              <div class="field-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.bare_mode" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.bare_mode }">
                  <label class="form-label small mb-1">bare_mode</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-iso-bare" v-model="form.config.bare_mode" :disabled="!included.bare_mode" />
                    <label class="form-check-label small" for="f-iso-bare">Bare mode (skips hooks, LSP, skills)</label>
                  </div>
                </div>
              </div>
              <!-- env_scrub_enabled -->
              <div class="field-row">
                <div class="field-toggle"><div class="form-check form-switch mb-0">
                  <input class="form-check-input" type="checkbox" v-model="included.env_scrub_enabled" />
                </div></div>
                <div class="field-body" :class="{ 'field-disabled': !included.env_scrub_enabled }">
                  <label class="form-label small mb-1">env_scrub_enabled</label>
                  <div class="form-check form-switch">
                    <input class="form-check-input" type="checkbox" id="f-iso-scrub" v-model="form.config.env_scrub_enabled" :disabled="!included.env_scrub_enabled" />
                    <label class="form-check-label small" for="f-iso-scrub">Scrub subprocess credentials</label>
                  </div>
                </div>
              </div>
            </template>

            <FieldSection
              v-else-if="currentArea === 'features'"
              :fields="FIELD_SCHEMAS.features"
              :config="form.config"
              :show-badges="false"
              :show-include-toggle="true"
              :field-states="{}"
              :included="included"
              @update:config="(key, val) => { form.config[key] = val }"
              @update:included="handleIncluded"
            />

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
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useProfileStore } from '@/stores/profile'
import { useMcpConfigStore } from '@/stores/mcpConfig'
import { PROFILE_AREA_LABELS } from '@/utils/profileAreas'
import { COMMON_TOOLS, COMMON_DENIED_TOOLS } from '@/utils/toolConstants'
import FieldSection from './fields/FieldSection.vue'
import SandboxSubSectionWidget from './fields/SandboxSubSectionWidget.vue'
import MultiSelectField from './fields/MultiSelectField.vue'
import TagListField from './fields/TagListField.vue'
import { FIELD_SCHEMAS } from './fields/fieldSchemas.js'

const profileStore = useProfileStore()
const mcpConfigStore = useMcpConfigStore()

// ---- Area metadata ----
const AREA_META = {
  model: {
    label: PROFILE_AREA_LABELS.model,
    description: 'Model selection, thinking mode, and effort level',
    fields: ['model', 'thinking_mode', 'thinking_budget_tokens', 'effort'],
  },
  permissions: {
    label: PROFILE_AREA_LABELS.permissions,
    description: 'Permission mode, allowed/disallowed tools, and directories',
    fields: ['permission_mode', 'allowed_tools', 'disallowed_tools', 'additional_directories', 'setting_sources'],
  },
  system_prompt: {
    label: PROFILE_AREA_LABELS.system_prompt,
    description: 'System prompt content and override behavior',
    fields: ['system_prompt', 'override_system_prompt'],
  },
  mcp: {
    label: PROFILE_AREA_LABELS.mcp,
    description: 'MCP server IDs and configuration toggles',
    fields: ['mcp_server_ids', 'enable_claudeai_mcp_servers', 'strict_mcp_config'],
  },
  isolation: {
    label: PROFILE_AREA_LABELS.isolation,
    description: 'CLI path, sandbox, Docker, and environment settings',
    fields: null,
  },
  features: {
    label: PROFILE_AREA_LABELS.features,
    description: 'History distillation, auto-memory, and skill creation',
    fields: ['history_distillation_enabled', 'auto_memory_mode', 'auto_memory_directory', 'skill_creating_enabled'],
  },
}

const isolationFields = [
  { key: 'cli_path', type: 'text', placeholder: '/path/to/claude-cli' },
  { key: 'sandbox_enabled', type: 'toggle', label: 'Enable sandbox mode' },
  { key: 'docker_enabled', type: 'toggle', label: 'Docker isolation' },
  { key: 'docker_image', type: 'text', placeholder: 'claude-code:local', dependsOn: 'docker_enabled' },
  { key: 'docker_extra_mounts', type: 'textarea', placeholder: '/host/path:/container/path:ro (one per line)', dependsOn: 'docker_enabled' },
  { key: 'docker_home_directory', type: 'text', placeholder: '/home/claude', dependsOn: 'docker_enabled' },
  { key: 'docker_proxy_enabled', type: 'toggle', label: 'Network proxy sidecar', dependsOn: 'docker_enabled' },
  { key: 'docker_proxy_image', type: 'text', placeholder: 'claude-proxy:local', dependsOn: 'docker_proxy_enabled' },
  { key: 'docker_proxy_allowlist_domains', type: 'tag-list', placeholder: 'e.g., api.example.com', dependsOn: 'docker_proxy_enabled' },
  { key: 'assigned_secrets', type: 'multi-select', optionsFrom: 'secrets', placeholder: 'Select secrets to assign...', dependsOn: 'docker_proxy_enabled' },
  { key: 'bare_mode', type: 'toggle', label: 'Bare mode (skips hooks, LSP, skills)' },
  { key: 'env_scrub_enabled', type: 'toggle', label: 'Scrub subprocess credentials' },
]

const dockerSubFields = isolationFields.filter(f => f.dependsOn === 'docker_enabled' || f.dependsOn === 'docker_proxy_enabled')

AREA_META.isolation.fields = [...isolationFields.map(f => f.key), 'sandbox_config']

// Permissions fields for PMT: inject quickAddItems and include permission_mode (profileOnly)
const permissionsFieldsForPmt = computed(() => {
  return FIELD_SCHEMAS.permissions.map((f) => {
    if (f.key === 'allowed_tools') return { ...f, quickAddItems: COMMON_TOOLS }
    if (f.key === 'disallowed_tools') return { ...f, quickAddItems: COMMON_DENIED_TOOLS }
    return f
  })
})

// ---- Form state ----
const showForm = ref(false)
const editingProfile = ref(null)
const form = reactive({ name: '', area: '', config: {} })
const included = reactive({})
const formError = ref('')
const formSubmitting = ref(false)

const currentArea = computed(() => editingProfile.value?.area || form.area)

const AREA_DEFAULTS = {
  model: { model: 'sonnet' },
  permissions: { permission_mode: 'default', setting_sources: ['user', 'project', 'local'] },
  mcp: { enable_claudeai_mcp_servers: true, strict_mcp_config: true },
  features: { history_distillation_enabled: true, auto_memory_mode: 'claude' },
}

watch(() => form.area, (newArea) => {
  if (!editingProfile.value) {
    form.config = { ...(AREA_DEFAULTS[newArea] || {}) }
    const fields = AREA_META[newArea]?.fields || []
    fields.forEach(f => { included[f] = false })
  }
})

const mcpServerIds = computed(() => {
  const v = form.config.mcp_server_ids
  if (!v) return []
  if (Array.isArray(v)) return v
  return v.split(',').map(s => s.trim()).filter(Boolean)
})

// ---- Include toggle handler ----
function handleIncluded(key, val) {
  included[key] = val
}

function isIsoParentActive(parentKey) {
  return included[parentKey] && form.config[parentKey] === true
}

function toggleMcpServer(id, checked) {
  const current = [...mcpServerIds.value]
  if (checked && !current.includes(id)) current.push(id)
  else if (!checked) { const i = current.indexOf(id); if (i >= 0) current.splice(i, 1) }
  form.config.mcp_server_ids = current
}

// ---- Form open/close ----
function hasValue(v) {
  if (v === undefined || v === null) return false
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'string') return v !== ''
  return true
}

function initIncluded(area, existingConfig = {}) {
  const fields = AREA_META[area]?.fields || []
  fields.forEach(f => {
    included[f] = hasValue(existingConfig[f])
  })
}

function openCreate() {
  editingProfile.value = null
  form.name = ''
  form.area = ''
  form.config = {}
  Object.keys(included).forEach(k => { included[k] = false })
  formError.value = ''
  showForm.value = true
}

function onSandboxConfigToggle(e) {
  if (e.target.checked && !form.config.sandbox_config) {
    form.config.sandbox_config = {
      autoAllowBashIfSandboxed: true,
      allowUnsandboxedCommands: false,
      excludedCommands: '',
      enableWeakerNestedSandbox: false,
      network: {
        allowedDomains: '',
        allowLocalBinding: false,
        allowUnixSockets: '',
        allowAllUnixSockets: false,
      },
      ignoreViolations: { file: '', network: '' },
    }
  }
}

function openEdit(profile) {
  editingProfile.value = profile
  form.name = profile.name
  form.area = profile.area
  form.config = JSON.parse(JSON.stringify(profile.config))
  formError.value = ''
  showForm.value = true
  nextTick(() => initIncluded(profile.area, profile.config))
}

function closeForm() {
  showForm.value = false
  editingProfile.value = null
  formError.value = ''
}

async function submitForm() {
  formError.value = ''
  if (!form.name.trim()) { formError.value = 'Name is required'; return }
  if (!editingProfile.value && !form.area) { formError.value = 'Area is required'; return }

  const cleanConfig = {}
  const area = currentArea.value
  const fields = AREA_META[area]?.fields || []
  for (const f of fields) {
    if (!included[f]) continue
    const v = form.config[f]
    if (v !== undefined && v !== null) {
      if (typeof v === 'boolean' || (Array.isArray(v) && v.length > 0) || (typeof v === 'number') || (typeof v === 'string' && v !== '') || (typeof v === 'object' && !Array.isArray(v))) {
        cleanConfig[f] = v
      }
    }
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
    await profileStore.deleteProfile(deleteTarget.value.profile_id)
    deleteTarget.value = null
  } catch (err) {
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
  profileStore.fetchIfEmpty()
  mcpConfigStore.fetchConfigs()
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

/* Area fields container */
.area-fields {
  background: var(--bs-light-bg-subtle);
  border-radius: 4px;
  border: 1px solid var(--bs-border-color);
  overflow: hidden;
}

/* Two-column field row */
.field-row {
  display: flex;
  align-items: flex-start;
  gap: 0;
  padding: 8px;
  border-bottom: 1px solid var(--bs-border-color-translucent);
}
.field-row:last-child {
  border-bottom: none;
}

/* Left column: toggle only, ~40px */
.field-toggle {
  width: 40px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding-top: 2px;
}

/* Right column: label + input, fills remaining width */
.field-body {
  flex: 1;
  min-width: 0;
}

/* Greyed-out state when toggle is off */
.field-body.field-disabled {
  opacity: 0.4;
  pointer-events: none;
}

/* Tag editor */
.tag-editor {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  cursor: text;
  min-height: 32px;
  align-items: center;
  background: var(--bs-body-bg);
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.75rem;
}

.tag-allowed { background: #d1f0d1; color: #1a5c1a; }
.tag-disallowed { background: #f8d7da; color: #842029; }

.tag-remove {
  cursor: pointer;
  font-size: 0.9rem;
  line-height: 1;
  opacity: 0.7;
}
.tag-remove:hover { opacity: 1; }

.tag-input {
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.8rem;
  min-width: 80px;
  flex: 1;
}

.quick-add-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}
.quick-add-btns .btn {
  font-size: 0.7rem;
  padding: 1px 6px;
}

/* Budget slider */
.budget-slider-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.budget-slider-group input[type="range"] {
  flex: 1;
}

/* Directory list */
.dir-list-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  background: var(--bs-secondary-bg);
  border-radius: 3px;
}
.dir-path { flex: 1; font-family: monospace; }
.dir-remove { cursor: pointer; color: var(--bs-danger); font-size: 1rem; }

/* MCP server list */
.mcp-server-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Isolation sub-rows (sandbox_config, docker sub-fields) */
.iso-sub-row {
  background: var(--bs-secondary-bg-subtle, #f8f9fa);
  border-left: 3px solid var(--bs-border-color);
  margin-left: 8px;
}

.iso-sub-section-label {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--bs-secondary);
  margin-bottom: 4px;
}
</style>
