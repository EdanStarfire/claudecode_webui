<template>
  <div class="app-shell">
    <!-- Row 1: Dark app chrome bar -->
    <HeaderRow1 />

    <!-- Row 2: Project pills -->
    <ProjectPillBar />

    <!-- Row 3: Agent strip -->
    <AgentStrip />

    <!-- Mobile backdrop for right sidebar overlay -->
    <div
      id="right-sidebar-backdrop"
      :class="{ 'show': !rightSidebarCollapsed && isMobile }"
      @click="toggleRightSidebar"
    ></div>

    <!-- Main Content (chat + right panel) -->
    <div class="main-layout">
      <!-- Center Panel (router view) -->
      <main class="center-panel">
        <router-view />
      </main>

      <!-- Right Panel -->
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
import HeaderRow1 from './components/layout/HeaderRow1.vue'
import ProjectPillBar from './components/layout/ProjectPillBar.vue'
import AgentStrip from './components/layout/AgentStrip.vue'
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
const pendingResourceAttachment = ref(null)

function addAttachmentFromResource(resource) {
  pendingResourceAttachment.value = resource
}

provide('addAttachmentFromResource', addAttachmentFromResource)
provide('pendingResourceAttachment', pendingResourceAttachment)

// Computed properties from UI store
const rightSidebarCollapsed = computed(() => uiStore.rightSidebarCollapsed)
const isMobile = computed(() => uiStore.isMobile)

// Auto-expand right sidebar when tasks first appear
watch(() => taskStore.currentHasTasks, (hasTasks, hadTasks) => {
  if (hasTasks && !hadTasks) {
    uiStore.setRightSidebarCollapsed(false)
  }
})

// Auto-expand right sidebar when resources first appear
watch(() => resourceStore.currentHasResources, (hasResources, hadResources) => {
  if (hasResources && !hadResources) {
    uiStore.setRightSidebarCollapsed(false)
  }
})

// Initialize app on mount
onMounted(async () => {
  uiStore.initBackgroundColor()

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
    router.push(`/timeline/${legionId}`)
  }

  // Handle browser back/forward
  window.addEventListener('popstate', handlePopState)

  // Handle window resize
  window.addEventListener('resize', uiStore.handleResize)
})

onUnmounted(() => {
  window.removeEventListener('popstate', handlePopState)
  window.removeEventListener('resize', uiStore.handleResize)
  wsStore.disconnectUI()
})

function handlePopState() {
  const hash = window.location.hash
  if (hash.startsWith('#/session/')) {
    const sessionId = hash.replace('#/session/', '')
    if (sessionStore.sessions.has(sessionId)) {
      sessionStore.selectSession(sessionId)
    }
  }
}

function toggleRightSidebar() {
  uiStore.toggleRightSidebar()
}
</script>

<style>
.app-shell {
  width: 100vw;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.main-layout {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.center-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: #fff;
}

/* Mobile backdrop for right sidebar */
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

#right-sidebar-backdrop.show {
  display: block;
}
</style>
