<template>
  <div class="read-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="read-file-info">
        <span class="file-icon">📄</span>
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
import { computed } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Parameters
const filePath = computed(() => {
  return props.toolCall.input?.file_path || 'Unknown'
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

// Result
const hasResult = computed(() => {
  return props.toolCall.result !== null && props.toolCall.result !== undefined
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const resultContent = computed(() => {
  if (!hasResult.value) return ''

  const result = props.toolCall.result

  if (result.content !== undefined) {
    return typeof result.content === 'string'
      ? result.content
      : JSON.stringify(result.content, null, 2)
  }

  if (result.message) {
    return result.message
  }

  return JSON.stringify(result, null, 2)
})

const previewLimit = 100
const lines = computed(() => {
  if (isError.value || !resultContent.value) return []
  return resultContent.value.split('\n')
})

const lineCount = computed(() => lines.value.length)
const hasMore = computed(() => lineCount.value > previewLimit)

const previewContent = computed(() => {
  if (isError.value) return ''
  const previewLines = lines.value.slice(0, previewLimit)
  return previewLines.join('\n')
})
</script>

<style scoped>
.read-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
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
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.file-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.file-path {
  background: #e9ecef;
  padding: 0.2rem 0.5rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  color: #0d6efd;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.read-range {
  color: #6c757d;
  font-size: 0.85rem;
  padding: 0.2rem 0.5rem;
  background: #e9ecef;
  border-radius: 0.2rem;
}

.tool-result {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.tool-result-error {
  background: #fff5f5;
  border-color: #dc3545;
}

.file-content-preview {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.content-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
  flex-wrap: wrap;
}

.content-label {
  font-weight: 600;
  color: #6c757d;
}

.line-count-badge {
  background: #0d6efd;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
}

.preview-note {
  color: #6c757d;
  font-size: 0.85rem;
  font-style: italic;
}

.content-display {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.4;
}

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.4;
}

.more-indicator {
  text-align: center;
  padding: 0.5rem;
  color: #6c757d;
  font-weight: bold;
  background: #e9ecef;
  border-top: 1px solid #dee2e6;
}
</style>
