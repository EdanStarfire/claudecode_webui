<template>
  <div class="project-overview d-flex flex-column h-100">
    <!-- Header -->
    <div class="project-overview-header border-bottom p-3">
      <div class="d-flex align-items-center justify-content-between">
        <div>
          <h4 class="mb-1">
            <span v-if="hasMinions" class="me-2">🏛</span>
            {{ project?.name || 'Project Overview' }}
          </h4>
          <small class="text-muted font-monospace">{{ project?.working_directory }}</small>
        </div>
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <button
            class="btn btn-sm btn-outline-secondary"
            title="Edit project"
            aria-label="Edit project"
            @click="showEditModal"
          >
            ✏️ Edit
          </button>
          <button
            class="btn btn-sm btn-outline-primary"
            :title="hasMinions ? 'Create minion' : 'Add session'"
            :aria-label="hasMinions ? 'Add minion' : 'Add session'"
            @click="showCreateModal"
          >
            ➕ {{ hasMinions ? 'Add Minion' : 'Add Session' }}
          </button>

          <span class="vr mx-1"></span>

          <!-- Stop All: default state -->
          <button
            v-if="stopState === 'default'"
            class="btn btn-sm btn-outline-danger"
            :disabled="projectSessions.length === 0"
            :title="projectSessions.length === 0 ? 'No sessions to stop' : 'Stop all sessions'"
            @click="beginStopConfirm"
          >
            ⏹ Stop All
          </button>

          <!-- Stop All: inline confirmation (no auto-cancel timer) -->
          <div
            v-else-if="stopState === 'confirming'"
            class="d-inline-flex align-items-center gap-1 border border-danger rounded px-2 py-1"
            style="background: rgba(var(--bs-danger-rgb), 0.12);"
          >
            <span class="text-danger small">⚠️ Stop {{ projectSessions.length }} session{{ projectSessions.length !== 1 ? 's' : '' }}?</span>
            <button class="btn btn-sm btn-outline-secondary py-0" @click="cancelStop">Cancel</button>
            <button class="btn btn-sm btn-danger py-0" @click="confirmStop">Confirm Stop All</button>
          </div>

          <!-- Stop All: stopping in progress -->
          <button
            v-else-if="stopState === 'stopping'"
            class="btn btn-sm btn-danger"
            disabled
          >
            <span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
            Stopping…
          </button>

          <!-- Resume Sessions -->
          <button
            class="btn btn-sm btn-outline-warning"
            :disabled="stoppedCount === 0 || stopState === 'stopping' || resuming"
            :title="stoppedCount === 0 ? 'No stopped sessions to resume' : `Resume ${stoppedCount} stopped session${stoppedCount !== 1 ? 's' : ''}`"
            @click="resumeSessions"
          >
            <span v-if="resuming" class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
            <span v-else>↻</span>
            {{ resuming ? 'Resuming…' : 'Resume Sessions' }}
            <span
              v-if="stoppedCount > 0 && !resuming"
              class="badge bg-warning text-dark ms-1"
            >{{ stoppedCount }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Fleet operation toast -->
    <div
      v-if="fleetToast"
      class="alert mb-0 py-2 px-3 rounded-0 border-start-0 border-end-0"
      :class="`alert-${fleetToast.type}`"
      role="alert"
    >
      {{ fleetToast.message }}
    </div>

    <!-- Content area -->
    <div class="project-overview-content flex-grow-1 overflow-auto p-3">
      <!-- Loading state -->
      <div v-if="loading" class="text-center py-5">
        <div class="spinner-border" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        <p class="text-muted mt-2">Loading project data...</p>
      </div>

      <!-- Error state -->
      <div v-else-if="error" class="alert alert-danger">
        {{ error }}
      </div>

      <!-- Project not found -->
      <div v-else-if="!project" class="text-center py-5">
        <h5 class="text-muted">Project not found</h5>
        <p class="text-secondary">The requested project does not exist.</p>
      </div>

      <!-- Project content -->
      <div v-else>
        <!-- Quick Stats Row -->
        <div class="row g-3 mb-4">
          <div class="col-md-4">
            <div class="card h-100">
              <div class="card-body">
                <h6 class="card-subtitle mb-2 text-muted">Sessions</h6>
                <h3 class="card-title mb-0">{{ sessionCount }}</h3>
                <small class="text-muted">{{ activeSessionCount }} active</small>
              </div>
            </div>
          </div>
          <div class="col-md-4">
            <div class="card h-100">
              <div class="card-body">
                <h6 class="card-subtitle mb-2 text-muted">Status</h6>
                <h3 class="card-title mb-0">
                  <span :class="overallStatusClass">{{ overallStatus }}</span>
                </h3>
                <small class="text-muted">{{ statusDescription }}</small>
              </div>
            </div>
          </div>
        </div>

        <!-- Minion Hierarchy (two-column layout) -->
        <div class="card mb-4">
          <div class="card-header d-flex align-items-center justify-content-between">
            <h5 class="mb-0">🌳 {{ hasMinions ? 'Minion Hierarchy' : 'Sessions' }}</h5>
            <span class="badge bg-secondary">{{ projectSessions.length }}</span>
          </div>
          <div class="card-body">
            <!-- Control row: view mode toggle + sort buttons + group-by-state -->
            <div class="d-flex flex-wrap justify-content-end gap-2 mb-2 me-4">
              <!-- View mode toggle -->
              <div class="btn-group btn-group-sm" role="group" aria-label="View mode">
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.projectViewMode === 'hierarchy' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setProjectViewMode('hierarchy')"
                  title="Hierarchy view"
                >🌳 Hierarchy</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.projectViewMode === 'flat' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setProjectViewMode('flat')"
                  title="Flat list view"
                >☰ Flat</button>
              </div>

              <!-- Hierarchy sort toggle (visible only in hierarchy mode) -->
              <div
                v-if="uiStore.projectViewMode === 'hierarchy'"
                class="btn-group btn-group-sm"
                role="group"
                aria-label="Hierarchy sort order"
              >
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.agentSort === 'alpha' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setAgentSort('alpha')"
                  title="Sort alphabetically"
                >A–Z</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.agentSort === 'creation' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setAgentSort('creation')"
                  title="Sort by creation order"
                >Created</button>
              </div>

              <!-- Flat sort toggle (visible only in flat mode) -->
              <div
                v-else
                class="btn-group btn-group-sm"
                role="group"
                aria-label="Flat list sort order"
              >
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatSort === 'alpha' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatSort('alpha')"
                  title="Sort alphabetically"
                >A–Z</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatSort === 'creation' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatSort('creation')"
                  title="Sort by creation order"
                >Created</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatSort === 'last_active' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatSort('last_active')"
                  title="Sort by most recent completion"
                >Last Active</button>
              </div>

              <!-- Group by state (flat mode only) -->
              <div v-if="uiStore.projectViewMode === 'flat'" class="form-check form-switch d-flex align-items-center mb-0 ms-2">
                <input
                  id="group-by-state-toggle"
                  type="checkbox"
                  role="switch"
                  class="form-check-input me-2"
                  :checked="uiStore.groupByState"
                  @change="uiStore.setGroupByState($event.target.checked)"
                >
                <label for="group-by-state-toggle" class="form-check-label small">Group by state</label>
              </div>
            </div>

            <!-- Hierarchy view -->
            <template v-if="uiStore.projectViewMode === 'hierarchy'">
              <!-- Loading hierarchy -->
              <div v-if="loadingHierarchy" class="text-center py-4">
                <div class="spinner-border spinner-border-sm" role="status">
                  <span class="visually-hidden">Loading...</span>
                </div>
                <p class="text-muted mt-2 mb-0">Loading hierarchy...</p>
              </div>

              <!-- Empty state -->
              <div v-else-if="!minionHierarchy || !minionHierarchy.children || minionHierarchy.children.length === 0" class="text-center text-muted py-4">
                No sessions yet. Click "Add Session" to create one.
              </div>

              <!-- Hierarchy tree with User root node -->
              <div v-else class="hierarchy-tree">
                <!-- User Root Node -->
                <div class="user-root-node mb-3 border rounded">
                  <div class="node-row">
                    <!-- Left Column: Status + Name (30%) -->
                    <div class="node-left">
                      <span class="me-2" style="font-size: 1.2rem;">👤</span>
                      <div class="node-text">
                        <div class="node-name-row">
                          <strong class="node-name">{{ minionHierarchy.name }}</strong>
                          <span class="badge bg-primary ms-2">
                            {{ minionHierarchy.children.length }} {{ minionHierarchy.children.length === 1 ? 'minion' : 'minions' }}
                          </span>
                        </div>
                      </div>
                    </div>

                    <!-- Right Column: Last Comm (70%) -->
                    <div class="node-right">
                      <div v-if="minionHierarchy.last_comm" class="last-comm-preview">
                        <span class="comm-direction">
                          → <strong>{{ getCommRecipient(minionHierarchy.last_comm) }}</strong>:
                        </span>
                        <span
                          class="comm-content"
                          :title="minionHierarchy.last_comm.content || ''"
                        >
                          {{ getCommSummary(minionHierarchy.last_comm) }}
                        </span>
                      </div>
                      <div v-else class="text-muted fst-italic">
                        No communications yet
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Root Minions (user-spawned) -->
                <div class="root-minions ms-4">
                  <MinionTreeNode
                    v-for="minion in sortedRootMinions"
                    :key="minion.id"
                    :minion-data="minion"
                    :level="1"
                    layout="two-column"
                    @minion-click="navigateToSession"
                  />
                </div>
              </div>
            </template>

            <!-- Flat list view -->
            <div v-else class="flat-list">
              <!-- Ungrouped -->
              <div v-if="!uiStore.groupByState" class="flat-list-nodes">
                <MinionTreeNode
                  v-for="node in sortedFlatNodes"
                  :key="node.id"
                  :minion-data="node"
                  :level="0"
                  layout="two-column"
                  @minion-click="navigateToSession"
                />
              </div>

              <!-- Grouped (action-priority: unread → prompting → error → active → idle → terminated) -->
              <div v-else>
                <div v-for="group in groupedFlatNodes" :key="group.key" class="flat-group mb-3">
                  <h6 class="flat-group-heading text-muted">
                    {{ group.label }}
                    <span class="badge bg-secondary ms-2">{{ group.nodes.length }}</span>
                  </h6>
                  <MinionTreeNode
                    v-for="node in group.nodes"
                    :key="node.id"
                    :minion-data="node"
                    :level="0"
                    layout="two-column"
                    @minion-click="navigateToSession"
                  />
                </div>
              </div>

              <!-- Empty state -->
              <div v-if="sortedFlatNodes.length === 0" class="text-center text-muted py-4">
                No sessions yet. Click "Add Session" to create one.
              </div>
            </div>
          </div>
        </div>

        <!-- Configuration -->
        <div class="card">
          <div class="card-header">
            <h5 class="mb-0">Configuration</h5>
          </div>
          <div class="card-body">
            <dl class="row mb-0">
              <dt class="col-sm-4">Working Directory</dt>
              <dd class="col-sm-8 font-monospace text-break">{{ project.working_directory }}</dd>

              <dt class="col-sm-4">Project ID</dt>
              <dd class="col-sm-8 font-monospace text-break">{{ project.project_id }}</dd>

              <dt class="col-sm-4">Legion Mode</dt>
              <dd class="col-sm-8">{{ hasMinions ? 'Yes (has minions)' : 'No' }}</dd>
            </dl>
          </div>
        </div>
      </div>
    </div>

    <!-- Status bar -->
    <div class="project-overview-statusbar border-top p-2 bg-body-tertiary d-flex align-items-center justify-content-between">
      <small class="text-muted">
        Project Overview
      </small>
      <small class="text-muted">
        {{ sessionCount }} session{{ sessionCount !== 1 ? 's' : '' }}
      </small>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useLegionStore } from '@/stores/legion'
import { useQueueStore } from '@/stores/queue'
import { compareAgents, normalizeLastActive } from '@/utils/agentSort'
import { getDisplayState } from '@/composables/useSessionState'
import { api } from '@/utils/api'
import { findInHierarchy } from '@/utils/hierarchyUtils'
import { getStoppedSet, setStoppedSet, addToStoppedSet, clearStoppedSet, pruneStoppedSet, removeFromStoppedSet } from '@/utils/stoppedSet'
import MinionTreeNode from '../legion/MinionTreeNode.vue'

const props = defineProps({
  projectId: {
    type: String,
    required: true
  }
})

const RESUME_MESSAGE = "Your session was stopped using an emergency stop feature, and is now being allowed to resume. If you had anything you were mid-progress on, continue it now. Otherwise, you are back online and ready to help."

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const legionStore = useLegionStore()
const queueStore = useQueueStore()

const loading = ref(false)
const error = ref(null)
const minionHierarchy = ref(null)
const loadingHierarchy = ref(false)
let sessionWatchStop = null

// Fleet control state
const stopState = ref('default') // 'default' | 'confirming' | 'stopping'
const resuming = ref(false)
const stoppedCount = ref(0)
const fleetToast = ref(null)
let fleetToastTimer = null

function setFleetToast(type, message, autoDismissMs = 8000) {
  fleetToast.value = { type, message }
  clearTimeout(fleetToastTimer)
  if (autoDismissMs) {
    fleetToastTimer = setTimeout(() => { fleetToast.value = null }, autoDismissMs)
  }
}

function refreshStoppedCount() {
  const allIds = projectSessions.value.map(s => s.session_id)
  const pruned = pruneStoppedSet(props.projectId, allIds)
  stoppedCount.value = pruned.length
}

function releaseStartedSessions() {
  const current = getStoppedSet(props.projectId)
  if (current.length === 0) return
  const started = current.filter(id => {
    const s = sessionStore.getSession(id)
    return s?.state?.toUpperCase() === 'ACTIVE'
  })
  if (started.length === 0) return
  removeFromStoppedSet(props.projectId, started)
  refreshStoppedCount()
}

function beginStopConfirm() {
  stopState.value = 'confirming'
  fleetToast.value = null
}

function cancelStop() {
  stopState.value = 'default'
}

async function confirmStop() {
  stopState.value = 'stopping'
  try {
    const result = await legionStore.haltAll(props.projectId)
    const stopped = result.stopped_session_ids ?? []
    const failed = result.failed_sessions ?? []

    addToStoppedSet(props.projectId, stopped)
    refreshStoppedCount()

    if (failed.length === 0) {
      setFleetToast('success', `✓ Stopped ${stopped.length} session${stopped.length !== 1 ? 's' : ''}. Click "Resume Sessions" to bring them back online.`)
    } else {
      const failedNames = failed.map(([id, err]) => {
        const s = sessionStore.getSession(id)
        return `${s?.name || id} (${err})`
      }).join(', ')
      setFleetToast('danger', `✗ Stopped ${stopped.length} of ${result.total_sessions} sessions. Failed: ${failedNames}. You can retry Stop All for remaining sessions.`, 0)
    }
  } catch (err) {
    setFleetToast('danger', `✗ Stop All failed: ${err.message || err}`, 0)
  } finally {
    stopState.value = 'default'
  }
}

async function resumeSessions() {
  if (resuming.value || stoppedCount.value === 0) return
  resuming.value = true
  fleetToast.value = null
  try {
    const allIds = projectSessions.value.map(s => s.session_id)
    const toResume = pruneStoppedSet(props.projectId, allIds).filter(id => {
      const s = sessionStore.getSession(id)
      if (!s) return false
      const state = s.state?.toUpperCase?.() || s.state
      return state !== 'ACTIVE' && state !== 'STARTING'
    })

    const settled = await Promise.allSettled(toResume.map(id => queueStore.enqueueMessage(id, RESUME_MESSAGE, false)))
    const succeeded = toResume.filter((_, i) => settled[i].status === 'fulfilled')
    const failed = toResume.filter((_, i) => settled[i].status === 'rejected')

    // Clear successfully queued sessions from the stopped set so retries don't double-enqueue
    const remaining = getStoppedSet(props.projectId).filter(id => failed.includes(id))
    setStoppedSet(props.projectId, remaining)
    refreshStoppedCount()

    if (failed.length === 0) {
      setFleetToast('success', `✓ Resume message queued to ${succeeded.length} session${succeeded.length !== 1 ? 's' : ''}. They will restart shortly.`)
    } else {
      setFleetToast('danger', `✗ Queued ${succeeded.length} sessions; ${failed.length} failed. You can retry Resume for the remaining sessions.`, 0)
    }
  } catch (err) {
    setFleetToast('danger', `✗ Resume failed: ${err.message || err}`, 0)
  } finally {
    resuming.value = false
  }
}

// Get project data
const project = computed(() => projectStore.getProject(props.projectId))

// Get sessions for this project
const projectSessions = computed(() => {
  if (!project.value?.session_ids) return []
  return project.value.session_ids
    .map(id => sessionStore.getSession(id))
    .filter(s => s !== null && s !== undefined)
})

// Issue #349: All sessions are minions - check if project has any sessions
const hasMinions = computed(() =>
  projectSessions.value.length > 0
)

// Stats
const sessionCount = computed(() => projectSessions.value.length)

const activeSessionCount = computed(() =>
  projectSessions.value.filter(s => s.state === 'ACTIVE').length
)

// Overall status
const overallStatus = computed(() => {
  const hasActive = projectSessions.value.some(s => s.state === 'ACTIVE')
  const hasProcessing = projectSessions.value.some(s => s.is_processing)
  const hasError = projectSessions.value.some(s => s.state === 'ERROR')

  if (hasError) return 'Error'
  if (hasProcessing) return 'Working'
  if (hasActive) return 'Active'
  if (sessionCount.value > 0) return 'Idle'
  return 'Empty'
})

const overallStatusClass = computed(() => {
  switch (overallStatus.value) {
    case 'Error': return 'text-danger'
    case 'Working': return 'text-warning'
    case 'Active': return 'text-success'
    case 'Idle': return 'text-muted'
    default: return 'text-secondary'
  }
})

const statusDescription = computed(() => {
  switch (overallStatus.value) {
    case 'Error': return 'One or more sessions have errors'
    case 'Working': return 'Processing in progress'
    case 'Active': return 'Sessions are running'
    case 'Idle': return 'No active processing'
    default: return 'No sessions created'
  }
})

// Load minion hierarchy from API
async function loadMinionHierarchy() {
  loadingHierarchy.value = true
  try {
    const response = await api.get(`/api/legions/${props.projectId}/hierarchy`)
    minionHierarchy.value = response
  } catch (err) {
    console.error('Failed to load minion hierarchy:', err)
    minionHierarchy.value = null
  } finally {
    loadingHierarchy.value = false
  }
}

const sortedRootMinions = computed(() => {
  const children = minionHierarchy.value?.children || []
  const mode = uiStore.agentSort
  return [...children].sort((a, b) => compareAgents(mode, a, b, {
    nameOf: n => n.name,
    orderOf: n => sessionStore.getSession(n.id)?.order,
    idOf: n => n.id
  }))
})

const flatNodes = computed(() =>
  projectSessions.value.map(s => ({
    id: s.session_id,
    name: s.name || s.session_id,
    type: 'minion',
    state: s.state,
    is_processing: s.is_processing,
    latest_message: s.latest_message,
    latest_message_type: s.latest_message_type,
    latest_message_time: s.latest_message_time,
    children: [],
  }))
)

const sortedFlatNodes = computed(() => {
  const nodes = flatNodes.value
  const mode = uiStore.flatSort
  return [...nodes].sort((a, b) => compareAgents(mode, a, b, {
    nameOf: n => n.name,
    orderOf: n => sessionStore.getSession(n.id)?.order,
    idOf: n => n.id,
    lastActiveOf: n => normalizeLastActive(sessionStore.getSession(n.id)?.last_completion_at),
  }))
})

const STATE_GROUPS = [
  { key: 'unread',     label: 'Unread' },
  { key: 'prompting',  label: 'Prompting' },
  { key: 'error',      label: 'Error' },
  { key: 'active',     label: 'Active' },
  { key: 'idle',       label: 'Idle' },
  { key: 'terminated', label: 'Terminated' },
]

function classifyNode(node) {
  if (sessionStore.isUnreviewed(node.id)) return 'unread'
  const live = sessionStore.getSession(node.id)
  const ds = getDisplayState(live || node)
  if (ds === 'pending-prompt') return 'prompting'
  if (ds === 'error' || ds === 'failed') return 'error'
  if (ds === 'active' || ds === 'starting' || ds === 'processing' || ds === 'running') return 'active'
  if (ds === 'created' || ds === 'paused') return 'idle'
  if (ds === 'terminated') return 'terminated'
  return 'idle'
}

const groupedFlatNodes = computed(() => {
  const sorted = sortedFlatNodes.value
  const buckets = STATE_GROUPS.map(g => ({ ...g, nodes: [] }))
  const byKey = Object.fromEntries(buckets.map(b => [b.key, b]))
  for (const node of sorted) {
    byKey[classifyNode(node)].nodes.push(node)
  }
  return buckets.filter(b => b.nodes.length > 0)
})

// Helper: Get comm recipient name
function getCommRecipient(comm) {
  if (comm.to_user) {
    return 'User'
  } else if (comm.to_minion_name) {
    return comm.to_minion_name
  }
  return 'unknown'
}

// Helper: Get comm summary (prioritize summary, fallback to content, truncate at 150 chars)
function getCommSummary(comm) {
  const text = comm.summary || comm.content || ''
  if (text.length > 150) {
    return text.substring(0, 150) + '...'
  }
  return text
}

// Navigation
function navigateToSession(sessionId) {
  router.push(`/session/${sessionId}`)
}

// Modals
function showEditModal() {
  uiStore.showModal('edit-project', { project: project.value })
}

function showCreateModal() {
  if (project.value) {
    router.push(`/settings/session/__new__/general?project_id=${project.value.project_id}`)
  }
}

// Select project on mount and load hierarchy
onMounted(() => {
  projectStore.selectProject(props.projectId)
  // Clear session selection when viewing project overview
  sessionStore.currentSessionId = null

  // Restore stopped set from sessionStorage — read raw count without pruning,
  // since sessions may not be loaded yet when onMounted fires.
  // Pruning happens in refreshStoppedCount() after stop/resume actions.
  const restored = getStoppedSet(props.projectId)
  stoppedCount.value = restored.length
  releaseStartedSessions()
  if (stoppedCount.value > 0) {
    setFleetToast('info', `ⓘ Stop All from earlier still pending — Resume is available for ${stoppedCount.value} session${stoppedCount.value !== 1 ? 's' : ''}.`, 0)
  }

  // Load minion hierarchy
  loadMinionHierarchy()

  // Watch for minion state changes from session store
  sessionWatchStop = watch(
    () => sessionStore.sessions,
    (sessions) => {
      releaseStartedSessions()

      if (!minionHierarchy.value) return

      // Update all minion states, is_processing, and latest_message in hierarchy
      for (const [sessionId, session] of sessions) {
        if (session.project_id === props.projectId) {
          const minion = findInHierarchy(minionHierarchy.value, n => n.id === sessionId)
          if (minion && minion.type === 'minion') {
            // Update state if changed
            if (minion.state !== session.state) {
              minion.state = session.state
            }
            // Update is_processing if changed
            if (minion.is_processing !== session.is_processing) {
              minion.is_processing = session.is_processing
            }
            // Update latest_message fields if changed
            if (minion.latest_message !== session.latest_message) {
              minion.latest_message = session.latest_message
              minion.latest_message_type = session.latest_message_type
              minion.latest_message_time = session.latest_message_time
            }
          }
        }
      }
    },
    { deep: true }
  )

  // Watch for minions being created or deleted (reload hierarchy)
  watch(
    () => {
      const proj = projectStore.projects.get(props.projectId)
      return proj?.session_ids?.length || 0
    },
    (newLength, oldLength) => {
      if (newLength !== oldLength) {
        loadMinionHierarchy()
      }
    }
  )
})

// Update selection when projectId changes
watch(() => props.projectId, (newId) => {
  projectStore.selectProject(newId)
  sessionStore.currentSessionId = null
  stopState.value = 'default'
  resuming.value = false
  fleetToast.value = null
  clearTimeout(fleetToastTimer)
  const restored = getStoppedSet(newId)
  stoppedCount.value = restored.length
  releaseStartedSessions()
  if (stoppedCount.value > 0) {
    setFleetToast('info', `ⓘ Stop All from earlier still pending — Resume is available for ${stoppedCount.value} session${stoppedCount.value !== 1 ? 's' : ''}.`, 0)
  }
  loadMinionHierarchy()
})

// Cleanup on unmount
onUnmounted(() => {
  if (sessionWatchStop) {
    sessionWatchStop()
  }
  clearTimeout(fleetToastTimer)
})
</script>

<style scoped>
.project-overview {
  background-color: var(--bs-body-bg);
}

.project-overview-header {
  background-color: var(--bs-secondary-bg);
}

.project-overview-content {
  background-color: var(--bs-body-bg);
}

.project-overview-statusbar {
  font-size: 0.85rem;
}

.card {
  box-shadow: 0 1px 3px rgba(var(--bs-emphasis-color-rgb, 0, 0, 0), 0.1);
}

.list-group-item-action:hover {
  background-color: var(--bs-secondary-bg);
}

/* Hierarchy tree styles */
.hierarchy-tree {
  background-color: var(--bs-secondary-bg);
  padding: 0.5rem;
  border-radius: 0.25rem;
}

.user-root-node {
  background-color: var(--bs-body-bg);
  border-color: var(--bs-link-color) !important;
  border-width: 2px !important;
  padding: 0;
  margin-bottom: 0.5rem;
}

.user-root-node .node-row {
  padding: 0.4rem 0.75rem;
  min-height: 44px;
  align-items: center;
}

.user-root-node .last-comm-preview,
.user-root-node .comm-content {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  word-wrap: normal;
}

/* Two-column layout: 30% left, 70% right */
.node-row {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.node-left {
  flex: 0 0 30%;
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 0.25rem;
  min-width: 0;
}

.node-text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  flex: 1;
  line-height: 1.15;
}

.node-name-row {
  display: flex;
  align-items: center;
  min-width: 0;
}

.node-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.node-right {
  flex: 1;
  min-width: 0;
  padding-left: 1rem;
  border-left: 1px solid var(--bs-border-color);
}

.last-comm-preview {
  font-size: 0.85rem;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}

.comm-direction {
  color: var(--bs-secondary-color);
  font-size: 0.85rem;
}

.comm-content {
  color: var(--bs-body-color);
  word-wrap: normal;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: help;
}

.flat-list {
  background-color: var(--bs-secondary-bg);
  padding: 0.5rem;
  border-radius: 0.25rem;
}

.flat-group-heading {
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 0.25rem 0.5rem;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid var(--bs-border-color);
}
</style>
