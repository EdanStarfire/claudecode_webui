<template>
  <div class="search-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="search-info">
        <span class="search-icon">{{ searchIcon }}</span>
        <strong>{{ searchLabel }}:</strong>
        <code class="search-pattern">{{ pattern }}</code>
        <span v-if="searchPath" class="search-path">in {{ searchPath }}</span>
        <span v-if="hasFilters" class="search-filters">
          <span v-if="fileType" class="filter-badge">type: {{ fileType }}</span>
          <span v-if="globPattern" class="filter-badge">glob: {{ globPattern }}</span>
          <span v-if="caseInsensitive" class="filter-badge">case-insensitive</span>
        </span>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="search-results">
        <div class="results-header">
          <span class="results-label">{{ resultsLabel }}:</span>
          <span class="results-count-badge">{{ resultCount }} {{ resultCount === 1 ? 'match' : 'matches' }}</span>
        </div>
        <div class="results-content">
          <pre class="tool-code">{{ resultContent }}</pre>
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

// Determine tool type
const isGrep = computed(() => props.toolCall.name === 'Grep')
const isGlob = computed(() => props.toolCall.name === 'Glob')

// Parameters
const pattern = computed(() => {
  return props.toolCall.input?.pattern || 'Unknown'
})

const searchPath = computed(() => {
  return props.toolCall.input?.path || null
})

const fileType = computed(() => {
  return props.toolCall.input?.type || null
})

const globPattern = computed(() => {
  return props.toolCall.input?.glob || null
})

const caseInsensitive = computed(() => {
  return props.toolCall.input?.['-i'] || false
})

const hasFilters = computed(() => {
  return fileType.value || globPattern.value || caseInsensitive.value
})

const searchIcon = computed(() => {
  return isGrep.value ? '🔍' : '📁'
})

const searchLabel = computed(() => {
  return isGrep.value ? 'Searching content' : 'Finding files'
})

const resultsLabel = computed(() => {
  return isGrep.value ? 'Matches' : 'Files found'
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

const resultCount = computed(() => {
  if (!hasResult.value || isError.value || !resultContent.value) return 0

  // Count non-empty lines
  const lines = resultContent.value.split('\n').filter(line => line.trim())
  return lines.length
})
</script>

<style scoped>
.search-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.search-info {
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

.search-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.search-pattern {
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

.search-path {
  color: #6c757d;
  font-size: 0.85rem;
}

.search-filters {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.filter-badge {
  background: #e7f1ff;
  color: #084298;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  font-family: 'Courier New', monospace;
}

.search-results {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  overflow: hidden;
}

.results-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #e9ecef;
  border-bottom: 1px solid #dee2e6;
  flex-wrap: wrap;
}

.results-label {
  font-weight: 600;
  color: #6c757d;
}

.results-count-badge {
  background: #0d6efd;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.8rem;
  font-weight: 600;
}

.results-content {
  padding: 0;
}

.tool-result {
  padding: 0.75rem;
  border-radius: 0.25rem;
}

.tool-result-error {
  background: #fff5f5;
  border: 1px solid #dc3545;
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
</style>
