<template>
  <div class="image-gallery-panel">
    <!-- Panel Header -->
    <div class="panel-header d-flex align-items-center justify-content-between p-3 border-bottom">
      <div class="d-flex align-items-center gap-2">
        <span class="panel-icon">🖼️</span>
        <h6 class="mb-0">Images</h6>
        <span v-if="imageCount > 0" class="badge bg-secondary">
          {{ imageCount }}
        </span>
      </div>
      <button
        class="chevron-toggle btn btn-link p-0"
        @click="togglePanel"
        :title="isCollapsed ? 'Expand' : 'Collapse'"
      >
        <svg class="chevron-icon" :class="{ expanded: !isCollapsed }" width="12" height="12" viewBox="0 0 12 12">
          <path d="M4.5 2L8.5 6L4.5 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        </svg>
      </button>
    </div>

    <!-- Image Grid -->
    <div v-if="!isCollapsed" class="image-grid-container p-2">
      <div v-if="images.length === 0" class="text-muted text-center py-4">
        <span class="empty-icon">📷</span>
        <p class="mb-0 small">No images yet</p>
      </div>

      <div v-else class="image-grid">
        <div
          v-for="(image, index) in images"
          :key="image.image_id"
          class="image-thumbnail"
          @click="openFullView(index)"
          :title="image.title || 'Click to view'"
        >
          <img
            :src="getImageUrl(image.image_id)"
            :alt="image.title || 'Image'"
            loading="lazy"
            @error="handleImageError"
          />
          <div class="image-caption" v-if="image.title">
            {{ truncateTitle(image.title) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useImageStore } from '@/stores/image'
import { useSessionStore } from '@/stores/session'

const imageStore = useImageStore()
const sessionStore = useSessionStore()

// Panel collapse state
const isCollapsed = ref(false)

// Computed properties
const images = computed(() => imageStore.currentImages)
const imageCount = computed(() => imageStore.currentImageCount)

function togglePanel() {
  isCollapsed.value = !isCollapsed.value
}

function getImageUrl(imageId) {
  return imageStore.getImageUrl(sessionStore.currentSessionId, imageId)
}

function openFullView(index) {
  imageStore.openFullView(sessionStore.currentSessionId, index)
}

function truncateTitle(title, maxLength = 20) {
  if (!title) return ''
  if (title.length <= maxLength) return title
  return title.substring(0, maxLength - 3) + '...'
}

function handleImageError(event) {
  // Replace with placeholder on error
  event.target.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"%3E%3Crect fill="%23eee" width="100" height="100"/%3E%3Ctext fill="%23999" x="50" y="50" text-anchor="middle" dy=".3em"%3E?%3C/text%3E%3C/svg%3E'
}
</script>

<style scoped>
.image-gallery-panel {
  /* Flex item that takes remaining space after TaskListPanel */
  flex: 1 1 auto;
  min-height: 0; /* Allow shrinking below content size */
  display: flex;
  flex-direction: column;
  border-top: 1px solid #dee2e6;
}

.panel-header {
  background-color: #f8f9fa;
  flex-shrink: 0;
}

.chevron-toggle {
  color: #6c757d;
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}

.chevron-toggle:hover {
  color: #495057;
}

.chevron-icon {
  transition: transform 0.2s ease;
}

.chevron-icon.expanded {
  transform: rotate(90deg);
}

.panel-icon {
  font-size: 1.2rem;
}

.image-grid-container {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.empty-icon {
  font-size: 2rem;
  display: block;
  margin-bottom: 0.5rem;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

@media (min-width: 576px) {
  .image-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.image-thumbnail {
  position: relative;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: 4px;
  cursor: pointer;
  background-color: #f0f0f0;
  border: 1px solid #dee2e6;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.image-thumbnail:hover {
  transform: scale(1.02);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.image-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-caption {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 4px 6px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.7));
  color: white;
  font-size: 0.75rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
