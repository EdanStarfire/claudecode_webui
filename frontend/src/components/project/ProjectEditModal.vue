<template>
  <div
    class="modal fade"
    id="editProjectModal"
    tabindex="-1"
    aria-labelledby="editProjectModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="editProjectModalLabel">Edit Project</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="handleSubmit">
            <div class="mb-3">
              <label for="editProjectName" class="form-label">Project Name</label>
              <input
                type="text"
                class="form-control"
                id="editProjectName"
                v-model="formData.name"
                :class="{ 'is-invalid': errors.name }"
                placeholder="My Project"
                required
              />
              <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
            </div>

            <div class="mb-3">
              <label class="form-label">Working Directory</label>
              <input
                type="text"
                class="form-control"
                :value="project?.working_directory"
                disabled
                readonly
              />
              <div class="form-text text-muted">
                Working directory cannot be changed after creation
              </div>
            </div>

            <div v-if="errorMessage" class="alert alert-danger" role="alert">
              {{ errorMessage }}
            </div>
          </form>

          <!-- Delete section -->
          <hr />
          <div class="danger-zone">
            <h6 class="text-danger">Danger Zone</h6>
            <p class="text-muted small">
              Deleting this project will also delete all {{ sessionCount }} session(s) and their data.
              This action cannot be undone.
            </p>
            <button
              type="button"
              class="btn btn-outline-danger"
              @click="showDeleteConfirmation = true"
              :disabled="isDeleting"
            >
              <i class="bi bi-trash"></i> Delete Project
            </button>
          </div>

          <!-- Delete confirmation -->
          <div v-if="showDeleteConfirmation" class="alert alert-warning mt-3" role="alert">
            <strong>Are you sure?</strong>
            <p class="mb-2">
              This will permanently delete the project "{{ project?.name }}" and all
              {{ sessionCount }} session(s).
            </p>
            <div class="d-flex gap-2">
              <button
                type="button"
                class="btn btn-danger btn-sm"
                @click="handleDelete"
                :disabled="isDeleting"
              >
                <span v-if="isDeleting" class="spinner-border spinner-border-sm me-2"></span>
                {{ isDeleting ? 'Deleting...' : 'Yes, Delete Permanently' }}
              </button>
              <button
                type="button"
                class="btn btn-secondary btn-sm"
                @click="showDeleteConfirmation = false"
                :disabled="isDeleting"
              >
                Cancel
              </button>
            </div>
          </div>
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
            {{ isSubmitting ? 'Saving...' : 'Save Changes' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useUIStore } from '@/stores/ui'

const projectStore = useProjectStore()
const uiStore = useUIStore()

// State
const project = ref(null)
const formData = ref({
  name: ''
})
const errors = ref({
  name: ''
})
const isSubmitting = ref(false)
const isDeleting = ref(false)
const errorMessage = ref('')
const showDeleteConfirmation = ref(false)
const modalElement = ref(null)
let modalInstance = null

// Computed
const sessionCount = computed(() => {
  return project.value?.session_ids?.length || 0
})

// Validate form
function validate() {
  errors.value = {}
  let isValid = true

  if (!formData.value.name.trim()) {
    errors.value.name = 'Project name is required'
    isValid = false
  }

  return isValid
}

// Handle form submission
async function handleSubmit() {
  if (!validate() || !project.value) {
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    await projectStore.updateProject(project.value.project_id, {
      name: formData.value.name
    })

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (error) {
    console.error('Failed to update project:', error)
    errorMessage.value = error.message || 'Failed to update project. Please try again.'
  } finally {
    isSubmitting.value = false
  }
}

// Handle delete
async function handleDelete() {
  if (!project.value) return

  isDeleting.value = true
  errorMessage.value = ''

  try {
    await projectStore.deleteProject(project.value.project_id)

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (error) {
    console.error('Failed to delete project:', error)
    errorMessage.value = error.message || 'Failed to delete project. Please try again.'
  } finally {
    isDeleting.value = false
  }
}

// Reset form
function resetForm() {
  formData.value = {
    name: ''
  }
  errors.value = {}
  errorMessage.value = ''
  showDeleteConfirmation.value = false
}

// Handle modal shown event
function onModalShown() {
  // Focus name input
  const nameInput = document.getElementById('editProjectName')
  if (nameInput) {
    nameInput.focus()
    nameInput.select()
  }
}

// Handle modal hidden event
function onModalHidden() {
  resetForm()
  project.value = null
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'edit-project' && modalInstance) {
      const data = modal.data || {}
      project.value = data.project
      if (project.value) {
        formData.value.name = project.value.name
      }
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)

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
.danger-zone {
  padding: 1rem;
  border: 1px solid #dc3545;
  border-radius: 0.25rem;
  background-color: #fff5f5;
}

.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}
</style>
