<template>
  <div
    class="modal fade"
    id="manageSessionModal"
    tabindex="-1"
    aria-labelledby="manageSessionModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="manageSessionModalLabel">
            {{ confirmationView ? confirmationTitle : 'Manage Session' }}
          </h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Loading State -->
          <div v-if="isPerformingAction" class="text-center py-4">
            <div class="spinner-border text-primary mb-3" role="status">
              <span class="visually-hidden">Loading...</span>
            </div>
            <p class="text-muted">{{ loadingMessage }}</p>
          </div>

          <!-- Confirmation Views -->
          <div v-else-if="confirmationView === 'reset'">
            <div class="alert alert-danger mb-3">
              <strong>⚠️ Warning: This action cannot be undone</strong>
            </div>
            <p class="text-muted">This will permanently delete all messages and conversation history for this session.</p>
            <p class="text-muted">The session settings and configuration will be preserved, but you will start with a completely fresh conversation.</p>
          </div>

          <div v-else-if="confirmationView === 'delete'">
            <div class="alert alert-danger mb-3">
              <strong>⚠️ Warning: This action cannot be undone</strong>
            </div>
            <p class="text-muted">This will permanently delete session <strong>{{ session?.name }}</strong>.</p>
            <p class="text-muted">All messages, conversation history, settings, and configuration will be completely destroyed. This is more destructive than Reset Session.</p>
          </div>

          <!-- Main Action List -->
          <div v-else>
            <!-- Error Message Display -->
            <div v-if="errorMessage" class="alert alert-danger alert-dismissible fade show" role="alert">
              <strong>Error:</strong> {{ errorMessage }}
              <button type="button" class="btn-close" @click="errorMessage = ''"></button>
            </div>

            <p class="text-muted">Choose how to manage this session:</p>

            <div class="d-grid gap-2">
              <!-- Non-destructive actions -->
              <button
                class="btn btn-outline-primary text-start"
                @click="handleRestart"
                :disabled="isPerformingAction"
              >
                <strong>🔄 Restart Agent</strong>
                <div class="small text-muted">Disconnect and resume session (keeps all messages)</div>
              </button>

              <button
                class="btn btn-outline-secondary text-start"
                @click="handleEndSession"
                :disabled="isPerformingAction"
              >
                <strong>🚪 End Session</strong>
                <div class="small text-muted">Close agent and return to session list</div>
              </button>

              <!-- Visual separator -->
              <hr class="my-2">

              <!-- Danger Zone -->
              <div class="danger-zone-header">
                <h6 class="text-danger mb-2">⚠️ Danger Zone</h6>
              </div>

              <!-- Destructive actions -->
              <button
                class="btn btn-outline-warning text-start"
                @click="showResetConfirmation"
                :disabled="isPerformingAction"
              >
                <strong>🧹 Reset Session</strong>
                <div class="small text-muted">Clear all messages and start fresh (keeps settings)</div>
              </button>

              <button
                class="btn btn-outline-danger text-start"
                @click="showDeleteConfirmation"
                :disabled="isPerformingAction"
              >
                <strong>🗑️ Delete Session</strong>
                <div class="small text-muted">Permanently delete this session and all its data</div>
              </button>
            </div>

            <div class="alert alert-warning mt-3 mb-0">
              <small><strong>Note:</strong> Restart is the recommended option for unsticking the agent.</small>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <!-- Confirmation View Buttons -->
          <template v-if="confirmationView && !isPerformingAction">
            <button type="button" class="btn btn-secondary" @click="cancelConfirmation">Cancel</button>
            <button
              type="button"
              class="btn"
              :class="confirmationView === 'delete' ? 'btn-danger' : 'btn-warning'"
              @click="executeConfirmation"
            >
              {{ confirmationView === 'delete' ? 'Delete Session' : 'Confirm Reset' }}
            </button>
          </template>
          <!-- Default Button -->
          <button v-else-if="!isPerformingAction" type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
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
import { useWebSocketStore } from '@/stores/websocket'
import { useMessageStore } from '@/stores/message'
import { api } from '@/utils/api'

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const wsStore = useWebSocketStore()
const messageStore = useMessageStore()

// State
const session = ref(null)
const isPerformingAction = ref(false)
const loadingMessage = ref('')
const errorMessage = ref('')
const confirmationView = ref(null) // 'reset', 'delete', or null
const modalElement = ref(null)
let modalInstance = null

// Computed
const confirmationTitle = computed(() => {
  if (confirmationView.value === 'reset') return 'Reset Session'
  if (confirmationView.value === 'delete') return 'Delete Session'
  return 'Manage Session'
})

// Show reset confirmation
function showResetConfirmation() {
  confirmationView.value = 'reset'
}

// Show delete confirmation
function showDeleteConfirmation() {
  confirmationView.value = 'delete'
}

// Cancel confirmation and return to main view
function cancelConfirmation() {
  confirmationView.value = null
}

// Execute the pending confirmation action
async function executeConfirmation() {
  if (confirmationView.value === 'reset') {
    await confirmResetSession()
  } else if (confirmationView.value === 'delete') {
    await confirmDeleteSession()
  }
}

// Handle restart (calls /restart endpoint)
async function handleRestart() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Restarting session...'

  try {
    const sessionId = session.value.session_id
    const response = await api.post(`/api/sessions/${sessionId}/restart`)

    if (response.success) {
      console.log('Session restarted successfully, reconnecting')

      // Disconnect WebSocket to force reconnection (await to ensure old socket is fully closed)
      await wsStore.disconnectSession()

      // Clear current session to force selectSession to re-run (bypass early return)
      // This is CRITICAL - without it, selectSession() returns early and doesn't reconnect
      sessionStore.currentSessionId = null

      // Reconnect to session (will fetch fresh data and reconnect WebSocket)
      await sessionStore.selectSession(sessionId)

      // Close modal
      if (modalInstance) {
        modalInstance.hide()
      }
    } else {
      const errorMsg = response.error || response.detail || 'Failed to restart session'
      console.error('Failed to restart session:', errorMsg)
      errorMessage.value = `Failed to restart session: ${errorMsg}`
    }
  } catch (error) {
    console.error('Error restarting session:', error)
    const errorMsg = error.data?.detail || error.message || 'Unknown error'
    errorMessage.value = `Error restarting session: ${errorMsg}`
  } finally {
    isPerformingAction.value = false
  }
}

// Handle end session (terminates and navigates away)
async function handleEndSession() {
  if (!session.value) return

  try {
    // Close modal first
    if (modalInstance) {
      modalInstance.hide()
    }

    console.log('Ending session', session.value.session_id)

    const sessionId = session.value.session_id

    // Call terminate endpoint
    await api.post(`/api/sessions/${sessionId}/terminate`)

    // Disconnect WebSocket (await to ensure proper cleanup)
    await wsStore.disconnectSession()

    // Deselect the session in the store
    if (sessionStore.currentSessionId === sessionId) {
      sessionStore.currentSessionId = null
    }

    // Navigate to home
    router.push('/')
  } catch (error) {
    console.error('Error ending session:', error)
    // Still navigate away even if terminate fails
    await wsStore.disconnectSession()

    // Deselect the session
    if (sessionStore.currentSessionId === session.value?.session_id) {
      sessionStore.currentSessionId = null
    }

    router.push('/')
  }
}

// Confirm reset session
async function confirmResetSession() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Resetting session...'

  try {
    const sessionId = session.value.session_id
    const response = await api.post(`/api/sessions/${sessionId}/reset`)

    if (response.success) {
      console.log('Session reset successfully, reconnecting')

      // Disconnect WebSocket to force reconnection (await to ensure old socket is fully closed)
      await wsStore.disconnectSession()

      // Clear current session to force selectSession to re-run (bypass early return)
      sessionStore.currentSessionId = null

      // Reconnect to session (will fetch fresh data and reconnect WebSocket)
      // Use selectSession to match restart behavior - this properly handles reconnection
      await sessionStore.selectSession(sessionId)

      // Close modal
      if (modalInstance) {
        modalInstance.hide()
      }
    } else {
      const errorMsg = response.error || response.detail || 'Failed to reset session'
      console.error('Failed to reset session:', errorMsg)
      errorMessage.value = `Failed to reset session: ${errorMsg}`
      confirmationView.value = null
    }
  } catch (error) {
    console.error('Error resetting session:', error)
    const errorMsg = error.data?.detail || error.message || 'Unknown error'
    errorMessage.value = `Error resetting session: ${errorMsg}`
    confirmationView.value = null
  } finally {
    isPerformingAction.value = false
  }
}

// Confirm delete session
async function confirmDeleteSession() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Deleting session...'

  try {
    const sessionIdToDelete = session.value.session_id

    // Delete session via store
    await sessionStore.deleteSession(sessionIdToDelete)

    // Navigate away if we're on the deleted session's page
    if (router.currentRoute.value.params.sessionId === sessionIdToDelete) {
      router.push('/')
    }

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (error) {
    console.error('Error deleting session:', error)
    const errorMsg = error.data?.detail || error.message || 'Unknown error'
    errorMessage.value = `Error deleting session: ${errorMsg}`
    confirmationView.value = null
  } finally {
    isPerformingAction.value = false
  }
}

// Reset state
function resetState() {
  confirmationView.value = null
  isPerformingAction.value = false
  loadingMessage.value = ''
  errorMessage.value = ''
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
      resetState()
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
.danger-zone-header {
  margin-top: 0.5rem;
}

.text-start {
  text-align: left !important;
}
</style>
