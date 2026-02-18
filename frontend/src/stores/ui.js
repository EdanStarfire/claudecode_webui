import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// localStorage helpers for sidebar persistence
const STORAGE_PREFIX = 'webui-sidebar-'

function readStorage(key, fallback) {
  try {
    const val = localStorage.getItem(STORAGE_PREFIX + key)
    return val !== null ? JSON.parse(val) : fallback
  } catch { return fallback }
}

function writeStorage(key, value) {
  try { localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value)) } catch {}
}

/**
 * UI Store - Manages UI state (sidebar, modals, scroll, etc.)
 */
export const useUIStore = defineStore('ui', () => {
  // ========== STATE ==========

  // Right Sidebar state (for task panel) — persisted to localStorage
  const rightSidebarCollapsed = ref(readStorage('rightCollapsed', true))
  const rightSidebarWidth = ref(readStorage('rightWidth', 300))

  // Right Sidebar active tab: 'diff', 'tasks', 'resources', 'comms'
  const rightSidebarActiveTab = ref(readStorage('activeTab', 'diff'))

  // Right panel visibility for responsive toggle (tablet/mobile overlay)
  const rightPanelVisible = ref(
    window.innerWidth >= 768 ? readStorage('rightVisible', true) : false
  )

  // Browsing project (which project's agents are shown in the strip)
  // Distinct from active project (the project of the currently selected session)
  const browsingProjectId = ref(null)

  // Expanded stacked chips (Set of parent session IDs whose children are expanded)
  const expandedStacks = ref(new Set())

  // Mobile detection (reactive to window size)
  const windowWidth = ref(window.innerWidth)
  const isMobile = computed(() => windowWidth.value < 768)

  // Auto-scroll state
  const autoScrollEnabled = ref(true)

  // Background color toggle state (for differentiating multiple instances)
  const isRedBackground = ref(false)

  // Modal state
  const activeModal = ref(null) // 'create-project', 'create-session', 'edit-session', etc.
  const modalData = ref(null) // Data for the active modal

  // Computed property for modal watchers
  const currentModal = computed(() => {
    if (!activeModal.value) return null
    return {
      name: activeModal.value,
      data: modalData.value
    }
  })

  // Loading overlay
  const isLoading = ref(false)
  const loadingMessage = ref('Loading...')

  // Restart state (issue #434)
  const restartInProgress = ref(false)
  const restartStatus = ref('idle') // idle, confirming, pulling, restarting, reconnecting, error

  // Suppress auto-show of right panel during session switching (issue #521)
  // Transient flag — not persisted to localStorage
  const suppressAutoShow = ref(false)

  // ========== ACTIONS ==========

  function toggleRightSidebar() {
    rightSidebarCollapsed.value = !rightSidebarCollapsed.value
    writeStorage('rightCollapsed', rightSidebarCollapsed.value)
  }

  function setRightSidebarCollapsed(collapsed) {
    rightSidebarCollapsed.value = collapsed
    writeStorage('rightCollapsed', collapsed)
  }

  function setRightSidebarWidth(width) {
    rightSidebarWidth.value = Math.max(200, Math.min(width, window.innerWidth * 0.3))
    writeStorage('rightWidth', rightSidebarWidth.value)
  }

  function setRightSidebarTab(tab) {
    rightSidebarActiveTab.value = tab
    writeStorage('activeTab', tab)
  }

  function setAutoScroll(enabled) {
    autoScrollEnabled.value = enabled
  }

  function setSuppressAutoShow(value) {
    suppressAutoShow.value = value
  }

  function initBackgroundColor() {
    const stored = localStorage.getItem('webui-background-color')
    isRedBackground.value = stored === 'red'
  }

  function toggleBackgroundColor() {
    isRedBackground.value = !isRedBackground.value
    localStorage.setItem('webui-background-color', isRedBackground.value ? 'red' : 'gray')
  }

  function showModal(modalName, data = null) {
    // Force a change by clearing first (ensures watcher fires even for same modal)
    activeModal.value = null
    modalData.value = null

    // Use nextTick to ensure watchers see the change
    setTimeout(() => {
      activeModal.value = modalName
      modalData.value = data
    }, 0)
  }

  function hideModal() {
    activeModal.value = null
    modalData.value = null
  }

  function showLoading(message = 'Loading...') {
    isLoading.value = true
    loadingMessage.value = message
  }

  function hideLoading() {
    isLoading.value = false
  }

  // Restart modal (issue #434)
  function showRestartModal() {
    showModal('restart-server')
  }

  // Browsing project management
  function setBrowsingProject(projectId) {
    browsingProjectId.value = projectId
  }

  // Stacked chip management
  function toggleStack(sessionId) {
    if (expandedStacks.value.has(sessionId)) {
      expandedStacks.value.delete(sessionId)
    } else {
      expandedStacks.value.add(sessionId)
    }
    expandedStacks.value = new Set(expandedStacks.value)
  }

  function collapseAllStacks() {
    if (expandedStacks.value.size > 0) {
      expandedStacks.value = new Set()
    }
  }

  // Right panel responsive toggle
  function toggleRightPanel() {
    rightPanelVisible.value = !rightPanelVisible.value
    writeStorage('rightVisible', rightPanelVisible.value)
  }

  function setRightPanelVisible(visible) {
    rightPanelVisible.value = visible
    writeStorage('rightVisible', visible)
  }

  // Handle window resize
  function handleResize() {
    const previousWidth = windowWidth.value
    windowWidth.value = window.innerWidth

    // Right panel visibility based on breakpoint
    if (windowWidth.value >= 768 && previousWidth < 768) {
      rightPanelVisible.value = readStorage('rightVisible', true)
    } else if (windowWidth.value < 768 && previousWidth >= 768) {
      rightPanelVisible.value = false
    }
  }

  // ========== RETURN ==========
  return {
    // State
    rightSidebarCollapsed,
    rightSidebarWidth,
    rightSidebarActiveTab,
    rightPanelVisible,
    browsingProjectId,
    expandedStacks,
    windowWidth,
    isMobile,
    autoScrollEnabled,
    isRedBackground,
    activeModal,
    modalData,
    currentModal,
    isLoading,
    loadingMessage,
    restartInProgress,
    restartStatus,
    suppressAutoShow,

    // Actions
    toggleRightSidebar,
    setRightSidebarCollapsed,
    setRightSidebarWidth,
    setRightSidebarTab,
    setBrowsingProject,
    toggleStack,
    collapseAllStacks,
    toggleRightPanel,
    setRightPanelVisible,
    setAutoScroll,
    setSuppressAutoShow,
    initBackgroundColor,
    toggleBackgroundColor,
    showModal,
    hideModal,
    showLoading,
    hideLoading,
    showRestartModal,
    handleResize
  }
})
