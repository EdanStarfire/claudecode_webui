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
    <h2 class="accordion-header" :id="`heading-${project.project_id}`" style="position: relative">
      <!-- Accordion Button -->
      <button
        class="accordion-button bg-white p-2"
        :class="{ collapsed: !isExpanded }"
        type="button"
        data-bs-toggle="collapse"
        :data-bs-target="`#collapse-${project.project_id}`"
        :aria-expanded="isExpanded"
        :aria-controls="`collapse-${project.project_id}`"
      >
        <div class="flex-grow-1 me-2 d-flex flex-column" style="min-width: 0;">
          <!-- Top row: Project name -->
          <div class="fw-semibold mb-1" style="flex-shrink: 1; min-width: 0;">
            <span v-if="project.is_multi_agent" class="legion-icon" style="font-size: 1rem; margin-right: 0.25rem;">🏛</span>
            {{ project.name }}
          </div>

          <!-- Bottom row: Folder path -->
          <small class="text-muted font-monospace text-truncate" :title="project.working_directory" style="display: block; min-width: 0;">
            {{ formattedPath }}
          </small>
        </div>
      </button>

      <!-- Action Buttons Container (outside accordion button) -->
      <div class="position-absolute d-flex gap-1" style="right: 0.5rem; top: 50%; transform: translateY(-50%); z-index: 10;">
        <!-- Edit Project Button -->
        <button
          class="btn btn-sm btn-outline-secondary"
          title="Edit or delete project"
          type="button"
          @click.stop.prevent="showEditModal"
        >
          ✏️
        </button>

        <!-- Add Session/Minion Button -->
        <button
          class="btn btn-sm btn-outline-primary"
          :title="project.is_multi_agent ? 'Create minion' : 'Add session to project'"
          type="button"
          @click.stop.prevent="showCreateSessionModal"
        >
          ➕
        </button>
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

          <!-- Spy and Horde for Legion projects -->
          <template v-if="project.is_multi_agent">
            <SpySelector :project="project" :sessions="projectSessions" />
            <HordeSelector :project="project" :sessions="projectSessions" />
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
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import ProjectStatusLine from './ProjectStatusLine.vue'
import SessionItem from '../session/SessionItem.vue'
import SpySelector from '../legion/SpySelector.vue'
import HordeSelector from '../legion/HordeSelector.vue'

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
</style>
