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
            <p class="text-muted">This will clear the current conversation and restart the session with a fresh context.</p>
            <p class="text-muted">Session settings and configuration will be preserved.</p>
          </div>

          <div v-else-if="confirmationView === 'erase-history'">
            <div class="alert alert-warning mb-3">
              <strong>Forget all past conversations?</strong>
            </div>
            <p class="text-muted">This will delete all distilled conversation history for this session. The agent will no longer have access to past context.</p>
          </div>

          <div v-else-if="confirmationView === 'erase-archives'">
            <div class="alert alert-danger mb-3">
              <strong>Clear playback records?</strong>
            </div>
            <p class="text-muted">This will delete all full playback records for this session. This cannot be undone.</p>
          </div>

          <div v-else-if="confirmationView === 'delete'">
            <div class="alert alert-danger mb-3">
              <strong>⚠️ Warning: This action cannot be undone</strong>
            </div>
            <p class="text-muted">This will permanently delete session <strong>{{ session?.name }}</strong> and all archive data.</p>

            <!-- Cascading deletion warning for sessions with children -->
            <div v-if="isLoadingDescendants" class="text-center py-2">
              <div class="spinner-border spinner-border-sm text-secondary" role="status">
                <span class="visually-hidden">Checking for child sessions...</span>
              </div>
              <span class="ms-2 text-muted small">Checking for child sessions...</span>
            </div>
            <div v-else-if="descendants.length > 0" class="alert alert-warning mt-3">
              <strong>Cascading Deletion:</strong> This session has <strong>{{ descendants.length }}</strong> child session{{ descendants.length === 1 ? '' : 's' }} that will also be deleted:
              <ul class="mb-0 mt-2">
                <li v-for="child in descendants" :key="child.session_id" class="small">
                  <strong>{{ child.name || child.session_id }}</strong>
                  <span v-if="child.role" class="text-muted"> ({{ child.role }})</span>
                </li>
              </ul>
            </div>
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
                <div class="small text-muted">Disconnect and resume session (keeps conversation)</div>
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
                <h6 class="text-danger mb-2">Danger Zone</h6>
              </div>

              <!-- Destructive actions -->
              <button
                class="btn btn-outline-warning text-start"
                @click="showResetConfirmation"
                :disabled="isPerformingAction"
              >
                <strong>Reset Session</strong>
                <div class="small text-muted">Clear conversation and restart session</div>
              </button>

              <button
                class="btn btn-outline-danger text-start"
                @click="showDeleteConfirmation"
                :disabled="isPerformingAction"
              >
                <strong>Delete Session</strong>
                <div class="small text-muted">Permanently delete session and archive data</div>
              </button>

              <!-- Knowledge Management (conditional) -->
              <template v-if="historyArchivesStatus.has_history || historyArchivesStatus.has_archives">
                <hr class="my-2">

                <div class="knowledge-section-header">
                  <h6 class="text-secondary mb-2">Knowledge Management</h6>
                </div>

                <button
                  v-if="historyArchivesStatus.has_history"
                  class="btn btn-outline-secondary text-start"
                  @click="confirmationView = 'erase-history'"
                  :disabled="isPerformingAction"
                >
                  <strong>Delete History</strong>
                  <div class="small text-muted">Forget all past conversations</div>
                </button>

                <button
                  v-if="historyArchivesStatus.has_archives"
                  class="btn btn-outline-secondary text-start"
                  @click="confirmationView = 'erase-archives'"
                  :disabled="isPerformingAction"
                >
                  <strong>Delete Archives</strong>
                  <div class="small text-muted">Clear full playback records for session</div>
                </button>
              </template>
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
              {{ confirmationView === 'delete' ? 'Delete Session'
                 : confirmationView === 'erase-history' ? 'Delete History'
                 : confirmationView === 'erase-archives' ? 'Delete Archives'
                 : 'Confirm Reset' }}
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
import { usePollingStore } from '@/stores/polling'
import { useMessageStore } from '@/stores/message'
import { api } from '@/utils/api'

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const wsStore = usePollingStore()
const messageStore = useMessageStore()

// State
const session = ref(null)
const isPerformingAction = ref(false)
const loadingMessage = ref('')
const errorMessage = ref('')
const confirmationView = ref(null) // 'reset', 'delete', 'erase-history', 'erase-archives', or null
const modalElement = ref(null)
let modalInstance = null

// Descendant tracking for cascading deletion warning
const descendants = ref([])
const isLoadingDescendants = ref(false)

// History/archives status
const historyArchivesStatus = ref({ has_history: false, has_archives: false })
const isLoadingStatus = ref(false)

// Computed
const confirmationTitle = computed(() => {
  if (confirmationView.value === 'reset') return 'Reset Session'
  if (confirmationView.value === 'delete') return 'Delete Session'
  if (confirmationView.value === 'erase-history') return 'Delete History'
  if (confirmationView.value === 'erase-archives') return 'Delete Archives'
  return 'Manage Session'
})

// Show reset confirmation
function showResetConfirmation() {
  confirmationView.value = 'reset'
}

// Show delete confirmation with descendant check
async function showDeleteConfirmation() {
  confirmationView.value = 'delete'
  descendants.value = []

  if (!session.value) return

  // Fetch descendants to show cascading deletion warning
  isLoadingDescendants.value = true
  try {
    const response = await api.get(`/api/sessions/${session.value.session_id}/descendants`)
    descendants.value = response.descendants || []
  } catch (error) {
    console.error('Failed to fetch descendants:', error)
    // Continue without descendants - not critical for deletion
    descendants.value = []
  } finally {
    isLoadingDescendants.value = false
  }
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
  } else if (confirmationView.value === 'erase-history') {
    await confirmEraseHistory()
  } else if (confirmationView.value === 'erase-archives') {
    await confirmEraseArchives()
  }
}

// Handle restart (calls /restart endpoint)
async function handleRestart() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Restarting session...'

  try {
    const sessionId = session.value.session_id
    const isCurrentSession = sessionStore.currentSessionId === sessionId
    const response = await api.post(`/api/sessions/${sessionId}/restart`)

    if (response.success) {
      console.log('Session restarted successfully')

      // Only reconnect if this is the currently selected session
      if (isCurrentSession) {
        // Disconnect WebSocket to force reconnection (await to ensure old socket is fully closed)
        await wsStore.disconnectSession()
        wsStore.resetSessionCursor(sessionId)

        // Clear current session to force selectSession to re-run (bypass early return)
        // This is CRITICAL - without it, selectSession() returns early and doesn't reconnect
        sessionStore.currentSessionId = null

        // Reconnect to session (fire-and-forget — connectSession is an infinite poll loop,
        // awaiting selectSession would hang the modal forever)
        sessionStore.selectSession(sessionId)
      }

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

    // Only disconnect WebSocket if ending the currently selected session
    if (sessionStore.currentSessionId === sessionId) {
      await wsStore.disconnectSession()
      sessionStore.currentSessionId = null
      // Navigate to home only if we ended the currently selected session
      router.push('/')
    }
    // If ending a different session, stay on current route
  } catch (error) {
    console.error('Error ending session:', error)
    // Still navigate away even if terminate fails
    // Only disconnect WebSocket if ending the currently selected session
    if (sessionStore.currentSessionId === session.value?.session_id) {
      await wsStore.disconnectSession()
      sessionStore.currentSessionId = null
      // Navigate to home only if we ended the currently selected session
      router.push('/')
    }
    // If ending a different session, stay on current route
  }
}

// Confirm reset session
async function confirmResetSession() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Resetting session...'

  try {
    const sessionId = session.value.session_id
    const isCurrentSession = sessionStore.currentSessionId === sessionId
    const response = await api.post(`/api/sessions/${sessionId}/reset`)

    if (response.success) {
      console.log('Session reset successfully')

      // Only reconnect if this is the currently selected session
      if (isCurrentSession) {
        // Disconnect WebSocket to force reconnection (await to ensure old socket is fully closed)
        await wsStore.disconnectSession()
        wsStore.resetSessionCursor(sessionId)

        // Clear current session to force selectSession to re-run (bypass early return)
        sessionStore.currentSessionId = null

        // Reconnect to session (fire-and-forget — connectSession is an infinite poll loop,
        // awaiting selectSession would hang the modal forever)
        sessionStore.selectSession(sessionId)
      }

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

// Confirm erase history
async function confirmEraseHistory() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Erasing history...'

  try {
    await sessionStore.eraseHistory(session.value.session_id)
    historyArchivesStatus.value.has_history = false
    confirmationView.value = null
  } catch (error) {
    console.error('Error erasing history:', error)
    errorMessage.value = `Error erasing history: ${error.message || 'Unknown error'}`
    confirmationView.value = null
  } finally {
    isPerformingAction.value = false
  }
}

// Confirm erase archives
async function confirmEraseArchives() {
  if (!session.value) return

  isPerformingAction.value = true
  loadingMessage.value = 'Erasing archives...'

  try {
    await sessionStore.eraseArchives(session.value.session_id)
    historyArchivesStatus.value.has_archives = false
    confirmationView.value = null
  } catch (error) {
    console.error('Error erasing archives:', error)
    errorMessage.value = `Error erasing archives: ${error.message || 'Unknown error'}`
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
  descendants.value = []
  isLoadingDescendants.value = false
  historyArchivesStatus.value = { has_history: false, has_archives: false }
  isLoadingStatus.value = false
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

      // Fetch history/archives status
      if (session.value?.session_id) {
        isLoadingStatus.value = true
        sessionStore.checkHistoryArchivesStatus(session.value.session_id)
          .then(status => {
            historyArchivesStatus.value = status
          })
          .catch(err => {
            console.warn('Failed to check history/archives status:', err)
          })
          .finally(() => {
            isLoadingStatus.value = false
          })
      }
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
.danger-zone-header,
.knowledge-section-header {
  margin-top: 0.5rem;
}

.text-start {
  text-align: left !important;
}
</style>
