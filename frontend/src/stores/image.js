import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { apiGet } from '../utils/api'

/**
 * Image Store - Manages images displayed via MCP tool per session
 *
 * Issue #404: The register_image MCP tool allows agents to display images
 * (screenshots, diagrams) in the task panel. This store manages:
 * - Image metadata per session
 * - Full view modal state
 * - Navigation between images
 */
export const useImageStore = defineStore('image', () => {
  // ========== STATE ==========

  // Images per session (sessionId -> Array<ImageMetadata>)
  // ImageMetadata shape: { image_id, session_id, title, description, format, size_bytes, timestamp }
  const imagesBySession = ref(new Map())

  // Full view modal state
  const fullViewOpen = ref(false)
  const currentImageIndex = ref(0)
  const fullViewSessionId = ref(null)

  // Loading state
  const loading = ref(false)

  // ========== COMPUTED ==========

  /**
   * Get images for a specific session as a sorted array
   */
  function imagesForSession(sessionId) {
    const images = imagesBySession.value.get(sessionId)
    if (!images) return []

    // Already sorted by timestamp during load/add
    return images
  }

  /**
   * Get image count for a session
   */
  function imageCount(sessionId) {
    return imagesBySession.value.get(sessionId)?.length || 0
  }

  /**
   * Check if a session has any images
   */
  function hasImages(sessionId) {
    return imageCount(sessionId) > 0
  }

  /**
   * Current session's images (computed)
   */
  const currentImages = computed(() => {
    const sessionStore = useSessionStore()
    return imagesForSession(sessionStore.currentSessionId)
  })

  /**
   * Current session's image count (computed)
   */
  const currentImageCount = computed(() => {
    const sessionStore = useSessionStore()
    return imageCount(sessionStore.currentSessionId)
  })

  /**
   * Check if current session has images (computed)
   */
  const currentHasImages = computed(() => {
    return currentImageCount.value > 0
  })

  /**
   * Currently displayed image in full view (computed)
   */
  const currentFullViewImage = computed(() => {
    if (!fullViewOpen.value || !fullViewSessionId.value) return null
    const images = imagesForSession(fullViewSessionId.value)
    return images[currentImageIndex.value] || null
  })

  /**
   * Total images in full view session (computed)
   */
  const fullViewTotalImages = computed(() => {
    if (!fullViewSessionId.value) return 0
    return imageCount(fullViewSessionId.value)
  })

  // ========== ACTIONS ==========

  /**
   * Load images for a session from the backend
   */
  async function loadImages(sessionId) {
    if (!sessionId) return

    loading.value = true
    try {
      const response = await apiGet(`/api/sessions/${sessionId}/images`)
      const images = response.images || []

      // Sort by timestamp (oldest first)
      images.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))

      imagesBySession.value.set(sessionId, images)

      // Trigger reactivity
      imagesBySession.value = new Map(imagesBySession.value)

      console.log(`Loaded ${images.length} images for session ${sessionId}`)
    } catch (error) {
      console.error(`Failed to load images for session ${sessionId}:`, error)
      // Set empty array to prevent repeated loading attempts
      imagesBySession.value.set(sessionId, [])
      imagesBySession.value = new Map(imagesBySession.value)
    } finally {
      loading.value = false
    }
  }

  /**
   * Add a new image from WebSocket image_registered event
   */
  function addImage(sessionId, imageMetadata) {
    if (!sessionId || !imageMetadata) return

    let images = imagesBySession.value.get(sessionId)
    if (!images) {
      images = []
      imagesBySession.value.set(sessionId, images)
    }

    // Check if image already exists (prevent duplicates)
    const existingIndex = images.findIndex(img => img.image_id === imageMetadata.image_id)
    if (existingIndex >= 0) {
      // Update existing
      images[existingIndex] = imageMetadata
    } else {
      // Add new
      images.push(imageMetadata)
    }

    // Sort by timestamp
    images.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))

    // Trigger reactivity
    imagesBySession.value = new Map(imagesBySession.value)

    console.log(`Added image ${imageMetadata.image_id} to session ${sessionId}`)
  }

  /**
   * Get the image URL for displaying an image
   */
  function getImageUrl(sessionId, imageId) {
    return `/api/sessions/${sessionId}/images/${imageId}`
  }

  /**
   * Open full view modal for an image
   */
  function openFullView(sessionId, index = 0) {
    fullViewSessionId.value = sessionId
    currentImageIndex.value = Math.max(0, Math.min(index, imageCount(sessionId) - 1))
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
   * Navigate to next image in full view
   */
  function nextImage() {
    if (!fullViewSessionId.value) return
    const total = imageCount(fullViewSessionId.value)
    if (total === 0) return

    currentImageIndex.value = (currentImageIndex.value + 1) % total
  }

  /**
   * Navigate to previous image in full view
   */
  function prevImage() {
    if (!fullViewSessionId.value) return
    const total = imageCount(fullViewSessionId.value)
    if (total === 0) return

    currentImageIndex.value = (currentImageIndex.value - 1 + total) % total
  }

  /**
   * Navigate to specific image index
   */
  function goToImage(index) {
    if (!fullViewSessionId.value) return
    const total = imageCount(fullViewSessionId.value)
    if (total === 0) return

    currentImageIndex.value = Math.max(0, Math.min(index, total - 1))
  }

  /**
   * Clear all images for a session (for session reset)
   */
  function clearImages(sessionId) {
    imagesBySession.value.delete(sessionId)

    // Trigger reactivity
    imagesBySession.value = new Map(imagesBySession.value)

    // Close full view if viewing this session
    if (fullViewSessionId.value === sessionId) {
      closeFullView()
    }

    console.log(`Cleared images for session ${sessionId}`)
  }

  // ========== RETURN ==========
  return {
    // State
    imagesBySession,
    fullViewOpen,
    currentImageIndex,
    fullViewSessionId,
    loading,

    // Computed
    currentImages,
    currentImageCount,
    currentHasImages,
    currentFullViewImage,
    fullViewTotalImages,

    // Getters (functions)
    imagesForSession,
    imageCount,
    hasImages,
    getImageUrl,

    // Actions
    loadImages,
    addImage,
    openFullView,
    closeFullView,
    nextImage,
    prevImage,
    goToImage,
    clearImages
  }
})
