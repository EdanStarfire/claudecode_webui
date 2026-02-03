<template>
  <div v-if="attachments.length > 0" class="attachment-list">
    <div
      v-for="(file, index) in attachments"
      :key="file.id || index"
      class="attachment-item"
    >
      <!-- Image preview -->
      <div v-if="isImageFile(file)" class="attachment-preview">
        <img
          :src="file.preview || file.url"
          :alt="file.name"
          class="attachment-thumbnail"
        />
      </div>

      <!-- File icon for non-images -->
      <div v-else class="attachment-icon">
        <span class="file-type-icon">{{ getFileIcon(file) }}</span>
      </div>

      <!-- File info -->
      <div class="attachment-info">
        <span class="attachment-name" :title="file.name">{{ truncateName(file.name) }}</span>
        <span class="attachment-size">{{ formatSize(file.size) }}</span>
      </div>

      <!-- Remove button -->
      <button
        type="button"
        class="btn-remove"
        title="Remove attachment"
        @click="removeAttachment(index)"
      >
        ✕
      </button>

      <!-- Upload progress or error -->
      <div v-if="file.uploading" class="upload-progress">
        <div class="progress-bar" :style="{ width: (file.progress || 0) + '%' }"></div>
      </div>
      <div v-if="file.error" class="upload-error" :title="file.error">
        ⚠️ {{ file.error }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { defineProps, defineEmits } from 'vue'

const props = defineProps({
  attachments: {
    type: Array,
    required: true,
    default: () => []
  }
})

const emit = defineEmits(['remove'])

/**
 * Check if file is an image based on MIME type or extension
 */
function isImageFile(file) {
  if (file.type && file.type.startsWith('image/')) {
    return true
  }
  const ext = file.name?.split('.').pop()?.toLowerCase()
  return ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext)
}

/**
 * Get appropriate icon for file type
 */
function getFileIcon(file) {
  const ext = file.name?.split('.').pop()?.toLowerCase() || ''
  const type = file.type || ''

  // Code files
  if (['py', 'js', 'ts', 'jsx', 'tsx', 'vue', 'java', 'c', 'cpp', 'rs', 'go'].includes(ext)) {
    return '📄'
  }
  // Config files
  if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'cfg', 'env'].includes(ext)) {
    return '⚙️'
  }
  // Log files
  if (['log', 'txt'].includes(ext) || type === 'text/plain') {
    return '📋'
  }
  // Markdown
  if (['md', 'rst'].includes(ext)) {
    return '📝'
  }
  // Default
  return '📁'
}

/**
 * Format file size for display
 */
function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

/**
 * Truncate long file names
 */
function truncateName(name, maxLength = 20) {
  if (!name || name.length <= maxLength) return name

  const ext = name.split('.').pop()
  const baseName = name.substring(0, name.length - ext.length - 1)

  if (baseName.length <= maxLength - ext.length - 4) {
    return name
  }

  return baseName.substring(0, maxLength - ext.length - 4) + '...' + ext
}

/**
 * Remove attachment at index
 */
function removeAttachment(index) {
  emit('remove', index)
}
</script>

<style scoped>
.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem;
  background: var(--bs-gray-100);
  border-radius: 4px;
  margin-bottom: 0.25rem;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.5rem;
  background: white;
  border: 1px solid var(--bs-gray-300);
  border-radius: 4px;
  font-size: 0.875rem;
  max-width: 200px;
  position: relative;
}

.attachment-preview {
  flex-shrink: 0;
}

.attachment-thumbnail {
  width: 32px;
  height: 32px;
  object-fit: cover;
  border-radius: 2px;
}

.attachment-icon {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-type-icon {
  font-size: 1.25rem;
}

.attachment-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex-grow: 1;
}

.attachment-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--bs-gray-700);
}

.attachment-size {
  font-size: 0.75rem;
  color: var(--bs-gray-500);
}

.btn-remove {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--bs-gray-500);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 0.75rem;
  transition: background-color 0.15s, color 0.15s;
}

.btn-remove:hover {
  background: var(--bs-gray-200);
  color: var(--bs-danger);
}

.upload-progress {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--bs-gray-200);
  border-radius: 0 0 4px 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--bs-primary);
  transition: width 0.2s;
}

.upload-error {
  position: absolute;
  bottom: -1.25rem;
  left: 0;
  right: 0;
  font-size: 0.625rem;
  color: var(--bs-danger);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Error state for the attachment item */
.attachment-item:has(.upload-error) {
  border-color: var(--bs-danger);
  margin-bottom: 1rem;
}
</style>
