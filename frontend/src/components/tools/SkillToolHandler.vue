<template>
  <div class="skill-tool-handler">
    <!-- Skill Info Section -->
    <div class="tool-section mb-3">
      <div class="skill-info">
        <span class="skill-icon">📋</span>
        <strong>Skill:</strong>
        <code class="skill-name">{{ skillName }}</code>
        <span class="skill-status-badge" :class="statusBadgeClass">{{ statusText }}</span>
      </div>
    </div>

    <!-- Base Directory Section (if available) -->
    <div v-if="baseDirectory" class="tool-section mb-3">
      <div class="base-directory-info">
        <span class="directory-icon">📁</span>
        <strong>Base directory:</strong>
        <code class="directory-path">{{ baseDirectory }}</code>
      </div>
    </div>

    <!-- Skill Content Section (collapsible) -->
    <div v-if="hasSkillContent" class="tool-section">
      <div class="skill-content-header" @click="toggleContentExpanded">
        <div class="d-flex align-items-center gap-2">
          <i class="bi" :class="isContentExpanded ? 'bi-chevron-down' : 'bi-chevron-right'"></i>
          <strong>Skill Content</strong>
          <span class="text-muted small">({{ skillContentLineCount }} lines)</span>
        </div>
      </div>

      <div v-if="isContentExpanded" class="skill-content-body">
        <pre class="skill-content-text">{{ skillContent }}</pre>
      </div>
    </div>

    <!-- Result Section (for errors or non-standard results) -->
    <div v-if="hasError" class="tool-section mt-3">
      <div class="tool-section-label text-danger">
        <i class="bi bi-x-circle"></i>
        Error:
      </div>
      <div class="tool-result tool-result-error">
        <pre class="tool-code">{{ errorMessage }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Collapsible content state
const isContentExpanded = ref(false)

function toggleContentExpanded() {
  isContentExpanded.value = !isContentExpanded.value
}

// Extract skill name from command parameter
const skillName = computed(() => {
  return props.toolCall.input?.command || 'Unknown Skill'
})

// Determine skill status
const statusText = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return 'Pending'
    case 'permission_required':
      return 'Awaiting Permission'
    case 'executing':
      return 'Launching'
    case 'completed':
      if (props.toolCall.result?.error) return 'Error'
      return 'Running'
    case 'error':
      return 'Error'
    default:
      return 'Unknown'
  }
})

const statusBadgeClass = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return 'badge-secondary'
    case 'permission_required':
      return 'badge-warning'
    case 'executing':
      return 'badge-primary'
    case 'completed':
      return props.toolCall.result?.error ? 'badge-danger' : 'badge-success'
    case 'error':
      return 'badge-danger'
    default:
      return 'badge-secondary'
  }
})

// Parse result content for base directory and skill content
const resultContent = computed(() => {
  if (!props.toolCall.result) return ''

  const result = props.toolCall.result

  // Handle result with content property
  if (result.content !== undefined) {
    return typeof result.content === 'string'
      ? result.content
      : JSON.stringify(result.content, null, 2)
  }

  // Handle result with message property
  if (result.message) {
    return result.message
  }

  // Fallback: stringify entire result
  return JSON.stringify(result, null, 2)
})

// Extract base directory from result content
// Format: "Base directory: /path/to/skill" or "Base directory for this skill: /path"
const baseDirectory = computed(() => {
  const content = resultContent.value
  if (!content) return null

  // Match "Base directory: <path>" or "Base directory for this skill: <path>"
  const match = content.match(/Base directory(?:\s+for\s+this\s+skill)?:\s*(.+?)(?:\n|$)/i)
  if (match && match[1]) {
    return match[1].trim()
  }

  return null
})

// Extract skill content (markdown after base directory line)
const skillContent = computed(() => {
  const content = resultContent.value
  if (!content) return ''

  // If there's a base directory line, get content after it
  if (baseDirectory.value) {
    const baseDirLinePattern = /Base directory(?:\s+for\s+this\s+skill)?:.*?\n/i
    const afterBaseDir = content.replace(baseDirLinePattern, '')
    return afterBaseDir.trim()
  }

  // Otherwise, return full content
  return content.trim()
})

const hasSkillContent = computed(() => {
  return skillContent.value.length > 0
})

const skillContentLineCount = computed(() => {
  if (!skillContent.value) return 0
  return skillContent.value.split('\n').length
})

// Error handling
const hasError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const errorMessage = computed(() => {
  if (!hasError.value) return ''

  const result = props.toolCall.result
  if (result?.message) return result.message
  if (result?.error) return result.error
  return 'An error occurred'
})
</script>

<style scoped>
.skill-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.skill-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  flex-wrap: wrap;
}

.skill-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
}

.skill-name {
  padding: 0.25rem 0.5rem;
  background: #e7f3ff;
  border-radius: 0.25rem;
  font-size: 0.85rem;
  font-family: 'Courier New', monospace;
  color: #0969da;
  font-weight: 600;
}

.skill-status-badge {
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}

.badge-secondary {
  background: #6c757d;
  color: white;
}

.badge-warning {
  background: #ffc107;
  color: #000;
}

.badge-primary {
  background: #0d6efd;
  color: white;
}

.badge-success {
  background: #198754;
  color: white;
}

.badge-danger {
  background: #dc3545;
  color: white;
}

.base-directory-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem;
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  flex-wrap: wrap;
}

.directory-icon {
  font-size: 1.1rem;
  flex-shrink: 0;
}

.directory-path {
  padding: 0.2rem 0.5rem;
  background: #e9ecef;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  color: #495057;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.skill-content-header {
  padding: 0.75rem;
  background: #e9ecef;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem 0.25rem 0 0;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.skill-content-header:hover {
  background: #dee2e6;
}

.skill-content-body {
  border: 1px solid #dee2e6;
  border-top: none;
  border-radius: 0 0 0.25rem 0.25rem;
  overflow: hidden;
}

.skill-content-text {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: #f8f9fa;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: 500px;
  overflow-y: auto;
  line-height: 1.5;
}

.tool-section-label {
  font-weight: 600;
  margin-bottom: 0.5rem;
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

.tool-code {
  margin: 0;
  padding: 0.75rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  background: transparent;
  border: none;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
</style>
