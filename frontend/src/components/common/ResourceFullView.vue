<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="isOpen"
        class="resource-full-view-overlay"
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
          v-if="totalResources > 1"
          class="nav-btn nav-prev"
          @click.stop="prevResource"
          title="Previous (Left Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>

        <!-- Image Container (for image resources) -->
        <div v-if="isCurrentImage" class="image-container" @click.stop>
          <img
            v-if="currentResource"
            :src="getResourceUrl(currentResource.resource_id || currentResource.image_id)"
            :alt="currentResource.title || 'Image'"
            class="full-image"
            @error="handleImageError"
          />

          <!-- Image Info -->
          <div class="resource-info" v-if="currentResource">
            <h5 v-if="currentResource.title" class="resource-title">
              {{ currentResource.title }}
            </h5>
            <p v-if="currentResource.description" class="resource-description">
              {{ currentResource.description }}
            </p>
            <div class="resource-meta">
              <span class="meta-item">
                {{ formatSize(currentResource.size_bytes) }}
              </span>
              <span class="meta-item">
                {{ currentResource.format?.toUpperCase() }}
              </span>
              <span class="meta-item">
                {{ formatTimestamp(currentResource.timestamp) }}
              </span>
            </div>
          </div>
        </div>

        <!-- Text Container (for text resources) -->
        <div v-else class="text-container" @click.stop>
          <!-- Text Header -->
          <div class="text-header">
            <div class="text-header-info">
              <h5 class="text-filename">
                {{ currentResource?.title || currentResource?.original_filename || 'Text Resource' }}
              </h5>
              <div class="resource-meta">
                <span v-if="currentResource?.size_bytes" class="meta-item">
                  {{ formatSize(currentResource.size_bytes) }}
                </span>
                <span v-if="currentResource?.format" class="meta-item">
                  {{ currentResource.format.toUpperCase() }}
                </span>
              </div>
            </div>
            <!-- Display Mode Toggle -->
            <div class="display-mode-toggle" v-if="textContent">
              <button
                class="toggle-btn"
                :class="{ active: displayMode === 'raw' }"
                @click.stop="displayMode = 'raw'"
                title="Raw text"
              >Raw</button>
              <button
                class="toggle-btn"
                :class="{ active: displayMode === 'markdown' }"
                @click.stop="displayMode = 'markdown'"
                title="Rendered markdown (M)"
              >Markdown</button>
            </div>
            <button
              class="copy-btn"
              @click.stop="copyToClipboard"
              :title="copyFeedback ? 'Copied!' : (displayMode === 'markdown' ? 'Copy raw text' : 'Copy to clipboard')"
              :disabled="!textContent"
            >
              <!-- Checkmark icon when copied -->
              <svg v-if="copyFeedback" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <!-- Copy icon -->
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span class="copy-label">{{ copyFeedback ? 'Copied!' : (displayMode === 'markdown' ? 'Copy raw' : 'Copy') }}</span>
            </button>
          </div>

          <!-- Text Body -->
          <div class="text-body">
            <!-- Loading state -->
            <div v-if="textLoading" class="text-loading">
              <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
              <span>Loading content...</span>
            </div>
            <!-- Error state -->
            <div v-else-if="textError" class="text-error">
              Failed to load content: {{ textError }}
            </div>
            <!-- Content: Raw -->
            <pre v-else-if="textContent && displayMode === 'raw'" class="text-content">{{ textContent }}</pre>
            <!-- Content: Markdown -->
            <div v-else-if="textContent && displayMode === 'markdown'" class="markdown-content" v-html="renderedMarkdown"></div>
            <!-- No content / unsupported -->
            <div v-else class="text-unavailable">
              Preview not available for this file type.
            </div>
          </div>
        </div>

        <!-- Navigation - Next -->
        <button
          v-if="totalResources > 1"
          class="nav-btn nav-next"
          @click.stop="nextResource"
          title="Next (Right Arrow)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </button>

        <!-- Position Indicator -->
        <div v-if="totalResources > 1" class="position-indicator">
          {{ currentIndex + 1 }} / {{ totalResources }}
        </div>

        <!-- Thumbnail Strip (for multiple resources) -->
        <div v-if="totalResources > 1" class="thumbnail-strip">
          <div
            v-for="(resource, index) in resources"
            :key="resource.resource_id || resource.image_id"
            class="strip-thumbnail"
            :class="{
              active: index === currentIndex,
              'is-text': !resourceStore.isImageResource(resource)
            }"
            @click.stop="goToResource(index)"
          >
            <img
              v-if="resourceStore.isImageResource(resource)"
              :src="getResourceUrl(resource.resource_id || resource.image_id)"
              :alt="resource.title || 'Thumbnail'"
              loading="lazy"
            />
            <span v-else class="strip-text-icon">{{ resourceStore.getResourceIcon(resource) }}</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, ref, nextTick, onUnmounted } from 'vue'
import { useResourceStore } from '@/stores/resource'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({
  breaks: true,
  gfm: true
})

const resourceStore = useResourceStore()
const overlayRef = ref(null)
const copyFeedback = ref(false)
const displayMode = ref('raw')
let copyTimeout = null

// Computed properties
const isOpen = computed(() => resourceStore.fullViewOpen)
const currentResource = computed(() => resourceStore.currentFullViewResource)
const currentIndex = computed(() => resourceStore.currentResourceIndex)
const totalResources = computed(() => resourceStore.fullViewTotalResources)
const resources = computed(() => {
  if (!resourceStore.fullViewSessionId) return []
  return resourceStore.resourcesForSession(resourceStore.fullViewSessionId)
})

const isCurrentImage = computed(() => {
  return resourceStore.isImageResource(currentResource.value)
})

const isCurrentText = computed(() => {
  return resourceStore.isTextResource(currentResource.value)
})

// Text content from cache
const textCacheEntry = computed(() => {
  if (!currentResource.value) return null
  return resourceStore.getTextContent(currentResource.value.resource_id)
})

const textContent = computed(() => textCacheEntry.value?.content || null)
const textLoading = computed(() => textCacheEntry.value?.loading || false)
const textError = computed(() => textCacheEntry.value?.error || null)

const renderedMarkdown = computed(() => {
  if (!textContent.value) return ''
  let html = marked.parse(textContent.value)
  html = html.replace(/\n</g, '<')
  html = html.replace(/\n+$/, '')
  return DOMPurify.sanitize(html)
})

// Focus overlay when opened for keyboard events
watch(isOpen, (open) => {
  if (open) {
    nextTick(() => {
      overlayRef.value?.focus()
    })
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

// Reset display mode when navigating between resources
watch(currentIndex, () => {
  displayMode.value = 'raw'
})

// Fetch text content when navigating to a text resource
watch([currentIndex, isOpen], () => {
  if (isOpen.value && currentResource.value && !isCurrentImage.value) {
    const rid = currentResource.value.resource_id
    const cached = resourceStore.getTextContent(rid)
    if (!cached || cached.error) {
      resourceStore.fetchTextContent(resourceStore.fullViewSessionId, rid)
    }
  }
}, { immediate: true })

// Clean up on unmount
onUnmounted(() => {
  document.body.style.overflow = ''
  if (copyTimeout) clearTimeout(copyTimeout)
})

function getResourceUrl(resourceId) {
  return resourceStore.getResourceUrl(resourceStore.fullViewSessionId, resourceId)
}

function closeFullView() {
  resourceStore.closeFullView()
}

function nextResource() {
  resourceStore.nextResource()
}

function prevResource() {
  resourceStore.prevResource()
}

function goToResource(index) {
  resourceStore.goToResource(index)
}

function handleOverlayClick(event) {
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
      prevResource()
      break
    case 'ArrowRight':
      nextResource()
      break
    case 'Home':
      goToResource(0)
      break
    case 'End':
      goToResource(totalResources.value - 1)
      break
    case 'm':
      if (!isCurrentImage.value && textContent.value) {
        displayMode.value = displayMode.value === 'raw' ? 'markdown' : 'raw'
      }
      break
  }
}

async function copyToClipboard() {
  if (!textContent.value) return
  try {
    await navigator.clipboard.writeText(textContent.value)
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

function formatTimestamp(timestamp) {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  return date.toLocaleString()
}

function handleImageError(event) {
  event.target.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"%3E%3Crect fill="%23333" width="200" height="200"/%3E%3Ctext fill="%23999" x="100" y="100" text-anchor="middle" dy=".3em"%3EFailed to load%3C/text%3E%3C/svg%3E'
}
</script>

<style scoped>
.resource-full-view-overlay {
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

/* ===== Image View ===== */

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

.resource-info {
  margin-top: 16px;
  text-align: center;
  color: white;
  max-width: 600px;
}

.resource-title {
  margin: 0 0 8px 0;
  font-weight: 500;
}

.resource-description {
  margin: 0 0 8px 0;
  opacity: 0.8;
  font-size: 0.9rem;
}

.resource-meta {
  display: flex;
  gap: 16px;
  justify-content: center;
  opacity: 0.6;
  font-size: 0.8rem;
}

/* ===== Text View ===== */

.text-container {
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

.text-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #dee2e6;
  background: #fff;
  flex-shrink: 0;
}

.text-header-info {
  min-width: 0;
  flex: 1;
}

.text-filename {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #212529;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.text-header .resource-meta {
  color: #6c757d;
  justify-content: flex-start;
  gap: 12px;
  margin-top: 2px;
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

.text-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  min-height: 200px;
}

.text-content {
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

.text-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #6c757d;
  padding: 24px;
  justify-content: center;
}

.text-error {
  color: #dc3545;
  padding: 24px;
  text-align: center;
}

.text-unavailable {
  color: #6c757d;
  padding: 24px;
  text-align: center;
  font-style: italic;
}

/* ===== Display Mode Toggle ===== */

.display-mode-toggle {
  display: flex;
  border: 1px solid #ced4da;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  margin-left: 12px;
}

.toggle-btn {
  padding: 5px 12px;
  background: #e9ecef;
  border: none;
  color: #495057;
  font-size: 0.8rem;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
}

.toggle-btn:first-child {
  border-right: 1px solid #ced4da;
}

.toggle-btn:hover:not(.active) {
  background: #dee2e6;
}

.toggle-btn.active {
  background: #0d6efd;
  color: #fff;
}

/* ===== Markdown Content ===== */

.markdown-content {
  font-size: 0.9rem;
  line-height: 1.6;
  color: #212529;
  word-wrap: break-word;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin-top: 1.2em;
  margin-bottom: 0.6em;
  font-weight: 600;
  color: #1a1a1a;
}

.markdown-content :deep(h1) { font-size: 1.6rem; border-bottom: 1px solid #dee2e6; padding-bottom: 0.3em; }
.markdown-content :deep(h2) { font-size: 1.35rem; border-bottom: 1px solid #dee2e6; padding-bottom: 0.3em; }
.markdown-content :deep(h3) { font-size: 1.15rem; }

.markdown-content :deep(p) {
  margin-bottom: 0.8em;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 2em;
  margin-bottom: 0.8em;
}

.markdown-content :deep(li) {
  margin-bottom: 0.3em;
}

.markdown-content :deep(code) {
  background: #e9ecef;
  padding: 0.15em 0.4em;
  border-radius: 3px;
  font-size: 0.85em;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.markdown-content :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 6px;
  overflow-x: auto;
  margin-bottom: 0.8em;
}

.markdown-content :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.markdown-content :deep(blockquote) {
  border-left: 3px solid #ced4da;
  padding-left: 1em;
  margin-left: 0;
  margin-bottom: 0.8em;
  color: #6c757d;
}

.markdown-content :deep(a) {
  color: #0d6efd;
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.markdown-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 0.8em;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #dee2e6;
  padding: 6px 12px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: #f1f3f5;
  font-weight: 600;
}

.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid #dee2e6;
  margin: 1.2em 0;
}

.markdown-content :deep(img) {
  max-width: 100%;
  height: auto;
}

/* ===== Position Indicator ===== */

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

/* ===== Thumbnail Strip ===== */

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
  display: flex;
  align-items: center;
  justify-content: center;
}

.strip-thumbnail.is-text {
  background: rgba(255, 255, 255, 0.1);
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

.strip-text-icon {
  font-size: 1.5rem;
}

/* ===== Modal Transition ===== */

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

/* ===== Mobile Adjustments ===== */

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

  .text-container {
    max-width: calc(100% - 20px);
    max-height: calc(100% - 80px);
    width: 100%;
  }

  .thumbnail-strip {
    display: none;
  }

  .close-btn {
    top: 10px;
    right: 10px;
  }

  .text-header {
    padding: 10px 12px;
  }

  .copy-label {
    display: none;
  }

  .toggle-btn {
    padding: 5px 8px;
    font-size: 0.75rem;
  }
}
</style>
