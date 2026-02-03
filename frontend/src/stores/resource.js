import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet } from '../utils/api'

/**
 * Resource Store - Manages resources (images, files) displayed via MCP tool per session
 *
 * Issue #404: The register_resource MCP tool allows agents to display resources
 * (screenshots, diagrams, documents, code files) in the task panel. This store manages:
 * - Resource metadata per session
 * - Full view modal state for images
 * - Navigation between resources
 * - Download functionality for all resource types
 */

// File extensions that are images (can be previewed)
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico'])

// File type icons for non-image resources
const FILE_TYPE_ICONS = {
  // Documents
  '.pdf': '📄',
  '.doc': '📝',
  '.docx': '📝',
  '.txt': '📄',
  '.rtf': '📝',
  '.odt': '📝',
  // Spreadsheets
  '.xls': '📊',
  '.xlsx': '📊',
  '.csv': '📊',
  '.ods': '📊',
  // Code
  '.py': '🐍',
  '.js': '📜',
  '.ts': '📜',
  '.jsx': '📜',
  '.tsx': '📜',
  '.html': '🌐',
  '.css': '🎨',
  '.json': '📋',
  '.xml': '📋',
  '.yaml': '📋',
  '.yml': '📋',
  '.md': '📝',
  '.sh': '💻',
  '.bat': '💻',
  '.sql': '🗄️',
  // Archives
  '.zip': '📦',
  '.tar': '📦',
  '.gz': '📦',
  '.rar': '📦',
  '.7z': '📦',
  // Data
  '.log': '📋',
  // Default
  'default': '📎'
}

export const useResourceStore = defineStore('resource', () => {
  // ========== STATE ==========

  // Resources per session (sessionId -> Array<ResourceMetadata>)
  // ResourceMetadata shape: { resource_id, session_id, title, description, format, size_bytes, timestamp, file_path, original_filename }
  const resourcesBySession = ref(new Map())

  // Full view modal state (for images)
  const fullViewOpen = ref(false)
  const currentResourceIndex = ref(0)
  const fullViewSessionId = ref(null)

  // Loading state
  const loading = ref(false)

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
   * Get resources for a specific session as a sorted array
   */
  function resourcesForSession(sessionId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return []

    // Already sorted by timestamp during load/add
    return resources
  }

  /**
   * Get resource count for a session
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
   * Load resources for a session from the backend
   */
  async function loadResources(sessionId) {
    if (!sessionId) return

    loading.value = true
    try {
      const response = await apiGet(`/api/sessions/${sessionId}/resources`)
      const resources = response.resources || []

      // Sort by timestamp (oldest first)
      resources.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))

      resourcesBySession.value.set(sessionId, resources)

      // Trigger reactivity
      resourcesBySession.value = new Map(resourcesBySession.value)

      console.log(`Loaded ${resources.length} resources for session ${sessionId}`)
    } catch (error) {
      console.error(`Failed to load resources for session ${sessionId}:`, error)
      // Set empty array to prevent repeated loading attempts
      resourcesBySession.value.set(sessionId, [])
      resourcesBySession.value = new Map(resourcesBySession.value)
    } finally {
      loading.value = false
    }
  }

  // Backward compatibility alias
  const loadImages = loadResources

  /**
   * Add a new resource from WebSocket resource_registered event
   */
  function addResource(sessionId, resourceMetadata) {
    if (!sessionId || !resourceMetadata) return

    let resources = resourcesBySession.value.get(sessionId)
    if (!resources) {
      resources = []
      resourcesBySession.value.set(sessionId, resources)
    }

    // Check if resource already exists (prevent duplicates)
    const existingIndex = resources.findIndex(r => r.resource_id === resourceMetadata.resource_id)
    if (existingIndex >= 0) {
      // Update existing
      resources[existingIndex] = resourceMetadata
    } else {
      // Add new
      resources.push(resourceMetadata)
    }

    // Sort by timestamp
    resources.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))

    // Trigger reactivity
    resourcesBySession.value = new Map(resourcesBySession.value)

    console.log(`Added resource ${resourceMetadata.resource_id} to session ${sessionId}`)
  }

  // Backward compatibility alias
  const addImage = addResource

  /**
   * Get the resource URL for displaying/downloading a resource
   */
  function getResourceUrl(sessionId, resourceId) {
    return `/api/sessions/${sessionId}/resources/${resourceId}`
  }

  // Backward compatibility alias
  const getImageUrl = getResourceUrl

  /**
   * Get the download URL for a resource
   */
  function getDownloadUrl(sessionId, resourceId) {
    return `/api/sessions/${sessionId}/resources/${resourceId}/download`
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
    // Keep session and index for potential re-open
  }

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

    // Trigger reactivity
    resourcesBySession.value = new Map(resourcesBySession.value)

    // Close full view if viewing this session
    if (fullViewSessionId.value === sessionId) {
      closeFullView()
    }

    console.log(`Cleared resources for session ${sessionId}`)
  }

  // Backward compatibility alias
  const clearImages = clearResources

  /**
   * Get a resource by ID
   */
  function getResourceById(sessionId, resourceId) {
    const resources = resourcesBySession.value.get(sessionId)
    if (!resources) return null
    return resources.find(r => r.resource_id === resourceId) || null
  }

  // ========== RETURN ==========
  return {
    // State
    resourcesBySession,
    fullViewOpen,
    currentResourceIndex,
    fullViewSessionId,
    loading,

    // Computed - Resources
    currentResources,
    currentResourceCount,
    currentHasResources,
    currentFullViewResource,
    fullViewTotalResources,

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
    getResourceUrl,
    getDownloadUrl,
    isImageResource,
    getResourceIcon,
    getResourceExtension,
    getResourceById,

    // Getters - Images (backward compatibility)
    imagesForSession,
    imageCount,
    hasImages,
    getImageUrl,

    // Actions
    loadResources,
    addResource,
    openFullView,
    closeFullView,
    nextResource,
    prevResource,
    goToResource,
    clearResources,

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
