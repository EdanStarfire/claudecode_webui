<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="createMinionModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="createMinionModalLabel" class="modal-title">Create Minion</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="createMinion">
            <!-- Template Selection -->
            <div class="mb-3">
              <label for="template-select" class="form-label">
                Template
                <button type="button" @click="openTemplateManager" class="btn btn-link btn-sm p-0 ms-2">
                  ⚙️ Manage Templates
                </button>
              </label>
              <select
                id="template-select"
                v-model="selectedTemplateId"
                @change="applyTemplate"
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

            <div class="mb-3">
              <label for="minion-name" class="form-label">
                Name <span class="text-danger">*</span>
              </label>
              <input
                id="minion-name"
                v-model="formData.name"
                type="text"
                class="form-control"
                required
                placeholder="main"
                pattern="[^\s]+"
                title="Minion name must be a single word with no spaces"
              />
              <div class="form-text">Must be a single word (no spaces) for #nametag matching</div>
            </div>

            <div class="mb-3">
              <label for="minion-role" class="form-label">
                Role
                <span v-if="fieldStates.role === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
                  ⚡
                </span>
                <span v-if="fieldStates.role === 'modified'" class="field-indicator modified" title="Modified from template">
                  ✏️
                </span>
              </label>
              <input
                id="minion-role"
                v-model="formData.role"
                type="text"
                class="form-control"
                :class="roleFieldClass"
                :placeholder="rolePlaceholder"
                :title="getFieldTooltip('role')"
              />
              <div class="form-text">
                Optional role description for the minion
              </div>
            </div>

            <div class="mb-3">
              <label for="minion-context" class="form-label">
                Initialization Context
                <span v-if="fieldStates.initialization_context === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
                  ⚡
                </span>
                <span v-if="fieldStates.initialization_context === 'modified'" class="field-indicator modified" title="Modified from template">
                  ✏️
                </span>
              </label>
              <textarea
                id="minion-context"
                v-model="formData.initialization_context"
                class="form-control"
                :class="[initContextFieldClass, { 'is-invalid': initContextExceedsLimit }]"
                rows="4"
                :maxlength="2000"
                placeholder="Instructions and context for the minion..."
                :title="getFieldTooltip('initialization_context')"
              ></textarea>
              <div class="form-text d-flex justify-content-between">
                <span>
                  {{ formData.override_system_prompt ? 'This context will replace Claude Code\'s preset and legion guide' : 'This context will be appended to legion guide and Claude Code\'s preset' }}
                </span>
                <span :class="{ 'text-danger': initContextExceedsLimit, 'text-warning': initContextNearLimit }">
                  {{ initContextCharCount }} / 2000 chars
                </span>
              </div>
              <div class="invalid-feedback" v-if="initContextExceedsLimit">
                Initialization context exceeds 2000 character limit (Windows command-line constraint)
              </div>
            </div>

            <!-- Override System Prompt -->
            <div class="mb-3 form-check">
              <input
                type="checkbox"
                class="form-check-input"
                id="overrideSystemPrompt"
                v-model="formData.override_system_prompt"
              />
              <label class="form-check-label" for="overrideSystemPrompt">
                Override Claude Code preset (use custom context only)
              </label>
              <div class="form-text text-warning" v-if="formData.override_system_prompt">
                <small>⚠️ Override mode may cause unexpected behaviors. Only custom context will be used (no legion guide or Claude Code preset).</small>
              </div>
            </div>

            <div class="mb-3">
              <label for="minion-capabilities" class="form-label">Capabilities</label>
              <input
                id="minion-capabilities"
                v-model="capabilitiesInput"
                type="text"
                class="form-control"
                placeholder="e.g., python, testing, debugging"
              />
              <div class="form-text">Comma-separated list of capability keywords (optional)</div>
            </div>

            <!-- Working Directory -->
            <div class="mb-3">
              <label for="working-directory" class="form-label">
                Working Directory (Optional)
              </label>
              <div class="input-group">
                <input
                  id="working-directory"
                  v-model="formData.working_directory"
                  type="text"
                  class="form-control"
                  placeholder="Leave empty to inherit from legion"
                />
                <button
                  type="button"
                  class="btn btn-outline-secondary"
                  @click="openFolderBrowser"
                >
                  📁 Browse
                </button>
              </div>
              <div class="form-text">
                Custom working directory for git worktrees or multi-repo workflows. Relative paths will be converted to absolute.
              </div>
            </div>

            <!-- Permission Mode -->
            <div class="mb-3">
              <label for="permission-mode" class="form-label">
                Permission Mode
                <span v-if="fieldStates.permission_mode === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
                  ⚡
                </span>
                <span v-if="fieldStates.permission_mode === 'modified'" class="field-indicator modified" title="Modified from template">
                  ✏️
                </span>
              </label>
              <select
                class="form-select"
                id="permission-mode"
                v-model="formData.permission_mode"
                :class="permissionModeFieldClass"
                :title="getFieldTooltip('permission_mode')"
              >
                <option value="default">Default (Prompt for tools not in settings)</option>
                <option value="acceptEdits">Accept Edits (Auto-approve Edit/Write)</option>
                <option value="plan">Plan Mode (Auto-resets after ExitPlanMode)</option>
                <option value="bypassPermissions">⚠️ Bypass Permissions (No prompts - use with caution)</option>
              </select>
              <div class="form-text">
                Controls which tool actions require permission prompts
              </div>
            </div>

            <!-- Allowed Tools -->
            <div class="mb-3">
              <label for="allowed-tools" class="form-label">
                Allowed Tools (Optional)
                <span v-if="fieldStates.allowed_tools === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
                  ⚡
                </span>
                <span v-if="fieldStates.allowed_tools === 'modified'" class="field-indicator modified" title="Modified from template">
                  ✏️
                </span>
              </label>
              <input
                type="text"
                class="form-control"
                id="allowed-tools"
                v-model="formData.allowed_tools"
                :class="allowedToolsFieldClass"
                placeholder="bash, read, edit, write, glob, grep, webfetch, websearch, task, todo"
                :title="getFieldTooltip('allowed_tools')"
              />
              <div class="form-text">
                Comma-separated list of allowed tools. Leave empty to allow all tools.
              </div>
            </div>

            <div v-if="errorMessage" class="alert alert-danger">
              {{ errorMessage }}
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!formData.name.trim() || isCreating"
            @click="createMinion"
          >
            <span v-if="isCreating" class="spinner-border spinner-border-sm me-2"></span>
            {{ isCreating ? 'Creating...' : 'Create Minion' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// State
const modalElement = ref(null)
let modalInstance = null

const legionId = ref(null)
const templates = ref([])
const selectedTemplateId = ref(null)
const templateAppliedFields = ref(new Set())

// Track original template values for modification detection
const templateOriginalValues = ref({
  role: null,
  initialization_context: null,
  permission_mode: null,
  allowed_tools: null
})

// Track field states: 'normal', 'autofilled', or 'modified'
const fieldStates = ref({
  role: 'normal',
  initialization_context: 'normal',
  permission_mode: 'normal',
  allowed_tools: 'normal'
})

const formData = ref({
  name: '',
  role: '',
  initialization_context: '',
  override_system_prompt: false,
  capabilities: [],
  permission_mode: 'default',
  allowed_tools: '',
  working_directory: ''
})
const capabilitiesInput = ref('')
const errorMessage = ref('')
const isCreating = ref(false)

// Computed
const capabilities = computed(() => {
  if (!capabilitiesInput.value.trim()) return []
  return capabilitiesInput.value
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)
})

const allowedTools = computed(() => {
  if (!formData.value.allowed_tools.trim()) return null
  return formData.value.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

// Computed - Initialization context character limits
const initContextCharCount = computed(() => {
  return formData.value.initialization_context.length
})

const initContextExceedsLimit = computed(() => {
  return initContextCharCount.value > 2000
})

const initContextNearLimit = computed(() => {
  return initContextCharCount.value > 1800 && initContextCharCount.value <= 2000
})

const selectedTemplate = computed(() => {
  if (!selectedTemplateId.value) return null
  return templates.value.find(t => t.template_id === selectedTemplateId.value)
})

const rolePlaceholder = computed(() => {
  return selectedTemplate.value?.default_role || 'e.g., Code Expert, Testing Specialist'
})

const isFieldFromTemplate = (fieldName) => {
  return templateAppliedFields.value.has(fieldName)
}

// Computed classes for field highlighting
const roleFieldClass = computed(() => ({
  'field-autofilled': fieldStates.value.role === 'autofilled',
  'field-modified': fieldStates.value.role === 'modified'
}))

const initContextFieldClass = computed(() => ({
  'field-autofilled': fieldStates.value.initialization_context === 'autofilled',
  'field-modified': fieldStates.value.initialization_context === 'modified'
}))

const permissionModeFieldClass = computed(() => ({
  'field-autofilled': fieldStates.value.permission_mode === 'autofilled',
  'field-modified': fieldStates.value.permission_mode === 'modified'
}))

const allowedToolsFieldClass = computed(() => ({
  'field-autofilled': fieldStates.value.allowed_tools === 'autofilled',
  'field-modified': fieldStates.value.allowed_tools === 'modified'
}))

// Get tooltip text for field state
const getFieldTooltip = (fieldName) => {
  const state = fieldStates.value[fieldName]
  if (state === 'autofilled') {
    return `Auto-filled from template: ${selectedTemplate.value?.name}`
  }
  if (state === 'modified') {
    return 'Modified from template value'
  }
  return ''
}

// Methods
async function loadTemplates() {
  try {
    console.log('[CreateMinionModal] Loading templates...')
    const data = await api.get('/api/templates')
    console.log('[CreateMinionModal] Templates data:', data)
    templates.value = data || []
    console.log('[CreateMinionModal] Templates set to:', templates.value)
  } catch (error) {
    console.error('[CreateMinionModal] Failed to load templates:', error)
    templates.value = []
  }
}

function applyTemplate() {
  templateAppliedFields.value.clear()

  if (!selectedTemplate.value) {
    // Clear fields when "[None]" selected
    formData.value.role = ''
    formData.value.initialization_context = ''
    formData.value.permission_mode = 'default'
    formData.value.allowed_tools = ''

    // Reset all field states to normal
    fieldStates.value = {
      role: 'normal',
      initialization_context: 'normal',
      permission_mode: 'normal',
      allowed_tools: 'normal'
    }

    // Clear template values
    templateOriginalValues.value = {
      role: null,
      initialization_context: null,
      permission_mode: null,
      allowed_tools: null
    }

    return
  }

  // Apply template values
  const template = selectedTemplate.value

  // Reset field states before applying new template
  fieldStates.value = {
    role: 'normal',
    initialization_context: 'normal',
    permission_mode: 'normal',
    allowed_tools: 'normal'
  }

  // Apply and track role
  if (template.default_role) {
    formData.value.role = template.default_role
    templateAppliedFields.value.add('role')
    templateOriginalValues.value.role = template.default_role
    fieldStates.value.role = 'autofilled'
  } else {
    formData.value.role = ''
    templateOriginalValues.value.role = null
    fieldStates.value.role = 'normal'
  }

  // Apply and track initialization context
  if (template.default_system_prompt) {
    formData.value.initialization_context = template.default_system_prompt
    templateAppliedFields.value.add('initialization_context')
    templateOriginalValues.value.initialization_context = template.default_system_prompt
    fieldStates.value.initialization_context = 'autofilled'
  } else {
    formData.value.initialization_context = ''
    templateOriginalValues.value.initialization_context = null
    fieldStates.value.initialization_context = 'normal'
  }

  // Apply and track permission mode
  if (template.permission_mode) {
    formData.value.permission_mode = template.permission_mode
    templateAppliedFields.value.add('permission_mode')
    templateOriginalValues.value.permission_mode = template.permission_mode
    fieldStates.value.permission_mode = 'autofilled'
  } else {
    formData.value.permission_mode = 'default'
    templateOriginalValues.value.permission_mode = null
    fieldStates.value.permission_mode = 'normal'
  }

  // Apply and track allowed tools
  if (template.allowed_tools && template.allowed_tools.length > 0) {
    const toolsStr = template.allowed_tools.join(', ')
    formData.value.allowed_tools = toolsStr
    templateAppliedFields.value.add('allowed_tools')
    templateOriginalValues.value.allowed_tools = toolsStr
    fieldStates.value.allowed_tools = 'autofilled'
  } else {
    formData.value.allowed_tools = ''
    templateOriginalValues.value.allowed_tools = null
    fieldStates.value.allowed_tools = 'normal'
  }
}

function openTemplateManager() {
  uiStore.showModal('template-management', {})
}

function openFolderBrowser() {
  // Get the project's working directory as default
  const project = legionId.value ? projectStore.projects.get(legionId.value) : null
  const defaultPath = project?.working_directory || ''

  uiStore.showModal('folder-browser', {
    defaultPath: defaultPath,
    currentPath: formData.value.working_directory || '',
    onSelect: (path) => {
      formData.value.working_directory = path
    }
  })
}

function resetForm() {
  formData.value = {
    name: '',
    role: '',
    initialization_context: '',
    override_system_prompt: false,
    capabilities: [],
    permission_mode: 'default',
    allowed_tools: '',
    working_directory: ''
  }
  capabilitiesInput.value = ''
  selectedTemplateId.value = null
  templateAppliedFields.value.clear()

  // Reset field states
  fieldStates.value = {
    role: 'normal',
    initialization_context: 'normal',
    permission_mode: 'normal',
    allowed_tools: 'normal'
  }

  // Clear template original values
  templateOriginalValues.value = {
    role: null,
    initialization_context: null,
    permission_mode: null,
    allowed_tools: null
  }

  errorMessage.value = ''
  isCreating.value = false
}

function onModalHidden() {
  resetForm()
  uiStore.hideModal()
}

async function createMinion() {
  if (!formData.value.name.trim()) {
    errorMessage.value = 'Minion name is required'
    return
  }

  // Validate that name has no spaces
  if (formData.value.name.includes(' ')) {
    errorMessage.value = 'Minion name must be a single word with no spaces (for #nametag matching)'
    return
  }

  // Validate initialization context length
  if (initContextExceedsLimit.value) {
    errorMessage.value = 'Initialization context exceeds 2000 character limit'
    return
  }

  if (!legionId.value) {
    errorMessage.value = 'Legion ID is missing'
    return
  }

  isCreating.value = true
  errorMessage.value = ''

  try {
    const payload = {
      name: formData.value.name.trim(),
      role: formData.value.role.trim(),
      initialization_context: formData.value.initialization_context.trim(),
      override_system_prompt: formData.value.override_system_prompt,
      capabilities: capabilities.value,
      permission_mode: formData.value.permission_mode,
      allowed_tools: allowedTools.value,
      working_directory: formData.value.working_directory.trim() || null
    }

    const response = await api.post(`/api/legions/${legionId.value}/minions`, payload)

    if (response.success && response.minion_id) {
      // Refresh projects to get updated session list
      await projectStore.fetchProjects()
      await sessionStore.fetchSessions()

      // Close modal
      if (modalInstance) {
        modalInstance.hide()
      }

      // Navigate to the new minion session
      router.push(`/session/${response.minion_id}`)
    } else {
      errorMessage.value = 'Failed to create minion'
    }
  } catch (error) {
    console.error('Failed to create minion:', error)
    errorMessage.value = error.response?.data?.detail || 'Failed to create minion'
  } finally {
    isCreating.value = false
  }
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal, oldModal) => {
    // If create-minion modal is being shown
    if (modal?.name === 'create-minion' && modalInstance) {
      const data = modal.data || {}
      legionId.value = data.project?.project_id || null
      resetForm()
      loadTemplates()  // Reload templates when modal opens (in case they changed)
      modalInstance.show()
    }
    // If template management modal just closed and create-minion is still visible
    else if (oldModal?.name === 'template-management' && modalInstance && modalElement.value?.classList.contains('show')) {
      loadTemplates()  // Reload templates after management modal closes
    }
  }
)

// Watch for modifications to auto-filled fields
watch(() => formData.value.role, (newVal) => {
  // Only track changes if field has a template value
  if (templateOriginalValues.value.role !== null) {
    if (newVal === templateOriginalValues.value.role) {
      fieldStates.value.role = 'autofilled'
    } else {
      fieldStates.value.role = 'modified'
    }
  }
})

watch(() => formData.value.initialization_context, (newVal) => {
  // Only track changes if field has a template value
  if (templateOriginalValues.value.initialization_context !== null) {
    if (newVal === templateOriginalValues.value.initialization_context) {
      fieldStates.value.initialization_context = 'autofilled'
    } else {
      fieldStates.value.initialization_context = 'modified'
    }
  }
})

watch(() => formData.value.permission_mode, (newVal) => {
  // Only track changes if field has a template value
  if (templateOriginalValues.value.permission_mode !== null) {
    if (newVal === templateOriginalValues.value.permission_mode) {
      fieldStates.value.permission_mode = 'autofilled'
    } else {
      fieldStates.value.permission_mode = 'modified'
    }
  }
})

watch(() => formData.value.allowed_tools, (newVal) => {
  // Only track changes if field has a template value
  if (templateOriginalValues.value.allowed_tools !== null) {
    if (newVal === templateOriginalValues.value.allowed_tools) {
      fieldStates.value.allowed_tools = 'autofilled'
    } else {
      fieldStates.value.allowed_tools = 'modified'
    }
  }
})

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then((module) => {
      const Modal = module.default
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }

  // Load templates on mount
  loadTemplates()
})

onUnmounted(() => {
  if (modalElement.value) {
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

/* Field highlighting states */
.field-autofilled {
  background-color: #fffbea !important; /* Light yellow */
  transition: background-color 0.3s ease;
}

.field-modified {
  background-color: #ffe4cc !important; /* Darker orange - more distinct from yellow */
  transition: background-color 0.3s ease;
}

/* Ensure text readability */
.field-autofilled,
.field-modified {
  color: #212529; /* Keep text dark */
}

/* Ensure borders remain visible */
.form-control.field-autofilled,
.form-control.field-modified,
.form-select.field-autofilled,
.form-select.field-modified {
  border: 1px solid #dee2e6;
}

/* Focus states should override background but keep highlight */
.form-control.field-autofilled:focus,
.form-control.field-modified:focus,
.form-select.field-autofilled:focus,
.form-select.field-modified:focus {
  border-color: #0d6efd;
  box-shadow: 0 0 0 0.25rem rgba(13, 110, 253, 0.25);
}

/* Field indicators (icons) */
.field-indicator {
  margin-left: 0.5rem;
  font-size: 0.875rem;
  cursor: help;
}

.field-indicator.autofilled {
  color: #856404; /* Dark yellow */
}

.field-indicator.modified {
  color: #cc5500; /* Dark orange */
}

/* Smooth transitions when switching templates */
.form-control,
.form-select {
  transition: background-color 0.3s ease, border-color 0.3s ease;
}
</style>
