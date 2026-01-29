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
        <div class="d-flex gap-2">
          <button
            class="btn btn-sm btn-outline-secondary"
            title="Edit project"
            @click="showEditModal"
          >
            ✏️ Edit
          </button>
          <button
            class="btn btn-sm btn-outline-primary"
            :title="hasMinions ? 'Create minion' : 'Add session'"
            @click="showCreateModal"
          >
            ➕ {{ hasMinions ? 'Add Minion' : 'Add Session' }}
          </button>
        </div>
      </div>
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

        <!-- Legion-specific: Quick Actions -->
        <div v-if="hasMinions" class="card mb-4">
          <div class="card-header">
            <h5 class="mb-0">🏛 Legion Controls</h5>
          </div>
          <div class="card-body">
            <div class="d-flex gap-2 flex-wrap">
              <button class="btn btn-outline-primary" @click="viewTimeline">
                📊 View Timeline
              </button>
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
              <div class="user-root-node mb-3 border rounded bg-white">
                <div class="node-row">
                  <!-- Left Column: Status + Name (30%) -->
                  <div class="node-left">
                    <span class="me-2" style="font-size: 1.2rem;">👤</span>
                    <strong>{{ minionHierarchy.name }}</strong>
                    <span class="badge bg-primary ms-2">
                      {{ minionHierarchy.children.length }} {{ minionHierarchy.children.length === 1 ? 'minion' : 'minions' }}
                    </span>
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
                  v-for="minion in minionHierarchy.children"
                  :key="minion.id"
                  :minion-data="minion"
                  :level="1"
                  layout="two-column"
                  @minion-click="navigateToSession"
                />
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
    <div class="project-overview-statusbar border-top p-2 bg-light d-flex align-items-center justify-content-between">
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
import { useWebSocketStore } from '@/stores/websocket'
import { api } from '@/utils/api'
import MinionTreeNode from '../legion/MinionTreeNode.vue'

const props = defineProps({
  projectId: {
    type: String,
    required: true
  }
})

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const legionStore = useLegionStore()
const websocketStore = useWebSocketStore()

const loading = ref(false)
const error = ref(null)
const minionHierarchy = ref(null)
const loadingHierarchy = ref(false)
let sessionWatchStop = null

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

// Helper: Find minion node in hierarchy tree recursively
function findMinionInTree(node, minionId) {
  if (node && node.id === minionId) {
    return node
  }
  if (node && node.children) {
    for (const child of node.children) {
      const found = findMinionInTree(child, minionId)
      if (found) return found
    }
  }
  return null
}

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

// Handle new comm events from Legion WebSocket
function handleNewComm(comm) {
  if (!minionHierarchy.value) return

  // If comm is from a minion, update that minion's last_comm
  if (comm.from_minion_id) {
    const minion = findMinionInTree(minionHierarchy.value, comm.from_minion_id)
    if (minion && minion.type === 'minion') {
      minion.last_comm = comm
    }
  }

  // If comm is from user, update user root node
  if (comm.from_user && minionHierarchy.value.type === 'user') {
    minionHierarchy.value.last_comm = comm
  }
}

// Navigation
function navigateToSession(sessionId) {
  router.push(`/session/${sessionId}`)
}

function viewTimeline() {
  router.push(`/timeline/${props.projectId}`)
}

// Modals
function showEditModal() {
  uiStore.showModal('edit-project', { project: project.value })
}

function showCreateModal() {
  uiStore.showModal('create-minion', { project: project.value })
}

// Select project on mount and load hierarchy
onMounted(() => {
  projectStore.selectProject(props.projectId)
  // Clear session selection when viewing project overview
  sessionStore.currentSessionId = null

  // Load minion hierarchy
  loadMinionHierarchy()

  // Connect to legion WebSocket for comm updates
  legionStore.setCurrentLegion(props.projectId)
  websocketStore.connectLegion(props.projectId)

  // Watch for minion state changes from session store
  sessionWatchStop = watch(
    () => sessionStore.sessions,
    (sessions) => {
      if (!minionHierarchy.value) return

      // Update all minion states, is_processing, and latest_message in hierarchy
      for (const [sessionId, session] of sessions) {
        if (session.project_id === props.projectId) {
          const minion = findMinionInTree(minionHierarchy.value, sessionId)
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

  // Watch for new comms (from Legion WebSocket)
  watch(
    () => legionStore.commsByLegion.get(props.projectId),
    (comms) => {
      if (comms && comms.length > 0) {
        // Get the most recent comm
        const latestComm = comms[comms.length - 1]
        handleNewComm(latestComm)
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
  loadMinionHierarchy()

  // Reconnect legion WebSocket for new project
  legionStore.setCurrentLegion(newId)
  websocketStore.connectLegion(newId)
})

// Cleanup on unmount
onUnmounted(() => {
  if (sessionWatchStop) {
    sessionWatchStop()
  }
})
</script>

<style scoped>
.project-overview {
  background-color: #fff;
}

.project-overview-header {
  background-color: #f8f9fa;
}

.project-overview-content {
  background-color: #fff;
}

.project-overview-statusbar {
  font-size: 0.85rem;
}

.card {
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.list-group-item-action:hover {
  background-color: #f8f9fa;
}

/* Hierarchy tree styles */
.hierarchy-tree {
  background-color: #f8f9fa;
  padding: 0.5rem;
  border-radius: 0.25rem;
}

.user-root-node {
  background-color: #ffffff;
  border-color: #0d6efd !important;
  border-width: 2px !important;
  padding: 0.75rem;
}

/* Two-column layout: 30% left, 70% right */
.node-row {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}

.node-left {
  flex: 0 0 30%;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.node-right {
  flex: 1;
  min-width: 0; /* Allow text truncation */
  padding-left: 1rem;
  border-left: 1px solid #dee2e6;
}

.last-comm-preview {
  font-size: 0.9rem;
  line-height: 1.4;
}

.comm-direction {
  color: #6c757d;
  font-size: 0.85rem;
}

.comm-content {
  color: #212529;
  word-wrap: break-word;
  cursor: help;
}
</style>
