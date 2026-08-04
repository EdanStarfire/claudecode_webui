import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet, apiDelete, getAuthToken } from '../utils/api'
import { IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, FILE_TYPE_ICONS } from '../utils/fileTypes'

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
  const directImageData = ref(null)
  const directImageMime = ref(null)

  // Loading state (legacy global; per-session loading is in paginationBySession)
  const loading = ref(false)

  // Text content cache (resourceId -> { content, loading, error })
  const textContentCache = ref(new Map())

  // Archive context: sessionId -> { projectId, archiveId }
  const archiveContext = ref(new Map())

  // Issue #1680: expanded version-group state, keyed by `${sessionId}:${groupKey}`
  const expandedResourceGroups = ref(new Set())

  // Issue #1680: when the full view is showing a superseded (non-latest) version
  // located inside a group's `versions` array, it's pinned here instead of being
  // addressed by index into the top-level (grouped) resources array.
  const fullViewPinnedResource = ref(null)

  // ========== HELPER FUNCTIONS ==========

  /**
   * Version-group key for a resource: case-insensitive original filename.
   * Issue #1680: must match the backend's _group_resources_by_filename() key.
   */
  function _groupKey(resource) {
    return (resource?.original_name || '').toLowerCase()
  }

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
   * Check if a resource is a video based on its format/extension
   */
  function isVideoResource(resource) {
    if (!resource) return false

    // Check explicit is_video flag from backend
    if (resource.is_video === true) return true

    // Check mime_type field (e.g., "video/mp4")
    const mimeType = (resource.mime_type || '').toLowerCase()
    if (mimeType.startsWith('video/')) return true

    // Check format field
    const format = (resource.format || '').toLowerCase()
    if (format.startsWith('video/')) return true
    if (VIDEO_EXTENSIONS.has('.' + format)) return true

    // Check original filename extension
    const filename = resource.original_filename || resource.original_name || ''
    if (filename) {
      const ext = '.' + filename.split('.').pop().toLowerCase()
      return VIDEO_EXTENSIONS.has(ext)
    }
    return false
  }

  /**
   * Check if a resource is an HTML file
   */
  function isHtmlResource(resource) {
    if (!resource) return false
    if (isImageResource(resource)) return false
    if (isVideoResource(resource)) return false

    const mimeType = (resource.mime_type || '').toLowerCase()
    if (mimeType === 'text/html') return true

    const format = (resource.format || '').toLowerCase()
    if (format === 'text/html' || format === 'html' || format === 'htm') return true

    const filename = resource.original_filename || resource.original_name || ''
    if (filename) {
      const ext = '.' + filename.split('.').pop().toLowerCase()
      if (ext === '.html' || ext === '.htm') return true
    }
    return false
  }

  /**
   * Check if a resource is a text-based file that can be previewed as text
   */
  function isTextResource(resource) {
    if (!resource) return false
    if (isImageResource(resource)) return false
    if (isVideoResource(resource)) return false

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
    if (isVideoResource(resource)) return '🎬'
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
   * Issue #1680: when a superseded (non-latest) version is pinned, it takes precedence
   * over the index into the top-level (grouped) resources array.
   */
  const currentFullViewResource = computed(() => {
    if (!fullViewOpen.value || !fullViewSessionId.value) return null
    if (fullViewPinnedResource.value) return fullViewPinnedResource.value
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
    if (fullViewPinnedResource.value) return 1
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
   * Issue #1680: A resource re-registered under an existing filename merges into that
   * group as a new version instead of creating a second top-level card. Total tracks
   * distinct filename groups, so merges don't increment it.
   */
  function addResource(sessionId, resourceMetadata) {
    if (!sessionId || !resourceMetadata) return

    const existing = resourcesBySession.value.get(sessionId) || []

    // Already tracked as a group's current representative — update in place.
    const topIndex = existing.findIndex(r => r.resource_id === resourceMetadata.resource_id)
    if (topIndex >= 0) {
      existing[topIndex] = { ...existing[topIndex], ...resourceMetadata }
      resourcesBySession.value = new Map(resourcesBySession.value)
      return
    }

    // Already tracked as a superseded version within a group — nothing to update.
    const nestedMatch = existing.some(
      r => (r.versions || []).some(v => v.resource_id === resourceMetadata.resource_id)
    )
    if (nestedMatch) return

    // A new version of a group already visible locally merges in place — this holds
    // regardless of whether a search/format filter is active, since it's not a new
    // distinct-filename group and must not inflate the group-count total.
    const groupKey = _groupKey(resourceMetadata)
    const groupIndex = existing.findIndex(r => _groupKey(r) === groupKey)
    if (groupIndex >= 0) {
      const group = existing[groupIndex]
      const priorVersions = group.versions || [{ ...group, version_number: 1 }]
      const versionNumber = (group.version_count || 1) + 1
      const versions = [{ ...resourceMetadata, version_number: versionNumber }, ...priorVersions]

      existing.splice(groupIndex, 1)
      existing.unshift({ ...resourceMetadata, version_count: versionNumber, versions })
      resourcesBySession.value = new Map(resourcesBySession.value)
      console.log(`Added resource ${resourceMetadata.resource_id} to session ${sessionId}`)
      return
    }

    const hasActiveFilter = currentFilter.value.search || currentFilter.value.formatFilter
    if (hasActiveFilter) {
      // Active filter and no locally-visible group match: can't tell whether this is a
      // new group or a version of a group the filter is currently hiding. Only bump
      // total (pre-existing #972 approximation — resource may or may not actually match).
      const pagination = paginationBySession.value.get(sessionId)
      if (pagination) {
        paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total + 1 })
        paginationBySession.value = new Map(paginationBySession.value)
      }
      console.log(`Added resource ${resourceMetadata.resource_id} to session ${sessionId}`)
      return
    }

    // No active filter, no existing group: brand-new distinct filename group.
    resourcesBySession.value.set(sessionId, [{ ...resourceMetadata, version_count: 1 }, ...existing])
    resourcesBySession.value = new Map(resourcesBySession.value)

    const pagination = paginationBySession.value.get(sessionId)
    if (pagination) {
      paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total + 1 })
      paginationBySession.value = new Map(paginationBySession.value)
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
    fullViewPinnedResource.value = null
    fullViewSessionId.value = sessionId
    currentResourceIndex.value = Math.max(0, Math.min(index, resourceCount(sessionId) - 1))
    fullViewOpen.value = true
  }

  /**
   * Close full view modal
   */
  function closeFullView() {
    fullViewOpen.value = false
    fullViewPinnedResource.value = null
    directContent.value = null
    directTitle.value = null
    directImageData.value = null
    directImageMime.value = null
  }

  /**
   * Open full view with arbitrary text content (no resource required).
   * Used by tool handlers to show truncated output in full screen.
   */
  function openWithDirectContent(title, content) {
    directTitle.value = title
    directContent.value = content
    directImageData.value = null
    directImageMime.value = null
    fullViewOpen.value = true
  }

  /**
   * Open full view with a direct base64 image (no resource required).
   * Used by ReadToolHandler and TimelineDetail when tool result contains image content.
   */
  function openWithDirectImage(title, base64Data, mimeType) {
    directTitle.value = title
    directContent.value = null
    directImageData.value = base64Data
    directImageMime.value = mimeType || 'image/png'
    fullViewOpen.value = true
  }

  /**
   * Whether the full view is showing direct content (not a resource)
   */
  const isDirectContentMode = computed(() => directContent.value != null)

  /**
   * Whether the full view is showing a direct image (not a resource)
   */
  const isDirectImageMode = computed(() => directImageData.value != null)

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
   *
   * Issue #1680: resourceId may be a group's current representative (top-level entry),
   * in which case the next-newest surviving version is promoted to represent the group;
   * or a superseded version nested in some group's `versions` array, in which case only
   * that version is removed. The top-level (group) count only changes when a whole group
   * is removed (its last remaining version deleted).
   */
  function _spliceResource(sessionId, resourceId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return

    let groupRemoved = false
    const topIndex = resources.findIndex(r => r.resource_id === resourceId)

    if (topIndex >= 0) {
      const group = resources[topIndex]
      const versions = group.versions
      if (versions && versions.length > 1) {
        // Promote the next-newest surviving version to represent the group.
        const remaining = versions.filter(v => v.resource_id !== resourceId)
        const promoted = { ...remaining[0], version_count: remaining.length }
        if (remaining.length > 1) {
          promoted.versions = remaining
        }
        resources[topIndex] = promoted
      } else {
        resources.splice(topIndex, 1)
        groupRemoved = true
      }
    } else {
      // Look for a superseded version nested inside some group's `versions` array.
      let found = false
      for (const group of resources) {
        if (!group.versions) continue
        const vIdx = group.versions.findIndex(v => v.resource_id === resourceId)
        if (vIdx >= 0) {
          group.versions.splice(vIdx, 1)
          group.version_count = group.versions.length
          if (group.versions.length <= 1) delete group.versions
          found = true
          break
        }
      }
      if (!found) return
    }

    // Trigger reactivity
    resourcesBySession.value = new Map(resourcesBySession.value)

    // Decrement total (group) count only when a whole group was removed
    if (groupRemoved) {
      const pagination = paginationBySession.value.get(sessionId)
      if (pagination && pagination.total > 0) {
        paginationBySession.value.set(sessionId, { ...pagination, total: pagination.total - 1 })
        paginationBySession.value = new Map(paginationBySession.value)
      }
    }

    // Adjust full view state if viewing this session
    if (fullViewOpen.value && fullViewSessionId.value === sessionId) {
      if (fullViewPinnedResource.value?.resource_id === resourceId) {
        closeFullView()
      } else if (groupRemoved) {
        if (resources.length === 0) {
          closeFullView()
        } else if (currentResourceIndex.value >= resources.length) {
          currentResourceIndex.value = resources.length - 1
        }
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
   * Get a resource by ID.
   * Issue #1680: also searches each group's `versions` array, since a resource_id
   * referenced by an older chat attachment may now be a superseded (non-latest) version.
   */
  function getResourceById(sessionId, resourceId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return null
    const top = resources.find(r => r.resource_id === resourceId)
    if (top) return top
    for (const group of resources) {
      const nested = (group.versions || []).find(v => v.resource_id === resourceId)
      if (nested) return nested
    }
    return null
  }

  /**
   * Open full view modal by resource ID (for attachment chips that only know the resource_id).
   * Issue #1680: if the ID belongs to a superseded version nested in a group, it's pinned
   * for direct display since it no longer has an index in the top-level (grouped) array.
   */
  function openFullViewById(resourceId, sessionId) {
    const sessionStore = useSessionStore()
    const sid = sessionId || sessionStore.currentSessionId
    const resources = resourcesBySession.value.get(sid) || []

    const topIndex = resources.findIndex(r => r.resource_id === resourceId)
    if (topIndex >= 0) {
      openFullView(sid, topIndex)
      return
    }

    for (const group of resources) {
      const nested = (group.versions || []).find(v => v.resource_id === resourceId)
      if (nested) {
        fullViewSessionId.value = sid
        fullViewPinnedResource.value = nested
        currentResourceIndex.value = 0
        fullViewOpen.value = true
        return
      }
    }
  }

  /**
   * Toggle expand/collapse of a resource's version group in the gallery.
   */
  function toggleResourceGroup(sessionId, groupKey) {
    const key = `${sessionId}:${groupKey}`
    if (expandedResourceGroups.value.has(key)) {
      expandedResourceGroups.value.delete(key)
    } else {
      expandedResourceGroups.value.add(key)
    }
    expandedResourceGroups.value = new Set(expandedResourceGroups.value)
  }

  /**
   * Whether a resource's version group is currently expanded in the gallery.
   */
  function isResourceGroupExpanded(sessionId, groupKey) {
    return expandedResourceGroups.value.has(`${sessionId}:${groupKey}`)
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
    directImageData,
    directImageMime,
    expandedResourceGroups,

    // Computed - Resources
    currentResources,
    currentResourceCount,
    currentHasResources,
    currentPagination,
    currentFullViewResource,
    fullViewTotalResources,
    isDirectContentMode,
    isDirectImageMode,

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
    isVideoResource,
    isHtmlResource,
    isTextResource,
    getResourceIcon,
    getResourceExtension,
    getResourceById,
    getTextContent,
    getResourceGroupKey: _groupKey,

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
    openWithDirectImage,
    closeFullView,
    nextResource,
    prevResource,
    goToResource,
    clearResources,
    fetchTextContent,
    clearTextCache,
    openFullViewById,
    toggleResourceGroup,
    isResourceGroupExpanded,

    // Actions - Backward compatibility
    loadImages,
    addImage,
    nextImage,
    prevImage,
    goToImage,
    clearImages
  }
})
