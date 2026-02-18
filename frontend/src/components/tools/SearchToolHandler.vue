<template>
  <div class="search-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
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
import { computed, toRef } from 'vue'
import { useToolResult } from '@/composables/useToolResult'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

// Shared result composable
const { hasResult, isError, resultContent } = useToolResult(toRef(props, 'toolCall'))

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

const resultCount = computed(() => {
  if (!hasResult.value || isError.value || !resultContent.value) return 0

  // Count non-empty lines
  const lines = resultContent.value.split('\n').filter(line => line.trim())
  return lines.length
})

// Exposed metadata for parent components
const summary = computed(() => `${searchLabel.value}: ${pattern.value}`)
const params = computed(() => ({ pattern: pattern.value, path: searchPath.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.search-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
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
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.search-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.search-pattern {
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

.search-path {
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
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
  border-radius: var(--tool-radius, 4px);
  font-size: 0.75rem;
  font-weight: 600;
  font-family: 'Courier New', monospace;
}

.search-results {
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px);
  overflow: hidden;
}

.results-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--tool-bg-header, #f1f5f9);
  border-bottom: 1px solid var(--tool-border, #e2e8f0);
  flex-wrap: wrap;
}

.results-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
}

.results-count-badge {
  background: #0d6efd;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.8rem;
  font-weight: 600;
}

.results-content {
  padding: 0;
}

.tool-result {
  padding: 0.75rem;
  border-radius: var(--tool-radius, 4px);
}

.tool-result-error {
  background: var(--tool-error-bg, #fee2e2);
  border: 1px solid var(--tool-error-border, #f87171);
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
</style>
