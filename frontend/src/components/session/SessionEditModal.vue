<template>
  <div
    class="modal fade"
    id="editSessionModal"
    tabindex="-1"
    aria-labelledby="editSessionModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="editSessionModalLabel">Edit Session</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="handleSubmit">
            <div class="mb-3">
              <label for="editSessionName" class="form-label">Session Name</label>
              <input
                type="text"
                class="form-control"
                id="editSessionName"
                v-model="formData.name"
                :class="{ 'is-invalid': errors.name }"
                placeholder="Session Name"
                required
              />
              <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
            </div>

            <div class="mb-3">
              <label class="form-label">Working Directory</label>
              <input
                type="text"
                class="form-control"
                :value="session?.working_directory"
                disabled
                readonly
              />
              <div class="form-text text-muted">
                Working directory is inherited from project
              </div>
            </div>

            <div class="mb-3">
              <label class="form-label">Current State</label>
              <div class="form-control-plaintext">
                <span
                  class="badge"
                  :class="getStateBadgeClass(session?.state)"
                >
                  {{ session?.state }}
                </span>
              </div>
            </div>

            <div class="mb-3">
              <label for="permissionMode" class="form-label">Permission Mode</label>
              <select
                class="form-select"
                id="permissionMode"
                v-model="formData.permission_mode"
                :disabled="!isSessionActive"
              >
                <option value="default">Default (Prompt for tools not in settings)</option>
                <option value="acceptEdits">Accept Edits (Auto-approve Edit/Write)</option>
                <option value="plan">Plan Mode (Auto-resets after ExitPlanMode)</option>
                <option v-if="canUseBypassPermissions" value="bypassPermissions">⚠️ Bypass Permissions (No prompts)</option>
              </select>
              <div class="form-text">
                <span v-if="!isSessionActive" class="text-warning">
                  ⚠️ Session must be active to change permission mode. Start the session first.
                </span>
                <span v-else>
                  Controls which tool actions require permission prompts
                </span>
              </div>
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
            {{ isSubmitting ? 'Saving...' : 'Save Changes' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const sessionStore = useSessionStore()
const uiStore = useUIStore()

// State
const session = ref(null)
const formData = ref({
  name: '',
  permission_mode: 'default'
})
const errors = ref({
  name: ''
})
const isSubmitting = ref(false)
const errorMessage = ref('')
const modalElement = ref(null)
let modalInstance = null

// Computed
const isSessionActive = computed(() => {
  return session.value?.state === 'active' || session.value?.state === 'starting'
})

// Only allow bypassPermissions if session was initially started with it
// (SDK bug prevents switching to bypassPermissions after initial start)
const canUseBypassPermissions = computed(() => {
  return session.value?.initial_permission_mode === 'bypassPermissions'
})

// Get badge class for session state
function getStateBadgeClass(state) {
  const classMap = {
    'created': 'bg-secondary',
    'starting': 'bg-info',
    'active': 'bg-success',
    'paused': 'bg-warning',
    'terminating': 'bg-warning',
    'terminated': 'bg-secondary',
    'error': 'bg-danger'
  }
  return classMap[state] || 'bg-secondary'
}

// Validate form
function validate() {
  errors.value = {}
  let isValid = true

  if (!formData.value.name.trim()) {
    errors.value.name = 'Session name is required'
    isValid = false
  }

  return isValid
}

// Handle form submission
async function handleSubmit() {
  if (!validate() || !session.value) {
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    // Update session name
    await sessionStore.updateSessionName(session.value.session_id, formData.value.name)

    // Update permission mode if changed
    if (formData.value.permission_mode !== session.value.current_permission_mode) {
      await sessionStore.setPermissionMode(session.value.session_id, formData.value.permission_mode)
    }

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (error) {
    console.error('Failed to update session:', error)
    errorMessage.value = error.message || 'Failed to update session. Please try again.'
  } finally {
    isSubmitting.value = false
  }
}

// Reset form
function resetForm() {
  formData.value = {
    name: '',
    permission_mode: 'default'
  }
  errors.value = {}
  errorMessage.value = ''
}

// Handle modal shown event
function onModalShown() {
  // Focus name input
  const nameInput = document.getElementById('editSessionName')
  if (nameInput) {
    nameInput.focus()
    nameInput.select()
  }
}

// Handle modal hidden event
function onModalHidden() {
  resetForm()
  session.value = null
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'edit-session' && modalInstance) {
      const data = modal.data || {}
      session.value = data.session
      if (session.value) {
        formData.value.name = session.value.name
        formData.value.permission_mode = session.value.current_permission_mode || 'default'
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
.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}
</style>
