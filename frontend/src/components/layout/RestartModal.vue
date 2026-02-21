<template>
  <div
    class="modal fade"
    id="restartModal"
    tabindex="-1"
    aria-labelledby="restartModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="restartModalLabel">Restart Server</h5>
          <button
            v-if="phase === 'confirm'"
            type="button"
            class="btn-close"
            data-bs-dismiss="modal"
            aria-label="Close"
          ></button>
        </div>
        <div class="modal-body">
          <!-- Phase 1: Confirmation -->
          <div v-if="phase === 'confirm'">
            <p>This will pull the latest code, sync dependencies, and restart the backend server. All active sessions will be interrupted.</p>

            <div v-if="gitLoading" class="text-center py-3">
              <div class="spinner-border spinner-border-sm" role="status"></div>
              <span class="ms-2">Loading git status...</span>
            </div>

            <div v-else-if="gitStatus" class="card bg-light mb-3">
              <div class="card-body py-2 px-3">
                <div class="small">
                  <div><strong>Branch:</strong> {{ gitStatus.branch }}</div>

                  <!-- Remote commit info (primary) -->
                  <template v-if="!gitStatus.remote_fetch_failed && gitStatus.remote_commit_hash">
                    <div><strong>Latest on origin:</strong> {{ gitStatus.remote_commit_message }}</div>
                    <div class="font-monospace text-muted" style="font-size: 0.75rem;">{{ gitStatus.remote_commit_hash?.substring(0, 12) }}</div>
                    <div v-if="gitStatus.commits_behind > 0" class="text-info mt-1">
                      {{ gitStatus.commits_behind }} commit{{ gitStatus.commits_behind !== 1 ? 's' : '' }} behind origin
                    </div>
                    <div v-else class="text-success mt-1">
                      Already up to date
                    </div>
                  </template>

                  <!-- Fallback: local commit when remote unavailable -->
                  <template v-else>
                    <div class="text-warning mt-1 mb-1" v-if="gitStatus.remote_fetch_failed">
                      <small>Could not fetch remote info — showing local commit</small>
                    </div>
                    <div><strong>Last commit:</strong> {{ gitStatus.last_commit_message }}</div>
                    <div class="font-monospace text-muted" style="font-size: 0.75rem;">{{ gitStatus.last_commit_hash?.substring(0, 12) }}</div>
                  </template>

                  <div v-if="gitStatus.has_uncommitted_changes" class="text-warning mt-1">
                    <strong>Warning:</strong> Uncommitted changes detected. git pull may fail.
                  </div>
                </div>
              </div>
            </div>

            <div v-else-if="gitError" class="alert alert-warning py-2 small">
              Could not fetch git status: {{ gitError }}
            </div>
          </div>

          <!-- Phase 2: Progress -->
          <div v-else-if="phase === 'progress'" class="text-center py-3">
            <div v-if="uiStore.restartStatus === 'pulling'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2">Pulling code &amp; syncing dependencies...</div>
            </div>
            <div v-else-if="uiStore.restartStatus === 'restarting'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2">Restarting server...</div>
            </div>
            <div v-else-if="uiStore.restartStatus === 'reconnecting'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2">Waiting for server to come back...</div>
              <div class="text-muted small mt-1">{{ reconnectCountdown }}s remaining</div>
            </div>
            <div v-else-if="uiStore.restartStatus === 'error'" class="mb-3">
              <div class="text-danger fs-3">&#x2717;</div>
              <div class="mt-2 text-danger">{{ errorMessage }}</div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <template v-if="phase === 'confirm'">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn btn-warning" @click="doRestart" :disabled="gitLoading">
              Pull &amp; Restart
            </button>
          </template>
          <template v-else-if="uiStore.restartStatus === 'error'">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { getGitStatus, restartServer } from '@/utils/api'

const uiStore = useUIStore()

const modalElement = ref(null)
let modalInstance = null

const phase = ref('confirm') // confirm | progress
const gitLoading = ref(false)
const gitStatus = ref(null)
const gitError = ref(null)
const errorMessage = ref('')
const reconnectCountdown = ref(60)
let healthPollInterval = null
let countdownInterval = null

async function fetchGitStatus() {
  gitLoading.value = true
  gitError.value = null
  try {
    gitStatus.value = await getGitStatus()
  } catch (e) {
    gitError.value = e.message || 'Unknown error'
  } finally {
    gitLoading.value = false
  }
}

async function doRestart() {
  phase.value = 'progress'
  uiStore.restartInProgress = true
  uiStore.restartStatus = 'pulling'

  try {
    await restartServer()
    uiStore.restartStatus = 'restarting'
    // Server will go down shortly, start polling for health
    startHealthPoll()
  } catch (e) {
    uiStore.restartStatus = 'error'
    errorMessage.value = e.message || 'Restart failed'
    uiStore.restartInProgress = false
  }
}

function startHealthPoll() {
  uiStore.restartStatus = 'reconnecting'
  reconnectCountdown.value = 60
  const startTime = Date.now()

  countdownInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000)
    reconnectCountdown.value = Math.max(0, 60 - elapsed)
  }, 1000)

  // Wait 2 seconds before first poll to give server time to go down
  setTimeout(() => {
    healthPollInterval = setInterval(async () => {
      try {
        const response = await fetch('/health')
        if (response.ok) {
          cleanup()
          uiStore.restartInProgress = false
          uiStore.restartStatus = 'idle'
          if (modalInstance) {
            modalInstance.hide()
          }
          // Reload the page to pick up any frontend changes
          window.location.reload()
        }
      } catch {
        // Server still down, keep polling
      }

      // Timeout after 60 seconds
      if (Date.now() - startTime > 60000) {
        cleanup()
        uiStore.restartStatus = 'error'
        errorMessage.value = 'Server did not come back within 60 seconds.'
        uiStore.restartInProgress = false
      }
    }, 2000)
  }, 2000)
}

function cleanup() {
  if (healthPollInterval) {
    clearInterval(healthPollInterval)
    healthPollInterval = null
  }
  if (countdownInterval) {
    clearInterval(countdownInterval)
    countdownInterval = null
  }
}

function resetState() {
  phase.value = 'confirm'
  gitStatus.value = null
  gitError.value = null
  errorMessage.value = ''
  reconnectCountdown.value = 60
  cleanup()
}

function onModalHidden() {
  // Only reset if not in progress
  if (!uiStore.restartInProgress) {
    resetState()
    uiStore.restartStatus = 'idle'
  }
  uiStore.hideModal()
}

watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'restart-server' && modalInstance) {
      resetState()
      modalInstance.show()
      fetchGitStatus()
    }
  }
)

onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value, {
        backdrop: 'static',
        keyboard: false
      })
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

onUnmounted(() => {
  cleanup()
  if (modalElement.value) {
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>
