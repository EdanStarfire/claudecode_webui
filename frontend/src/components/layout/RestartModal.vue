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

            <div v-else-if="gitStatus" class="card bg-body-secondary mb-3">
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

            <!-- Branch/commit picker disclosure (issue #1760) -->
            <div v-if="gitStatus" class="mb-2">
              <button
                type="button"
                class="btn btn-link btn-sm px-0 text-decoration-none"
                @click="togglePicker"
              >
                {{ showPicker ? 'Hide branch/commit picker ▾' : 'Change branch or commit ▾' }}
              </button>

              <div v-if="showPicker" class="border rounded p-2 mt-1">
                <div v-if="branchesLoading" class="text-center py-2">
                  <div class="spinner-border spinner-border-sm" role="status"></div>
                  <span class="ms-2 small">Loading branches...</span>
                </div>
                <template v-else>
                  <div class="mb-2">
                    <label class="form-label small mb-1" for="restart-branch-select">Branch</label>
                    <select
                      id="restart-branch-select"
                      class="form-select form-select-sm"
                      v-model="selectedBranch"
                    >
                      <option v-for="b in branches" :key="b.name" :value="b.name">
                        {{ b.name }}{{ b.is_current ? ' (current)' : '' }}{{ b.is_remote_only ? ' [remote]' : '' }}
                      </option>
                    </select>
                  </div>

                  <div v-if="commitsLoading" class="text-center py-2">
                    <div class="spinner-border spinner-border-sm" role="status"></div>
                    <span class="ms-2 small">Loading commits...</span>
                  </div>
                  <template v-else>
                    <label class="form-label small mb-1">Commit</label>
                    <div class="list-group commit-list" style="max-height: 200px; overflow-y: auto;">
                      <button
                        type="button"
                        class="list-group-item list-group-item-action py-1 px-2 small"
                        :class="{ active: selectedCommitHash === null }"
                        @click="selectedCommitHash = null"
                      >
                        <span class="font-monospace">(latest)</span>
                      </button>
                      <button
                        v-for="c in commits"
                        :key="c.hash"
                        type="button"
                        class="list-group-item list-group-item-action py-1 px-2 small"
                        :class="{ active: selectedCommitHash === c.hash }"
                        @click="selectedCommitHash = c.hash"
                      >
                        <span class="font-monospace">{{ c.short_hash }}</span> {{ c.subject }}
                        <span class="text-muted">— {{ getRelativeTime(c.date) }}</span>
                      </button>
                    </div>
                    <div v-if="commitsTruncated" class="text-muted small mt-1">
                      Showing latest 50 commits
                    </div>
                  </template>

                  <div v-if="confirmDisabled && hasCustomSelection && gitStatus.has_uncommitted_changes" class="text-danger small mt-2">
                    Resolve uncommitted changes before switching branch or commit.
                  </div>
                </template>
              </div>
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
            <button type="button" class="btn btn-warning" @click="doRestart" :disabled="confirmDisabled">
              {{ hasCustomSelection ? 'Switch & Restart' : 'Pull & Restart' }}
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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { getGitStatus, getGitBranches, getGitCommits, restartServer } from '@/utils/api'
import { getRelativeTime } from '@/utils/time'

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

// Branch/commit picker state (issue #1760)
const showPicker = ref(false)
const branches = ref([])
const branchesLoading = ref(false)
const selectedBranch = ref(null)
const commits = ref([])
const commitsLoading = ref(false)
const commitsTruncated = ref(false)
const selectedCommitHash = ref(null) // null = "latest on branch"
let pickerLoaded = false
// Bumped on every modal reset so in-flight fetches from a closed/reopened modal can detect
// they're stale and avoid clobbering fresh state (or resurrecting `pickerLoaded`).
let modalGeneration = 0

const hasCustomSelection = computed(() =>
  showPicker.value &&
  gitStatus.value &&
  selectedBranch.value !== null &&
  (selectedBranch.value !== gitStatus.value.branch || selectedCommitHash.value !== null)
)

const confirmDisabled = computed(() =>
  gitLoading.value || (hasCustomSelection.value && gitStatus.value?.has_uncommitted_changes)
)

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

async function fetchBranches() {
  const generation = modalGeneration
  branchesLoading.value = true
  try {
    const result = await getGitBranches()
    if (generation !== modalGeneration) return // modal was reset/closed while this was in flight
    branches.value = result.branches || []
    selectedBranch.value = gitStatus.value?.branch || branches.value.find(b => b.is_current)?.name || null
    if (selectedBranch.value) {
      await fetchCommits(selectedBranch.value)
    }
  } catch (e) {
    if (generation === modalGeneration) gitError.value = e.message || 'Unknown error'
  } finally {
    if (generation === modalGeneration) branchesLoading.value = false
  }
}

async function fetchCommits(branch) {
  const generation = modalGeneration
  commitsLoading.value = true
  selectedCommitHash.value = null
  try {
    const result = await getGitCommits(branch)
    // Stale response guard: ignore results for a branch we've since navigated away from, or a
    // modal instance that's since been reset/closed (issue #1760).
    if (generation !== modalGeneration || branch !== selectedBranch.value) return
    commits.value = result.commits || []
    commitsTruncated.value = !!result.truncated
  } catch (e) {
    if (generation === modalGeneration && branch === selectedBranch.value) {
      gitError.value = e.message || 'Unknown error'
    }
  } finally {
    if (generation === modalGeneration && branch === selectedBranch.value) {
      commitsLoading.value = false
    }
  }
}

async function togglePicker() {
  showPicker.value = !showPicker.value
  if (showPicker.value && !pickerLoaded) {
    const generation = modalGeneration
    await fetchBranches()
    if (generation === modalGeneration) pickerLoaded = true
  }
}

watch(selectedBranch, (branch, oldBranch) => {
  if (branch && branch !== oldBranch && pickerLoaded) {
    fetchCommits(branch)
  }
})

async function doRestart() {
  phase.value = 'progress'
  uiStore.restartInProgress = true
  uiStore.restartStatus = 'pulling'

  try {
    const target = hasCustomSelection.value
      ? { branch: selectedBranch.value, commit: selectedCommitHash.value }
      : undefined
    await restartServer(target)
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
  modalGeneration++
  phase.value = 'confirm'
  gitStatus.value = null
  gitError.value = null
  errorMessage.value = ''
  reconnectCountdown.value = 60
  showPicker.value = false
  branches.value = []
  selectedBranch.value = null
  commits.value = []
  commitsTruncated.value = false
  selectedCommitHash.value = null
  pickerLoaded = false
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
