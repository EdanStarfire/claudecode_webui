import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet, apiDelete, getAuthToken } from '../utils/api'
import { IMAGE_EXTENSIONS, FILE_TYPE_ICONS } from '../utils/fileTypes'

/**
 * Resource Store - Manages resources (images, files) displayed via MCP tool per session
 *
 * Issue #404: The register_resource MCP tool allows agents to display resources
 * (screenshots, diagrams, documents, code files) in the task panel. This store manages:
 * - Resource metadata per session
 * - Full view modal state for images
 * - Navigation between resources
 * - Download functionality for all resource types
 *
 * Issue #972: Added server-side pagination, filtering, and sorting.
 */

export const useResourceStore = defineStore('resource', () => {
  // ========== STATE ==========

  // Resources per session (sessionId -> Array<ResourceMetadata>)
  // ResourceMetadata shape: { resource_id, session_id, title, description, format, size_bytes, timestamp, file_path, original_filename }
  const resourcesBySession = ref(new Map())

  // Pagination state per session (sessionId -> { total, hasMore, loading, offset })
  const paginationBySession = ref(new Map())

  // Current filter/sort state (shared across all sessions — resets on session switch)
  const currentFilter = ref({ search: '', formatFilter: '', sort: 'newest' })

  // Full view modal state (for images)
  const fullViewOpen = ref(false)
  const currentResourceIndex = ref(0)
  const fullViewSessionId = ref(null)

  // Direct content mode (for viewing tool output in full screen)
  const directContent = ref(null)
  const directTitle = ref(null)

  // Loading state (legacy global; per-session loading is in paginationBySession)
  const loading = ref(false)

  // Text content cache (resourceId -> { content, loading, error })
  const textContentCache = ref(new Map())

  // Archive context: sessionId -> { projectId, archiveId }
  const archiveContext = ref(new Map())

  // ========== HELPER FUNCTIONS ==========

  /**
   * Check if a resource is an image based on its format/extension
   */
  function isImageResource(resource) {
    if (!resource) return false

    // Check explicit is_image flag from backend
    if (resource.is_image === true) return true

    // Check mime_type field (e.g., "image/jpeg")
    const mimeType = (resource.mime_type || '').toLowerCase()
    if (mimeType.startsWith('image/')) return true

    // Check format field - could be mime type or extension
    const format = (resource.format || '').toLowerCase()
    if (format.startsWith('image/')) return true
    // Also check if format is just the extension (e.g., "jpeg", "png")
    if (IMAGE_EXTENSIONS.has('.' + format)) return true

    // Check original filename extension
    const filename = resource.original_filename || resource.original_name || ''
    if (filename) {
      const ext = '.' + filename.split('.').pop().toLowerCase()
      return IMAGE_EXTENSIONS.has(ext)
    }
    return false
  }

  /**
   * Check if a resource is a text-based file that can be previewed as text
   */
  function isTextResource(resource) {
    if (!resource) return false
    if (isImageResource(resource)) return false

    const textExtensions = new Set([
      '.txt', '.log', '.md', '.json', '.xml', '.yaml', '.yml', '.csv',
      '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss',
      '.sh', '.bat', '.sql', '.toml', '.ini', '.cfg', '.conf',
      '.env', '.gitignore', '.dockerfile', '.vue', '.svelte',
      '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp', '.rb', '.php'
    ])

    const filename = resource.original_filename || resource.original_name || ''
    if (filename) {
      const ext = '.' + filename.split('.').pop().toLowerCase()
      if (textExtensions.has(ext)) return true
    }

    const mimeType = (resource.mime_type || resource.format || '').toLowerCase()
    if (mimeType.startsWith('text/')) return true
    if (mimeType === 'application/json' || mimeType === 'application/xml') return true

    return false
  }

  /**
   * Get the icon for a resource type
   */
  function getResourceIcon(resource) {
    if (isImageResource(resource)) return '🖼️'
    if (resource.original_filename) {
      const ext = '.' + resource.original_filename.split('.').pop().toLowerCase()
      return FILE_TYPE_ICONS[ext] || FILE_TYPE_ICONS['default']
    }
    return FILE_TYPE_ICONS['default']
  }

  /**
   * Get file extension from resource
   */
  function getResourceExtension(resource) {
    if (resource.original_filename) {
      return '.' + resource.original_filename.split('.').pop().toLowerCase()
    }
    return ''
  }

  // ========== COMPUTED ==========

  /**
   * Get resources for a specific session as an array
   */
  function resourcesForSession(sessionId) {
    return resourcesBySession.value.get(sessionId) || []
  }

  /**
   * Get resource count for a session (loaded count, not total)
   */
  function resourceCount(sessionId) {
    return resourcesBySession.value.get(sessionId)?.length || 0
  }

  /**
   * Check if a session has any resources
   */
  function hasResources(sessionId) {
    return resourceCount(sessionId) > 0
  }

  /**
   * Get pagination state for a session
   */
  function paginationForSession(sessionId) {
    return paginationBySession.value.get(sessionId) || { total: 0, hasMore: false, loading: false, offset: 0 }
  }

  /**
   * Get images only for a session (for backward compatibility)
   */
  function imagesForSession(sessionId) {
    return resourcesForSession(sessionId).filter(isImageResource)
  }

  /**
   * Get image count for a session
   */
  function imageCount(sessionId) {
    return imagesForSession(sessionId).length
  }

  /**
   * Check if a session has any images
   */
  function hasImages(sessionId) {
    return imageCount(sessionId) > 0
  }

  /**
   * Current session's resources (computed)
   */
  const currentResources = computed(() => {
    const sessionStore = useSessionStore()
    return resourcesForSession(sessionStore.currentSessionId)
  })

  /**
   * Current session's resource count (computed)
   */
  const currentResourceCount = computed(() => {
    const sessionStore = useSessionStore()
    return resourceCount(sessionStore.currentSessionId)
  })

  /**
   * Check if current session has resources (computed)
   */
  const currentHasResources = computed(() => {
    return currentResourceCount.value > 0
  })

  /**
   * Current session pagination state (computed)
   */
  const currentPagination = computed(() => {
    const sessionStore = useSessionStore()
    return paginationForSession(sessionStore.currentSessionId)
  })

  // Backward compatibility aliases
  const currentImages = computed(() => {
    return currentResources.value.filter(isImageResource)
  })

  const currentImageCount = computed(() => {
    return currentImages.value.length
  })

  const currentHasImages = computed(() => {
    return currentImageCount.value > 0
  })

  /**
   * Currently displayed resource in full view (computed)
   */
  const currentFullViewResource = computed(() => {
    if (!fullViewOpen.value || !fullViewSessionId.value) return null
    const resources = resourcesForSession(fullViewSessionId.value)
    return resources[currentResourceIndex.value] || null
  })

  // Backward compatibility alias
  const currentFullViewImage = currentFullViewResource

  /**
   * Total resources in full view session (computed)
   */
  const fullViewTotalResources = computed(() => {
    if (!fullViewSessionId.value) return 0
    return resourceCount(fullViewSessionId.value)
  })

  // Backward compatibility alias
  const fullViewTotalImages = fullViewTotalResources

  // ========== ACTIONS ==========

  /**
   * Load resources for a session from the backend.
   * Issue #972: Supports pagination (append mode) and passes current filter/sort to server.
   */
  async function loadResources(sessionId, { append = false } = {}) {
    if (!sessionId) return

    const existing = paginationBySession.value.get(sessionId) || { total: 0, hasMore: false, loading: false, offset: 0 }
    if (existing.loading) return

    // Mark loading
    paginationBySession.value.set(sessionId, { ...existing, loading: true })
    paginationBySession.value = new Map(paginationBySession.value)
    loading.value = true

    const offset = append ? (resourcesBySession.value.get(sessionId)?.length || 0) : 0
    const limit = 50

    const params = { limit, offset, sort: currentFilter.value.sort }
    if (currentFilter.value.search) params.search = currentFilter.value.search
    if (currentFilter.value.formatFilter) params.format_filter = currentFilter.value.formatFilter

    try {
      const ctx = archiveContext.value.get(sessionId)
      let response
      if (ctx) {
        response = await apiGet(
          `/api/projects/${ctx.projectId}/archives/${sessionId}/${ctx.archiveId}/resources`,
          { params }
        )
      } else {
        response = await apiGet(`/api/sessions/${sessionId}/resources`, { params })
      }

      const newResources = response.resources || []

      if (append) {
        const prev = resourcesBySession.value.get(sessionId) || []
        resourcesBySession.value.set(sessionId, [...prev, ...newResources])
      } else {
        resourcesBySession.value.set(sessionId, newResources)
      }
      resourcesBySession.value = new Map(resourcesBySession.value)

      paginationBySession.value.set(sessionId, {
        total: response.total ?? newResources.length,
        hasMore: response.has_more ?? false,
        loading: false,
        offset: offset + newResources.length,
      })
      paginationBySession.value = new Map(paginationBySession.value)

      console.log(`Loaded ${newResources.length} resources for session ${sessionId} (total ${response.total})`)
    } catch (error) {
      console.error(`Failed to load resources for session ${sessionId}:`, error)
      if (!append) {
        resourcesBySession.value.set(sessionId, [])
        resourcesBySession.value = new Map(resourcesBySession.value)
      }
      paginationBySession.value.set(sessionId, { ...existing, loading: false })
      paginationBySession.value = new Map(paginationBySession.value)
    } finally {
      loading.value = false
    }
  }

  // Backward compatibility alias
  const loadImages = loadResources

  /**
   * Load more resources (append next page).
   * Issue #972: Called by Load More button in ResourceGallery.
   */
  async function loadMore(sessionId) {
    await loadResources(sessionId, { append: true })
  }

  /**
   * Apply a filter/sort and reload from scratch.
   * Issue #972: Called when search, formatFilter, or sort changes.
   */
  async function applyFilter(sessionId, { search, formatFilter, sort } = {}) {
    currentFilter.value = {
      search: search ?? '',
      formatFilter: formatFilter ?? '',
      sort: sort ?? 'newest',
    }
    await loadResources(sessionId)
  }

  /**
   * Load resources for an archived session from the archive endpoint.
   * Issue #972: Uses unified loadResources() with archive context set.
   */
  async function loadArchiveResources(sessionId, projectId, archiveId) {
    if (!sessionId || !projectId || !archiveId) return

    // Store archive context so loadResources() builds the correct URL
    archiveContext.value.set(sessionId, { projectId, archiveId })
    archiveContext.value = new Map(archiveContext.value)

    await loadResources(sessionId)
  }

  /**
   * Clear archive context for a session (when leaving archive mode)
   */
  function clearArchiveContext(sessionId) {
    archiveContext.value.delete(sessionId)
    archiveContext.value = new Map(archiveContext.value)
  }

  /**
   * Add a new resource from WebSocket resource_registered event.
   * Issue #972: Increments total; only prepends if no active filter (or resource matches filter).
   */
  function addResource(sessionId, resourceMetadata) {
    if (!sessionId || !resourceMetadata) return

    // Check if resource already exists (prevent duplicates)
    const existing = resourcesBySession.value.get(sessionId) || []
    const existingIndex = existing.findIndex(r => r.resource_id === resourceMetadata.resource_id)

    const hasActiveFilter = currentFilter.value.search || currentFilter.value.formatFilter

    if (existingIndex >= 0) {
      // Update existing
      existing[existingIndex] = resourceMetadata
      resourcesBySession.value = new Map(resourcesBySession.value)
    } else if (!hasActiveFilter) {
      // No active filter: prepend (newest first to match default sort)
      resourcesBySession.value.set(sessionId, [resourceMetadata, ...existing])
      resourcesBySession.value = new Map(resourcesBySession.value)

      // Increment total
      const pagination = paginationBySession.value.get(sessionId)
      if (pagination) {
        paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total + 1 })
        paginationBySession.value = new Map(paginationBySession.value)
      }
    } else {
      // Active filter: only increment total count (resource may or may not match)
      const pagination = paginationBySession.value.get(sessionId)
      if (pagination) {
        paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total + 1 })
        paginationBySession.value = new Map(paginationBySession.value)
      }
    }

    console.log(`Added resource ${resourceMetadata.resource_id} to session ${sessionId}`)
  }

  // Backward compatibility alias
  const addImage = addResource

  /**
   * Get the resource URL for displaying/downloading a resource.
   * Returns archive endpoint URL when viewing an archived session.
   */
  function getResourceUrl(sessionId, resourceId) {
    const ctx = archiveContext.value.get(sessionId)
    let url
    if (ctx) {
      url = `/api/projects/${ctx.projectId}/archives/${sessionId}/${ctx.archiveId}/resources/${resourceId}`
    } else {
      url = `/api/sessions/${sessionId}/resources/${resourceId}`
    }
    const token = getAuthToken()
    if (token) {
      url += `?token=${encodeURIComponent(token)}`
    }
    return url
  }

  // Backward compatibility alias
  const getImageUrl = getResourceUrl

  /**
   * Get the download URL for a resource
   */
  function getDownloadUrl(sessionId, resourceId) {
    let url = `/api/sessions/${sessionId}/resources/${resourceId}/download`
    const token = getAuthToken()
    if (token) {
      url += `?token=${encodeURIComponent(token)}`
    }
    return url
  }

  /**
   * Open full view modal for a resource (primarily for images)
   */
  function openFullView(sessionId, index = 0) {
    fullViewSessionId.value = sessionId
    currentResourceIndex.value = Math.max(0, Math.min(index, resourceCount(sessionId) - 1))
    fullViewOpen.value = true
  }

  /**
   * Close full view modal
   */
  function closeFullView() {
    fullViewOpen.value = false
    directContent.value = null
    directTitle.value = null
  }

  /**
   * Open full view with arbitrary text content (no resource required).
   * Used by tool handlers to show truncated output in full screen.
   */
  function openWithDirectContent(title, content) {
    directTitle.value = title
    directContent.value = content
    fullViewOpen.value = true
  }

  /**
   * Whether the full view is showing direct content (not a resource)
   */
  const isDirectContentMode = computed(() => directContent.value != null)

  /**
   * Navigate to next resource in full view
   */
  function nextResource() {
    if (!fullViewSessionId.value) return
    const total = resourceCount(fullViewSessionId.value)
    if (total === 0) return

    currentResourceIndex.value = (currentResourceIndex.value + 1) % total
  }

  // Backward compatibility alias
  const nextImage = nextResource

  /**
   * Navigate to previous resource in full view
   */
  function prevResource() {
    if (!fullViewSessionId.value) return
    const total = resourceCount(fullViewSessionId.value)
    if (total === 0) return

    currentResourceIndex.value = (currentResourceIndex.value - 1 + total) % total
  }

  // Backward compatibility alias
  const prevImage = prevResource

  /**
   * Navigate to specific resource index
   */
  function goToResource(index) {
    if (!fullViewSessionId.value) return
    const total = resourceCount(fullViewSessionId.value)
    if (total === 0) return

    currentResourceIndex.value = Math.max(0, Math.min(index, total - 1))
  }

  // Backward compatibility alias
  const goToImage = goToResource

  /**
   * Clear all resources for a session (for session reset)
   */
  function clearResources(sessionId) {
    resourcesBySession.value.delete(sessionId)
    resourcesBySession.value = new Map(resourcesBySession.value)

    paginationBySession.value.delete(sessionId)
    paginationBySession.value = new Map(paginationBySession.value)

    // Close full view if viewing this session
    if (fullViewSessionId.value === sessionId) {
      closeFullView()
    }

    console.log(`Cleared resources for session ${sessionId}`)
  }

  // Backward compatibility alias
  const clearImages = clearResources

  /**
   * Remove a resource from the session display (soft-remove via API).
   * Issue #423: The resource file is preserved on disk.
   */
  async function removeResource(sessionId, resourceId) {
    if (!sessionId || !resourceId) return

    try {
      await apiDelete(`/api/sessions/${sessionId}/resources/${resourceId}`)
      // Optimistically remove from local state
      _spliceResource(sessionId, resourceId)
    } catch (error) {
      console.error(`Failed to remove resource ${resourceId}:`, error)
    }
  }

  /**
   * Handle resource_removed WebSocket event (multi-client sync).
   * Issue #423: Called from websocket store when another client removes a resource.
   */
  function handleResourceRemoved(sessionId, resourceId) {
    _spliceResource(sessionId, resourceId)
  }

  /**
   * Internal: splice a resource out of local state and adjust full view index.
   */
  function _spliceResource(sessionId, resourceId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return

    const idx = resources.findIndex(r => r.resource_id === resourceId)
    if (idx === -1) return

    resources.splice(idx, 1)

    // Trigger reactivity
    resourcesBySession.value = new Map(resourcesBySession.value)

    // Decrement total count
    const pagination = paginationBySession.value.get(sessionId)
    if (pagination && pagination.total > 0) {
      paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total - 1 })
      paginationBySession.value = new Map(paginationBySession.value)
    }

    // Adjust full view index if viewing this session
    if (fullViewOpen.value && fullViewSessionId.value === sessionId) {
      if (resources.length === 0) {
        closeFullView()
      } else if (currentResourceIndex.value >= resources.length) {
        currentResourceIndex.value = resources.length - 1
      }
    }

    // Clear text cache for removed resource
    textContentCache.value.delete(resourceId)
    textContentCache.value = new Map(textContentCache.value)
  }

  /**
   * Fetch text content for a resource and cache it
   */
  async function fetchTextContent(sessionId, resourceId) {
    if (!sessionId || !resourceId) return null

    // Return cached content if available
    const cached = textContentCache.value.get(resourceId)
    if (cached && !cached.error) return cached.content

    // Mark as loading
    textContentCache.value.set(resourceId, { content: null, loading: true, error: null })
    textContentCache.value = new Map(textContentCache.value)

    try {
      const url = getResourceUrl(sessionId, resourceId)
      const headers = {}
      const token = getAuthToken()
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }
      const response = await fetch(url, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)

      const text = await response.text()
      textContentCache.value.set(resourceId, { content: text, loading: false, error: null })
      textContentCache.value = new Map(textContentCache.value)
      return text
    } catch (error) {
      console.error(`Failed to fetch text content for resource ${resourceId}:`, error)
      textContentCache.value.set(resourceId, { content: null, loading: false, error: error.message })
      textContentCache.value = new Map(textContentCache.value)
      return null
    }
  }

  /**
   * Get cached text content for a resource
   */
  function getTextContent(resourceId) {
    return textContentCache.value.get(resourceId) || null
  }

  /**
   * Clear text content cache for a session's resources
   */
  function clearTextCache(sessionId) {
    if (sessionId) {
      const resources = resourcesBySession.value.get(sessionId)
      if (resources) {
        resources.forEach(r => textContentCache.value.delete(r.resource_id))
        textContentCache.value = new Map(textContentCache.value)
      }
    } else {
      textContentCache.value = new Map()
    }
  }

  /**
   * Get a resource by ID
   */
  function getResourceById(sessionId, resourceId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return null
    return resources.find(r => r.resource_id === resourceId) || null
  }

  /**
   * Open full view modal by resource ID (for attachment chips that only know the resource_id)
   */
  function openFullViewById(resourceId, sessionId) {
    const sessionStore = useSessionStore()
    const sid = sessionId || sessionStore.currentSessionId
    const resources = resourcesBySession.value.get(sid) || []
    const index = resources.findIndex(r => r.resource_id === resourceId)
    if (index >= 0) openFullView(sid, index)
  }

  // ========== RETURN ==========
  return {
    // State
    resourcesBySession,
    paginationBySession,
    currentFilter,
    fullViewOpen,
    currentResourceIndex,
    fullViewSessionId,
    loading,
    textContentCache,
    directContent,
    directTitle,

    // Computed - Resources
    currentResources,
    currentResourceCount,
    currentHasResources,
    currentPagination,
    currentFullViewResource,
    fullViewTotalResources,
    isDirectContentMode,

    // Computed - Images (backward compatibility)
    currentImages,
    currentImageCount,
    currentHasImages,
    currentFullViewImage,
    fullViewTotalImages,

    // Backward compatibility state alias
    imagesBySession: resourcesBySession,

    // Getters (functions)
    resourcesForSession,
    resourceCount,
    hasResources,
    paginationForSession,
    getResourceUrl,
    getDownloadUrl,
    isImageResource,
    isTextResource,
    getResourceIcon,
    getResourceExtension,
    getResourceById,
    getTextContent,

    // Getters - Images (backward compatibility)
    imagesForSession,
    imageCount,
    hasImages,
    getImageUrl,

    // Actions
    loadResources,
    loadMore,
    applyFilter,
    loadArchiveResources,
    clearArchiveContext,
    addResource,
    removeResource,
    handleResourceRemoved,
    openFullView,
    openWithDirectContent,
    closeFullView,
    nextResource,
    prevResource,
    goToResource,
    clearResources,
    fetchTextContent,
    clearTextCache,
    openFullViewById,

    // Actions - Backward compatibility
    loadImages,
    addImage,
    nextImage,
    prevImage,
    goToImage,
    clearImages
  }
})

// Backward compatibility alias
export const useImageStore = useResourceStore
