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
          <div v-if="hasMinions" class="col-md-4">
            <div class="card h-100">
              <div class="card-body">
                <h6 class="card-subtitle mb-2 text-muted">Minions</h6>
                <h3 class="card-title mb-0">{{ minionCount }}</h3>
                <small class="text-muted">{{ activeMinionCount }} active</small>
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
              <button class="btn btn-outline-primary" @click="viewHierarchy">
                🌳 View Hierarchy
              </button>
            </div>
          </div>
        </div>

        <!-- Sessions List -->
        <div class="card mb-4">
          <div class="card-header d-flex align-items-center justify-content-between">
            <h5 class="mb-0">{{ hasMinions ? 'Sessions & Minions' : 'Sessions' }}</h5>
            <span class="badge bg-secondary">{{ projectSessions.length }}</span>
          </div>
          <div class="card-body p-0">
            <div v-if="projectSessions.length === 0" class="text-center text-muted py-4">
              No sessions yet. Click "Add Session" to create one.
            </div>
            <div v-else class="list-group list-group-flush">
              <div
                v-for="session in projectSessions"
                :key="session.session_id"
                class="list-group-item list-group-item-action d-flex align-items-center"
                style="cursor: pointer"
                @click="navigateToSession(session.session_id)"
              >
                <div class="flex-grow-1">
                  <div class="d-flex align-items-center">
                    <span v-if="session.is_minion" class="me-2">🤖</span>
                    <span v-else class="me-2">💬</span>
                    <strong>{{ session.name || 'Unnamed Session' }}</strong>
                    <span
                      class="badge ms-2"
                      :class="getSessionStateBadgeClass(session.state)"
                    >
                      {{ session.state }}
                    </span>
                    <span
                      v-if="session.is_processing"
                      class="badge bg-warning ms-1"
                    >
                      Processing
                    </span>
                  </div>
                  <small class="text-muted">
                    {{ session.role || session.current_permission_mode || 'default' }}
                  </small>
                </div>
                <span class="text-muted">→</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Recent Activity (placeholder for future) -->
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
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

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

const loading = ref(false)
const error = ref(null)

// Get project data
const project = computed(() => projectStore.getProject(props.projectId))

// Get sessions for this project
const projectSessions = computed(() => {
  if (!project.value?.session_ids) return []
  return project.value.session_ids
    .map(id => sessionStore.getSession(id))
    .filter(s => s !== null && s !== undefined)
})

// Check if project has minions
const hasMinions = computed(() =>
  projectSessions.value.some(s => s.is_minion)
)

// Stats
const sessionCount = computed(() => projectSessions.value.length)

const activeSessionCount = computed(() =>
  projectSessions.value.filter(s => s.state === 'ACTIVE').length
)

const minionCount = computed(() =>
  projectSessions.value.filter(s => s.is_minion).length
)

const activeMinionCount = computed(() =>
  projectSessions.value.filter(s => s.is_minion && s.state === 'ACTIVE').length
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

// Session state badge class
function getSessionStateBadgeClass(state) {
  switch (state) {
    case 'ACTIVE': return 'bg-success'
    case 'CREATED': return 'bg-secondary'
    case 'STARTING': return 'bg-info'
    case 'PAUSED': return 'bg-warning'
    case 'TERMINATED': return 'bg-dark'
    case 'ERROR': return 'bg-danger'
    default: return 'bg-secondary'
  }
}

// Navigation
function navigateToSession(sessionId) {
  router.push(`/session/${sessionId}`)
}

function viewTimeline() {
  router.push(`/timeline/${props.projectId}`)
}

function viewHierarchy() {
  router.push(`/hierarchy/${props.projectId}`)
}

// Modals
function showEditModal() {
  uiStore.showModal('edit-project', { project: project.value })
}

function showCreateModal() {
  uiStore.showModal('create-minion', { project: project.value })
}

// Select project on mount
onMounted(() => {
  projectStore.selectProject(props.projectId)
  // Clear session selection when viewing project overview
  sessionStore.currentSessionId = null
})

// Update selection when projectId changes
watch(() => props.projectId, (newId) => {
  projectStore.selectProject(newId)
  sessionStore.currentSessionId = null
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
</style>
