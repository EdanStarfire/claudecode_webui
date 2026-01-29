<template>
  <div
    class="modal fade"
    id="createProjectModal"
    tabindex="-1"
    aria-labelledby="createProjectModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="createProjectModalLabel">Create New Project</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="handleSubmit">
            <div class="mb-3">
              <label for="projectName" class="form-label">Project Name</label>
              <input
                type="text"
                class="form-control"
                id="projectName"
                v-model="formData.name"
                :class="{ 'is-invalid': errors.name }"
                placeholder="My Project"
                required
              />
              <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
            </div>

            <div class="mb-3">
              <label for="workingDirectory" class="form-label">Working Directory</label>
              <div class="input-group">
                <input
                  type="text"
                  class="form-control"
                  id="workingDirectory"
                  v-model="formData.workingDirectory"
                  :class="{ 'is-invalid': errors.workingDirectory }"
                  placeholder="/path/to/project"
                  required
                />
                <button
                  class="btn btn-outline-secondary"
                  type="button"
                  @click="openFolderBrowser"
                >
                  Browse...
                </button>
                <div class="invalid-feedback" v-if="errors.workingDirectory">
                  {{ errors.workingDirectory }}
                </div>
              </div>
              <div class="form-text">
                The project's working directory (cannot be changed after creation)
              </div>
            </div>

            <!-- Issue #313: Removed multi-agent checkbox - all projects support minions -->
            <div class="mb-3 form-check">
              <input
                type="checkbox"
                class="form-check-input"
                id="createSession"
                v-model="formData.createSession"
              />
              <label class="form-check-label" for="createSession">
                Create initial session
              </label>
              <div class="form-text">
                You can add minions later from the project sidebar
              </div>
            </div>

            <div v-if="formData.createSession" class="mb-3 ms-4">
              <label for="sessionName" class="form-label">
                Initial Session Name
              </label>
              <input
                type="text"
                class="form-control"
                id="sessionName"
                v-model="formData.sessionName"
                placeholder="main"
              />
            </div>

            <div v-if="errorMessage" class="alert alert-danger" role="alert">
              {{ errorMessage }}
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            @click="handleSubmit"
            :disabled="isSubmitting"
          >
            <span v-if="isSubmitting" class="spinner-border spinner-border-sm me-2"></span>
            {{ isSubmitting ? 'Creating...' : 'Create Project' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// Form data
const formData = ref({
  name: '',
  workingDirectory: '',
  createSession: true,
  sessionName: 'main'
})

// Validation errors
const errors = ref({
  name: '',
  workingDirectory: ''
})

// UI state
const isSubmitting = ref(false)
const errorMessage = ref('')
const modalElement = ref(null)
let modalInstance = null

// Open folder browser modal
function openFolderBrowser() {
  uiStore.showModal('folder-browser', {
    currentPath: formData.value.workingDirectory || '',
    onSelect: (path) => {
      formData.value.workingDirectory = path
      errors.value.workingDirectory = ''
    }
  })
}

// Validate form
function validate() {
  errors.value = {}
  let isValid = true

  if (!formData.value.name.trim()) {
    errors.value.name = 'Project name is required'
    isValid = false
  }

  if (!formData.value.workingDirectory.trim()) {
    errors.value.workingDirectory = 'Working directory is required'
    isValid = false
  }

  return isValid
}

// Handle form submission
async function handleSubmit() {
  if (!validate()) {
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    // Create project (all projects support minions - issue #313)
    const project = await projectStore.createProject(
      formData.value.name,
      formData.value.workingDirectory
    )

    // Create initial session/minion if requested
    if (formData.value.createSession) {
      await sessionStore.createSession(project.project_id, {
        name: formData.value.sessionName || 'main',
        permission_mode: 'default',
        tools: [],
        model: 'claude-sonnet-4-5-20250929'
      })
    }

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }

    // Reset form
    resetForm()
  } catch (error) {
    console.error('Failed to create project:', error)
    errorMessage.value = error.message || 'Failed to create project. Please try again.'
  } finally {
    isSubmitting.value = false
  }
}

// Reset form
function resetForm() {
  formData.value = {
    name: '',
    workingDirectory: '',
    createSession: true,
    sessionName: 'main'
  }
  errors.value = {}
  errorMessage.value = ''
}

// Handle modal events
function onModalShown() {
  // Focus name input when modal opens
  const nameInput = document.getElementById('projectName')
  if (nameInput) {
    nameInput.focus()
  }
}

function onModalHidden() {
  resetForm()
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'create-project' && modalInstance) {
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    // Import Bootstrap Modal
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)

      // Listen for modal events
      modalElement.value.addEventListener('shown.bs.modal', onModalShown)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
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
</style>
