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

          <!-- Form View (create/edit session or template) -->
          <div v-else>
            <!-- Tab Navigation -->
            <ul class="nav nav-tabs nav-tabs-responsive mb-3" role="tablist">
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'general', 'has-error': tabErrors.general }"
                  type="button"
                  @click="activeTab = 'general'"
                >
                  General
                  <span v-if="tabErrors.general" class="error-indicator" title="This tab has validation errors"></span>
                </button>
              </li>
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'permissions', 'has-error': tabErrors.permissions }"
                  type="button"
                  @click="activeTab = 'permissions'"
                >
                  Permissions
                  <span v-if="tabErrors.permissions" class="error-indicator" title="This tab has validation errors"></span>
                </button>
              </li>
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'advanced', 'has-error': tabErrors.advanced }"
                  type="button"
                  @click="activeTab = 'advanced'"
                >
                  Advanced
                  <span v-if="tabErrors.advanced" class="error-indicator" title="This tab has validation errors"></span>
                </button>
              </li>
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'sandbox' }"
                  type="button"
                  @click="activeTab = 'sandbox'"
                >
                  Sandbox
                </button>
              </li>
            </ul>

            <!-- Tab Content -->
            <div class="tab-content">
              <GeneralTab
                v-show="activeTab === 'general'"
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
              />
              <PermissionsTab
                v-show="activeTab === 'permissions'"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                :session="editSession"
                :field-states="fieldStates"
                @update:form-data="updateFormData"
                @preview-permissions="showPermissionPreview"
              />
              <AdvancedTab
                v-show="activeTab === 'advanced'"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                :session="editSession"
                :field-states="fieldStates"
                @update:form-data="updateFormData"
              />
              <SandboxTab
                v-show="activeTab === 'sandbox'"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                :session="editSession"
                :field-states="fieldStates"
                @update:form-data="updateFormData"
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
import GeneralTab from './GeneralTab.vue'
import PermissionsTab from './PermissionsTab.vue'
import AdvancedTab from './AdvancedTab.vue'
import SandboxTab from './SandboxTab.vue'
import PermissionPreviewModal from './PermissionPreviewModal.vue'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// Modal state
const modalElement = ref(null)
let modalInstance = null
const permissionPreviewModal = ref(null)  // Issue #36

// Mode: 'create-session', 'edit-session', 'create-template', 'edit-template'
const mode = ref('create-session')
const activeTab = ref('general')

// Context
const projectId = ref(null)
const editSession = ref(null)
const editTemplate = ref(null)

// Templates for dropdown
const templates = ref([])
const selectedTemplateId = ref(null)

// Context for returning to session creation after template management
const returnContext = ref(null)

// Track original template values for modification detection
const templateOriginalValues = ref({
  default_role: null,
  initialization_context: null,
  permission_mode: null,
  allowed_tools: null,
  disallowed_tools: null,
  model: null,
  capabilities: null,
  sandbox_excludedCommands: null,
  sandbox_network_allowedDomains: null,
  sandbox_network_allowUnixSockets: null,
  sandbox_ignoreViolations_file: null,
  sandbox_ignoreViolations_network: null
})

// Track field states: 'normal', 'autofilled', or 'modified'
const fieldStates = reactive({
  default_role: 'normal',
  initialization_context: 'normal',
  permission_mode: 'normal',
  allowed_tools: 'normal',
  disallowed_tools: 'normal',
  model: 'normal',
  capabilities: 'normal',
  sandbox_excludedCommands: 'normal',
  sandbox_network_allowedDomains: 'normal',
  sandbox_network_allowUnixSockets: 'normal',
  sandbox_ignoreViolations_file: 'normal',
  sandbox_ignoreViolations_network: 'normal'
})

// Form data (shared across tabs)
const formData = reactive({
  // General tab
  name: '',
  model: 'sonnet',
  permission_mode: 'default',
  working_directory: '',
  description: '',  // template only
  default_role: '',  // template only
  startImmediately: true,  // create-session only

  // Permissions tab
  allowed_tools: '',
  disallowed_tools: '',  // Issue #461: denied tools
  capabilities: '',  // template only
  setting_sources: ['user', 'project', 'local'],  // Issue #36: settings sources to load

  // Advanced tab
  system_prompt: '',
  override_system_prompt: false,
  initialization_context: '',  // template only
  sandbox_enabled: false,  // session only

  // Sandbox tab (issue #458)
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

// Validation errors
const errors = reactive({
  name: ''
})

// Track errors per tab
const tabErrors = computed(() => ({
  general: !!errors.name,
  permissions: false,
  advanced: false
}))

const isSubmitting = ref(false)
const errorMessage = ref('')

// Computed properties
const isSessionMode = computed(() => mode.value === 'create-session' || mode.value === 'edit-session')
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
    default: return 'Configuration'
  }
})

const submitButtonText = computed(() => {
  if (isSubmitting.value) {
    return isEditMode.value ? 'Saving...' : 'Creating...'
  }
  return isEditMode.value ? 'Save Changes' : (isSessionMode.value ? 'Create Session' : 'Save Template')
})

const isFormValid = computed(() => {
  // Name is required for all modes
  if (!formData.name.trim()) return false

  // Session name validation - no spaces for sessions (for nametag matching)
  if (isSessionMode.value && formData.name.includes(' ')) return false

  return true
})

// Warning banner computed properties (Issue #459)
const isSessionActive = computed(() => {
  return editSession.value?.state === 'active' || editSession.value?.state === 'starting'
})

const hasResetChanges = computed(() => {
  if (!editSession.value) return false

  const initContextChanged = (formData.initialization_context || '') !== (editSession.value.system_prompt || '')
  const overrideChanged = (formData.override_system_prompt || false) !== (editSession.value.override_system_prompt || false)
  const sandboxEnabledChanged = (formData.sandbox_enabled || false) !== (editSession.value.sandbox_enabled || false)

  if (initContextChanged || overrideChanged || sandboxEnabledChanged) return true

  // Compare sandbox config fields if sandbox is enabled
  if (formData.sandbox_enabled) {
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

const hasRestartChanges = computed(() => {
  if (!editSession.value) return false

  const modelChanged = (formData.model || 'sonnet') !== (editSession.value.model || 'sonnet')
  const allowedToolsChanged = (formData.allowed_tools || '') !== (editSession.value.allowed_tools?.join(', ') || '')

  const originalSources = editSession.value.setting_sources || ['user', 'project', 'local']
  const currentSources = formData.setting_sources || ['user', 'project', 'local']
  const settingSourcesChanged = JSON.stringify([...originalSources].sort()) !== JSON.stringify([...currentSources].sort())

  return modelChanged || allowedToolsChanged || settingSourcesChanged
})

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
    formData.default_role = ''
    formData.initialization_context = ''
    formData.permission_mode = 'default'
    formData.allowed_tools = ''
    formData.disallowed_tools = ''
    formData.model = 'sonnet'
    formData.capabilities = ''
    formData.override_system_prompt = false
    formData.sandbox_enabled = false

    // Reset sandbox config
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

    // Reset all field states to normal
    fieldStates.default_role = 'normal'
    fieldStates.initialization_context = 'normal'
    fieldStates.permission_mode = 'normal'
    fieldStates.allowed_tools = 'normal'
    fieldStates.disallowed_tools = 'normal'
    fieldStates.model = 'normal'
    fieldStates.capabilities = 'normal'
    fieldStates.sandbox_excludedCommands = 'normal'
    fieldStates.sandbox_network_allowedDomains = 'normal'
    fieldStates.sandbox_network_allowUnixSockets = 'normal'
    fieldStates.sandbox_ignoreViolations_file = 'normal'
    fieldStates.sandbox_ignoreViolations_network = 'normal'

    // Clear template values
    templateOriginalValues.value = {
      default_role: null,
      initialization_context: null,
      permission_mode: null,
      allowed_tools: null,
      disallowed_tools: null,
      model: null,
      capabilities: null,
      sandbox_excludedCommands: null,
      sandbox_network_allowedDomains: null,
      sandbox_network_allowUnixSockets: null,
      sandbox_ignoreViolations_file: null,
      sandbox_ignoreViolations_network: null
    }
    return
  }

  const template = templates.value.find(t => t.template_id === selectedTemplateId.value)
  if (!template) return

  // Reset field states before applying new template
  fieldStates.default_role = 'normal'
  fieldStates.initialization_context = 'normal'
  fieldStates.permission_mode = 'normal'
  fieldStates.allowed_tools = 'normal'
  fieldStates.disallowed_tools = 'normal'
  fieldStates.model = 'normal'
  fieldStates.capabilities = 'normal'
  fieldStates.sandbox_excludedCommands = 'normal'
  fieldStates.sandbox_network_allowedDomains = 'normal'
  fieldStates.sandbox_network_allowUnixSockets = 'normal'
  fieldStates.sandbox_ignoreViolations_file = 'normal'
  fieldStates.sandbox_ignoreViolations_network = 'normal'

  // Apply and track role
  if (template.default_role) {
    formData.default_role = template.default_role
    templateOriginalValues.value.default_role = template.default_role
    fieldStates.default_role = 'autofilled'
  } else {
    formData.default_role = ''
    templateOriginalValues.value.default_role = null
  }

  // Apply and track initialization context
  if (template.default_system_prompt) {
    formData.initialization_context = template.default_system_prompt
    templateOriginalValues.value.initialization_context = template.default_system_prompt
    fieldStates.initialization_context = 'autofilled'
  } else {
    formData.initialization_context = ''
    templateOriginalValues.value.initialization_context = null
  }

  // Apply and track permission mode
  if (template.permission_mode) {
    formData.permission_mode = template.permission_mode
    templateOriginalValues.value.permission_mode = template.permission_mode
    fieldStates.permission_mode = 'autofilled'
  } else {
    formData.permission_mode = 'default'
    templateOriginalValues.value.permission_mode = null
  }

  // Apply and track allowed tools
  if (template.allowed_tools && template.allowed_tools.length > 0) {
    const toolsStr = template.allowed_tools.join(', ')
    formData.allowed_tools = toolsStr
    templateOriginalValues.value.allowed_tools = toolsStr
    fieldStates.allowed_tools = 'autofilled'
  } else {
    formData.allowed_tools = ''
    templateOriginalValues.value.allowed_tools = null
  }

  // Apply and track disallowed tools
  if (template.disallowed_tools && template.disallowed_tools.length > 0) {
    const toolsStr = template.disallowed_tools.join(', ')
    formData.disallowed_tools = toolsStr
    templateOriginalValues.value.disallowed_tools = toolsStr
    fieldStates.disallowed_tools = 'autofilled'
  } else {
    formData.disallowed_tools = ''
    templateOriginalValues.value.disallowed_tools = null
  }

  // Apply and track model
  if (template.model) {
    formData.model = template.model
    templateOriginalValues.value.model = template.model
    fieldStates.model = 'autofilled'
  } else {
    formData.model = 'sonnet'
    templateOriginalValues.value.model = null
  }

  // Apply and track capabilities (array → comma-separated string)
  if (template.capabilities && template.capabilities.length > 0) {
    const capsStr = template.capabilities.join(', ')
    formData.capabilities = capsStr
    templateOriginalValues.value.capabilities = capsStr
    fieldStates.capabilities = 'autofilled'
  } else {
    formData.capabilities = ''
    templateOriginalValues.value.capabilities = null
  }

  // Apply override_system_prompt (boolean, no field-state tracking)
  formData.override_system_prompt = template.override_system_prompt || false

  // Apply sandbox_enabled (boolean, no field-state tracking)
  formData.sandbox_enabled = template.sandbox_enabled || false

  // Apply sandbox config fields
  const sc = template.sandbox_config || {}
  formData.sandbox.autoAllowBashIfSandboxed = sc.autoAllowBashIfSandboxed ?? true
  formData.sandbox.allowUnsandboxedCommands = sc.allowUnsandboxedCommands ?? false
  formData.sandbox.enableWeakerNestedSandbox = sc.enableWeakerNestedSandbox ?? false

  const net = sc.network || {}
  formData.sandbox.network.allowLocalBinding = net.allowLocalBinding ?? false
  formData.sandbox.network.allowAllUnixSockets = net.allowAllUnixSockets ?? false

  // Sandbox string fields with field-state tracking (array → comma-separated)
  const excludedStr = (sc.excludedCommands || []).join(', ')
  formData.sandbox.excludedCommands = excludedStr
  templateOriginalValues.value.sandbox_excludedCommands = excludedStr || null
  if (excludedStr) fieldStates.sandbox_excludedCommands = 'autofilled'

  const domainsStr = (net.allowedDomains || []).join(', ')
  formData.sandbox.network.allowedDomains = domainsStr
  templateOriginalValues.value.sandbox_network_allowedDomains = domainsStr || null
  if (domainsStr) fieldStates.sandbox_network_allowedDomains = 'autofilled'

  const unixSocketsStr = (net.allowUnixSockets || []).join(', ')
  formData.sandbox.network.allowUnixSockets = unixSocketsStr
  templateOriginalValues.value.sandbox_network_allowUnixSockets = unixSocketsStr || null
  if (unixSocketsStr) fieldStates.sandbox_network_allowUnixSockets = 'autofilled'

  const iv = sc.ignoreViolations || {}
  const ivFileStr = (iv.file || []).join(', ')
  formData.sandbox.ignoreViolations.file = ivFileStr
  templateOriginalValues.value.sandbox_ignoreViolations_file = ivFileStr || null
  if (ivFileStr) fieldStates.sandbox_ignoreViolations_file = 'autofilled'

  const ivNetStr = (iv.network || []).join(', ')
  formData.sandbox.ignoreViolations.network = ivNetStr
  templateOriginalValues.value.sandbox_ignoreViolations_network = ivNetStr || null
  if (ivNetStr) fieldStates.sandbox_ignoreViolations_network = 'autofilled'
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
  activeTab.value = 'general'
}

function switchToEditTemplate(template) {
  mode.value = 'edit-template'
  editTemplate.value = template
  resetForm()
  populateFormFromTemplate(template)
  activeTab.value = 'general'
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

function validate() {
  let isValid = true
  errors.name = ''

  if (!formData.name.trim()) {
    errors.name = 'Name is required'
    isValid = false
  }

  // Session name must be single word for nametag matching
  if (isSessionMode.value && formData.name.includes(' ')) {
    errors.name = 'Session name must be a single word (no spaces)'
    isValid = false
  }

  return isValid
}

async function handleSubmit() {
  if (!validate()) {
    // Switch to tab with error
    if (errors.name) activeTab.value = 'general'
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
      case 'create-template':
        await createTemplate()
        // createTemplate returns to template list, don't close
        break
      case 'edit-template':
        await updateTemplate()
        // updateTemplate returns to template list, don't close
        break
    }
  } catch (error) {
    console.error('Submit failed:', error)
    errorMessage.value = error.response?.data?.detail || error.message || 'Operation failed. Please try again.'
  } finally {
    isSubmitting.value = false
  }
}

async function createSession() {
  if (!projectId.value) {
    throw new Error('Project ID is missing')
  }

  // Parse allowed_tools from comma-separated string
  const toolsList = formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse disallowed_tools
  const deniedList = formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse capabilities
  const capsList = formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  const payload = {
    name: formData.name.trim(),
    model: formData.model || null,
    role: formData.default_role.trim() || null,
    initialization_context: formData.initialization_context.trim() || null,
    override_system_prompt: formData.override_system_prompt,
    capabilities: capsList.length > 0 ? capsList : null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    disallowed_tools: deniedList.length > 0 ? deniedList : null,
    working_directory: formData.working_directory.trim() || null,
    sandbox_enabled: formData.sandbox_enabled,
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
    setting_sources: formData.setting_sources  // Issue #36
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

async function updateSession() {
  if (!editSession.value) {
    throw new Error('Session data is missing')
  }

  const sessionId = editSession.value.session_id
  const isActive = editSession.value.state === 'active' || editSession.value.state === 'starting'

  // Parse allowed tools from input
  const toolsList = formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse disallowed tools from input
  const deniedList = formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse capabilities from input
  const capsList = formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  // Build updates object with all changed fields
  // Note: UI uses initialization_context, backend stores as system_prompt
  const updates = {
    name: formData.name.trim(),
    model: formData.model,
    role: formData.default_role.trim() || null,
    system_prompt: formData.initialization_context.trim() || null,
    override_system_prompt: formData.override_system_prompt,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    disallowed_tools: deniedList.length > 0 ? deniedList : null,
    capabilities: capsList.length > 0 ? capsList : null,
    sandbox_enabled: formData.sandbox_enabled,
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null,
    setting_sources: formData.setting_sources  // Issue #36
  }

  // Update session via PATCH (takes effect on next restart if session is active)
  await sessionStore.patchSession(sessionId, updates)

  // Update permission mode if changed and session is active (this goes through SDK)
  if (isActive && formData.permission_mode !== editSession.value.current_permission_mode) {
    await sessionStore.setPermissionMode(sessionId, formData.permission_mode)
  }
}

async function createTemplate() {
  // Parse allowed tools from input
  const toolsList = formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse disallowed tools
  const deniedList = formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse capabilities
  const capsList = formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  const payload = {
    name: formData.name.trim(),
    description: formData.description.trim() || null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    disallowed_tools: deniedList.length > 0 ? deniedList : null,
    default_role: formData.default_role.trim() || null,
    default_system_prompt: formData.initialization_context.trim() || null,
    model: formData.model || null,
    capabilities: capsList.length > 0 ? capsList : null,
    override_system_prompt: formData.override_system_prompt,
    sandbox_enabled: formData.sandbox_enabled,
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null
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

  // Parse allowed tools from input
  const toolsList = formData.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse disallowed tools
  const deniedList = formData.disallowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)

  // Parse capabilities
  const capsList = formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  const payload = {
    name: formData.name.trim(),
    description: formData.description.trim() || null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    disallowed_tools: deniedList.length > 0 ? deniedList : null,
    default_role: formData.default_role.trim() || null,
    default_system_prompt: formData.initialization_context.trim() || null,
    model: formData.model || null,
    capabilities: capsList.length > 0 ? capsList : null,
    override_system_prompt: formData.override_system_prompt,
    sandbox_enabled: formData.sandbox_enabled,
    sandbox_config: formData.sandbox_enabled ? buildSandboxConfig() : null
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
  formData.name = ''
  formData.model = 'sonnet'
  formData.permission_mode = 'default'
  formData.working_directory = ''
  formData.description = ''
  formData.default_role = ''
  formData.startImmediately = true
  formData.allowed_tools = ''
  formData.disallowed_tools = ''
  formData.capabilities = ''
  formData.setting_sources = ['user', 'project', 'local']  // Issue #36
  formData.system_prompt = ''
  formData.override_system_prompt = false
  formData.initialization_context = ''
  formData.sandbox_enabled = false

  // Reset sandbox config (issue #458)
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

  errors.name = ''

  // Reset field states
  fieldStates.default_role = 'normal'
  fieldStates.initialization_context = 'normal'
  fieldStates.permission_mode = 'normal'
  fieldStates.allowed_tools = 'normal'
  fieldStates.disallowed_tools = 'normal'
  fieldStates.model = 'normal'
  fieldStates.capabilities = 'normal'
  fieldStates.sandbox_excludedCommands = 'normal'
  fieldStates.sandbox_network_allowedDomains = 'normal'
  fieldStates.sandbox_network_allowUnixSockets = 'normal'
  fieldStates.sandbox_ignoreViolations_file = 'normal'
  fieldStates.sandbox_ignoreViolations_network = 'normal'

  // Clear template original values
  templateOriginalValues.value = {
    default_role: null,
    initialization_context: null,
    permission_mode: null,
    allowed_tools: null,
    disallowed_tools: null,
    model: null,
    capabilities: null,
    sandbox_excludedCommands: null,
    sandbox_network_allowedDomains: null,
    sandbox_network_allowUnixSockets: null,
    sandbox_ignoreViolations_file: null,
    sandbox_ignoreViolations_network: null
  }

  selectedTemplateId.value = null
  errorMessage.value = ''
  activeTab.value = 'general'
}

function populateFormFromSession(session) {
  formData.name = session.name || ''
  formData.model = session.model || 'sonnet'
  formData.permission_mode = session.current_permission_mode || 'default'
  formData.working_directory = session.working_directory || ''
  formData.default_role = session.role || ''
  // Backend stores the prompt as system_prompt, UI shows it as initialization_context
  formData.initialization_context = session.system_prompt || ''
  formData.override_system_prompt = session.override_system_prompt || false
  formData.system_prompt = ''  // Not used for sessions (only templates have separate additional instructions)
  formData.allowed_tools = session.allowed_tools?.join(', ') || ''
  formData.disallowed_tools = session.disallowed_tools?.join(', ') || ''
  formData.capabilities = session.capabilities?.join(', ') || ''
  formData.sandbox_enabled = session.sandbox_enabled || false
  // Issue #36: Load setting_sources, default to all enabled if not set
  formData.setting_sources = session.setting_sources || ['user', 'project', 'local']

  // Issue #458: Load sandbox config
  const sc = session.sandbox_config || {}
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

function populateFormFromTemplate(template) {
  formData.name = template.name || ''
  formData.description = template.description || ''
  formData.permission_mode = template.permission_mode || 'default'
  formData.default_role = template.default_role || ''
  formData.initialization_context = template.default_system_prompt || ''
  formData.allowed_tools = template.allowed_tools?.join(', ') || ''
  formData.disallowed_tools = template.disallowed_tools?.join(', ') || ''
  formData.model = template.model || 'sonnet'
  formData.capabilities = template.capabilities?.join(', ') || ''
  formData.override_system_prompt = template.override_system_prompt || false
  formData.sandbox_enabled = template.sandbox_enabled || false

  // Issue #458: Load sandbox config from template
  const sc = template.sandbox_config || {}
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

      // Load templates for session creation
      if (mode.value === 'create-session') {
        await loadTemplates()
      }

      // Populate form based on mode
      if (mode.value === 'edit-session' && editSession.value) {
        populateFormFromSession(editSession.value)
      } else if (mode.value === 'edit-template' && editTemplate.value) {
        populateFormFromTemplate(editTemplate.value)
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

// Watch for modifications to auto-filled fields
watch(() => formData.default_role, (newVal) => {
  if (templateOriginalValues.value.default_role !== null) {
    fieldStates.default_role = newVal === templateOriginalValues.value.default_role ? 'autofilled' : 'modified'
  }
})

watch(() => formData.initialization_context, (newVal) => {
  if (templateOriginalValues.value.initialization_context !== null) {
    fieldStates.initialization_context = newVal === templateOriginalValues.value.initialization_context ? 'autofilled' : 'modified'
  }
})

watch(() => formData.permission_mode, (newVal) => {
  if (templateOriginalValues.value.permission_mode !== null) {
    fieldStates.permission_mode = newVal === templateOriginalValues.value.permission_mode ? 'autofilled' : 'modified'
  }
})

watch(() => formData.allowed_tools, (newVal) => {
  if (templateOriginalValues.value.allowed_tools !== null) {
    fieldStates.allowed_tools = newVal === templateOriginalValues.value.allowed_tools ? 'autofilled' : 'modified'
  }
})

watch(() => formData.disallowed_tools, (newVal) => {
  if (templateOriginalValues.value.disallowed_tools !== null) {
    fieldStates.disallowed_tools = newVal === templateOriginalValues.value.disallowed_tools ? 'autofilled' : 'modified'
  }
})

watch(() => formData.model, (newVal) => {
  if (templateOriginalValues.value.model !== null) {
    fieldStates.model = newVal === templateOriginalValues.value.model ? 'autofilled' : 'modified'
  }
})

watch(() => formData.capabilities, (newVal) => {
  if (templateOriginalValues.value.capabilities !== null) {
    fieldStates.capabilities = newVal === templateOriginalValues.value.capabilities ? 'autofilled' : 'modified'
  }
})

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
.nav-tabs-responsive {
  flex-wrap: nowrap;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.nav-tabs-responsive .nav-item {
  flex-shrink: 0;
}

.nav-link {
  position: relative;
  white-space: nowrap;
}

.nav-link.has-error {
  color: var(--bs-danger);
}

.error-indicator {
  display: inline-block;
  width: 8px;
  height: 8px;
  background-color: var(--bs-danger);
  border-radius: 50%;
  margin-left: 6px;
  vertical-align: middle;
}

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

/* Mobile responsive tabs */
@media (max-width: 576px) {
  .nav-tabs-responsive {
    border-bottom: none;
  }

  .nav-tabs-responsive .nav-link {
    border: 1px solid var(--bs-border-color);
    border-radius: 0.375rem;
    margin-right: 0.5rem;
    padding: 0.375rem 0.75rem;
    font-size: 0.875rem;
  }

  .nav-tabs-responsive .nav-link.active {
    background-color: var(--bs-primary);
    color: white;
    border-color: var(--bs-primary);
  }
}
</style>
