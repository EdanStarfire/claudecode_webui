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
                @update:form-data="updateFormData"
              />
              <AdvancedTab
                v-show="activeTab === 'advanced'"
                :mode="mode"
                :form-data="formData"
                :errors="errors"
                @update:form-data="updateFormData"
              />
            </div>
          </div>

          <div v-if="errorMessage" class="alert alert-danger mt-3">
            {{ errorMessage }}
          </div>
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

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// Modal state
const modalElement = ref(null)
let modalInstance = null

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
  capabilities: '',  // template only

  // Advanced tab
  system_prompt: '',
  override_system_prompt: false,
  initialization_context: '',  // template only
  sandbox_enabled: false  // session only
})

// Validation errors
const errors = reactive({
  name: '',
  system_prompt: '',
  initialization_context: ''
})

// Track errors per tab
const tabErrors = computed(() => ({
  general: !!errors.name,
  permissions: false,
  advanced: !!errors.system_prompt || !!errors.initialization_context
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

  // Check character limits
  if (formData.system_prompt.length > 6000) return false
  if (formData.initialization_context.length > 2000) return false

  // Session name validation - no spaces for sessions (for nametag matching)
  if (isSessionMode.value && formData.name.includes(' ')) return false

  return true
})

// Methods
function updateFormData(field, value) {
  formData[field] = value

  // Clear related errors
  if (field === 'name') errors.name = ''
  if (field === 'system_prompt') errors.system_prompt = ''
  if (field === 'initialization_context') errors.initialization_context = ''
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
    return
  }

  const template = templates.value.find(t => t.template_id === selectedTemplateId.value)
  if (!template) return

  // Apply template values
  if (template.default_role) formData.default_role = template.default_role
  if (template.default_system_prompt) formData.initialization_context = template.default_system_prompt
  if (template.permission_mode) formData.permission_mode = template.permission_mode
  if (template.allowed_tools && template.allowed_tools.length > 0) {
    formData.allowed_tools = template.allowed_tools.join(', ')
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
  errors.system_prompt = ''
  errors.initialization_context = ''

  if (!formData.name.trim()) {
    errors.name = 'Name is required'
    isValid = false
  }

  // Session name must be single word for nametag matching
  if (isSessionMode.value && formData.name.includes(' ')) {
    errors.name = 'Session name must be a single word (no spaces)'
    isValid = false
  }

  if (formData.system_prompt.length > 6000) {
    errors.system_prompt = 'System prompt exceeds 6000 character limit'
    isValid = false
  }

  if (formData.initialization_context.length > 2000) {
    errors.initialization_context = 'Initialization context exceeds 2000 character limit'
    isValid = false
  }

  return isValid
}

async function handleSubmit() {
  if (!validate()) {
    // Switch to tab with error
    if (errors.name) activeTab.value = 'general'
    else if (errors.system_prompt || errors.initialization_context) activeTab.value = 'advanced'
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

  // Parse capabilities
  const capsList = formData.capabilities
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)

  const payload = {
    name: formData.name.trim(),
    model: formData.model,
    role: formData.default_role.trim() || null,
    initialization_context: formData.initialization_context.trim() || null,
    override_system_prompt: formData.override_system_prompt,
    capabilities: capsList.length > 0 ? capsList : null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    working_directory: formData.working_directory.trim() || null,
    sandbox_enabled: formData.sandbox_enabled
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

  // Update session name
  await sessionStore.updateSessionName(sessionId, formData.name.trim())

  // Update permission mode if changed and session is active
  const isActive = editSession.value.state === 'active' || editSession.value.state === 'starting'
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

  const payload = {
    name: formData.name.trim(),
    description: formData.description.trim() || null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    default_role: formData.default_role.trim() || null,
    default_system_prompt: formData.initialization_context.trim() || null
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

  const payload = {
    name: formData.name.trim(),
    description: formData.description.trim() || null,
    permission_mode: formData.permission_mode,
    allowed_tools: toolsList.length > 0 ? toolsList : null,
    default_role: formData.default_role.trim() || null,
    default_system_prompt: formData.initialization_context.trim() || null
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
  formData.capabilities = ''
  formData.system_prompt = ''
  formData.override_system_prompt = false
  formData.initialization_context = ''
  formData.sandbox_enabled = false

  errors.name = ''
  errors.system_prompt = ''
  errors.initialization_context = ''

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
  formData.system_prompt = session.system_prompt || ''
  formData.override_system_prompt = session.override_system_prompt || false
  formData.initialization_context = session.initialization_context || ''
  formData.allowed_tools = session.allowed_tools?.join(', ') || ''
  formData.capabilities = session.capabilities?.join(', ') || ''
  formData.sandbox_enabled = session.sandbox_enabled || false
}

function populateFormFromTemplate(template) {
  formData.name = template.name || ''
  formData.description = template.description || ''
  formData.permission_mode = template.permission_mode || 'default'
  formData.default_role = template.default_role || ''
  formData.initialization_context = template.default_system_prompt || ''
  formData.allowed_tools = template.allowed_tools?.join(', ') || ''
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
