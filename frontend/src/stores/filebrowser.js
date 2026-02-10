import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiGet } from '../utils/api'

/**
 * File Browser Store - Manages file tree data, expansion state, and file viewer
 *
 * Issue #437: File browser panel in right sidebar
 */
export const useFileBrowserStore = defineStore('filebrowser', () => {
  // ========== STATE ==========

  // Directory entries per session per path: sessionId -> Map<path, entries[]>
  const treeBySession = ref(new Map())

  // Expanded folder paths per session: sessionId -> Set<path>
  const expandedPaths = ref(new Map())

  // Loading paths per session: sessionId -> Set<path>
  const loadingPaths = ref(new Map())

  // Show hidden files toggle (global, not per-session)
  const showHidden = ref(false)

  // File viewer state
  const viewerOpen = ref(false)
  const viewerFilePath = ref(null)
  const viewerContent = ref(null)
  const viewerSize = ref(0)
  const viewerTruncated = ref(false)
  const viewerBinary = ref(false)
  const viewerError = ref(null)
  const viewerLoading = ref(false)

  // ========== ACTIONS ==========

  /**
   * Load directory entries for a given path
   */
  async function loadDirectory(sessionId, path) {
    if (!sessionId || !path) return

    // Set loading state
    if (!loadingPaths.value.has(sessionId)) {
      loadingPaths.value.set(sessionId, new Set())
    }
    loadingPaths.value.get(sessionId).add(path)
    loadingPaths.value = new Map(loadingPaths.value)

    try {
      const data = await apiGet('/api/filesystem/tree', {
        params: { path, show_hidden: showHidden.value }
      })

      if (!treeBySession.value.has(sessionId)) {
        treeBySession.value.set(sessionId, new Map())
      }
      treeBySession.value.get(sessionId).set(path, data.entries || [])
      treeBySession.value = new Map(treeBySession.value)
    } catch (e) {
      console.error(`Failed to load directory ${path}:`, e)
    } finally {
      const loading = loadingPaths.value.get(sessionId)
      if (loading) {
        loading.delete(path)
        loadingPaths.value = new Map(loadingPaths.value)
      }
    }
  }

  /**
   * Check if a path is currently loading
   */
  function isLoading(sessionId, path) {
    const loading = loadingPaths.value.get(sessionId)
    return loading ? loading.has(path) : false
  }

  /**
   * Get entries for a given path
   */
  function getEntries(sessionId, path) {
    const tree = treeBySession.value.get(sessionId)
    return tree ? (tree.get(path) || []) : []
  }

  /**
   * Check if a folder is expanded
   */
  function isExpanded(sessionId, path) {
    const expanded = expandedPaths.value.get(sessionId)
    return expanded ? expanded.has(path) : false
  }

  /**
   * Toggle folder expansion - loads contents on first expand
   */
  async function toggleFolder(sessionId, path) {
    if (!expandedPaths.value.has(sessionId)) {
      expandedPaths.value.set(sessionId, new Set())
    }

    const expanded = expandedPaths.value.get(sessionId)

    if (expanded.has(path)) {
      expanded.delete(path)
    } else {
      expanded.add(path)
      // Load or refresh contents when expanding
      await loadDirectory(sessionId, path)
    }

    expandedPaths.value = new Map(expandedPaths.value)
  }

  /**
   * Refresh a specific folder
   */
  async function refreshFolder(sessionId, path) {
    await loadDirectory(sessionId, path)
  }

  /**
   * Refresh all loaded directories for a session
   */
  async function refreshAll(sessionId) {
    const tree = treeBySession.value.get(sessionId)
    if (!tree) return

    const paths = Array.from(tree.keys())
    await Promise.all(paths.map(p => loadDirectory(sessionId, p)))
  }

  /**
   * Open file in viewer
   */
  async function openFile(filePath) {
    viewerOpen.value = true
    viewerFilePath.value = filePath
    viewerContent.value = null
    viewerSize.value = 0
    viewerTruncated.value = false
    viewerBinary.value = false
    viewerError.value = null
    viewerLoading.value = true

    try {
      const data = await apiGet('/api/filesystem/read', {
        params: { path: filePath }
      })

      viewerSize.value = data.size || 0
      viewerTruncated.value = data.truncated || false
      viewerBinary.value = data.binary || false

      if (data.binary) {
        viewerError.value = data.error || 'Binary file - preview not available'
        viewerContent.value = null
      } else {
        viewerContent.value = data.content
      }
    } catch (e) {
      viewerError.value = e.message || 'Failed to load file'
    } finally {
      viewerLoading.value = false
    }
  }

  /**
   * Close file viewer
   */
  function closeViewer() {
    viewerOpen.value = false
    viewerFilePath.value = null
    viewerContent.value = null
    viewerError.value = null
  }

  /**
   * Toggle hidden files visibility and reload all loaded directories
   */
  async function toggleHidden(sessionId) {
    showHidden.value = !showHidden.value
    if (sessionId) {
      await refreshAll(sessionId)
    }
  }

  /**
   * Handle tool result - auto-refresh affected folder
   * Called from message store when Write/Edit/NotebookEdit completes
   */
  async function onToolResult(sessionId, toolName, filePath) {
    if (!sessionId || !filePath) return

    // Only handle file-modifying tools
    const fileTools = ['Write', 'Edit', 'NotebookEdit']
    if (!fileTools.includes(toolName)) return

    // Extract parent directory
    const lastSep = filePath.lastIndexOf('/')
    const parentDir = lastSep > 0 ? filePath.substring(0, lastSep) : filePath

    // Refresh parent directory if it's loaded
    const tree = treeBySession.value.get(sessionId)
    if (tree && tree.has(parentDir)) {
      await loadDirectory(sessionId, parentDir)
    }
  }

  /**
   * Clear session data
   */
  function clearSession(sessionId) {
    treeBySession.value.delete(sessionId)
    expandedPaths.value.delete(sessionId)
    loadingPaths.value.delete(sessionId)
    treeBySession.value = new Map(treeBySession.value)
    expandedPaths.value = new Map(expandedPaths.value)
    loadingPaths.value = new Map(loadingPaths.value)
  }

  // ========== RETURN ==========
  return {
    // State
    treeBySession,
    expandedPaths,
    loadingPaths,
    showHidden,
    viewerOpen,
    viewerFilePath,
    viewerContent,
    viewerSize,
    viewerTruncated,
    viewerBinary,
    viewerError,
    viewerLoading,

    // Actions
    loadDirectory,
    isLoading,
    getEntries,
    isExpanded,
    toggleFolder,
    refreshFolder,
    refreshAll,
    openFile,
    closeViewer,
    toggleHidden,
    onToolResult,
    clearSession,
  }
})
