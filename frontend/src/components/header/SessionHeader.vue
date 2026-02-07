<template>
  <div class="session-header border-bottom p-3 d-flex justify-content-between align-items-center" :class="{ 'theme-red-panel': uiStore.isRedBackground }">
    <div>
      <div class="project-name">{{ projectName }}</div>
      <div class="session-name" :title="sessionId">{{ sessionName }}</div>
    </div>

    <!-- Right Sidebar Toggle Button -->
    <button
      class="btn btn-sm sidebar-toggle-btn"
      :class="{ 'btn-primary': !rightSidebarCollapsed, 'btn-outline-secondary': rightSidebarCollapsed }"
      @click="toggleRightSidebar"
      :title="rightSidebarCollapsed ? 'Show panel' : 'Hide panel'"
    >
      <span class="toggle-icon">{{ rightSidebarCollapsed ? '◀' : '▶' }}</span>
      <span v-if="hasTasks" class="task-badge ms-1">📋 {{ taskStats.completed }}/{{ taskStats.total }}</span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/task'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const projectStore = useProjectStore()
const taskStore = useTaskStore()
const uiStore = useUIStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))
const sessionName = computed(() => session.value?.name || props.sessionId)

// Find the project that contains this session
const project = computed(() => {
  for (const proj of projectStore.projects.values()) {
    if (proj.session_ids?.includes(props.sessionId)) {
      return proj
    }
  }
  return null
})
const projectName = computed(() => project.value?.name || 'Unknown Project')

// Task panel state
const hasTasks = computed(() => taskStore.hasTasks(props.sessionId))
const taskStats = computed(() => taskStore.taskStats(props.sessionId))
const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)

function toggleRightSidebar() {
  uiStore.toggleRightSidebar()
}
</script>

<style scoped>
.session-header {
  background-color: #f8f9fa;
}

.session-header.theme-red-panel {
  background-color: #ffebee;
}

.project-name {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.2;
}

.session-name {
  font-size: 0.875rem;
  color: #6c757d;
  cursor: help;
}

.sidebar-toggle-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.5rem;
}

.toggle-icon {
  font-size: 0.75rem;
}

.task-badge {
  font-size: 0.75rem;
  font-weight: 600;
}
</style>
