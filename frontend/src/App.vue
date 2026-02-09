<template>
  <div class="container-fluid vh-100 d-flex flex-column p-0">
    <!-- Header -->
    <AppHeader />

    <!-- Mobile backdrop for sidebar overlay -->
    <div id="sidebar-backdrop" :class="{ 'show': !sidebarCollapsed && isMobile }" @click="toggleSidebar"></div>

    <!-- Mobile backdrop for right sidebar overlay -->
    <div id="right-sidebar-backdrop" :class="{ 'show': !rightSidebarCollapsed && isMobile }" @click="toggleRightSidebar"></div>

    <!-- Main Content -->
    <div class="d-flex flex-grow-1 overflow-hidden position-relative">
      <!-- Sidebar -->
      <Sidebar />

      <!-- Router View (main content area) -->
      <main class="flex-grow-1 d-flex flex-column overflow-hidden">
        <router-view />
      </main>

      <!-- Right Sidebar (Task Panel and future panels) -->
      <RightSidebar />
    </div>

    <!-- Global Modals -->
    <FolderBrowserModal />
    <ProjectCreateModal />
    <ProjectEditModal />
    <ConfigurationModal />
    <SessionManageModal />
    <SessionInfoModal />
    <MinionViewModal />
    <RestartModal />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch, provide, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppHeader from './components/layout/AppHeader.vue'
import Sidebar from './components/layout/Sidebar.vue'
import RightSidebar from './components/layout/RightSidebar.vue'
import FolderBrowserModal from './components/common/FolderBrowserModal.vue'
import ProjectCreateModal from './components/project/ProjectCreateModal.vue'
import ProjectEditModal from './components/project/ProjectEditModal.vue'
import ConfigurationModal from './components/configuration/ConfigurationModal.vue'
import SessionManageModal from './components/session/SessionManageModal.vue'
import SessionInfoModal from './components/session/SessionInfoModal.vue'
import MinionViewModal from './components/legion/MinionViewModal.vue'
import RestartModal from './components/layout/RestartModal.vue'
import { useUIStore } from './stores/ui'
import { useWebSocketStore } from './stores/websocket'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { useTaskStore } from './stores/task'
import { useResourceStore } from './stores/resource'

const uiStore = useUIStore()
const wsStore = useWebSocketStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()
const taskStore = useTaskStore()
const resourceStore = useResourceStore()
const router = useRouter()

// Provide function for adding resource to attachments
// This will be implemented by InputArea and consumed by ResourceGallery
const pendingResourceAttachment = ref(null)

function addAttachmentFromResource(resource) {
  // Set the pending resource attachment - InputArea will watch for this
  pendingResourceAttachment.value = resource
}

// Provide to child components
provide('addAttachmentFromResource', addAttachmentFromResource)
provide('pendingResourceAttachment', pendingResourceAttachment)

// Computed properties from UI store
const sidebarCollapsed = computed(() => uiStore.sidebarCollapsed)
const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)

// Auto-expand right sidebar when tasks first appear
watch(() => taskStore.currentHasTasks, (hasTasks, hadTasks) => {
  if (hasTasks && !hadTasks) {
    // Tasks appeared for the first time - auto-expand sidebar
    uiStore.setRightSidebarCollapsed(false)
  }
})

// Auto-expand right sidebar when resources first appear
watch(() => resourceStore.currentHasResources, (hasResources, hadResources) => {
  if (hasResources && !hadResources) {
    // Resources appeared for the first time - auto-expand sidebar
    uiStore.setRightSidebarCollapsed(false)
  }
})

// Initialize app on mount
onMounted(async () => {
  // Initialize background color from localStorage
  uiStore.initBackgroundColor()

  // Initialize marked and DOMPurify if needed
  if (typeof marked !== 'undefined') {
    marked.setOptions({
      breaks: true,
      gfm: true
    })
  }

  // Connect to global UI WebSocket
  wsStore.connectUI()

  // Load initial data
  await Promise.all([
    projectStore.fetchProjects(),
    sessionStore.fetchSessions()
  ])

  // Handle URL-based navigation (deep linking)
  const hash = window.location.hash
  if (hash.startsWith('#/session/')) {
    const sessionId = hash.replace('#/session/', '')
    if (sessionStore.sessions.has(sessionId)) {
      await sessionStore.selectSession(sessionId)
    }
  } else if (hash.startsWith('#/timeline/')) {
    const legionId = hash.replace('#/timeline/', '')
    // Handle Legion timeline view
    router.push(`/timeline/${legionId}`)
  }

  // Handle browser back/forward
  window.addEventListener('popstate', handlePopState)

  // Handle window resize for mobile/desktop detection
  window.addEventListener('resize', uiStore.handleResize)
})

onUnmounted(() => {
  window.removeEventListener('popstate', handlePopState)
  window.removeEventListener('resize', uiStore.handleResize)
  wsStore.disconnectUI()
})

function handlePopState() {
  // Handle browser navigation
  const hash = window.location.hash
  if (hash.startsWith('#/session/')) {
    const sessionId = hash.replace('#/session/', '')
    if (sessionStore.sessions.has(sessionId)) {
      sessionStore.selectSession(sessionId)
    }
  }
}

function toggleSidebar() {
  uiStore.toggleSidebar()
}

function toggleRightSidebar() {
  uiStore.toggleRightSidebar()
}
</script>

<style>
/* Mobile backdrop for sidebar */
#sidebar-backdrop,
#right-sidebar-backdrop {
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background-color: rgba(0, 0, 0, 0.5);
  z-index: 1040;
}

#sidebar-backdrop.show,
#right-sidebar-backdrop.show {
  display: block;
}

@media (max-width: 768px) {
  #sidebar-backdrop.show,
  #right-sidebar-backdrop.show {
    display: block;
  }
}
</style>
