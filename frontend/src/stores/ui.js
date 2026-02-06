import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/**
 * UI Store - Manages UI state (sidebar, modals, scroll, etc.)
 */
export const useUIStore = defineStore('ui', () => {
  // ========== STATE ==========

  // Left Sidebar state
  // Default: collapsed on mobile, visible on desktop
  const sidebarCollapsed = ref(window.innerWidth < 768)
  const sidebarWidth = ref(300)

  // Right Sidebar state (for task panel)
  // Default: collapsed, only shown when tasks exist
  const rightSidebarCollapsed = ref(true)
  const rightSidebarWidth = ref(300)

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

  // ========== ACTIONS ==========

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function setSidebarCollapsed(collapsed) {
    sidebarCollapsed.value = collapsed
  }

  function setSidebarWidth(width) {
    sidebarWidth.value = Math.max(200, Math.min(width, window.innerWidth * 0.3))
  }

  function toggleRightSidebar() {
    rightSidebarCollapsed.value = !rightSidebarCollapsed.value
  }

  function setRightSidebarCollapsed(collapsed) {
    rightSidebarCollapsed.value = collapsed
  }

  function setRightSidebarWidth(width) {
    rightSidebarWidth.value = Math.max(200, Math.min(width, window.innerWidth * 0.3))
  }

  function setAutoScroll(enabled) {
    autoScrollEnabled.value = enabled
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

  // Handle window resize
  function handleResize() {
    const previousWidth = windowWidth.value
    windowWidth.value = window.innerWidth

    // Collapse sidebar when resizing down to mobile
    if (windowWidth.value < 768 && previousWidth >= 768) {
      sidebarCollapsed.value = true
    }
    // Expand sidebar when resizing up to desktop
    else if (windowWidth.value >= 768 && previousWidth < 768) {
      sidebarCollapsed.value = false
    }
  }

  // ========== RETURN ==========
  return {
    // State
    sidebarCollapsed,
    sidebarWidth,
    rightSidebarCollapsed,
    rightSidebarWidth,
    isMobile,
    autoScrollEnabled,
    isRedBackground,
    activeModal,
    modalData,
    currentModal,
    isLoading,
    loadingMessage,

    // Actions
    toggleSidebar,
    setSidebarCollapsed,
    setSidebarWidth,
    toggleRightSidebar,
    setRightSidebarCollapsed,
    setRightSidebarWidth,
    setAutoScroll,
    initBackgroundColor,
    toggleBackgroundColor,
    showModal,
    hideModal,
    showLoading,
    hideLoading,
    handleResize
  }
})
