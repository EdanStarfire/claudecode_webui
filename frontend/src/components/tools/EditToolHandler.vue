<template>
  <div class="edit-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
      <div class="edit-file-info">
        <span class="file-icon">✏️</span>
        <strong>Editing:</strong>
        <code class="file-path">{{ filePath }}</code>
        <span v-if="replaceAll" class="replace-all-badge">Replace All</span>
      </div>

      <div class="edit-diff-container">
        <div class="diff-header">
          <span class="diff-label">Changes:</span>
          <span class="diff-stats">
            <span class="stat-removed">-{{ removedCount }}</span>
            <span class="stat-added">+{{ addedCount }}</span>
          </span>
        </div>

        <div class="unified-diff">
          <div class="diff-scroll-container">
            <div
              v-for="(line, index) in diffLines"
              :key="index"
              class="diff-line"
              :class="{
                'diff-line-removed': line.type === 'removed',
                'diff-line-added': line.type === 'added',
                'diff-line-context': line.type === 'context',
                'diff-line-hunk': line.type === 'hunk'
              }"
            >
              <span class="diff-marker">{{
                line.type === 'removed' ? '-' :
                line.type === 'added' ? '+' :
                line.type === 'hunk' ? '@' :
                ' '
              }}</span>
              <span class="diff-content">{{ line.content }}</span>
            </div>
          </div>
        </div>
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
          <strong>File edited successfully</strong>
          <span v-if="replaceAll" class="success-detail">(all occurrences replaced)</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { createPatch } from 'diff'

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

const oldString = computed(() => {
  return props.toolCall.input?.old_string || ''
})

const newString = computed(() => {
  return props.toolCall.input?.new_string || ''
})

const replaceAll = computed(() => {
  return props.toolCall.input?.replace_all || false
})

// Use the diff library to create a proper unified diff
const diffLines = computed(() => {
  const patch = createPatch(
    filePath.value,
    oldString.value,
    newString.value,
    '',
    '',
    { context: 3 }
  )

  // Parse the patch into lines
  const lines = patch.split('\n')
  const result = []

  // Skip header lines (first 4 lines are patch header)
  for (let i = 4; i < lines.length; i++) {
    const line = lines[i]
    if (!line) continue // Skip empty lines at end

    if (line.startsWith('-')) {
      result.push({ type: 'removed', content: line.substring(1) })
    } else if (line.startsWith('+')) {
      result.push({ type: 'added', content: line.substring(1) })
    } else if (line.startsWith(' ')) {
      result.push({ type: 'context', content: line.substring(1) })
    } else if (line.startsWith('@@')) {
      result.push({ type: 'hunk', content: line })
    }
  }

  return result
})

const removedCount = computed(() => {
  return diffLines.value.filter(line => line.type === 'removed').length
})

const addedCount = computed(() => {
  return diffLines.value.filter(line => line.type === 'added').length
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
.edit-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 0.2rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.edit-file-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem 0.25rem 0 0;
  border-bottom: none;
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

.replace-all-badge {
  background: #ffc107;
  color: #000;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
}

.edit-diff-container {
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 0 0 0.25rem 0.25rem;
  overflow: hidden;
}

.diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
}

.diff-label {
  font-weight: 600;
  color: #6c757d;
}

.diff-stats {
  display: flex;
  gap: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  font-weight: 600;
}

.stat-removed {
  color: #dc3545;
}

.stat-added {
  color: #198754;
}

.unified-diff {
  max-height: 400px;
  overflow: auto;
  white-space: pre;
}

.diff-scroll-container {
  display: inline-block;
  min-width: 100%;
}

.diff-line {
  display: flex;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  line-height: 1.4;
  white-space: nowrap;
}

.diff-line-removed {
  background: #ffebe9;
  color: #24292f;
}

.diff-line-removed .diff-marker {
  background: #ffcecb;
  color: #dc3545;
}

.diff-line-added {
  background: #dafbe1;
  color: #24292f;
}

.diff-line-added .diff-marker {
  background: #abf2bc;
  color: #198754;
}

.diff-line-context {
  background: #fff;
  color: #57606a;
}

.diff-line-context .diff-marker {
  background: #f6f8fa;
  color: #57606a;
}

.diff-line-hunk {
  background: #f6f8fa;
  color: #57606a;
  font-weight: 600;
}

.diff-line-hunk .diff-marker {
  background: #e1e4e8;
  color: #0969da;
}

.diff-line-hunk .diff-content {
  color: #0969da;
}

.diff-marker {
  width: 2rem;
  flex-shrink: 0;
  text-align: center;
  font-weight: 700;
  user-select: none;
}

.diff-content {
  padding: 0.25rem 0.5rem;
  white-space: pre;
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

.success-detail {
  color: #6c757d;
  font-size: 0.9rem;
  font-style: italic;
}

.tool-code {
  margin: 0;
  padding: 0;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre;
  overflow-x: auto;
}
</style>
