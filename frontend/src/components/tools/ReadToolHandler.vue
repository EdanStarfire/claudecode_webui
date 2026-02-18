<template>
  <div class="read-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="read-file-info">
        <span class="file-icon">{{ isImageFile ? '🖼️' : '📄' }}</span>
        <strong>Reading:</strong>
        <code class="file-path">{{ filePath }}</code>
        <span v-if="hasRange" class="read-range">Lines {{ startLine }}-{{ endLine }}</span>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <!-- Image Result -->
      <div v-else-if="imageData" class="image-preview">
        <div class="content-header">
          <span class="content-label">Image Preview:</span>
          <span class="image-badge">{{ imageMimeType }}</span>
        </div>
        <div class="image-container">
          <img
            :src="`data:${imageMimeType};base64,${imageData}`"
            :alt="fileName"
            class="preview-image"
          />
        </div>
      </div>

      <!-- Text Result -->
      <div v-else class="file-content-preview">
        <div class="content-header">
          <span class="content-label">Content Preview:</span>
          <span class="line-count-badge">{{ lineCount }} lines</span>
          <span v-if="hasMore" class="preview-note">(showing first {{ previewLimit }})</span>
        </div>
        <pre class="tool-code content-display">{{ previewContent }}</pre>
        <div v-if="hasMore" class="more-indicator">...</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Parameters
const filePath = computed(() => {
  return props.toolCall.input?.file_path || 'Unknown'
})

const fileName = computed(() => {
  const path = filePath.value
  return path.split('/').pop() || path
})

const hasRange = computed(() => {
  const offset = props.toolCall.input?.offset
  const limit = props.toolCall.input?.limit
  return offset !== undefined || limit !== undefined
})

const startLine = computed(() => {
  const offset = props.toolCall.input?.offset
  return offset !== undefined ? offset + 1 : 1
})

const endLine = computed(() => {
  const offset = props.toolCall.input?.offset || 0
  const limit = props.toolCall.input?.limit
  return limit !== undefined ? offset + limit : '∞'
})

// Check if file path indicates an image
const isImageFile = computed(() => {
  const path = filePath.value.toLowerCase()
  return /\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$/.test(path)
})

// Extract image data from result if present
// Structure: result.content = [{ type: 'image', source: { type: 'base64', data: '...' } }]
const imageData = computed(() => {
  if (!hasResult.value || isError.value) return null

  const result = props.toolCall.result
  const content = result?.content

  // Check if content is an array with image blocks
  if (Array.isArray(content)) {
    for (const block of content) {
      if (block?.type === 'image' && block?.source?.type === 'base64' && block?.source?.data) {
        return block.source.data
      }
    }
  }

  return null
})

// Determine MIME type for image
const imageMimeType = computed(() => {
  if (!imageData.value) return ''

  const path = filePath.value.toLowerCase()
  if (path.endsWith('.png')) return 'image/png'
  if (path.endsWith('.jpg') || path.endsWith('.jpeg')) return 'image/jpeg'
  if (path.endsWith('.gif')) return 'image/gif'
  if (path.endsWith('.webp')) return 'image/webp'
  if (path.endsWith('.svg')) return 'image/svg+xml'
  if (path.endsWith('.bmp')) return 'image/bmp'
  if (path.endsWith('.ico')) return 'image/x-icon'

  // Default to PNG if we can't determine
  return 'image/png'
})

const previewLimit = 100
const lines = computed(() => {
  if (isError.value || !resultContent.value || imageData.value) return []
  return resultContent.value.split('\n')
})

const lineCount = computed(() => lines.value.length)
const hasMore = computed(() => lineCount.value > previewLimit)

const previewContent = computed(() => {
  if (isError.value || imageData.value) return ''
  const previewLines = lines.value.slice(0, previewLimit)
  return previewLines.join('\n')
})

const summary = computed(() => `Read: ${filePath.value}`)
const params = computed(() => ({ file_path: filePath.value, range: hasRange.value ? `${startLine.value}-${endLine.value}` : null }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.read-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.read-file-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.75rem;
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.file-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.file-path {
  background: var(--tool-bg-header, #f1f5f9);
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  color: #0d6efd;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.read-range {
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
  padding: 0.2rem 0.5rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-radius: var(--tool-radius, 4px);
}

.tool-result {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border-color: var(--tool-error-border, #f87171);
}

.file-content-preview {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.image-preview {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.content-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-bottom: 1px solid var(--tool-border, #e2e8f0);
  flex-wrap: wrap;
}

.content-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
}

.line-count-badge {
  background: #0d6efd;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
}

.image-badge {
  background: #6f42c1;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
}

.preview-note {
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
  font-style: italic;
}

.image-container {
  padding: 0.75rem;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #fff;
}

.preview-image {
  max-width: 100%;
  max-height: var(--tool-code-max-height, 200px);
  object-fit: contain;
  border-radius: var(--tool-radius, 4px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.content-display {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.4;
}

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: var(--tool-code-max-height, 200px);
  overflow-y: auto;
  line-height: 1.4;
}

.more-indicator {
  text-align: center;
  padding: 0.5rem;
  color: var(--tool-text-muted, #64748b);
  font-weight: bold;
  background: var(--tool-bg-header, #f1f5f9);
  border-top: 1px solid var(--tool-border, #e2e8f0);
}
</style>
