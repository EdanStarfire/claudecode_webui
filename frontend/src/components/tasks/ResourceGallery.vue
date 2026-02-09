<template>
  <div class="resource-gallery-panel">
    <!-- Resource Grid -->
    <div class="resource-grid-container p-2">
      <div v-if="resources.length === 0" class="text-muted text-center py-4">
        <span class="empty-icon">📂</span>
        <p class="mb-0 small">No resources yet</p>
      </div>

      <div v-else class="resource-grid">
        <div
          v-for="(resource, index) in resources"
          :key="resource.resource_id"
          class="resource-item"
          :class="{ 'is-image': isImage(resource) }"
        >
          <!-- Image thumbnail -->
          <div
            v-if="isImage(resource)"
            class="resource-thumbnail"
            @click="openFullView(index)"
            :title="resource.title || 'Click to view'"
          >
            <img
              :src="getResourceUrl(resource.resource_id)"
              :alt="resource.title || 'Image'"
              loading="lazy"
              @error="handleImageError"
            />
          </div>

          <!-- File type placeholder -->
          <div
            v-else
            class="resource-placeholder"
            :title="resource.title || resource.original_filename || 'Click to view'"
            @click="openFullView(index)"
          >
            <span class="file-icon">{{ getIcon(resource) }}</span>
            <span class="file-ext">{{ getExtension(resource) }}</span>
          </div>

          <!-- Resource info and actions -->
          <div class="resource-info">
            <div class="resource-title" :title="resource.title || resource.original_filename">
              {{ truncateTitle(resource.title || resource.original_filename) }}
            </div>
            <div class="resource-actions">
              <!-- Add to attachments button -->
              <button
                class="btn btn-sm btn-outline-primary action-btn"
                @click.stop="addToAttachments(resource)"
                title="Add to message attachments"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M8 4a.5.5 0 0 1 .5.5v3h3a.5.5 0 0 1 0 1h-3v3a.5.5 0 0 1-1 0v-3h-3a.5.5 0 0 1 0-1h3v-3A.5.5 0 0 1 8 4z"/>
                </svg>
              </button>
              <!-- Download button -->
              <a
                :href="getDownloadUrl(resource.resource_id)"
                class="btn btn-sm btn-outline-secondary action-btn"
                :download="resource.original_filename || resource.title || 'download'"
                title="Download"
                @click.stop
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/>
                  <path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/>
                </svg>
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { useResourceStore } from '@/stores/resource'
import { useSessionStore } from '@/stores/session'

const resourceStore = useResourceStore()
const sessionStore = useSessionStore()

// Inject the addAttachmentFromResource function from parent
const addAttachmentFromResource = inject('addAttachmentFromResource', null)

// Computed properties
const resources = computed(() => resourceStore.currentResources)
const resourceCount = computed(() => resourceStore.currentResourceCount)

function isImage(resource) {
  return resourceStore.isImageResource(resource)
}

function getIcon(resource) {
  return resourceStore.getResourceIcon(resource)
}

function getExtension(resource) {
  const ext = resourceStore.getResourceExtension(resource)
  return ext ? ext.toUpperCase().slice(1) : ''
}

function getResourceUrl(resourceId) {
  return resourceStore.getResourceUrl(sessionStore.currentSessionId, resourceId)
}

function getDownloadUrl(resourceId) {
  return resourceStore.getDownloadUrl(sessionStore.currentSessionId, resourceId)
}

function openFullView(index) {
  resourceStore.openFullView(sessionStore.currentSessionId, index)
}

function truncateTitle(title, maxLength = 18) {
  if (!title) return ''
  if (title.length <= maxLength) return title
  return title.substring(0, maxLength - 3) + '...'
}

function handleImageError(event) {
  // Replace with placeholder on error
  event.target.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"%3E%3Crect fill="%23eee" width="100" height="100"/%3E%3Ctext fill="%23999" x="50" y="50" text-anchor="middle" dy=".3em"%3E?%3C/text%3E%3C/svg%3E'
}

function addToAttachments(resource) {
  if (addAttachmentFromResource) {
    addAttachmentFromResource(resource)
  } else {
    console.warn('addAttachmentFromResource not provided')
  }
}
</script>

<style scoped>
.resource-gallery-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.resource-grid-container {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.empty-icon {
  font-size: 2rem;
  display: block;
  margin-bottom: 0.5rem;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

@media (min-width: 576px) {
  .resource-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.resource-item {
  display: flex;
  flex-direction: column;
  border-radius: 6px;
  background-color: #f8f9fa;
  border: 1px solid #dee2e6;
  overflow: hidden;
  transition: box-shadow 0.15s ease;
}

.resource-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

.resource-thumbnail {
  aspect-ratio: 1;
  overflow: hidden;
  cursor: pointer;
  background-color: #e9ecef;
}

.resource-thumbnail:hover {
  opacity: 0.9;
}

.resource-thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.resource-placeholder {
  aspect-ratio: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background-color: #e9ecef;
  gap: 4px;
  cursor: pointer;
}

.resource-placeholder:hover {
  opacity: 0.9;
}

.file-icon {
  font-size: 2rem;
}

.file-ext {
  font-size: 0.65rem;
  font-weight: 600;
  color: #6c757d;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.resource-info {
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.resource-title {
  font-size: 0.75rem;
  color: #495057;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.resource-actions {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}

.action-btn {
  padding: 2px 6px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.action-btn svg {
  display: block;
}
</style>
