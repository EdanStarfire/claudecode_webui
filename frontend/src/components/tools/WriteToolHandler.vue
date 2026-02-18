<template>
  <div class="write-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
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
        <pre class="tool-code-block content-display">{{ previewContent }}</pre>
        <div v-if="hasMore" class="more-indicator">...</div>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code-block">{{ resultContent }}</pre>
      </div>

      <div v-else>
        <ToolSuccessMessage :message="`File created successfully (${lineCount} lines written)`" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'
import ToolSuccessMessage from './ToolSuccessMessage.vue'

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
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

// Expose for parent components
const summary = computed(() => `Write: ${filePath.value}`)
const params = computed(() => ({ file_path: filePath.value, lines: lineCount.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.write-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
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
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  color: #0d6efd;
  overflow-x: auto;
  white-space: nowrap;
  max-width: 100%;
}

.write-content-preview {
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
  color: #6c757d;
}

.line-count-badge {
  background: #0d6efd;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
}

.preview-note {
  color: #6c757d;
  font-size: var(--tool-code-font-size, 11px);
  font-style: italic;
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

.more-indicator {
  text-align: center;
  padding: 0.5rem;
  color: #6c757d;
  font-weight: bold;
  background: var(--tool-bg-header, #f1f5f9);
  border-top: 1px solid var(--tool-border, #e2e8f0);
}

.tool-result {
  padding: 0.75rem;
  border-radius: var(--tool-radius, 4px);
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
}
</style>
