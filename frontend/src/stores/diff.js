import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet } from '../utils/api'

/**
 * Diff Store - Manages git diff data for the current session
 *
 * Issue #435: Shows changes since remote main branch in the right sidebar.
 * Two view modes: "total" (aggregated) and "commits" (per-commit).
 * Full-screen diff viewer for individual files.
 */
export const useDiffStore = defineStore('diff', () => {
  // ========== STATE ==========

  // Diff data per session (sessionId -> diff response object)
  const diffBySession = ref(new Map())

  // Loading / error state
  const loading = ref(false)
  const error = ref(null)

  // View mode: "total" or "commits"
  const currentMode = ref('total')

  // Full view modal state
  const fullViewOpen = ref(false)
  const fullViewSessionId = ref(null)
  const currentFilePath = ref(null)
  const fullViewRef = ref(null)

  // Per-file diff content cache (sessionId:ref:path -> { content, loading, error })
  const fileDiffCache = ref(new Map())

  // ========== COMPUTED ==========

  /**
   * Get diff data for the current session
   */
  const currentDiff = computed(() => {
    const sessionStore = useSessionStore()
    if (!sessionStore.currentSessionId) return null
    return diffBySession.value.get(sessionStore.currentSessionId) || null
  })

  /**
   * Sorted array of changed files for current session
   */
  const currentFiles = computed(() => {
    if (!currentDiff.value || !currentDiff.value.files) return []
    return Object.entries(currentDiff.value.files)
      .map(([path, info]) => ({ path, ...info }))
      .sort((a, b) => a.path.localeCompare(b.path))
  })

  /**
   * Number of changed files
   */
  const fileCount = computed(() => currentFiles.value.length)

  /**
   * Number of commits since merge base
   */
  const commitCount = computed(() => {
    if (!currentDiff.value || !currentDiff.value.commits) return 0
    return currentDiff.value.commits.length
  })

  /**
   * Whether diff data indicates a git repo
   */
  const isGitRepo = computed(() => {
    return currentDiff.value?.is_git_repo === true
  })

  /**
   * Files scoped to the current full view context.
   * For commit-specific views, returns only that commit's files.
   * For total/uncommitted views, returns all changed files.
   */
  const fullViewFiles = computed(() => {
    const ref = fullViewRef.value
    if (ref && ref !== 'uncommitted') {
      // Commit-specific: find the commit's file list
      const diff = currentDiff.value
      if (diff?.commits) {
        const commit = diff.commits.find(c => c.hash === ref)
        if (commit?.files) {
          return commit.files.map(p => ({ path: p })).sort((a, b) => a.path.localeCompare(b.path))
        }
      }
      return []
    }
    return currentFiles.value
  })

  /**
   * Current file diff content for full view
   */
  const currentFullViewFileDiff = computed(() => {
    if (!fullViewSessionId.value || !currentFilePath.value) return null
    const refKey = fullViewRef.value || 'committed'
    const key = `${fullViewSessionId.value}:${refKey}:${currentFilePath.value}`
    return fileDiffCache.value.get(key) || null
  })

  // ========== ACTIONS ==========

  /**
   * Load diff summary for a session
   */
  async function loadDiff(sessionId) {
    if (!sessionId) return

    loading.value = true
    error.value = null
    try {
      const response = await apiGet(`/api/sessions/${sessionId}/diff`)
      diffBySession.value.set(sessionId, response)
      diffBySession.value = new Map(diffBySession.value)
    } catch (err) {
      console.error(`Failed to load diff for session ${sessionId}:`, err)
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  /**
   * Refresh diff for a session (force reload)
   */
  async function refreshDiff(sessionId) {
    // Clear cache for this session
    for (const key of fileDiffCache.value.keys()) {
      if (key.startsWith(`${sessionId}:`)) {
        fileDiffCache.value.delete(key)
      }
    }
    fileDiffCache.value = new Map(fileDiffCache.value)
    await loadDiff(sessionId)
  }

  /**
   * Load per-file diff content
   */
  async function loadFileDiff(sessionId, filePath, fileRef = null) {
    if (!sessionId || !filePath) return

    const refKey = fileRef || 'committed'
    const key = `${sessionId}:${refKey}:${filePath}`
    const cached = fileDiffCache.value.get(key)
    if (cached && !cached.error && cached.content !== null) return

    fileDiffCache.value.set(key, { content: null, loading: true, error: null })
    fileDiffCache.value = new Map(fileDiffCache.value)

    try {
      const params = { path: filePath }
      if (fileRef) params.ref = fileRef
      const response = await apiGet(`/api/sessions/${sessionId}/diff/file`, {
        params
      })
      fileDiffCache.value.set(key, { content: response.diff, loading: false, error: null })
    } catch (err) {
      console.error(`Failed to load file diff for ${filePath}:`, err)
      fileDiffCache.value.set(key, { content: null, loading: false, error: err.message })
    }
    fileDiffCache.value = new Map(fileDiffCache.value)
  }

  /**
   * Open full-screen diff view for a file
   */
  function openFullView(sessionId, filePath, fileRef = null) {
    fullViewSessionId.value = sessionId
    currentFilePath.value = filePath
    fullViewRef.value = fileRef
    fullViewOpen.value = true
    loadFileDiff(sessionId, filePath, fileRef)
  }

  /**
   * Close full-screen diff view
   */
  function closeFullView() {
    fullViewOpen.value = false
    fullViewRef.value = null
  }

  /**
   * Navigate to next file in full view
   */
  function nextFile() {
    if (!fullViewSessionId.value) return
    const files = fullViewFiles.value
    if (files.length === 0) return

    const idx = files.findIndex(f => f.path === currentFilePath.value)
    const nextIdx = (idx + 1) % files.length
    currentFilePath.value = files[nextIdx].path
    loadFileDiff(fullViewSessionId.value, currentFilePath.value, fullViewRef.value)
  }

  /**
   * Navigate to previous file in full view
   */
  function prevFile() {
    if (!fullViewSessionId.value) return
    const files = fullViewFiles.value
    if (files.length === 0) return

    const idx = files.findIndex(f => f.path === currentFilePath.value)
    const prevIdx = (idx - 1 + files.length) % files.length
    currentFilePath.value = files[prevIdx].path
    loadFileDiff(fullViewSessionId.value, currentFilePath.value, fullViewRef.value)
  }

  /**
   * Set view mode
   */
  function setMode(mode) {
    currentMode.value = mode
  }

  /**
   * Clear diff data for a session
   */
  function clearDiff(sessionId) {
    diffBySession.value.delete(sessionId)
    diffBySession.value = new Map(diffBySession.value)
    for (const key of fileDiffCache.value.keys()) {
      if (key.startsWith(`${sessionId}:`)) {
        fileDiffCache.value.delete(key)
      }
    }
    fileDiffCache.value = new Map(fileDiffCache.value)
    if (fullViewSessionId.value === sessionId) {
      closeFullView()
    }
  }

  return {
    // State
    diffBySession,
    loading,
    error,
    currentMode,
    fullViewOpen,
    fullViewSessionId,
    currentFilePath,
    fullViewRef,
    fileDiffCache,

    // Computed
    currentDiff,
    currentFiles,
    fileCount,
    commitCount,
    isGitRepo,
    fullViewFiles,
    currentFullViewFileDiff,

    // Actions
    loadDiff,
    refreshDiff,
    loadFileDiff,
    openFullView,
    closeFullView,
    nextFile,
    prevFile,
    setMode,
    clearDiff
  }
})
