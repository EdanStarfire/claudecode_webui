<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="fileBrowserStore.viewerOpen"
        class="file-viewer-overlay"
        @click="handleOverlayClick"
        @keydown="handleKeydown"
        tabindex="0"
        ref="overlayRef"
      >
        <!-- Close Button -->
        <button
          class="close-btn"
          @click.stop="fileBrowserStore.closeViewer()"
          title="Close (Escape)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>

        <!-- Content Container -->
        <div class="file-container" @click.stop>
          <!-- Header -->
          <div class="file-header">
            <div class="file-header-info">
              <h5 class="file-path-display">{{ fileName }}</h5>
              <div class="file-meta">
                <span class="meta-item" :title="fileBrowserStore.viewerFilePath">
                  {{ fileBrowserStore.viewerFilePath }}
                </span>
                <span v-if="fileBrowserStore.viewerSize" class="meta-item">
                  {{ formatSize(fileBrowserStore.viewerSize) }}
                </span>
              </div>
            </div>
            <button
              class="copy-btn"
              @click.stop="copyToClipboard"
              :title="copyFeedback ? 'Copied!' : 'Copy to clipboard'"
              :disabled="!fileBrowserStore.viewerContent"
            >
              <svg v-if="copyFeedback" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span class="copy-label">{{ copyFeedback ? 'Copied!' : 'Copy' }}</span>
            </button>
          </div>

          <!-- Body -->
          <div class="file-body">
            <!-- Loading -->
            <div v-if="fileBrowserStore.viewerLoading" class="file-loading">
              <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
              <span>Loading file...</span>
            </div>
            <!-- Error / Binary -->
            <div v-else-if="fileBrowserStore.viewerError" class="file-error">
              {{ fileBrowserStore.viewerError }}
            </div>
            <!-- Content -->
            <pre v-else-if="fileBrowserStore.viewerContent" class="file-content">{{ fileBrowserStore.viewerContent }}</pre>
            <!-- No content -->
            <div v-else class="file-unavailable">
              No content available.
            </div>
          </div>

          <!-- Footer -->
          <div v-if="fileBrowserStore.viewerTruncated" class="file-footer">
            File truncated at 1MB. Full size: {{ formatSize(fileBrowserStore.viewerSize) }}
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, ref, nextTick, onUnmounted } from 'vue'
import { useFileBrowserStore } from '@/stores/filebrowser'

const fileBrowserStore = useFileBrowserStore()
const overlayRef = ref(null)
const copyFeedback = ref(false)
let copyTimeout = null

const fileName = computed(() => {
  const path = fileBrowserStore.viewerFilePath
  if (!path) return ''
  const parts = path.split('/')
  return parts[parts.length - 1]
})

// Focus overlay when opened
watch(() => fileBrowserStore.viewerOpen, (open) => {
  if (open) {
    nextTick(() => {
      overlayRef.value?.focus()
    })
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

onUnmounted(() => {
  document.body.style.overflow = ''
  if (copyTimeout) clearTimeout(copyTimeout)
})

function handleOverlayClick(event) {
  if (event.target === event.currentTarget) {
    fileBrowserStore.closeViewer()
  }
}

function handleKeydown(event) {
  if (event.key === 'Escape') {
    fileBrowserStore.closeViewer()
  }
}

async function copyToClipboard() {
  if (!fileBrowserStore.viewerContent) return
  try {
    await navigator.clipboard.writeText(fileBrowserStore.viewerContent)
    copyFeedback.value = true
    if (copyTimeout) clearTimeout(copyTimeout)
    copyTimeout = setTimeout(() => {
      copyFeedback.value = false
    }, 2000)
  } catch {
    console.error('Failed to copy to clipboard')
  }
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}
</script>

<style scoped>
.file-viewer-overlay {
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

.file-container {
  max-width: calc(100% - 160px);
  max-height: calc(100% - 120px);
  width: 900px;
  display: flex;
  flex-direction: column;
  background: #f8f9fa;
  border-radius: 8px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.4);
  overflow: hidden;
}

.file-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #dee2e6;
  background: #fff;
  flex-shrink: 0;
}

.file-header-info {
  min-width: 0;
  flex: 1;
}

.file-path-display {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #212529;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  gap: 12px;
  color: #6c757d;
  font-size: 0.75rem;
  margin-top: 2px;
}

.file-meta .meta-item {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta .meta-item:first-child {
  max-width: 500px;
}

.copy-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #e9ecef;
  border: 1px solid #ced4da;
  border-radius: 6px;
  color: #495057;
  font-size: 0.8rem;
  cursor: pointer;
  transition: background-color 0.2s, color 0.2s;
  flex-shrink: 0;
  margin-left: 12px;
}

.copy-btn:hover:not(:disabled) {
  background: #dee2e6;
  color: #212529;
}

.copy-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.copy-label {
  white-space: nowrap;
}

.file-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  min-height: 200px;
}

.file-content {
  margin: 0;
  padding: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.85rem;
  line-height: 1.5;
  color: #212529;
  white-space: pre-wrap;
  word-wrap: break-word;
  background: transparent;
  border: none;
}

.file-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #6c757d;
  padding: 24px;
  justify-content: center;
}

.file-error {
  color: #6c757d;
  padding: 24px;
  text-align: center;
  font-style: italic;
}

.file-unavailable {
  color: #6c757d;
  padding: 24px;
  text-align: center;
  font-style: italic;
}

.file-footer {
  padding: 8px 16px;
  border-top: 1px solid #dee2e6;
  background: #fff3cd;
  color: #856404;
  font-size: 0.8rem;
  text-align: center;
  flex-shrink: 0;
}

/* Modal Transition */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

/* Mobile */
@media (max-width: 576px) {
  .file-container {
    max-width: calc(100% - 20px);
    max-height: calc(100% - 80px);
    width: 100%;
  }

  .close-btn {
    top: 10px;
    right: 10px;
  }

  .file-header {
    padding: 10px 12px;
  }

  .copy-label {
    display: none;
  }
}
</style>
