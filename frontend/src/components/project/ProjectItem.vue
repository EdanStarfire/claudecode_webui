<template>
  <div
    class="accordion-item border rounded mb-2"
    :data-project-id="project.project_id"
    draggable="true"
    @dragstart="onDragStart"
    @dragend="onDragEnd"
    @dragover="onDragOver"
    @drop="onDrop"
  >
    <!-- Accordion Header -->
    <h2 class="accordion-header" :id="`heading-${project.project_id}`">
      <!-- Accordion Button (changed to div to allow nested buttons) -->
      <div
        class="accordion-button bg-white p-2"
        :class="{ collapsed: !isExpanded }"
        role="button"
        tabindex="0"
        :aria-expanded="isExpanded"
        :aria-controls="`collapse-${project.project_id}`"
        @click="onAccordionClick"
        @keydown.enter.prevent="onAccordionClick"
        @keydown.space.prevent="onAccordionClick"
      >
        <div class="flex-grow-1 d-flex flex-column" style="min-width: 0;">
          <!-- Top row: Project name AND action buttons -->
          <div class="d-flex align-items-center mb-1" style="gap: 0.5rem;">
            <div class="fw-semibold" style="flex-shrink: 0;">
              <span v-if="project.is_multi_agent" class="legion-icon" style="font-size: 1rem; margin-right: 0.25rem;">🏛</span>
              {{ project.name }}
            </div>

            <!-- Action Buttons Container -->
            <div class="d-flex gap-1 ms-auto" style="flex-shrink: 0;">
              <!-- Edit Project Button -->
              <button
                class="btn btn-sm btn-outline-secondary"
                title="Edit or delete project"
                type="button"
                @click.stop="showEditModal"
              >
                ✏️
              </button>

              <!-- Add Session/Minion Button -->
              <button
                class="btn btn-sm btn-outline-primary"
                :title="project.is_multi_agent ? 'Create minion' : 'Add session to project'"
                type="button"
                @click.stop="showCreateSessionModal"
              >
                ➕
              </button>
            </div>
          </div>

          <!-- Bottom row: Folder path (with ellipsis overflow) -->
          <small
            class="text-muted font-monospace"
            :title="project.working_directory"
            style="display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;"
          >
            {{ formattedPath }}
          </small>
        </div>
      </div>
    </h2>

    <!-- Project Status Line (always visible) -->
    <ProjectStatusLine :project="project" :sessions="projectSessions" />

    <!-- Collapsible Sessions Area -->
    <div
      :id="`collapse-${project.project_id}`"
      class="accordion-collapse collapse"
      :class="{ show: isExpanded }"
      :aria-labelledby="`heading-${project.project_id}`"
      @shown.bs.collapse="onExpand"
      @hidden.bs.collapse="onCollapse"
    >
      <div class="accordion-body p-0">
        <div class="list-group list-group-flush">
          <!-- Timeline for Legion projects -->
          <div
            v-if="project.is_multi_agent"
            class="list-group-item list-group-item-action timeline-item d-flex align-items-center p-2"
            :class="{ active: isTimelineActive }"
            style="cursor: pointer"
            @click="viewTimeline"
          >
            <div class="flex-grow-1">
              <span style="font-size: 1rem; margin-right: 0.5rem;">📊</span>
              <span class="fw-semibold">Timeline</span>
              <small class="text-muted ms-2">(Legion Comms)</small>
            </div>
          </div>

          <!-- Hierarchy View for Legion projects -->
          <template v-if="project.is_multi_agent">
            <!-- Hierarchy View link (for full-page view) -->
            <div
              class="list-group-item list-group-item-action hierarchy-item d-flex align-items-center p-2"
              :class="{ active: isHierarchyActive }"
              style="cursor: pointer"
              @click="viewHierarchy"
            >
              <div class="flex-grow-1">
                <span style="font-size: 1rem; margin-right: 0.5rem;">🌳</span>
                <span class="fw-semibold">Hierarchy</span>
                <small class="text-muted ms-2">(Minion Tree)</small>
              </div>
            </div>

            <!-- Minion Hierarchy (inline in sidebar) -->
            <div class="minion-hierarchy-container">
              <!-- Loading state -->
              <div v-if="loadingHierarchy" class="text-center py-2">
                <div class="spinner-border spinner-border-sm" role="status">
                  <span class="visually-hidden">Loading...</span>
                </div>
              </div>

              <!-- Minion tree -->
              <div v-else-if="minionHierarchy && minionHierarchy.children && minionHierarchy.children.length > 0">
                <MinionTreeNode
                  v-for="minion in minionHierarchy.children"
                  :key="minion.id"
                  :minion-data="minion"
                  :level="0"
                  layout="sidebar"
                  @minion-click="handleMinionClick"
                />
              </div>

              <!-- Empty state -->
              <div v-else class="text-muted fst-italic small p-2">
                No minions yet
              </div>
            </div>
          </template>

          <!-- Regular Sessions for non-Legion projects -->
          <template v-else>
            <SessionItem
              v-for="session in projectSessions"
              :key="session.session_id"
              :session="session"
              :project-id="project.project_id"
            />
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'
import ProjectStatusLine from './ProjectStatusLine.vue'
import SessionItem from '../session/SessionItem.vue'
import MinionTreeNode from '../legion/MinionTreeNode.vue'

const props = defineProps({
  project: {
    type: Object,
    required: true
  }
})

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const isExpanded = ref(props.project.is_expanded || false)
const minionHierarchy = ref(null)
const loadingHierarchy = ref(false)
let sessionWatchStop = null

// Computed
const formattedPath = computed(() =>
  projectStore.formatPath(props.project.working_directory)
)

const projectSessions = computed(() => {
  // Get sessions in the order defined by project.session_ids
  if (!props.project?.session_ids || props.project.session_ids.length === 0) {
    return []
  }

  // Map session IDs to actual session objects, maintaining the order
  return props.project.session_ids
    .map(sessionId => sessionStore.getSession(sessionId))
    .filter(session => session !== null && session !== undefined) // Remove any null/undefined values if session not found
})

const isTimelineActive = computed(() => {
  const route = router.currentRoute.value
  return route.name === 'timeline' && route.params.legionId === props.project.project_id
})

const isHierarchyActive = computed(() => {
  const route = router.currentRoute.value
  return route.name === 'hierarchy' && route.params.legionId === props.project.project_id
})

// Accordion click handler (manually toggle since we changed button to div)
function onAccordionClick(event) {
  // Only toggle if clicking the accordion itself, not nested buttons
  // The @click.stop on nested buttons will prevent this from being called
  const bootstrap = window.bootstrap
  if (!bootstrap) return

  const collapseElement = document.getElementById(`collapse-${props.project.project_id}`)
  if (!collapseElement) return

  const bsCollapse = bootstrap.Collapse.getOrCreateInstance(collapseElement)
  bsCollapse.toggle()
}

// Collapse event handlers
function onExpand() {
  if (!isExpanded.value) {
    projectStore.toggleExpansion(props.project.project_id)
    isExpanded.value = true
  }
}

function onCollapse() {
  if (isExpanded.value) {
    projectStore.toggleExpansion(props.project.project_id)
    isExpanded.value = false

    // Exit session/timeline if current view belongs to this project
    const currentSessionId = sessionStore.currentSessionId
    if (currentSessionId) {
      const currentSession = sessionStore.getSession(currentSessionId)
      if (currentSession && props.project.session_ids?.includes(currentSessionId)) {
        router.push('/')
      }
    }

    // Check if viewing this legion's timeline
    const currentProjectId = projectStore.currentProjectId
    if (currentProjectId === props.project.project_id) {
      router.push('/')
    }
  }
}

// Modals
function showEditModal() {
  uiStore.showModal('edit-project', { project: props.project })
}

function showCreateSessionModal() {
  if (props.project.is_multi_agent) {
    uiStore.showModal('create-minion', { project: props.project })
  } else {
    uiStore.showModal('create-session', { project: props.project })
  }
}

// Timeline view
function viewTimeline() {
  router.push(`/timeline/${props.project.project_id}`)
}

// Hierarchy view
function viewHierarchy() {
  router.push(`/hierarchy/${props.project.project_id}`)
}

// Load minion hierarchy for Legion projects
async function loadMinionHierarchy() {
  if (!props.project.is_multi_agent) return

  loadingHierarchy.value = true
  try {
    const response = await api.get(`/api/legions/${props.project.project_id}/hierarchy`)
    minionHierarchy.value = response
  } catch (error) {
    console.error('Failed to load minion hierarchy:', error)
    minionHierarchy.value = null
  } finally {
    loadingHierarchy.value = false
  }
}

// Handle minion click - navigate to session view
function handleMinionClick(minionId) {
  router.push(`/session/${minionId}`)
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

// Load hierarchy when project is expanded (for Legion projects)
watch(isExpanded, (newVal) => {
  if (newVal && props.project.is_multi_agent) {
    // Reload hierarchy to get latest data
    loadMinionHierarchy()
  }
})

// Watch session store changes and update hierarchy (always watching)
onMounted(() => {
  if (props.project.is_multi_agent) {
    sessionWatchStop = watch(
      () => sessionStore.sessions,
      (sessions) => {
        if (!minionHierarchy.value || !isExpanded.value) return

        // Update all minion states, is_processing, and latest_message in our hierarchy
        for (const [sessionId, session] of sessions) {
          if (session.is_minion && session.project_id === props.project.project_id) {
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

    // Watch for minions being created or deleted (reload hierarchy)
    watch(
      () => sessionStore.sessions.size,
      (newSize, oldSize) => {
        // Only reload if project is expanded
        if (!isExpanded.value) return

        // Check for new minion creation (size increased)
        if (newSize > oldSize) {
          const sessions = Array.from(sessionStore.sessions.values())
          const hasNewMinion = sessions.some(s =>
            s.is_minion &&
            s.project_id === props.project.project_id &&
            minionHierarchy.value &&
            !findMinionInTree(minionHierarchy.value, s.session_id)
          )

          if (hasNewMinion) {
            console.log('New minion detected, reloading hierarchy')
            loadMinionHierarchy()
          }
        }

        // Check for minion deletion (size decreased)
        if (newSize < oldSize && minionHierarchy.value) {
          // Check if a minion in our hierarchy no longer exists in session store
          const sessions = sessionStore.sessions
          const checkMinion = (node) => {
            if (node.type === 'minion' && !sessions.has(node.id)) {
              return true // Minion was deleted
            }
            if (node.children) {
              return node.children.some(child => checkMinion(child))
            }
            return false
          }

          if (checkMinion(minionHierarchy.value)) {
            console.log('Minion deletion detected, reloading hierarchy')
            loadMinionHierarchy()
          }
        }
      }
    )
  }

  // Load hierarchy immediately if already expanded
  if (isExpanded.value && props.project.is_multi_agent) {
    loadMinionHierarchy()
  }
})

// Cleanup watcher on unmount
onUnmounted(() => {
  if (sessionWatchStop) {
    sessionWatchStop()
  }
})

// Drag and Drop
function onDragStart(event) {
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('application/project-id', props.project.project_id)
  event.dataTransfer.setData('text/plain', props.project.project_id) // Fallback
  event.target.classList.add('dragging')
}

function onDragEnd(event) {
  event.target.classList.remove('dragging')
}

function onDragOver(event) {
  // Always prevent default to allow drop
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'

  // Show drop indicator
  const rect = event.currentTarget.getBoundingClientRect()
  const midpoint = rect.top + rect.height / 2
  if (event.clientY < midpoint) {
    event.currentTarget.classList.add('drop-above')
    event.currentTarget.classList.remove('drop-below')
  } else {
    event.currentTarget.classList.add('drop-below')
    event.currentTarget.classList.remove('drop-above')
  }
}

function onDrop(event) {
  event.preventDefault()
  event.currentTarget.classList.remove('drop-above', 'drop-below')

  // Try to get the dragged project ID (try both keys for compatibility)
  const draggedId = event.dataTransfer.getData('application/project-id') ||
                     event.dataTransfer.getData('text/plain')
  if (!draggedId || draggedId === props.project.project_id) {
    return
  }

  // Determine drop position
  const rect = event.currentTarget.getBoundingClientRect()
  const midpoint = rect.top + rect.height / 2
  const dropBefore = event.clientY < midpoint

  // Get current project order
  const orderedProjects = projectStore.orderedProjects
  const currentIndex = orderedProjects.findIndex(p => p.project_id === props.project.project_id)
  const draggedIndex = orderedProjects.findIndex(p => p.project_id === draggedId)

  if (currentIndex === -1 || draggedIndex === -1) return

  // Calculate new order
  const newOrder = [...orderedProjects]
  const [removed] = newOrder.splice(draggedIndex, 1)

  let insertIndex = dropBefore ? currentIndex : currentIndex + 1
  if (draggedIndex < currentIndex) {
    insertIndex--
  }

  newOrder.splice(insertIndex, 0, removed)

  // Update backend
  const newProjectIds = newOrder.map(p => p.project_id)
  projectStore.reorderProjects(newProjectIds)
}

</script>

<style scoped>
.dragging {
  opacity: 0.5;
}

.drop-above {
  border-top: 3px solid #0d6efd;
}

.drop-below {
  border-bottom: 3px solid #0d6efd;
}

.legion-icon {
  display: inline-block;
}

.minion-hierarchy-container {
  padding: 0.5rem;
  background-color: #f8f9fa;
}
</style>
