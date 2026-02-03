<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="isOpen"
        class="image-full-view-overlay"
        @click="handleOverlayClick"
        @keydown="handleKeydown"
        tabindex="0"
        ref="overlayRef"
      >
        <!-- Close Button -->
        <button
          class="close-btn"
          @click.stop="closeFullView"
          title="Close (Escape)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>

        <!-- Navigation - Previous -->
        <button
          v-if="totalImages > 1"
          class="nav-btn nav-prev"
          @click.stop="prevImage"
          title="Previous (Left Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>

        <!-- Image Container -->
        <div class="image-container" @click.stop>
          <img
            v-if="currentImage"
            :src="getImageUrl(currentImage.resource_id || currentImage.image_id)"
            :alt="currentImage.title || 'Image'"
            class="full-image"
            @error="handleImageError"
          />

          <!-- Image Info -->
          <div class="image-info" v-if="currentImage">
            <h5 v-if="currentImage.title" class="image-title">
              {{ currentImage.title }}
            </h5>
            <p v-if="currentImage.description" class="image-description">
              {{ currentImage.description }}
            </p>
            <div class="image-meta">
              <span class="meta-item">
                {{ formatSize(currentImage.size_bytes) }}
              </span>
              <span class="meta-item">
                {{ currentImage.format?.toUpperCase() }}
              </span>
              <span class="meta-item">
                {{ formatTimestamp(currentImage.timestamp) }}
              </span>
            </div>
          </div>
        </div>

        <!-- Navigation - Next -->
        <button
          v-if="totalImages > 1"
          class="nav-btn nav-next"
          @click.stop="nextImage"
          title="Next (Right Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </button>

        <!-- Position Indicator -->
        <div v-if="totalImages > 1" class="position-indicator">
          {{ currentIndex + 1 }} / {{ totalImages }}
        </div>

        <!-- Thumbnail Strip (for multiple images) -->
        <div v-if="totalImages > 1" class="thumbnail-strip">
          <div
            v-for="(image, index) in images"
            :key="image.resource_id || image.image_id"
            class="strip-thumbnail"
            :class="{ active: index === currentIndex }"
            @click.stop="goToImage(index)"
          >
            <img
              :src="getImageUrl(image.resource_id || image.image_id)"
              :alt="image.title || 'Thumbnail'"
              loading="lazy"
            />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useResourceStore } from '@/stores/resource'

const resourceStore = useResourceStore()
const overlayRef = ref(null)

// Computed properties - now using resource store (which has backward-compatible aliases)
const isOpen = computed(() => resourceStore.fullViewOpen)
const currentImage = computed(() => resourceStore.currentFullViewResource)
const currentIndex = computed(() => resourceStore.currentResourceIndex)
const totalImages = computed(() => resourceStore.fullViewTotalResources)
const images = computed(() => {
  if (!resourceStore.fullViewSessionId) return []
  return resourceStore.resourcesForSession(resourceStore.fullViewSessionId)
})

// Focus overlay when opened for keyboard events
watch(isOpen, (open) => {
  if (open) {
    nextTick(() => {
      overlayRef.value?.focus()
    })
    // Prevent body scroll
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

// Clean up on unmount
onUnmounted(() => {
  document.body.style.overflow = ''
})

function getImageUrl(resourceId) {
  return resourceStore.getResourceUrl(resourceStore.fullViewSessionId, resourceId)
}

function closeFullView() {
  resourceStore.closeFullView()
}

function nextImage() {
  resourceStore.nextResource()
}

function prevImage() {
  resourceStore.prevResource()
}

function goToImage(index) {
  resourceStore.goToResource(index)
}

function handleOverlayClick(event) {
  // Close when clicking on overlay (not on image or controls)
  if (event.target === event.currentTarget) {
    closeFullView()
  }
}

function handleKeydown(event) {
  switch (event.key) {
    case 'Escape':
      closeFullView()
      break
    case 'ArrowLeft':
      prevImage()
      break
    case 'ArrowRight':
      nextImage()
      break
    case 'Home':
      goToImage(0)
      break
    case 'End':
      goToImage(totalImages.value - 1)
      break
  }
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatTimestamp(timestamp) {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  return date.toLocaleString()
}

function handleImageError(event) {
  // Show error placeholder
  event.target.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"%3E%3Crect fill="%23333" width="200" height="200"/%3E%3Ctext fill="%23999" x="100" y="100" text-anchor="middle" dy=".3em"%3EFailed to load%3C/text%3E%3C/svg%3E'
}
</script>

<style scoped>
.image-full-view-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  outline: none;
}

.close-btn {
  position: absolute;
  top: 20px;
  right: 20px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
  z-index: 10;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.nav-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 48px;
  height: 80px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
  z-index: 10;
}

.nav-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.nav-prev {
  left: 20px;
}

.nav-next {
  right: 20px;
}

.image-container {
  max-width: calc(100% - 160px);
  max-height: calc(100% - 160px);
  display: flex;
  flex-direction: column;
  align-items: center;
}

.full-image {
  max-width: 100%;
  max-height: calc(100vh - 200px);
  object-fit: contain;
  border-radius: 4px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
}

.image-info {
  margin-top: 16px;
  text-align: center;
  color: white;
  max-width: 600px;
}

.image-title {
  margin: 0 0 8px 0;
  font-weight: 500;
}

.image-description {
  margin: 0 0 8px 0;
  opacity: 0.8;
  font-size: 0.9rem;
}

.image-meta {
  display: flex;
  gap: 16px;
  justify-content: center;
  opacity: 0.6;
  font-size: 0.8rem;
}

.position-indicator {
  position: absolute;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  color: white;
  background: rgba(0, 0, 0, 0.5);
  padding: 6px 16px;
  border-radius: 20px;
  font-size: 0.9rem;
}

.thumbnail-strip {
  position: absolute;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
  padding: 8px;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 8px;
  max-width: calc(100% - 40px);
  overflow-x: auto;
}

.strip-thumbnail {
  width: 60px;
  height: 60px;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  opacity: 0.6;
  transition: opacity 0.2s, transform 0.2s;
  flex-shrink: 0;
  border: 2px solid transparent;
}

.strip-thumbnail:hover {
  opacity: 0.9;
  transform: scale(1.05);
}

.strip-thumbnail.active {
  opacity: 1;
  border-color: white;
}

.strip-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Modal transition */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

/* Mobile adjustments */
@media (max-width: 576px) {
  .nav-btn {
    width: 40px;
    height: 60px;
  }

  .nav-prev {
    left: 8px;
  }

  .nav-next {
    right: 8px;
  }

  .image-container {
    max-width: calc(100% - 100px);
    max-height: calc(100% - 120px);
  }

  .thumbnail-strip {
    display: none;
  }

  .close-btn {
    top: 10px;
    right: 10px;
  }
}
</style>
