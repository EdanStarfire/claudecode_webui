<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="templateManagementModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="templateManagementModalLabel" class="modal-title">Manage Minion Templates</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Template List View -->
          <div v-if="!editingTemplate" class="template-list">
            <div class="d-flex justify-content-between align-items-center mb-3">
              <h6 class="mb-0">Templates</h6>
              <button @click="createNew" class="btn btn-primary btn-sm">
                <i class="bi-plus-lg"></i> Create New
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
                    <p class="card-text text-muted small mb-2">{{ template.description }}</p>
                    <div class="template-meta">
                      <span class="badge bg-secondary me-2">{{ template.permission_mode }}</span>
                      <span class="text-muted small">{{ template.allowed_tools?.length || 0 }} tools</span>
                    </div>
                  </div>
                  <div class="btn-group btn-group-sm ms-3">
                    <button @click="editTemplate(template)" class="btn btn-outline-primary" title="Edit">
                      <i class="bi-pencil"></i>
                    </button>
                    <button @click="deleteTemplate(template)" class="btn btn-outline-danger" title="Delete">
                      <i class="bi-trash"></i>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Edit/Create Form View -->
          <div v-if="editingTemplate" class="template-form">
            <h6 class="mb-3">{{ editingTemplate.template_id ? 'Edit Template' : 'Create Template' }}</h6>

            <div class="mb-3">
              <label for="template-name" class="form-label">
                Name <span class="text-danger">*</span>
              </label>
              <input
                id="template-name"
                v-model="editingTemplate.name"
                type="text"
                class="form-control"
                required
                placeholder="e.g., Code Expert"
              />
            </div>

            <div class="mb-3">
              <label for="template-description" class="form-label">Description</label>
              <textarea
                id="template-description"
                v-model="editingTemplate.description"
                class="form-control"
                rows="2"
                placeholder="Brief description of this template's purpose"
              ></textarea>
            </div>

            <div class="mb-3">
              <label for="template-permission-mode" class="form-label">
                Permission Mode <span class="text-danger">*</span>
              </label>
              <select
                id="template-permission-mode"
                v-model="editingTemplate.permission_mode"
                class="form-select"
                required
              >
                <option value="default">Default</option>
                <option value="acceptEdits">Accept Edits</option>
                <option value="plan">Plan Mode</option>
                <option value="bypassPermissions">Bypass Permissions</option>
              </select>
            </div>

            <div class="mb-3">
              <label for="template-tools" class="form-label">Allowed Tools</label>
              <input
                id="template-tools"
                v-model="allowedToolsInput"
                type="text"
                class="form-control"
                placeholder="bash, read, edit, write, glob, grep"
              />
              <div class="form-text">Comma-separated list of tools</div>
            </div>

            <div class="mb-3">
              <label for="template-role" class="form-label">Default Role</label>
              <input
                id="template-role"
                v-model="editingTemplate.default_role"
                type="text"
                class="form-control"
                placeholder="e.g., Code review and refactoring specialist"
              />
            </div>

            <div class="mb-3">
              <label for="template-prompt" class="form-label">Default System Prompt</label>
              <textarea
                id="template-prompt"
                v-model="editingTemplate.default_system_prompt"
                class="form-control"
                rows="3"
                placeholder="Optional initialization context"
              ></textarea>
            </div>

            <div v-if="errorMessage" class="alert alert-danger">
              {{ errorMessage }}
            </div>

            <div class="d-flex gap-2">
              <button @click="saveTemplate" class="btn btn-primary" :disabled="isSaving">
                <span v-if="isSaving" class="spinner-border spinner-border-sm me-2"></span>
                {{ isSaving ? 'Saving...' : 'Save Template' }}
              </button>
              <button @click="cancelEdit" class="btn btn-secondary">Cancel</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'

const uiStore = useUIStore()

// State
const modalElement = ref(null)
let modalInstance = null

const templates = ref([])
const editingTemplate = ref(null)
const allowedToolsInput = ref('')
const errorMessage = ref('')
const isSaving = ref(false)

// Methods
async function loadTemplates() {
  try {
    const response = await api.get('/api/templates')
    templates.value = response.data || []
  } catch (error) {
    console.error('Failed to load templates:', error)
    templates.value = []
  }
}

function createNew() {
  editingTemplate.value = {
    name: '',
    description: '',
    permission_mode: 'default',
    allowed_tools: [],
    default_role: '',
    default_system_prompt: ''
  }
  allowedToolsInput.value = ''
  errorMessage.value = ''
}

function editTemplate(template) {
  editingTemplate.value = { ...template }
  allowedToolsInput.value = template.allowed_tools?.join(', ') || ''
  errorMessage.value = ''
}

async function saveTemplate() {
  if (!editingTemplate.value.name.trim()) {
    errorMessage.value = 'Template name is required'
    return
  }

  isSaving.value = true
  errorMessage.value = ''

  try {
    // Parse allowed tools from input
    const toolsList = allowedToolsInput.value
      .split(',')
      .map(t => t.trim())
      .filter(t => t.length > 0)

    const payload = {
      name: editingTemplate.value.name.trim(),
      description: editingTemplate.value.description?.trim() || null,
      permission_mode: editingTemplate.value.permission_mode,
      allowed_tools: toolsList.length > 0 ? toolsList : null,
      default_role: editingTemplate.value.default_role?.trim() || null,
      default_system_prompt: editingTemplate.value.default_system_prompt?.trim() || null
    }

    const isNew = !editingTemplate.value.template_id

    if (isNew) {
      await api.post('/api/templates', payload)
    } else {
      await api.put(`/api/templates/${editingTemplate.value.template_id}`, payload)
    }

    await loadTemplates()
    editingTemplate.value = null
    allowedToolsInput.value = ''
  } catch (error) {
    console.error('Failed to save template:', error)
    errorMessage.value = error.response?.data?.detail || 'Failed to save template'
  } finally {
    isSaving.value = false
  }
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
    alert(`Failed to delete template: ${error.response?.data?.detail || error.message}`)
  }
}

function cancelEdit() {
  editingTemplate.value = null
  allowedToolsInput.value = ''
  errorMessage.value = ''
}

function onModalHidden() {
  editingTemplate.value = null
  allowedToolsInput.value = ''
  errorMessage.value = ''
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'template-management' && modalInstance) {
      loadTemplates()
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then((module) => {
      const Modal = module.default
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
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
.template-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}
</style>
