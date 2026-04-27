<template>
  <div class="app-shell" data-testid="app-root">
    <!-- Row 1: Dark app chrome bar -->
    <HeaderRow1 />

    <!-- Row 2: Project pills -->
    <ProjectPillBar />

    <!-- Row 3: Agent strip -->
    <AgentStrip />

    <!-- Overlay backdrop for responsive right panel -->
    <div
      id="right-panel-backdrop"
      :class="{ 'show': rightPanelVisible && isTabletOrMobile }"
      @click="uiStore.setRightPanelVisible(false)"
    ></div>

    <!-- Main Content (chat + right panel) -->
    <div class="main-layout">
      <!-- Center Panel (router view) -->
      <main class="center-panel">
        <router-view />
      </main>

      <!-- Right Panel: On desktop, v-show controls in-flow visibility;
           on mobile/tablet, overlay CSS handles transform-based slide -->
      <RightSidebar
        v-show="!isFullWidthRoute && (rightPanelVisible || isTabletOrMobile)"
        :class="{ 'panel-overlay': isTabletOrMobile, 'panel-visible': rightPanelVisible }"
      />
    </div>

    <!-- Auth Prompt (issue #728) -->
    <AuthPrompt v-if="showAuthPrompt" @authenticated="onAuthenticated" />

    <!-- Watchdog alert banners (issue #1130) -->
    <AlertBanner />

    <!-- Global Modals -->
    <FolderBrowserModal />
    <ProjectCreateModal />
    <ProjectEditModal />
    <ConfigurationModal />
    <SessionManageModal />
    <SessionInfoModal />
    <MinionViewModal />
    <DeletedAgentsModal />
    <RestartModal />
    <GlobalConfigModal />
    <MermaidFullView />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch, provide, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
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
import DeletedAgentsModal from './components/layout/DeletedAgentsModal.vue'
import RestartModal from './components/layout/RestartModal.vue'
import GlobalConfigModal from './components/configuration/GlobalConfigModal.vue'
import AuthPrompt from './components/common/AuthPrompt.vue'
import MermaidFullView from './components/common/MermaidFullView.vue'
import AlertBanner from './components/common/AlertBanner.vue'
import { useUIStore } from './stores/ui'
import { usePollingStore } from './stores/polling'
import { useSessionStore } from './stores/session'
import { useProjectStore } from './stores/project'
import { useTaskStore } from './stores/task'
import { apiGet, getAuthToken, setAuthToken } from './utils/api'

const uiStore = useUIStore()
const showAuthPrompt = ref(false)
const wsStore = usePollingStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()
const taskStore = useTaskStore()
const router = useRouter()
const route = useRoute()

// Provide function for adding resource to attachments
const pendingResourceAttachment = ref(null)

function addAttachmentFromResource(resource) {
  pendingResourceAttachment.value = resource
}

provide('addAttachmentFromResource', addAttachmentFromResource)
provide('pendingResourceAttachment', pendingResourceAttachment)

// Computed properties from UI store
const rightPanelVisible = computed(() => uiStore.rightPanelVisible)
const isTabletOrMobile = computed(() => uiStore.windowWidth < 768)

// Full-width routes suppress the right sidebar (#1154)
const isFullWidthRoute = computed(() => ['analytics', 'audit'].includes(route.name))

// Clear project/session selection when navigating to analytics (#1155)
watch(() => route.name, (routeName) => {
  if (routeName === 'analytics') {
    projectStore.clearProjectSelection()
    sessionStore.clearSessionSelection()
  }
})

// Auto-show right panel when tasks first appear (suppressed during session switch, #521)
watch(() => taskStore.currentHasTasks, (hasTasks, hadTasks) => {
  if (hasTasks && !hadTasks && !uiStore.suppressAutoShow) {
    uiStore.setRightSidebarTab('tasks')
    uiStore.setRightPanelVisible(true)
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

  // Issue #728: Capture token from URL query param
  const urlParams = new URLSearchParams(window.location.search)
  const urlToken = urlParams.get('token')
  if (urlToken) {
    setAuthToken(urlToken)
    // Strip token from URL for security
    urlParams.delete('token')
    const cleanUrl = urlParams.toString()
      ? `${window.location.pathname}?${urlParams.toString()}${window.location.hash}`
      : `${window.location.pathname}${window.location.hash}`
    window.history.replaceState({}, '', cleanUrl)
  }

  // Issue #728: Check auth status before connecting
  try {
    const authStatus = await apiGet('/api/auth/check')
    if (authStatus.auth_required && !authStatus.authenticated) {
      showAuthPrompt.value = true
      return  // Don't initialize app until authenticated
    }
  } catch {
    // If auth check fails, try to proceed (server may be down)
  }

  initializeApp()
})

async function onAuthenticated() {
  window.location.reload()
}

async function initializeApp() {
  wsStore.setupVisibilityHandler()

  // Fetch cursor alongside initial data so polling starts from current head,
  // preventing historical events from replaying and corrupting session_ids (#870)
  const [, , cursorResult] = await Promise.allSettled([
    projectStore.fetchProjects(),
    sessionStore.fetchSessions(),
    apiGet('/api/poll/cursor')
  ])

  const cursor = cursorResult.status === 'fulfilled' ? (cursorResult.value?.cursor ?? 0) : 0
  if (cursorResult.status === 'rejected') {
    console.warn('[App] Failed to fetch poll cursor, falling back to 0')
  }

  // Start polling after initial data is loaded, from current cursor (skips history)
  wsStore.startUIPolling(cursor)

  // Handle URL-based navigation (deep linking)
  const hash = window.location.hash
  if (hash.startsWith('#/session/')) {
    const sessionId = hash.replace('#/session/', '')
    if (sessionStore.sessions.has(sessionId)) {
      await sessionStore.selectSession(sessionId)
    }
  }

  // Handle browser back/forward
  window.addEventListener('popstate', handlePopState)

  // Handle window resize
  window.addEventListener('resize', uiStore.handleResize)
}

onUnmounted(() => {
  window.removeEventListener('popstate', handlePopState)
  window.removeEventListener('resize', uiStore.handleResize)
  wsStore.stopUIPolling()
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

/* Overlay backdrop for responsive right panel — matches panel vertical extent */
#right-panel-backdrop {
  display: none;
  position: fixed;
  top: 142px;
  left: 0;
  width: 100vw;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.4);
  z-index: 1040;
  transition: opacity 0.2s;
}

#right-panel-backdrop.show {
  display: block;
}

/* Right panel overlay mode (tablet and mobile)
   Uses #right-sidebar ID to override scoped styles (position: relative) */
@media (max-width: 767px) {
  #right-sidebar.panel-overlay {
    position: fixed;
    right: 0;
    top: 142px;
    bottom: 0;
    width: min(380px, 90vw);
    z-index: 1050;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    box-shadow: -4px 0 16px rgba(0, 0, 0, 0.1);
  }

  #right-sidebar.panel-overlay.panel-visible {
    transform: translateX(0);
  }
}
</style>
