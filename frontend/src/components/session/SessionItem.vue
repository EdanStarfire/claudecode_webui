<template>
  <div
    class="list-group-item list-group-item-action d-flex align-items-center p-2"
    :class="{ active: isSelected, 'session-deleting': isDeleting }"
    style="cursor: pointer"
    draggable="true"
    @click="selectSession"
    @dragstart="onDragStart"
    @dragend="onDragEnd"
    @dragover="onDragOver"
    @drop="onDrop"
  >
    <!-- Status Indicator Dot -->
    <div class="status-dot me-2" :class="statusDotClass" :style="statusDotStyle"></div>

    <!-- Session Info (Name + Latest Activity) -->
    <div class="session-info flex-grow-1">
      <div class="session-name">
        <span>{{ session.name || session.session_id }}</span>
        <span v-if="session.is_minion && session.role" class="text-muted small ms-1">({{ session.role }})</span>
      </div>
      <!-- Latest Activity below name - Issue #291 -->
      <div v-if="session.latest_message" class="latest-activity text-muted">
        <span v-if="activityPrefix" class="activity-prefix">{{ activityPrefix }} </span>
        <span class="activity-content">{{ truncatedMessage }}</span>
        <span v-if="relativeTime" class="activity-time ms-1">({{ relativeTime }})</span>
      </div>
    </div>

    <!-- Action Buttons -->
    <button
      class="btn btn-sm btn-outline-secondary me-1"
      title="Edit session"
      @click.stop="showEditModal"
    >
      ✏️
    </button>
    <button
      class="btn btn-sm btn-outline-secondary"
      title="Manage session"
      @click.stop="showManageModal"
    >
      ⚙️
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  session: {
    type: Object,
    required: true
  },
  projectId: {
    type: String,
    required: true
  }
})

const router = useRouter()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()
const uiStore = useUIStore()

const isSelected = computed(() =>
  sessionStore.currentSessionId === props.session.session_id
)

const isDeleting = computed(() =>
  sessionStore.deletingSessions.has(props.session.session_id)
)

const displayState = computed(() => {
  // Special case: PAUSED + processing = waiting for permission response (yellow blinking)
  if (props.session.state === 'paused' && props.session.is_processing) {
    return 'pending-prompt'
  }
  // Normal case: processing overrides state
  return props.session.is_processing ? 'processing' : props.session.state
})

const statusDotClass = computed(() => {
  const state = displayState.value
  const classMap = {
    'created': 'status-dot-grey',
    'starting': 'status-dot-green status-blinking',
    'active': 'status-dot-green',
    'running': 'status-dot-green',
    'processing': 'status-dot-purple status-blinking',
    'paused': 'status-dot-grey',
    'pending-prompt': 'status-dot-yellow status-blinking',
    'terminated': 'status-dot-grey',
    'error': 'status-dot-red',
    'failed': 'status-dot-red'
  }
  return classMap[state] || 'status-dot-grey'
})

const statusDotStyle = computed(() => {
  const state = displayState.value
  const bgColorMap = {
    'created': '#d3d3d3',
    'starting': '#90ee90',
    'active': '#90ee90',
    'running': '#90ee90',
    'processing': '#dda0dd',
    'paused': '#d3d3d3',
    'pending-prompt': '#ffc107',  // warning yellow (was light yellow)
    'terminated': '#d3d3d3',
    'error': '#ffb3b3',
    'failed': '#ffb3b3'
  }

  const borderColorMap = {
    'created': '#6c757d',
    'starting': '#28a745',
    'active': '#28a745',
    'running': '#28a745',
    'processing': '#6f42c1',
    'paused': '#6c757d',
    'pending-prompt': '#e0a800',  // darker warning yellow
    'terminated': '#6c757d',
    'error': '#dc3545',
    'failed': '#dc3545'
  }

  return {
    backgroundColor: bgColorMap[state] || '#d3d3d3',
    borderColor: borderColorMap[state] || '#6c757d'
  }
})

// Latest activity display (issue #291)
const activityPrefix = computed(() => {
  const type = props.session.latest_message_type
  if (type === 'user') return 'User -->:'
  if (type === 'system') return 'System:'
  return ''  // Assistant messages have no prefix
})

const truncatedMessage = computed(() => {
  const msg = props.session.latest_message || ''
  const maxLen = 100  // Desktop length
  return msg.length > maxLen ? msg.slice(0, maxLen) + '...' : msg
})

const relativeTime = computed(() => {
  if (!props.session.latest_message_time) return ''

  const now = Date.now()
  const msgTime = new Date(props.session.latest_message_time).getTime()
  const diffMs = now - msgTime

  const minutes = Math.floor(diffMs / 60000)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (days > 0) return `${days}d ago`
  if (hours > 0) return `${hours}h ago`
  if (minutes > 0) return `${minutes}m ago`
  return 'just now'
})

function selectSession() {
  sessionStore.selectSession(props.session.session_id)
  router.push(`/session/${props.session.session_id}`)
}

function showEditModal() {
  uiStore.showModal('edit-session', { session: props.session })
}

function showManageModal() {
  uiStore.showModal('manage-session', { session: props.session })
}

// Drag and Drop
function onDragStart(event) {
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('application/session-id', props.session.session_id)
  event.dataTransfer.setData('application/project-id', props.projectId)
  event.dataTransfer.setData('text/plain', props.session.session_id) // Fallback
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

  // Try to get the dragged session ID (try both keys for compatibility)
  const draggedSessionId = event.dataTransfer.getData('application/session-id') ||
                            event.dataTransfer.getData('text/plain')
  const draggedProjectId = event.dataTransfer.getData('application/project-id')

  if (!draggedSessionId || draggedSessionId === props.session.session_id) {
    return
  }

  // Only allow reordering within the same project
  if (draggedProjectId !== props.projectId) {
    return
  }

  // Determine drop position
  const rect = event.currentTarget.getBoundingClientRect()
  const midpoint = rect.top + rect.height / 2
  const dropBefore = event.clientY < midpoint

  // Get current session order for this project
  const project = projectStore.getProject(props.projectId)
  if (!project || !project.session_ids) return

  const sessionIds = [...project.session_ids]
  const currentIndex = sessionIds.indexOf(props.session.session_id)
  const draggedIndex = sessionIds.indexOf(draggedSessionId)

  if (currentIndex === -1 || draggedIndex === -1) return

  // Calculate new order
  const newOrder = [...sessionIds]
  const [removed] = newOrder.splice(draggedIndex, 1)

  let insertIndex = dropBefore ? currentIndex : currentIndex + 1
  if (draggedIndex < currentIndex) {
    insertIndex--
  }

  newOrder.splice(insertIndex, 0, removed)

  // Update backend
  projectStore.reorderSessionsInProject(props.projectId, newOrder)
}
</script>

<style scoped>
.list-group-item {
  transition: background-color 0.2s;
}

.list-group-item.active {
  background-color: #0d6efd;
  color: white;
}

.list-group-item:hover:not(.active) {
  background-color: #f8f9fa;
}

.session-deleting {
  opacity: 0.5;
  pointer-events: none;
}

.list-group-item.dragging {
  opacity: 0.5;
}

.list-group-item.drop-above {
  border-top: 3px solid #0d6efd;
}

.list-group-item.drop-below {
  border-bottom: 3px solid #0d6efd;
}

.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
  flex-shrink: 0;
}

/* Session info container - vertical layout */
.session-info {
  display: flex;
  flex-direction: column;
  min-width: 0;  /* Allow text truncation */
}

.session-name {
  /* Name keeps its default size */
}

/* Latest activity display (issue #291) - below name, 1pt smaller */
.latest-activity {
  font-size: calc(1em - 1pt);  /* 1pt smaller than name */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;  /* Small space between name and message */
}

.activity-prefix {
  font-weight: 500;
}

.activity-content {
  font-style: italic;
}

.activity-time {
  opacity: 0.7;
  font-size: 0.9em;  /* Slightly smaller than activity text */
}

/* Responsive design for mobile */
@media (max-width: 768px) {
  .latest-activity {
    max-width: 200px;  /* Limit width on mobile */
  }

  .latest-activity .activity-time {
    display: none;  /* Hide timestamp on mobile to save space */
  }
}

.status-dot-grey {
  background-color: #d3d3d3;
  border-color: #6c757d;
}

.status-dot-green {
  background-color: #90ee90;
  border-color: #28a745;
}

.status-dot-purple {
  background-color: #dda0dd;
  border-color: #6f42c1;
}

.status-dot-red {
  background-color: #ffb3b3;
  border-color: #dc3545;
}

.status-dot-yellow {
  background-color: #ffc107;
  border-color: #e0a800;
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.dragging {
  opacity: 0.5;
}
</style>
