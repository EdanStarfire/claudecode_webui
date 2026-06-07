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

// Panel state defaults: links + tasks + resources expanded by default, all weights equal
// Issue #1530: links panel defaults expanded=true (ambient visibility even when empty)
const DEFAULT_PANEL_STATE = {
  links:     { expanded: true,  weight: 1 },
  tasks:     { expanded: true,  weight: 1 },
  resources: { expanded: true,  weight: 1 },
  queue:     { expanded: false, weight: 1 },
  diffs:     { expanded: false, weight: 1 },
  edits:     { expanded: false, weight: 1 },
  proxy:     { expanded: false, weight: 1 },
  schedules: { expanded: false, weight: 1 },
}

function loadPanelState() {
  try {
    const stored = localStorage.getItem('webui-sidebar-panels')
    if (!stored) return JSON.parse(JSON.stringify(DEFAULT_PANEL_STATE))
    const parsed = JSON.parse(stored)
    // Merge stored state against defaults so new panels get sensible defaults
    const result = {}
    for (const [id, defaults] of Object.entries(DEFAULT_PANEL_STATE)) {
      result[id] = { ...defaults, ...(parsed[id] || {}) }
    }
    return result
  } catch {
    return JSON.parse(JSON.stringify(DEFAULT_PANEL_STATE))
  }
}

// One-time cleanup of stale localStorage keys from the old tab/queue-height system
try { localStorage.removeItem('webui-sidebar-activeTab') } catch {}
try { localStorage.removeItem('webui-sidebar-queuePanelHeight') } catch {}

/**
 * UI Store - Manages UI state (sidebar, modals, scroll, etc.)
 */
export const useUIStore = defineStore('ui', () => {
  // ========== STATE ==========

  // Right Sidebar state (for task panel) — persisted to localStorage
  const rightSidebarCollapsed = ref(readStorage('rightCollapsed', true))
  const rightSidebarWidth = ref(readStorage('rightWidth', 300))

  // Right panel visibility for responsive toggle (tablet/mobile overlay)
  const rightPanelVisible = ref(
    window.innerWidth >= 768 ? readStorage('rightVisible', true) : false
  )

  // Right sidebar collapsible panel state (replaces single active-tab model)
  const rightSidebarPanels = ref(loadPanelState())

  // Ephemeral expansion recency order — most-recently-expanded first.
  // Used by enforceCapacity() in RightSidebar to collapse oldest panels first.
  // NOT persisted to localStorage and NOT included in commitPanelState().
  const panelExpansionOrder = ref([])

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

  // Issue #1631: Per-session sticky-to-bottom visibility (read by SessionStatusBar)
  const stickyToBottomBySession = ref(new Map())
  // Issue #1631: Per-session scroll-to-bottom request tokens (watched by MessageList)
  const scrollToBottomTokenBySession = ref(new Map())

  // Theme: cycles through light → dark → sensitive-light → sensitive-dark
  const THEMES = ['light', 'dark', 'sensitive-light', 'sensitive-dark']
  const theme = ref('light')
  // Backward-compat computed for components using isRedBackground
  const isRedBackground = computed(() => theme.value === 'sensitive-light' || theme.value === 'sensitive-dark')

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

  // TTS Read Aloud toggle (issue #735)
  const ttsReadAloudEnabled = ref(
    (() => {
      try {
        const val = localStorage.getItem('webui-tts-readaloud-enabled')
        return val !== null ? JSON.parse(val) : false
      } catch { return false }
    })()
  )

  // Issue #899: Rate limit state (global, not per-session); null = no data yet
  const rateLimits = ref(null)

  // Issue #1130: Watchdog alert queue (session_watchdog_alert events from UI poll)
  const watchdogAlerts = ref([])

  // Agent sort preference ('alpha' | 'creation') — persisted
  const agentSort = ref(readStorage('agentSort', 'alpha'))

  function setAgentSort(mode) {
    if (mode !== 'alpha' && mode !== 'creation') return
    agentSort.value = mode
    writeStorage('agentSort', mode)
  }

  // Project overview view mode ('hierarchy' | 'flat') — persisted
  const projectViewMode = ref(readStorage('projectViewMode', 'hierarchy'))

  function setProjectViewMode(mode) {
    if (mode !== 'hierarchy' && mode !== 'flat') return
    projectViewMode.value = mode
    writeStorage('projectViewMode', mode)
  }

  // Flat-list sort preference (independent from hierarchy's agentSort) — persisted
  const flatSort = ref(readStorage('flatSort', 'last_active'))

  function setFlatSort(mode) {
    if (mode !== 'alpha' && mode !== 'creation' && mode !== 'last_active') return
    flatSort.value = mode
    writeStorage('flatSort', mode)
  }

  // Flat-list state grouping toggle — persisted
  const groupByState = ref(readStorage('groupByState', false))

  function setGroupByState(value) {
    groupByState.value = !!value
    writeStorage('groupByState', groupByState.value)
  }

  // Issue #1587: Max peek cards in collapsed chip stack — synced from /api/config via settings page
  const maxPeekCards = ref(readStorage('maxPeekCards', 100))

  function setMaxPeekCards(n) {
    const value = Math.max(1, parseInt(n, 10) || 100)
    maxPeekCards.value = value
    writeStorage('maxPeekCards', value)
  }

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

  function setAutoScroll(enabled) {
    autoScrollEnabled.value = enabled
  }

  function setStickyToBottom(sessionId, value) {
    if (stickyToBottomBySession.value.get(sessionId) === value) return
    const next = new Map(stickyToBottomBySession.value)
    next.set(sessionId, value)
    stickyToBottomBySession.value = next
  }

  function requestScrollToBottom(sessionId) {
    const next = new Map(scrollToBottomTokenBySession.value)
    next.set(sessionId, (next.get(sessionId) || 0) + 1)
    scrollToBottomTokenBySession.value = next
  }

  function setSuppressAutoShow(value) {
    suppressAutoShow.value = value
  }

  function setTTSReadAloud(enabled) {
    ttsReadAloudEnabled.value = enabled
    try { localStorage.setItem('webui-tts-readaloud-enabled', JSON.stringify(enabled)) } catch {}
  }

  // Issue #899: Update global rate limit state from UI poll event
  function setRateLimits(data) {
    rateLimits.value = data
  }

  // Issue #1130: Watchdog alert management
  function pushAlert(payload) {
    const id = `${payload.session_id}-${payload.watchdog}-${Date.now()}`
    watchdogAlerts.value = [...watchdogAlerts.value, { ...payload, id }]
  }

  function dismissAlert(id) {
    watchdogAlerts.value = watchdogAlerts.value.filter(a => a.id !== id)
  }

  // --- Panel state actions (replaces tab + queue-height model) ---

  let _pendingPanelState = null
  let _panelStateRafId = null

  function commitPanelState() {
    _pendingPanelState = rightSidebarPanels.value
    if (_panelStateRafId === null) {
      _panelStateRafId = requestAnimationFrame(() => {
        try { localStorage.setItem('webui-sidebar-panels', JSON.stringify(_pendingPanelState)) } catch {}
        _panelStateRafId = null
      })
    }
  }

  function togglePanel(id) {
    if (!rightSidebarPanels.value[id]) return
    const wasExpanded = rightSidebarPanels.value[id].expanded
    rightSidebarPanels.value[id].expanded = !wasExpanded
    if (!wasExpanded) {
      panelExpansionOrder.value = [id, ...panelExpansionOrder.value.filter(x => x !== id)]
    }
    commitPanelState()
  }

  function setPanelExpanded(id, value) {
    if (!rightSidebarPanels.value[id]) return
    const wasExpanded = rightSidebarPanels.value[id].expanded
    rightSidebarPanels.value[id].expanded = value
    if (value && !wasExpanded) {
      panelExpansionOrder.value = [id, ...panelExpansionOrder.value.filter(x => x !== id)]
    }
    commitPanelState()
  }

  function setPanelWeights(weightsObj) {
    for (const [id, weight] of Object.entries(weightsObj)) {
      if (rightSidebarPanels.value[id]) {
        rightSidebarPanels.value[id].weight = weight
      }
    }
  }

  // --- Theme ---

  function initTheme() {
    const stored = localStorage.getItem('webui-theme') || localStorage.getItem('dev-theme') || 'light'
    theme.value = THEMES.includes(stored) ? stored : 'light'
    document.documentElement.setAttribute('data-bs-theme', theme.value)
  }

  function cycleTheme() {
    const idx = THEMES.indexOf(theme.value)
    theme.value = THEMES[(idx + 1) % THEMES.length]
    localStorage.setItem('webui-theme', theme.value)
    document.documentElement.setAttribute('data-bs-theme', theme.value)
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
    rightSidebarPanels,
    panelExpansionOrder,
    rightPanelVisible,
    browsingProjectId,
    expandedStacks,
    windowWidth,
    isMobile,
    autoScrollEnabled,
    stickyToBottomBySession,
    scrollToBottomTokenBySession,
    theme,
    isRedBackground,
    activeModal,
    modalData,
    currentModal,
    isLoading,
    loadingMessage,
    restartInProgress,
    restartStatus,
    suppressAutoShow,
    ttsReadAloudEnabled,
    rateLimits,
    watchdogAlerts,
    agentSort,
    projectViewMode,
    flatSort,
    groupByState,
    maxPeekCards,

    // Actions
    toggleRightSidebar,
    setRightSidebarCollapsed,
    setRightSidebarWidth,
    togglePanel,
    setPanelExpanded,
    setPanelWeights,
    commitPanelState,
    setBrowsingProject,
    toggleStack,
    collapseAllStacks,
    toggleRightPanel,
    setRightPanelVisible,
    setAutoScroll,
    setStickyToBottom,
    requestScrollToBottom,
    setTTSReadAloud,
    setSuppressAutoShow,
    initTheme,
    cycleTheme,
    showModal,
    hideModal,
    showLoading,
    hideLoading,
    showRestartModal,
    handleResize,
    setRateLimits,
    pushAlert,
    dismissAlert,
    setAgentSort,
    setProjectViewMode,
    setFlatSort,
    setGroupByState,
    setMaxPeekCards,
  }
})
