<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="configurationModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-lg modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="configurationModalLabel" class="modal-title">
            <button
              v-if="mode === 'create-template' || mode === 'edit-template'"
              type="button"
              class="btn btn-link p-0 me-2"
              @click="switchToTemplateList"
              title="Back to template list"
            >
              &larr;
            </button>
            <button
              v-if="mode === 'save-as-template' || mode === 'update-template-from-session'"
              type="button"
              class="btn btn-link p-0 me-2"
              @click="returnToEditSession"
              title="Back to session editor"
            >
              &larr;
            </button>
            {{ modalTitle }}
          </h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Template List View -->
          <div v-if="mode === 'template-list'" class="template-list-view">
            <div class="d-flex justify-content-between align-items-center mb-3">
              <h6 class="mb-0">Templates</h6>
              <button @click="switchToCreateTemplate" class="btn btn-primary btn-sm">
                + Create New
              </button>
            </div>

            <div v-if="templates.length === 0" class="alert alert-info">
              No templates found. Create your first template!
            </div>

            <div v-for="template in templates" :key="template.template_id" class="card mb-2">
              <div class="card-body">
                <div class="d-flex justify-content-between align-items-start">
                  <div class="flex-grow-1">
                    <h6 class="card-title mb-1">{{ template.name }}</h6>
                    <p v-if="template.description" class="card-text text-muted small mb-2">{{ template.description }}</p>
                    <div class="template-meta">
                      <span class="badge bg-secondary me-2">{{ template.permission_mode }}</span>
                      <span class="text-muted small">{{ template.allowed_tools?.length || 0 }} tools</span>
                    </div>
                  </div>
                  <div class="btn-group btn-group-sm ms-3">
                    <button @click="switchToEditTemplate(template)" class="btn btn-outline-primary" title="Edit">
                      Edit
                    </button>
                    <button @click="deleteTemplate(template)" class="btn btn-outline-danger" title="Delete">
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Save as Template View (issue #580) -->
          <div v-else-if="mode === 'save-as-template'" class="save-as-template-view">
            <div class="mb-3">
              <label for="sat-name" class="form-label">Template Name <span class="text-danger">*</span></label>
              <input
                type="text"
                class="form-control"
                id="sat-name"
                v-model="saveAsTemplateName"
                placeholder="e.g., My Config Template"
                :class="{ 'is-invalid': saveAsTemplateError && !saveAsTemplateName.trim() }"
              />
            </div>
            <div class="mb-3">
              <label for="sat-description" class="form-label">Description</label>
              <input
                type="text"
                class="form-control"
                id="sat-description"
                v-model="saveAsTemplateDescription"
                placeholder="Optional description"
              />
            </div>
            <div class="mb-3">
              <label for="sat-capabilities" class="form-label">Capabilities</label>
              <input
                type="text"
                class="form-control"
                id="sat-capabilities"
                v-model="saveAsTemplateCapabilities"
                placeholder="e.g., python, testing, devops (comma-separated)"
              />
            </div>
            <div class="card">
              <div class="card-header"><small class="fw-bold">Configuration Summary</small></div>
              <div class="card-body p-2">
                <table class="table table-sm table-borderless mb-0">
                  <tbody>
                    <tr v-for="field in sessionConfigSummary" :key="field.label">
                      <td class="text-muted" style="width: 40%"><small>{{ field.label }}</small></td>
                      <td><small>{{ field.value }}</small></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- Update Template from Session View (issue #580) -->
          <div v-else-if="mode === 'update-template-from-session'" class="update-template-view">
            <div class="mb-3">
              <label for="utt-select" class="form-label">Select Template to Update</label>
              <select
                id="utt-select"
                class="form-select"
                v-model="updateTargetTemplateId"
                @change="computeTemplateDiff"
              >
                <option :value="null" disabled>Choose a template...</option>
                <option v-for="t in templates" :key="t.template_id" :value="t.template_id">
                  {{ t.name }}
                </option>
              </select>
            </div>

            <div v-if="updateTargetTemplateId && templateDiff !== null">
              <div v-if="templateDiff.length === 0" class="alert alert-info">
                Session configuration matches this template &mdash; no changes to apply.
              </div>
              <div v-else>
                <h6 class="mb-2">Changes to apply:</h6>
                <table class="table table-sm table-bordered">
                  <thead>
                    <tr>
                      <th style="width: 30%">Field</th>
                      <th style="width: 35%">Template (current)</th>
                      <th style="width: 35%">Session (new)</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="d in templateDiff" :key="d.field">
                      <td><small class="fw-bold">{{ d.label }}</small></td>
                      <td>
                        <small v-if="d.type === 'list'">
                          <span v-for="(item, i) in d.removed" :key="'r'+i" class="badge bg-danger me-1 mb-1">- {{ item }}</span>
                          <span v-for="(item, i) in d.kept" :key="'k'+i" class="badge bg-secondary me-1 mb-1">{{ item }}</span>
                          <span v-if="!d.removed.length && !d.kept.length" class="text-muted">(empty)</span>
                        </small>
                        <small v-else class="text-danger-emphasis">{{ d.oldDisplay }}</small>
                      </td>
                      <td>
                        <small v-if="d.type === 'list'">
                          <span v-for="(item, i) in d.added" :key="'a'+i" class="badge bg-success me-1 mb-1">+ {{ item }}</span>
                          <span v-for="(item, i) in d.kept" :key="'k2'+i" class="badge bg-secondary me-1 mb-1">{{ item }}</span>
                          <span v-if="!d.added.length && !d.kept.length" class="text-muted">(empty)</span>
                        </small>
                        <small v-else class="text-success-emphasis">{{ d.newDisplay }}</small>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- Form View (create/edit session or template) — Slide Layout -->
          <div v-else class="config-slide-body">
            <div class="slide-container" :class="{ 'show-advanced': showAdvanced }">
              <QuickSettingsPanel
                class="slide-panel"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                :templates="templates"
                :selected-template-id="selectedTemplateId"
                :session="editSession"
                :field-states="fieldStates"
                @update:form-data="updateFormData"
                @update:selected-template-id="updateSelectedTemplate"
                @open-folder-browser="openFolderBrowser"
                @open-template-manager="switchToTemplateList"
                @show-advanced="showAdvanced = true"
              />
              <AdvancedSettingsPanel
                class="slide-panel"
                ref="advancedPanelRef"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                :session="editSession"
                :field-states="fieldStates"
                @update:form-data="updateFormData"
                @preview-permissions="showPermissionPreview"
                @show-quick="showAdvanced = false"
                @browse-additional-dir="browseAdditionalDir"
              />
            </div>
          </div>

          <div v-if="errorMessage" class="alert alert-danger mt-3">
            {{ errorMessage }}
          </div>
        </div>
        <!-- Unified warning banner (Issue #459) -->
        <div v-if="showWarningBanner" class="warning-banner" :class="'warning-banner-' + warningLevel">
          <small v-if="warningLevel === 'reset'">
            Changes require a <strong>reset</strong> to take effect. This starts a new conversation.
          </small>
          <small v-else>
            Changes require a <strong>restart</strong> to take effect.
          </small>
        </div>
        <div class="modal-footer">
          <!-- Issue #580: Save as Template / Update Template buttons in edit-session mode -->
          <div v-if="mode === 'edit-session'" class="me-auto d-flex gap-2">
            <button
              type="button"
              class="btn btn-outline-info btn-sm"
              @click="switchToSaveAsTemplate"
            >
              Save as Template
            </button>
            <button
              type="button"
              class="btn btn-outline-info btn-sm"
              @click="switchToUpdateTemplate"
            >
              Update Template
            </button>
          </div>
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
            {{ mode === 'template-list' ? 'Close' : 'Cancel' }}
          </button>
          <button
            v-if="mode !== 'template-list'"
            type="button"
            class="btn btn-primary"
            :disabled="isSubmitting || !isFormValid"
            @click="handleSubmit"
          >
            <span v-if="isSubmitting" class="spinner-border spinner-border-sm me-2"></span>
            {{ submitButtonText }}
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- Permission Preview Modal (Issue #36) -->
  <PermissionPreviewModal
    ref="permissionPreviewModal"
    :working-directory="currentWorkingDirectory"
    :setting-sources="formData.setting_sources"
    :session-allowed-tools="currentAllowedTools"
    :session-disallowed-tools="currentDisallowedTools"
  />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, reactive, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'
import QuickSettingsPanel from './QuickSettingsPanel.vue'
import AdvancedSettingsPanel from './AdvancedSettingsPanel.vue'
import PermissionPreviewModal from './PermissionPreviewModal.vue'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// Modal state
const modalElement = ref(null)
let modalInstance = null
const permissionPreviewModal = ref(null)  // Issue #36

// Mode: 'create-session', 'edit-session', 'create-template', 'edit-template', 'configure-ephemeral',
//       'save-as-template', 'update-template-from-session'
const mode = ref('create-session')
const showAdvanced = ref(false)

// Component refs
const advancedPanelRef = ref(null)

// Context
const projectId = ref(null)
const editSession = ref(null)
const editTemplate = ref(null)

// Ephemeral config callback (issue #578)
const onConfiguredCallback = ref(null)

// Templates for dropdown
const templates = ref([])
const selectedTemplateId = ref(null)

// Context for returning to session creation after template management
const returnContext = ref(null)

// Issue #580: Save-as-template state
const saveAsTemplateName = ref('')
const saveAsTemplateDescription = ref('')
const saveAsTemplateCapabilities = ref('')
const saveAsTemplateError = ref('')

// Issue #580: Update-template-from-session state
const updateTargetTemplateId = ref(null)
const templateDiff = ref(null)

// --- CONFIG_FIELDS schema (issue #731) ---
// Each field: { default, change, contexts, trackState?, toPayload?, toUpdatePayload?, fromSource?, compare? }
const CONFIG_FIELDS = {
  name: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'update'],
    toPayload: (v) => v.trim(),
  },
  model: {
    default: 'sonnet',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    trackState: true,
    toPayload: (v) => v || null,
  },
  permission_mode: {
    default: 'default',
    change: null,
    contexts: ['session', 'template', 'ephemeral', 'update'],
    trackState: true,
    fromSource: (s) => s.permission_mode || s.current_permission_mode || 'default',
  },
  working_directory: {
    default: '',
    change: null,
    contexts: ['session', 'ephemeral'],
    toPayload: (v) => v.trim() || null,
  },
  description: {
    default: '',
    change: null,
    contexts: ['template'],
    toPayload: (v) => v.trim() || null,
  },
  role: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'update'],
    trackState: true,
    toPayload: (v) => v.trim() || null,
  },
  startImmediately: {
    default: true,
    change: null,
    contexts: [],
  },
  thinking_mode: {
    default: '',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    toPayload: (v) => v || null,
  },
  thinking_budget_tokens: {
    default: 10240,
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    toPayload: (v, fd) => fd.thinking_mode === 'enabled' ? v : null,
  },
  effort: {
    default: '',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    toPayload: (v) => v || null,
  },
  allowed_tools: {
    default: '',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    trackState: true,
    toPayload: (v) => { const l = v.split(',').map(s => s.trim()).filter(Boolean); return l.length ? l : null },
    fromSource: (s) => s.allowed_tools?.join?.(', ') ?? (s.allowed_tools || ''),
  },
  disallowed_tools: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'ephemeral', 'update'],
    trackState: true,
    toPayload: (v) => { const l = v.split(',').map(s => s.trim()).filter(Boolean); return l.length ? l : null },
    fromSource: (s) => s.disallowed_tools?.join?.(', ') ?? (s.disallowed_tools || ''),
  },
  capabilities: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'update'],
    trackState: true,
    toPayload: (v) => { const l = v.split(',').map(s => s.trim()).filter(Boolean); return l.length ? l : null },
    fromSource: (s) => s.capabilities?.join?.(', ') || '',
  },
  setting_sources: {
    default: ['user', 'project', 'local'],
    change: 'restart',
    contexts: ['session', 'ephemeral', 'update'],
    compare: (form, orig) => JSON.stringify([...form].sort()) !== JSON.stringify([...(orig || ['user', 'project', 'local'])].sort()),
  },
  system_prompt: {
    default: '',
    change: 'reset',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    trackState: true,
    toPayload: (v) => v.trim() || null,
  },
  override_system_prompt: {
    default: false,
    change: 'reset',
    contexts: ['session', 'template', 'ephemeral', 'update'],
  },
  sandbox_enabled: {
    default: false,
    change: 'reset',
    contexts: ['session', 'template', 'ephemeral', 'update'],
  },
  cli_path: {
    default: '',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    toPayload: (v) => v.trim() || null,
    toUpdatePayload: (v) => v.trim(),
  },
  additional_directories: {
    default: '',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    toPayload: (v) => v.trim() ? v.trim().split('\n').map(d => d.trim()).filter(Boolean) : null,
    toUpdatePayload: (v) => v.trim() ? v.trim().split('\n').map(d => d.trim()).filter(Boolean) : [],
    fromSource: (s) => Array.isArray(s.additional_directories) ? s.additional_directories.join('\n') : '',
    compare: (form, orig) => (form || '') !== ((orig || []).join?.('\n') ?? ''),
  },
  docker_enabled: {
    default: false,
    change: null,
    contexts: ['session', 'template', 'ephemeral'],
  },
  docker_image: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'ephemeral'],
    toPayload: (v) => v.trim() || null,
  },
  docker_extra_mounts: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'ephemeral'],
    toPayload: (v) => v.trim() ? v.trim().split('\n').map(m => m.trim()).filter(Boolean) : null,
    fromSource: (s) => Array.isArray(s.docker_extra_mounts) ? s.docker_extra_mounts.join('\n') : '',
  },
  docker_home_directory: {
    default: '',
    change: null,
    contexts: ['session', 'template', 'ephemeral'],
    toPayload: (v) => v.trim() || null,
  },
  history_distillation_enabled: {
    default: true,
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
  },
  auto_memory_mode: {
    default: 'claude',
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
  },
  skill_creating_enabled: {
    default: false,
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
  },
  mcp_server_ids: {
    default: [],
    change: 'restart',
    contexts: ['session', 'template', 'ephemeral', 'update'],
    compare: (a, b) => JSON.stringify(a || []) === JSON.stringify(b || []),
  },
  enable_claudeai_mcp_servers: {
    default: true,
    change: 'restart',
    contexts: ['session', 'ephemeral', 'update'],
  },
}

// Derive fieldStates and templateOriginalValues from schema
const fieldStates = reactive(
  Object.fromEntries(Object.entries(CONFIG_FIELDS).filter(([, m]) => m.trackState).map(([k]) => [k, 'normal']))
)
const templateOriginalValues = ref(
  Object.fromEntries(Object.entries(CONFIG_FIELDS).filter(([, m]) => m.trackState).map(([k]) => [k, null]))
)

// Form data — defaults from schema + sandbox (nested, out of scope for schema)
const formData = reactive({
  ...Object.fromEntries(Object.entries(CONFIG_FIELDS).map(([k, m]) => [k, structuredClone(m.default)])),
  sandbox: {
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
    ignoreViolations: {
      file: '',
      network: '',
    }
  }
})

// --- Schema helper functions (issue #731) ---

function resetFormFields() {
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    formData[field] = structuredClone(meta.default)
  }
}

function populateFormFromSource(source) {
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (meta.fromSource) {
      formData[field] = meta.fromSource(source)
    } else {
      formData[field] = source[field] ?? meta.default
    }
  }
}

function extractPayload(context) {
  const payload = {}
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (!meta.contexts.includes(context)) continue
    const transform = (context === 'update' && meta.toUpdatePayload) ? meta.toUpdatePayload : meta.toPayload
    payload[field] = transform ? transform(formData[field], formData) : formData[field]
  }
  return payload
}

function hasSchemaChanges(changeType) {
  if (!editSession.value) return false
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (meta.change !== changeType) continue
    if (meta.compare) {
      if (meta.compare(formData[field], editSession.value[field])) return true
    } else {
      const formVal = formData[field] ?? meta.default
      const origVal = editSession.value[field] ?? meta.default
      if (formVal !== origVal) return true
    }
  }
  return false
}

function resetFieldStates() {
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (meta.trackState) {
      fieldStates[field] = 'normal'
      templateOriginalValues.value[field] = null
    }
  }
}

function setupFieldStateWatchers() {
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (!meta.trackState) continue
    watch(() => formData[field], (newVal) => {
      if (templateOriginalValues.value[field] !== null) {
        fieldStates[field] = newVal === templateOriginalValues.value[field] ? 'autofilled' : 'modified'
      }
    })
  }
}

function populateSandboxFromSource(source) {
  const sc = source.sandbox_config || {}
  formData.sandbox.autoAllowBashIfSandboxed = sc.autoAllowBashIfSandboxed ?? true
  formData.sandbox.allowUnsandboxedCommands = sc.allowUnsandboxedCommands ?? false
  formData.sandbox.excludedCommands = (sc.excludedCommands || []).join(', ')
  formData.sandbox.enableWeakerNestedSandbox = sc.enableWeakerNestedSandbox ?? false
  const net = sc.network || {}
  formData.sandbox.network.allowedDomains = (net.allowedDomains || []).join(', ')
  formData.sandbox.network.allowLocalBinding = net.allowLocalBinding ?? false
  formData.sandbox.network.allowUnixSockets = (net.allowUnixSockets || []).join(', ')
  formData.sandbox.network.allowAllUnixSockets = net.allowAllUnixSockets ?? false
  const iv = sc.ignoreViolations || {}
  formData.sandbox.ignoreViolations.file = (iv.file || []).join(', ')
  formData.sandbox.ignoreViolations.network = (iv.network || []).join(', ')
}

function resetSandboxFields() {
  formData.sandbox.autoAllowBashIfSandboxed = true
  formData.sandbox.allowUnsandboxedCommands = false
  formData.sandbox.excludedCommands = ''
  formData.sandbox.enableWeakerNestedSandbox = false
  formData.sandbox.network.allowedDomains = ''
  formData.sandbox.network.allowLocalBinding = false
  formData.sandbox.network.allowUnixSockets = ''
  formData.sandbox.network.allowAllUnixSockets = false
  formData.sandbox.ignoreViolations.file = ''
  formData.sandbox.ignoreViolations.network = ''
}

// Validation errors
const errors = reactive({
  name: ''
})

// Track if form has errors (used in validation)
const hasFormErrors = computed(() => !!errors.name)

const isSubmitting = ref(false)
const errorMessage = ref('')

// Computed properties
const isSessionMode = computed(() => mode.value === 'create-session' || mode.value === 'edit-session' || mode.value === 'configure-ephemeral')
const isTemplateMode = computed(() => mode.value === 'create-template' || mode.value === 'edit-template')
const isCreateMode = computed(() => mode.value === 'create-session' || mode.value === 'create-template')
const isEditMode = computed(() => mode.value === 'edit-session' || mode.value === 'edit-template')

const modalTitle = computed(() => {
  switch (mode.value) {
    case 'create-session': return 'Create Session'
    case 'edit-session': return 'Edit Session'
    case 'create-template': return 'Create Template'
    case 'edit-template': return 'Edit Template'
    case 'template-list': return 'Manage Templates'
    case 'configure-ephemeral': return 'Configure Scheduled Session'
    case 'save-as-template': return 'Save as Template'
    case 'update-template-from-session': return 'Update Template from Session'
    default: return 'Configuration'
  }
})

const submitButtonText = computed(() => {
  if (mode.value === 'configure-ephemeral') return 'Apply Configuration'
  if (mode.value === 'save-as-template') return isSubmitting.value ? 'Saving...' : 'Save Template'
  if (mode.value === 'update-template-from-session') return isSubmitting.value ? 'Updating...' : 'Confirm Update'
  if (isSubmitting.value) {
    return isEditMode.value ? 'Saving...' : 'Creating...'
  }
  return isEditMode.value ? 'Save Changes' : (isSessionMode.value ? 'Create Session' : 'Save Template')
})

const isFormValid = computed(() => {
  // Name is optional for configure-ephemeral mode
  if (mode.value === 'configure-ephemeral') return true
  // Save as template: name required
  if (mode.value === 'save-as-template') return !!saveAsTemplateName.value.trim()
  // Update template: must have selected a template and diff must show changes
  if (mode.value === 'update-template-from-session') {
    return updateTargetTemplateId.value && templateDiff.value !== null && templateDiff.value.length > 0
  }
  // Name is required for all other modes
  if (!formData.name.trim()) return false
  return true
})

// Warning banner computed properties (Issue #459)
const isSessionActive = computed(() => {
  return editSession.value?.state === 'active' || editSession.value?.state === 'starting'
})

const hasResetChanges = computed(() => {
  if (hasSchemaChanges('reset')) return true
  // Compare sandbox config fields if sandbox is enabled
  if (formData.sandbox_enabled && editSession.value) {
    const sc = editSession.value.sandbox_config || {}
    const fd = formData.sandbox
    const origStr = JSON.stringify({
      autoAllowBashIfSandboxed: sc.autoAllowBashIfSandboxed ?? true,
      allowUnsandboxedCommands: sc.allowUnsandboxedCommands ?? false,
      excludedCommands: (sc.excludedCommands || []).join(', '),
      enableWeakerNestedSandbox: sc.enableWeakerNestedSandbox ?? false,
      network: {
        allowedDomains: (sc.network?.allowedDomains || []).join(', '),
        allowLocalBinding: sc.network?.allowLocalBinding ?? false,
        allowUnixSockets: (sc.network?.allowUnixSockets || []).join(', '),
        allowAllUnixSockets: sc.network?.allowAllUnixSockets ?? false,
      },
      ignoreViolations: {
        file: (sc.ignoreViolations?.file || []).join(', '),
        network: (sc.ignoreViolations?.network || []).join(', '),
      }
    })
    const currStr = JSON.stringify({
      autoAllowBashIfSandboxed: fd.autoAllowBashIfSandboxed,
      allowUnsandboxedCommands: fd.allowUnsandboxedCommands,
      excludedCommands: fd.excludedCommands,
      enableWeakerNestedSandbox: fd.enableWeakerNestedSandbox,
      network: {
        allowedDomains: fd.network.allowedDomains,
        allowLocalBinding: fd.network.allowLocalBinding,
        allowUnixSockets: fd.network.allowUnixSockets,
        allowAllUnixSockets: fd.network.allowAllUnixSockets,
      },
      ignoreViolations: {
        file: fd.ignoreViolations.file,
        network: fd.ignoreViolations.network,
      }
    })
    if (origStr !== currStr) return true
  }
  return false
})

const hasRestartChanges = computed(() => hasSchemaChanges('restart'))

const warningLevel = computed(() => {
  if (hasResetChanges.value) return 'reset'
  if (hasRestartChanges.value) return 'restart'
  return null
})

const showWarningBanner = computed(() => {
  return mode.value === 'edit-session' && isSessionActive.value && warningLevel.value !== null
})

// Issue #36: Permission preview helpers
const currentWorkingDirectory = computed(() => {
  // For edit session, use the session's working directory
  if (editSession.value?.working_directory) {
    return editSession.value.working_directory
  }
  // For create session, use the form's working directory or project's working directory
  if (formData.working_directory) {
    return formData.working_directory
  }
  // Fall back to project's working directory
  const project = projectStore.projects.get(projectId.value)
  return project?.working_directory || ''
})

const currentAllowedTools = computed(() => {
  if (!formData.allowed_tools || !formData.allowed_tools.trim()) return []
  return formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

const currentDisallowedTools = computed(() => {
  if (!formData.disallowed_tools || !formData.disallowed_tools.trim()) return []
  return formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

// Methods

// Issue #36: Show permission preview modal
function showPermissionPreview() {
  if (permissionPreviewModal.value) {
    permissionPreviewModal.value.show()
  }
}

// Issue #458: Build sandbox_config dict from formData.sandbox for API payload
function buildSandboxConfig() {
  const parseList = (str) => str.split(',').map(s => s.trim()).filter(s => s.length > 0)
  return {
    autoAllowBashIfSandboxed: formData.sandbox.autoAllowBashIfSandboxed,
    allowUnsandboxedCommands: formData.sandbox.allowUnsandboxedCommands,
    excludedCommands: parseList(formData.sandbox.excludedCommands),
    enableWeakerNestedSandbox: formData.sandbox.enableWeakerNestedSandbox,
    network: {
      allowedDomains: parseList(formData.sandbox.network.allowedDomains),
      allowLocalBinding: formData.sandbox.network.allowLocalBinding,
      allowUnixSockets: parseList(formData.sandbox.network.allowUnixSockets),
      allowAllUnixSockets: formData.sandbox.network.allowAllUnixSockets,
    },
    ignoreViolations: {
      file: parseList(formData.sandbox.ignoreViolations.file),
      network: parseList(formData.sandbox.ignoreViolations.network),
    }
  }
}

function updateFormData(field, value) {
  formData[field] = value

  // Clear related errors
  if (field === 'name') errors.name = ''
}

function updateSelectedTemplate(templateId) {
  selectedTemplateId.value = templateId
  applyTemplate()
}

async function loadTemplates() {
  try {
    const data = await api.get('/api/templates')
    templates.value = data || []
  } catch (error) {
    console.error('Failed to load templates:', error)
    templates.value = []
  }
}

function applyTemplate() {
  if (!selectedTemplateId.value) {
    // Clear template-derived fields when "[None]" selected
    resetFormFields()
    resetSandboxFields()
    resetFieldStates()
    return
  }

  const template = templates.value.find(t => t.template_id === selectedTemplateId.value)
  if (!template) return

  // Reset field states before applying new template
  resetFieldStates()

  // Apply schema fields from template with field-state tracking
  for (const [field, meta] of Object.entries(CONFIG_FIELDS)) {
    if (!meta.trackState) {
      // Non-tracked fields: just populate
      if (meta.fromSource) {
        formData[field] = meta.fromSource(template)
      } else {
        formData[field] = template[field] ?? meta.default
      }
      continue
    }
    // Tracked fields: populate + set autofilled state
    let value
    if (meta.fromSource) {
      value = meta.fromSource(template)
    } else {
      value = template[field] ?? null
    }
    if (value !== null && value !== '' && value !== meta.default) {
      formData[field] = value
      templateOriginalValues.value[field] = value
      fieldStates[field] = 'autofilled'
    } else {
      formData[field] = meta.default
      templateOriginalValues.value[field] = null
    }
  }

  // Apply sandbox config from template
  populateSandboxFromSource(template)

  // Sandbox string fields with field-state tracking (array → comma-separated)
  const sc = template.sandbox_config || {}
  const net = sc.network || {}
  const iv = sc.ignoreViolations || {}
  const sandboxTracked = [
    ['sandbox_excludedCommands', (sc.excludedCommands || []).join(', ')],
    ['sandbox_network_allowedDomains', (net.allowedDomains || []).join(', ')],
    ['sandbox_network_allowUnixSockets', (net.allowUnixSockets || []).join(', ')],
    ['sandbox_ignoreViolations_file', (iv.file || []).join(', ')],
    ['sandbox_ignoreViolations_network', (iv.network || []).join(', ')],
  ]
  for (const [key, value] of sandboxTracked) {
    templateOriginalValues.value[key] = value || null
    if (value) fieldStates[key] = 'autofilled'
  }
}

function openFolderBrowser() {
  const project = projectId.value ? projectStore.projects.get(projectId.value) : null
  const defaultPath = project?.working_directory || ''

  uiStore.showModal('folder-browser', {
    defaultPath: defaultPath,
    currentPath: formData.working_directory || '',
    onSelect: (path) => {
      formData.working_directory = path
    }
  })
}

function browseAdditionalDir() {
  const project = projectId.value ? projectStore.projects.get(projectId.value) : null
  const defaultPath = formData.working_directory || project?.working_directory || ''

  uiStore.showModal('folder-browser', {
    defaultPath: defaultPath,
    currentPath: '',
    onSelect: (path) => {
      if (advancedPanelRef.value) {
        advancedPanelRef.value.addDirectoryPath(path)
      }
    }
  })
}

// Template list navigation
function switchToTemplateList() {
  // Store return context if coming from session creation
  if (mode.value === 'create-session') {
    returnContext.value = {
      mode: 'create-session',
      projectId: projectId.value,
      formData: { ...formData },
      selectedTemplateId: selectedTemplateId.value
    }
  }
  mode.value = 'template-list'
  resetForm()
}

function switchToCreateTemplate() {
  mode.value = 'create-template'
  resetForm()
  showAdvanced.value = false
}

function switchToEditTemplate(template) {
  mode.value = 'edit-template'
  editTemplate.value = template
  resetForm()
  populateFormFromTemplate(template)
  showAdvanced.value = false
}

async function deleteTemplate(template) {
  if (!confirm(`Delete template "${template.name}"?\n\nThis action cannot be undone.`)) {
    return
  }

  try {
    await api.delete(`/api/templates/${template.template_id}`)
    await loadTemplates()
  } catch (error) {
    console.error('Failed to delete template:', error)
    errorMessage.value = error.response?.data?.detail || 'Failed to delete template'
  }
}

// Issue #580: Build template config object from session data
function buildTemplateFromSession() {
  const session = editSession.value
  if (!session) return {}

  const parseList = (val) => {
    if (Array.isArray(val)) return val.length > 0 ? val : null
    if (typeof val === 'string' && val.trim()) return val.split(',').map(s => s.trim()).filter(s => s)
    return null
  }

  return {
    permission_mode: session.current_permission_mode || 'default',
    allowed_tools: parseList(session.allowed_tools),
    disallowed_tools: parseList(session.disallowed_tools),
    model: session.model || null,
    system_prompt: session.system_prompt || null,
    override_system_prompt: session.override_system_prompt ?? false,
    role: session.role || null,
    sandbox_enabled: session.sandbox_enabled ?? false,
    sandbox_config: session.sandbox_config || null,
    cli_path: session.cli_path || null,
    docker_enabled: session.docker_enabled ?? false,
    docker_image: session.docker_image || null,
    docker_extra_mounts: parseList(session.docker_extra_mounts),
    docker_home_directory: session.docker_home_directory || null,
    thinking_mode: session.thinking_mode || null,
    thinking_budget_tokens: session.thinking_budget_tokens || null,
    effort: session.effort || null,
  }
}

// Issue #580: Config summary for save-as-template view
const sessionConfigSummary = computed(() => {
  const config = buildTemplateFromSession()
  const fields = []
  const addField = (label, value) => {
    if (value !== null && value !== undefined && value !== '' && value !== false) {
      fields.push({ label, value: Array.isArray(value) ? value.join(', ') : String(value) })
    }
  }
  addField('Permission Mode', config.permission_mode)
  addField('Model', config.model)
  addField('Allowed Tools', config.allowed_tools)
  addField('Disallowed Tools', config.disallowed_tools)
  addField('System Prompt', config.system_prompt ? (config.system_prompt.length > 80 ? config.system_prompt.substring(0, 80) + '...' : config.system_prompt) : null)
  addField('Override Prompt', config.override_system_prompt)
  addField('Role', config.role)
  addField('Sandbox', config.sandbox_enabled)
  addField('CLI Path', config.cli_path)
  addField('Docker', config.docker_enabled)
  addField('Thinking Mode', config.thinking_mode)
  addField('Thinking Budget', config.thinking_budget_tokens)
  addField('Effort', config.effort)
  if (fields.length === 0) {
    fields.push({ label: 'Configuration', value: 'Default settings' })
  }
  return fields
})

function switchToSaveAsTemplate() {
  saveAsTemplateName.value = ''
  saveAsTemplateDescription.value = ''
  saveAsTemplateCapabilities.value = editSession.value?.capabilities?.join(', ') || ''
  saveAsTemplateError.value = ''
  errorMessage.value = ''
  loadTemplates()
  mode.value = 'save-as-template'
}

function switchToUpdateTemplate() {
  updateTargetTemplateId.value = null
  templateDiff.value = null
  errorMessage.value = ''
  loadTemplates()
  mode.value = 'update-template-from-session'
}

function returnToEditSession() {
  errorMessage.value = ''
  mode.value = 'edit-session'
}

function computeTemplateDiff() {
  if (!updateTargetTemplateId.value) {
    templateDiff.value = null
    return
  }

  const template = templates.value.find(t => t.template_id === updateTargetTemplateId.value)
  if (!template) {
    errorMessage.value = 'Template not found — it may have been deleted.'
    templateDiff.value = null
    return
  }

  errorMessage.value = ''
  const sessionConfig = buildTemplateFromSession()
  const diffs = []

  const normList = (val) => {
    if (!val || (Array.isArray(val) && val.length === 0)) return []
    if (Array.isArray(val)) return [...val].sort()
    return []
  }

  const normScalar = (val) => (val === null || val === undefined) ? '' : String(val)

  const scalarFields = [
    { field: 'permission_mode', label: 'Permission Mode' },
    { field: 'model', label: 'Model' },
    { field: 'system_prompt', label: 'System Prompt' },
    { field: 'override_system_prompt', label: 'Override Prompt' },
    { field: 'role', label: 'Role' },
    { field: 'sandbox_enabled', label: 'Sandbox Enabled' },
    { field: 'cli_path', label: 'CLI Path' },
    { field: 'docker_enabled', label: 'Docker Enabled' },
    { field: 'docker_image', label: 'Docker Image' },
    { field: 'docker_home_directory', label: 'Docker Home Dir' },
    { field: 'thinking_mode', label: 'Thinking Mode' },
    { field: 'thinking_budget_tokens', label: 'Thinking Budget' },
    { field: 'effort', label: 'Effort' },
  ]

  const listFields = [
    { field: 'allowed_tools', label: 'Allowed Tools' },
    { field: 'disallowed_tools', label: 'Disallowed Tools' },
    { field: 'additional_directories', label: 'Additional Directories' },
    { field: 'docker_extra_mounts', label: 'Docker Mounts' },
  ]

  for (const { field, label } of scalarFields) {
    const oldVal = normScalar(template[field])
    const newVal = normScalar(sessionConfig[field])
    if (oldVal !== newVal) {
      diffs.push({
        field,
        label,
        type: 'scalar',
        oldDisplay: oldVal || '(none)',
        newDisplay: newVal || '(none)',
      })
    }
  }

  for (const { field, label } of listFields) {
    const oldList = normList(template[field])
    const newList = normList(sessionConfig[field])
    const oldSet = new Set(oldList)
    const newSet = new Set(newList)
    const added = newList.filter(x => !oldSet.has(x))
    const removed = oldList.filter(x => !newSet.has(x))
    const kept = oldList.filter(x => newSet.has(x))
    if (added.length > 0 || removed.length > 0) {
      diffs.push({ field, label, type: 'list', added, removed, kept })
    }
  }

  templateDiff.value = diffs
}

async function saveAsTemplate() {
  const name = saveAsTemplateName.value.trim()
  if (!name) {
    saveAsTemplateError.value = 'Name is required'
    return
  }

  const config = buildTemplateFromSession()
  const capsList = saveAsTemplateCapabilities.value
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  const payload = {
    name,
    description: saveAsTemplateDescription.value.trim() || null,
    capabilities: capsList.length > 0 ? capsList : null,
    ...config,
  }

  await api.post('/api/templates', payload)
  await loadTemplates()
  mode.value = 'edit-session'
}

async function updateTemplateFromSession() {
  if (!updateTargetTemplateId.value) return

  const template = templates.value.find(t => t.template_id === updateTargetTemplateId.value)
  if (!template) {
    errorMessage.value = 'Template not found — it may have been deleted.'
    return
  }

  const config = buildTemplateFromSession()
  const payload = {
    name: template.name,
    description: template.description,
    capabilities: template.capabilities,
    ...config,
  }

  await api.put(`/api/templates/${template.template_id}`, payload)
  await loadTemplates()
  mode.value = 'edit-session'
}

function validate() {
  let isValid = true
  errors.name = ''

  // These modes handle their own validation
  if (mode.value === 'save-as-template' || mode.value === 'update-template-from-session') return true

  // Name is optional for configure-ephemeral mode
  if (mode.value !== 'configure-ephemeral' && !formData.name.trim()) {
    errors.name = 'Name is required'
    isValid = false
  }

  return isValid
}

async function handleSubmit() {
  if (!validate()) {
    // Switch to quick panel if there's a name error
    if (errors.name) showAdvanced.value = false
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    switch (mode.value) {
      case 'create-session':
        await createSession()
        // Close modal after session creation
        if (modalInstance) modalInstance.hide()
        break
      case 'edit-session':
        await updateSession()
        // Close modal after session edit
        if (modalInstance) modalInstance.hide()
        break
      case 'configure-ephemeral':
        emitEphemeralConfig()
        if (modalInstance) modalInstance.hide()
        break
      case 'create-template':
        await createTemplate()
        // createTemplate returns to template list, don't close
        break
      case 'edit-template':
        await updateTemplate()
        // updateTemplate returns to template list, don't close
        break
      case 'save-as-template':
        await saveAsTemplate()
        break
      case 'update-template-from-session':
        await updateTemplateFromSession()
        break
    }
  } catch (error) {
    console.error('Submit failed:', error)
    // Issue #580: Show friendly slug conflict message
    const detail = error.response?.data?.detail || error.message || 'Operation failed. Please try again.'
    if (mode.value === 'save-as-template' && detail.includes('already exists')) {
      errorMessage.value = `A template with a similar name already exists. Choose a different name or use Update Template to overwrite it.`
    } else {
      errorMessage.value = detail
    }
  } finally {
    isSubmitting.value = false
  }
}

async function createSession() {
  if (!projectId.value) {
    throw new Error('Project ID is missing')
  }

  const payload = {
    ...extractPayload('session'),
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
  }

  const response = await api.post(`/api/legions/${projectId.value}/minions`, payload)

  if (response.success && response.minion_id) {
    // Refresh data
    await projectStore.fetchProjects()
    await sessionStore.fetchSessions()

    // Start session if requested
    if (formData.startImmediately) {
      await sessionStore.startSession(response.minion_id)
    }

    // Navigate to the new session
    router.push(`/session/${response.minion_id}`)
  } else {
    throw new Error('Failed to create session')
  }
}

function emitEphemeralConfig() {
  const payload = {
    ...extractPayload('ephemeral'),
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
  }

  if (onConfiguredCallback.value) {
    onConfiguredCallback.value(payload)
  }
}

function populateFormFromConfig(config) {
  populateFormFromSource(config)
  populateSandboxFromSource(config)
}

async function updateSession() {
  if (!editSession.value) {
    throw new Error('Session data is missing')
  }

  const sessionId = editSession.value.session_id
  const isActive = editSession.value.state === 'active' || editSession.value.state === 'starting'

  const updates = {
    ...extractPayload('update'),
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
  }

  // Update session via PATCH (takes effect on next restart if session is active)
  await sessionStore.patchSession(sessionId, updates)

  // Update permission mode if changed and session is active (this goes through SDK)
  if (isActive && formData.permission_mode !== editSession.value.current_permission_mode) {
    await sessionStore.setPermissionMode(sessionId, formData.permission_mode)
  }
}

async function createTemplate() {
  const payload = {
    ...extractPayload('template'),
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
  }

  await api.post('/api/templates', payload)

  // Reload templates
  await loadTemplates()

  // Return to template list instead of closing modal
  mode.value = 'template-list'
  resetForm()
}

async function updateTemplate() {
  if (!editTemplate.value) {
    throw new Error('Template data is missing')
  }

  const payload = {
    ...extractPayload('template'),
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
  }

  await api.put(`/api/templates/${editTemplate.value.template_id}`, payload)

  // Reload templates
  await loadTemplates()

  // Return to template list instead of closing modal
  mode.value = 'template-list'
  editTemplate.value = null
  resetForm()
}

function resetForm() {
  resetFormFields()
  resetSandboxFields()
  errors.name = ''
  resetFieldStates()
  selectedTemplateId.value = null
  errorMessage.value = ''
  showAdvanced.value = false
}

function populateFormFromSession(session) {
  populateFormFromSource(session)
  populateSandboxFromSource(session)
}

function populateFormFromTemplate(template) {
  populateFormFromSource(template)
  populateSandboxFromSource(template)
}

function onModalShown() {
  // Focus name input
  nextTick(() => {
    const nameInput = document.getElementById('config-name')
    if (nameInput) {
      nameInput.focus()
      if (isEditMode.value) {
        nameInput.select()
      }
    }
  })
}

function onModalHidden() {
  resetForm()
  mode.value = 'create-session'
  projectId.value = null
  editSession.value = null
  editTemplate.value = null
  onConfiguredCallback.value = null
  // Issue #580: Reset save-as/update-template state
  saveAsTemplateName.value = ''
  saveAsTemplateDescription.value = ''
  saveAsTemplateCapabilities.value = ''
  saveAsTemplateError.value = ''
  updateTargetTemplateId.value = null
  templateDiff.value = null
  uiStore.hideModal()
}

// Watch for modal show requests
watch(
  () => uiStore.currentModal,
  async (modal, oldModal) => {
    if (!modal || !modalInstance) return

    // Handle configuration modal
    if (modal.name === 'configuration') {
      const data = modal.data || {}
      mode.value = data.mode || 'create-session'
      projectId.value = data.project?.project_id || data.projectId || null
      editSession.value = data.session || null
      editTemplate.value = data.template || null

      resetForm()

      // Load templates for session creation, configure-ephemeral, and template-list
      if (mode.value === 'create-session' || mode.value === 'configure-ephemeral' || mode.value === 'template-list') {
        await loadTemplates()
      }

      // Populate form based on mode
      if (mode.value === 'edit-session' && editSession.value) {
        populateFormFromSession(editSession.value)
      } else if (mode.value === 'edit-template' && editTemplate.value) {
        populateFormFromTemplate(editTemplate.value)
      } else if (mode.value === 'configure-ephemeral') {
        onConfiguredCallback.value = data.onConfigured || null
        if (data.seedConfig) {
          populateFormFromConfig(data.seedConfig)
        }
      }

      modalInstance.show()
    }

    // Handle legacy modal names for backward compatibility
    else if (modal.name === 'create-minion' || modal.name === 'create-session') {
      const data = modal.data || {}
      mode.value = 'create-session'
      projectId.value = data.project?.project_id || null
      editSession.value = null
      editTemplate.value = null

      resetForm()
      await loadTemplates()
      modalInstance.show()
    }
    else if (modal.name === 'edit-session') {
      const data = modal.data || {}
      mode.value = 'edit-session'
      editSession.value = data.session || null
      projectId.value = editSession.value?.project_id || null
      editTemplate.value = null

      resetForm()
      if (editSession.value) {
        populateFormFromSession(editSession.value)
      }
      modalInstance.show()
    }
    else if (modal.name === 'template-management') {
      // Open in template list mode - show template list view
      mode.value = 'template-list'
      resetForm()
      await loadTemplates()
      modalInstance.show()
    }

    // Reload templates if template management just closed
    if (oldModal?.name === 'configuration' && oldModal.data?.mode?.includes('template')) {
      if (modal?.name === 'configuration' && mode.value === 'create-session') {
        await loadTemplates()
      }
    }
  }
)

// Setup schema-driven field-state watchers (issue #731)
setupFieldStateWatchers()

// Sandbox field-state watchers (not in schema, stays manual)
watch(() => formData.sandbox.excludedCommands, (newVal) => {
  if (templateOriginalValues.value.sandbox_excludedCommands !== null) {
    fieldStates.sandbox_excludedCommands = newVal === templateOriginalValues.value.sandbox_excludedCommands ? 'autofilled' : 'modified'
  }
})
watch(() => formData.sandbox.network.allowedDomains, (newVal) => {
  if (templateOriginalValues.value.sandbox_network_allowedDomains !== null) {
    fieldStates.sandbox_network_allowedDomains = newVal === templateOriginalValues.value.sandbox_network_allowedDomains ? 'autofilled' : 'modified'
  }
})
watch(() => formData.sandbox.network.allowUnixSockets, (newVal) => {
  if (templateOriginalValues.value.sandbox_network_allowUnixSockets !== null) {
    fieldStates.sandbox_network_allowUnixSockets = newVal === templateOriginalValues.value.sandbox_network_allowUnixSockets ? 'autofilled' : 'modified'
  }
})
watch(() => formData.sandbox.ignoreViolations.file, (newVal) => {
  if (templateOriginalValues.value.sandbox_ignoreViolations_file !== null) {
    fieldStates.sandbox_ignoreViolations_file = newVal === templateOriginalValues.value.sandbox_ignoreViolations_file ? 'autofilled' : 'modified'
  }
})
watch(() => formData.sandbox.ignoreViolations.network, (newVal) => {
  if (templateOriginalValues.value.sandbox_ignoreViolations_network !== null) {
    fieldStates.sandbox_ignoreViolations_network = newVal === templateOriginalValues.value.sandbox_ignoreViolations_network ? 'autofilled' : 'modified'
  }
})

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then((module) => {
      const Modal = module.default
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('shown.bs.modal', onModalShown)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

onUnmounted(() => {
  if (modalElement.value) {
    modalElement.value.removeEventListener('shown.bs.modal', onModalShown)
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>

<style scoped>
.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}

/* Warning banner (Issue #459) */
.warning-banner {
  padding: 0.5rem 1rem;
  border-top: 1px solid;
}

.warning-banner-reset {
  background-color: #ffe4cc;
  border-color: #f0c9a6;
  color: #663c00;
}

.warning-banner-restart {
  background-color: #fffbea;
  border-color: #f0e6c0;
  color: #664d03;
}
</style>
