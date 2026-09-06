<template>
  <div
    class="modal fade"
    id="restartModal"
    tabindex="-1"
    aria-labelledby="restartModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered" :class="{ 'modal-lg': isRemoteMode }">
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

            <div v-if="modeLoading" class="text-center py-3">
              <div class="spinner-border spinner-border-sm" role="status"></div>
              <span class="ms-2">Checking deployment mode...</span>
            </div>

            <!-- Embedded mode: unchanged single-target picker (issue #1760) -->
            <template v-else-if="!isRemoteMode">
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
            </template>

            <!-- Remote mode: dual-tier status/targets (issue #1847) -->
            <template v-else>
              <div class="text-muted small mb-2">
                Remote mode: Backend and Frontend restart independently. This can take several minutes.
              </div>

              <div v-if="driftDetected" class="alert alert-warning py-2 small">
                <strong>Drift detected:</strong> Frontend and Backend are on different commits.
              </div>

              <div class="row g-2">
                <div class="col-6">
                  <GitTargetPicker label="Frontend" :state="feTier.state" @toggle-picker="feTier.togglePicker" />
                </div>
                <div class="col-6">
                  <GitTargetPicker label="Backend" :state="beTier.state" @toggle-picker="beTier.togglePicker" />
                </div>
              </div>
            </template>
          </div>

          <!-- Phase 2: Progress -->
          <div v-else-if="phase === 'progress'" class="text-center py-3">
            <div v-if="uiStore.restartStatus === 'pulling'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2" v-if="isRemoteMode">Restarting Backend — this can take several minutes...</div>
              <div class="mt-2" v-else>Pulling code &amp; syncing dependencies...</div>
            </div>
            <div v-else-if="uiStore.restartStatus === 'restarting'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2">Restarting server...</div>
              <div v-if="isRemoteMode && restartResult?.backend" class="text-success small mt-2">
                Backend: {{ restartResult.backend.detail }}
              </div>
            </div>
            <div v-else-if="uiStore.restartStatus === 'reconnecting'" class="mb-3">
              <div class="spinner-border" role="status"></div>
              <div class="mt-2">Waiting for server to come back...</div>
              <div class="text-muted small mt-1">{{ reconnectCountdown }}s remaining</div>
              <div v-if="isRemoteMode && restartResult?.backend" class="text-success small mt-2">
                Backend: {{ restartResult.backend.detail }}
              </div>
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
              {{ hasAnyCustomSelection ? 'Switch & Restart' : 'Pull & Restart' }}
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
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import {
  getGitStatus,
  getGitBranches,
  getGitCommits,
  getFrontendGitStatus,
  getFrontendGitBranches,
  getFrontendGitCommits,
  getFrontendMode,
  restartServer
} from '@/utils/api'
import { getRelativeTime } from '@/utils/time'
import GitTargetPicker from './GitTargetPicker.vue'

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

// Branch/commit picker state (issue #1760) — embedded-mode single target, unchanged.
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

// Remote mode (issue #1847): independent Frontend/Backend targets, each with its own
// status/branches/commits/picker state — factored into one function since the two tiers
// are otherwise an exact duplicate of each other.
function createTier(fetchStatusFn, fetchBranchesFn, fetchCommitsFn) {
  const state = reactive({
    loading: false,
    status: null,
    error: null,
    showPicker: false,
    branches: [],
    branchesLoading: false,
    selectedBranch: null,
    commits: [],
    commitsLoading: false,
    commitsTruncated: false,
    selectedCommitHash: null,
    // Deliberately does NOT require showPicker to stay open (issue #1847 review finding):
    // collapsing the disclosure is a visibility toggle, not a "discard my selection" action —
    // gating on showPicker let a chosen branch/commit silently revert to "pull latest" (and
    // bypass the uncommitted-changes guard below) just by closing the picker before confirming.
    hasCustomSelection: computed(() =>
      state.status &&
      state.selectedBranch !== null &&
      (state.selectedBranch !== state.status.branch || state.selectedCommitHash !== null)
    )
  })
  let tierPickerLoaded = false
  let generation = 0

  async function fetchStatus() {
    const gen = generation
    state.loading = true
    state.error = null
    try {
      const result = await fetchStatusFn()
      if (gen !== generation) return // modal was reset/closed while this was in flight
      state.status = result
    } catch (e) {
      if (gen === generation) state.error = e.message || 'Unknown error'
    } finally {
      if (gen === generation) state.loading = false
    }
  }

  async function fetchCommitsList(branch) {
    const gen = generation
    state.commitsLoading = true
    state.selectedCommitHash = null
    try {
      const result = await fetchCommitsFn(branch)
      if (gen !== generation || branch !== state.selectedBranch) return
      state.commits = result.commits || []
      state.commitsTruncated = !!result.truncated
    } catch (e) {
      if (gen === generation && branch === state.selectedBranch) {
        state.error = e.message || 'Unknown error'
      }
    } finally {
      if (gen === generation && branch === state.selectedBranch) {
        state.commitsLoading = false
      }
    }
  }

  async function fetchBranchesList() {
    const gen = generation
    state.branchesLoading = true
    try {
      const result = await fetchBranchesFn()
      if (gen !== generation) return
      state.branches = result.branches || []
      state.selectedBranch = state.status?.branch || state.branches.find(b => b.is_current)?.name || null
      if (state.selectedBranch) {
        await fetchCommitsList(state.selectedBranch)
      }
    } catch (e) {
      if (gen === generation) state.error = e.message || 'Unknown error'
    } finally {
      if (gen === generation) state.branchesLoading = false
    }
  }

  async function togglePicker() {
    state.showPicker = !state.showPicker
    if (state.showPicker && !tierPickerLoaded) {
      const gen = generation
      await fetchBranchesList()
      if (gen === generation) tierPickerLoaded = true
    }
  }

  watch(() => state.selectedBranch, (branch, oldBranch) => {
    if (branch && branch !== oldBranch && tierPickerLoaded) {
      fetchCommitsList(branch)
    }
  })

  function reset() {
    generation++
    state.loading = false
    state.status = null
    state.error = null
    state.showPicker = false
    state.branches = []
    state.branchesLoading = false
    state.selectedBranch = null
    state.commits = []
    state.commitsLoading = false
    state.commitsTruncated = false
    state.selectedCommitHash = null
    tierPickerLoaded = false
  }

  return { state, fetchStatus, togglePicker, reset }
}

const isRemoteMode = ref(false)
const modeLoading = ref(false)
const restartResult = ref(null)
const feTier = createTier(getFrontendGitStatus, getFrontendGitBranches, getFrontendGitCommits)
const beTier = createTier(getGitStatus, getGitBranches, getGitCommits)

const driftDetected = computed(() => {
  const fe = feTier.state.status
  const be = beTier.state.status
  if (!fe || !be) return false
  return !!(fe.last_commit_hash && be.last_commit_hash && fe.last_commit_hash !== be.last_commit_hash)
})

const hasAnyCustomSelection = computed(() =>
  isRemoteMode.value
    ? (feTier.state.hasCustomSelection || beTier.state.hasCustomSelection)
    : hasCustomSelection.value
)

const confirmDisabled = computed(() => {
  // Issue #1847 review finding: this must gate regardless of which branch below runs —
  // isRemoteMode still holds its resetState() default while modeLoading is true, so nesting
  // this check only inside the isRemoteMode branch let the confirm button stay clickable
  // during the mode-detection round-trip (before we even know which restart path applies).
  if (modeLoading.value) return true
  if (isRemoteMode.value) {
    return (
      feTier.state.loading ||
      beTier.state.loading ||
      (feTier.state.hasCustomSelection && feTier.state.status?.has_uncommitted_changes) ||
      (beTier.state.hasCustomSelection && beTier.state.status?.has_uncommitted_changes)
    )
  }
  return gitLoading.value || (hasCustomSelection.value && gitStatus.value?.has_uncommitted_changes)
})

async function detectMode() {
  modeLoading.value = true
  try {
    // Issue #1847 review finding: the authoritative signal for which restart path the server
    // will actually take is whether Backend was auto-started (webui.backend_supervisor is
    // None means remote), not config.json's persisted backend_connection.remote_backend_url —
    // a Frontend launched with --remote-backend-url/--remote-backend-token CLI flags only
    // (a supported path per main.py) never persists that to config.json, so reading the merged
    // /api/config here would silently misreport such a deployment as embedded mode. A
    // dedicated endpoint reflects the live wiring directly instead of reconstructing it.
    const data = await getFrontendMode()
    isRemoteMode.value = !!data?.remote_mode
  } catch {
    // Fail closed to embedded mode's simpler single-picker UX rather than guessing.
    isRemoteMode.value = false
  } finally {
    modeLoading.value = false
  }
}

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
  restartResult.value = null

  try {
    if (isRemoteMode.value) {
      const frontendTarget = feTier.state.hasCustomSelection
        ? { branch: feTier.state.selectedBranch, commit: feTier.state.selectedCommitHash }
        : undefined
      const backendTarget = beTier.state.hasCustomSelection
        ? { branch: beTier.state.selectedBranch, commit: beTier.state.selectedCommitHash }
        : undefined
      restartResult.value = await restartServer(frontendTarget, backendTarget)
    } else {
      const target = hasCustomSelection.value
        ? { branch: selectedBranch.value, commit: selectedCommitHash.value }
        : undefined
      await restartServer(target)
    }
    uiStore.restartStatus = 'restarting'
    // Server will go down shortly, start polling for health
    startHealthPoll()
  } catch (e) {
    uiStore.restartStatus = 'error'
    // Issue #1847: in remote mode, discriminate "Backend itself failed/was unreachable"
    // (502 — Frontend never touched, per _restart_remote_backend's contract) from
    // "Backend already moved to its new target, but Frontend's own restart then failed"
    // (409/500/504 — only reachable after Backend already responded successfully). A
    // plain ref-validation 400 returns before Backend is ever touched, so it falls
    // through to the generic message like embedded mode's failures do.
    if (isRemoteMode.value && e.status === 502) {
      errorMessage.value = e.data?.detail || e.message || 'Backend restart failed'
    } else if (isRemoteMode.value && [409, 500, 504].includes(e.status)) {
      errorMessage.value =
        `Backend already restarted to its new target, but Frontend's own restart failed: ${e.data?.detail || e.message}`
    } else {
      errorMessage.value = e.message || 'Restart failed'
    }
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

  isRemoteMode.value = false
  modeLoading.value = false
  restartResult.value = null
  feTier.reset()
  beTier.reset()
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
  async (modal) => {
    if (modal?.name === 'restart-server' && modalInstance) {
      resetState()
      modalInstance.show()
      await detectMode()
      if (isRemoteMode.value) {
        feTier.fetchStatus()
        beTier.fetchStatus()
      } else {
        fetchGitStatus()
      }
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
