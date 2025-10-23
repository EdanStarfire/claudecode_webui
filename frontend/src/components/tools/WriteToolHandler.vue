<template>
  <div class="write-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="write-file-info mb-2">
        <span class="file-icon">📝</span>
        <strong>Writing:</strong>
        <code class="file-path">{{ filePath }}</code>
      </div>

      <div class="write-content-preview">
        <div class="content-header">
          <span class="content-label">Content:</span>
          <span class="line-count-badge">{{ lineCount }} lines</span>
          <span v-if="hasMore" class="preview-note">(showing first {{ previewLimit }})</span>
        </div>
        <pre class="tool-code content-display">{{ previewContent }}</pre>
        <div v-if="hasMore" class="more-indicator">...</div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="tool-result tool-result-success">
        <div class="success-message">
          <span class="success-icon">✅</span>
          <strong>File created successfully ({{ lineCount }} lines written)</strong>
        </div>
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

const content = computed(() => {
  return props.toolCall.input?.content || ''
})

const previewLimit = 100
const lines = computed(() => {
  return content.value.split('\n')
})

const lineCount = computed(() => lines.value.length)
const hasMore = computed(() => lineCount.value > previewLimit)

const previewContent = computed(() => {
  const previewLines = lines.value.slice(0, previewLimit)
  return previewLines.join('\n')
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
</script>

<style scoped>
.write-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.write-file-info {
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

.write-content-preview {
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

.more-indicator {
  text-align: center;
  padding: 0.5rem;
  color: #6c757d;
  font-weight: bold;
  background: #e9ecef;
  border-top: 1px solid #dee2e6;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-success {
  background: #d1e7dd;
  border: 1px solid #badbcc;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
}

.success-message {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #0f5132;
}

.success-icon {
  font-size: 1.2rem;
}

.tool-code {
  margin: 0;
  padding: 0;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>
