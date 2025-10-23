<template>
  <div
    class="modal fade"
    id="manageSessionModal"
    tabindex="-1"
    aria-labelledby="manageSessionModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="manageSessionModalLabel">
            Manage Session: {{ session?.name }}
          </h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Session Info -->
          <div class="mb-3">
            <h6>Session Information</h6>

            <div class="mb-2">
              <label class="form-label small text-muted mb-0">State</label>
              <div>
                <span class="badge" :class="getStateBadgeClass(session?.state)">
                  {{ session?.state }}
                </span>
              </div>
            </div>

            <div class="mb-2">
              <label class="form-label small text-muted mb-0">Working Directory</label>
              <input
                type="text"
                class="form-control form-control-sm"
                :value="session?.working_directory"
                disabled
                readonly
              />
            </div>

            <div class="mb-2">
              <label class="form-label small text-muted mb-0">Permission Mode</label>
              <input
                type="text"
                class="form-control form-control-sm"
                :value="session?.current_permission_mode"
                disabled
                readonly
              />
            </div>

            <div class="mb-2">
              <label class="form-label small text-muted mb-0">Model</label>
              <input
                type="text"
                class="form-control form-control-sm"
                :value="session?.model"
                disabled
                readonly
              />
            </div>
          </div>

          <!-- Actions -->
          <div class="mb-3">
            <h6>Session Actions</h6>

            <!-- Start/Resume -->
            <button
              v-if="canStart"
              class="btn btn-success mb-2 w-100"
              @click="handleStart"
              :disabled="isPerformingAction"
            >
              <i class="bi bi-play-fill"></i>
              {{ session?.state === 'CREATED' ? 'Start Session' : 'Resume Session' }}
            </button>

            <!-- Pause -->
            <button
              v-if="canPause"
              class="btn btn-warning mb-2 w-100"
              @click="handlePause"
              :disabled="isPerformingAction"
            >
              <i class="bi bi-pause-fill"></i> Pause Session
            </button>

            <!-- Terminate -->
            <button
              v-if="canTerminate"
              class="btn btn-secondary mb-2 w-100"
              @click="handleTerminate"
              :disabled="isPerformingAction"
            >
              <i class="bi bi-stop-fill"></i> Terminate Session
            </button>

            <!-- Restart (Terminate + Start) -->
            <button
              v-if="canRestart"
              class="btn btn-info mb-2 w-100"
              @click="showRestartConfirmation = true"
              :disabled="isPerformingAction"
            >
              <i class="bi bi-arrow-clockwise"></i> Restart Session
            </button>
          </div>

          <!-- Restart Confirmation -->
          <div v-if="showRestartConfirmation" class="alert alert-info" role="alert">
            <strong>Restart Session?</strong>
            <p class="mb-2">
              This will terminate the current session and start a new one. The conversation history
              will be preserved, but the SDK instance will be reset.
            </p>
            <div class="d-flex gap-2">
              <button
                class="btn btn-info btn-sm"
                @click="handleRestart"
                :disabled="isPerformingAction"
              >
                <span v-if="isPerformingAction" class="spinner-border spinner-border-sm me-2"></span>
                {{ isPerformingAction ? 'Restarting...' : 'Yes, Restart' }}
              </button>
              <button
                class="btn btn-secondary btn-sm"
                @click="showRestartConfirmation = false"
                :disabled="isPerformingAction"
              >
                Cancel
              </button>
            </div>
          </div>

          <!-- Danger Zone -->
          <hr />
          <div class="danger-zone">
            <h6 class="text-danger">Danger Zone</h6>
            <p class="text-muted small">
              Deleting this session will permanently remove all conversation history and data.
              This action cannot be undone.
            </p>
            <button
              class="btn btn-outline-danger"
              @click="showDeleteConfirmation = true"
              :disabled="isPerformingAction"
            >
              <i class="bi bi-trash"></i> Delete Session
            </button>
          </div>

          <!-- Delete Confirmation -->
          <div v-if="showDeleteConfirmation" class="alert alert-warning mt-3" role="alert">
            <strong>Are you sure?</strong>
            <p class="mb-2">
              This will permanently delete "{{ session?.name }}" and all its data.
            </p>
            <div class="d-flex gap-2">
              <button
                class="btn btn-danger btn-sm"
                @click="handleDelete"
                :disabled="isPerformingAction"
              >
                <span v-if="isPerformingAction" class="spinner-border spinner-border-sm me-2"></span>
                {{ isPerformingAction ? 'Deleting...' : 'Yes, Delete Permanently' }}
              </button>
              <button
                class="btn btn-secondary btn-sm"
                @click="showDeleteConfirmation = false"
                :disabled="isPerformingAction"
              >
                Cancel
              </button>
            </div>
          </div>

          <!-- Error Message -->
          <div v-if="errorMessage" class="alert alert-danger mt-3" role="alert">
            {{ errorMessage }}
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// State
const session = ref(null)
const isPerformingAction = ref(false)
const errorMessage = ref('')
const showRestartConfirmation = ref(false)
const showDeleteConfirmation = ref(false)
const modalElement = ref(null)
let modalInstance = null

// Computed - Action availability
const canStart = computed(() => {
  const state = session.value?.state
  return state === 'created' || state === 'paused' || state === 'terminated'
})

const canPause = computed(() => {
  return session.value?.state === 'active'
})

const canTerminate = computed(() => {
  const state = session.value?.state
  return state === 'active' || state === 'paused' || state === 'starting'
})

const canRestart = computed(() => {
  const state = session.value?.state
  return state === 'active' || state === 'paused'
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

// Handle start/resume
async function handleStart() {
  if (!session.value) return

  isPerformingAction.value = true
  errorMessage.value = ''

  try {
    await sessionStore.startSession(session.value.session_id)
  } catch (error) {
    console.error('Failed to start session:', error)
    errorMessage.value = error.message || 'Failed to start session'
  } finally {
    isPerformingAction.value = false
  }
}

// Handle pause
async function handlePause() {
  if (!session.value) return

  isPerformingAction.value = true
  errorMessage.value = ''

  try {
    await sessionStore.pauseSession(session.value.session_id)
  } catch (error) {
    console.error('Failed to pause session:', error)
    errorMessage.value = error.message || 'Failed to pause session'
  } finally {
    isPerformingAction.value = false
  }
}

// Handle terminate
async function handleTerminate() {
  if (!session.value) return

  isPerformingAction.value = true
  errorMessage.value = ''

  try {
    await sessionStore.terminateSession(session.value.session_id)
  } catch (error) {
    console.error('Failed to terminate session:', error)
    errorMessage.value = error.message || 'Failed to terminate session'
  } finally {
    isPerformingAction.value = false
  }
}

// Handle restart
async function handleRestart() {
  if (!session.value) return

  isPerformingAction.value = true
  errorMessage.value = ''
  showRestartConfirmation.value = false

  try {
    // Terminate then start
    await sessionStore.terminateSession(session.value.session_id)
    await new Promise(resolve => setTimeout(resolve, 500)) // Brief delay
    await sessionStore.startSession(session.value.session_id)
  } catch (error) {
    console.error('Failed to restart session:', error)
    errorMessage.value = error.message || 'Failed to restart session'
  } finally {
    isPerformingAction.value = false
  }
}

// Handle delete
async function handleDelete() {
  if (!session.value) return

  isPerformingAction.value = true
  errorMessage.value = ''

  try {
    await sessionStore.deleteSession(session.value.session_id)

    // Navigate away if we're on the deleted session's page
    if (router.currentRoute.value.params.sessionId === session.value.session_id) {
      router.push('/')
    }

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (error) {
    console.error('Failed to delete session:', error)
    errorMessage.value = error.message || 'Failed to delete session'
  } finally {
    isPerformingAction.value = false
  }
}

// Reset state
function resetState() {
  errorMessage.value = ''
  showRestartConfirmation.value = false
  showDeleteConfirmation.value = false
}

// Handle modal hidden event
function onModalHidden() {
  resetState()
  session.value = null
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'manage-session' && modalInstance) {
      const data = modal.data || {}
      session.value = data.session
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
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

.bi {
  margin-right: 0.25rem;
}
</style>
