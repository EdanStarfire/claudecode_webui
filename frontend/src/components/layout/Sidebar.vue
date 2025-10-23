<template>
  <aside
    id="sidebar"
    class="col-auto bg-light border-end d-flex flex-column overflow-auto"
    :class="{
      'collapsed': sidebarCollapsed,
      'mobile-open': !sidebarCollapsed && isMobile,
      'desktop-collapsed': sidebarCollapsed && !isMobile
    }"
    :style="sidebarStyle"
  >
    <!-- Sidebar Header Buttons -->
    <div class="d-flex gap-2 p-3 border-bottom bg-white">
      <div class="d-flex gap-2 flex-grow-1">
        <button
          id="create-project-btn"
          class="btn btn-sm btn-primary"
          title="New Project"
          @click="showCreateProjectModal"
        >
          📁 New
        </button>
        <button
          id="refresh-sessions-btn"
          class="btn btn-sm btn-outline-secondary"
          title="Refresh"
          @click="refreshData"
        >
          ↻
        </button>
      </div>
    </div>

    <!-- Projects List -->
    <div class="flex-grow-1 overflow-auto p-3">
      <h6 class="text-muted mb-3">Projects</h6>
      <div id="sessions-container">
        <ProjectList />
      </div>
    </div>

    <!-- Resize Handle -->
    <div
      class="sidebar-resize-handle"
      @mousedown="startResize"
    ></div>
  </aside>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'
import ProjectList from '../project/ProjectList.vue'

const uiStore = useUIStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const sidebarCollapsed = computed(() => uiStore.sidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)

const sidebarStyle = computed(() => {
  // On mobile, let CSS handle the transform via classes
  if (isMobile.value) {
    return {}
  }
  // On desktop, apply custom width and CSS variable for collapse calculation
  const width = `${uiStore.sidebarWidth}px`
  return {
    width: width,
    minWidth: '200px',
    maxWidth: '30vw',
    '--sidebar-width': width
  }
})

function showCreateProjectModal() {
  uiStore.showModal('create-project')
}

async function refreshData() {
  await Promise.all([
    projectStore.fetchProjects(),
    sessionStore.fetchSessions()
  ])
}

// Sidebar resize functionality
const isResizing = ref(false)

function startResize(event) {
  isResizing.value = true
  document.addEventListener('mousemove', handleResize)
  document.addEventListener('mouseup', stopResize)
}

function handleResize(event) {
  if (isResizing.value) {
    uiStore.setSidebarWidth(event.clientX)
  }
}

function stopResize() {
  isResizing.value = false
  document.removeEventListener('mousemove', handleResize)
  document.removeEventListener('mouseup', stopResize)
}
</script>

<style scoped>
#sidebar {
  position: relative;
  transition: transform 0.3s ease;
}

.sidebar-resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  width: 4px;
  height: 100%;
  cursor: col-resize;
  z-index: 10;
}

.sidebar-resize-handle:hover {
  background-color: rgba(0, 0, 0, 0.1);
}

@media (max-width: 768px) {
  #sidebar.collapsed {
    transform: translateX(-100%);
  }
}
</style>
