<template>
  <div class="web-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section mb-3">
      <div class="web-info">
        <span class="web-icon">{{ webIcon }}</span>
        <strong>{{ webLabel }}:</strong>
        <a v-if="url" :href="url" target="_blank" class="web-url">{{ url }}</a>
        <code v-else-if="query" class="web-query">{{ query }}</code>
      </div>

      <div v-if="prompt" class="web-prompt-container">
        <div class="prompt-label">Prompt:</div>
        <div class="prompt-content">{{ prompt }}</div>
      </div>

      <div v-if="domains.length > 0" class="web-domains">
        <span class="domains-label">{{ domainsLabel }}:</span>
        <span v-for="domain in domains" :key="domain" class="domain-badge">{{ domain }}</span>
      </div>
    </div>

    <!-- Result Section -->
    <div v-if="hasResult" class="tool-section">
      <div v-if="isError" class="tool-result tool-result-error">
        <strong>Error:</strong>
        <pre class="tool-code">{{ resultContent }}</pre>
      </div>

      <div v-else class="web-results">
        <div class="results-header">
          <span class="results-label">{{ resultsLabel }}:</span>
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
const isWebFetch = computed(() => props.toolCall.name === 'WebFetch')
const isWebSearch = computed(() => props.toolCall.name === 'WebSearch')

// Parameters
const url = computed(() => {
  return props.toolCall.input?.url || null
})

const query = computed(() => {
  return props.toolCall.input?.query || null
})

const prompt = computed(() => {
  return props.toolCall.input?.prompt || null
})

const allowedDomains = computed(() => {
  return props.toolCall.input?.allowed_domains || []
})

const blockedDomains = computed(() => {
  return props.toolCall.input?.blocked_domains || []
})

const domains = computed(() => {
  if (allowedDomains.value.length > 0) return allowedDomains.value
  if (blockedDomains.value.length > 0) return blockedDomains.value
  return []
})

const domainsLabel = computed(() => {
  if (allowedDomains.value.length > 0) return 'Allowed domains'
  if (blockedDomains.value.length > 0) return 'Blocked domains'
  return 'Domains'
})

const webIcon = computed(() => {
  return isWebFetch.value ? '🌐' : '🔎'
})

const webLabel = computed(() => {
  return isWebFetch.value ? 'Fetching URL' : 'Searching web'
})

const resultsLabel = computed(() => {
  return isWebFetch.value ? 'Fetched content' : 'Search results'
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
.web-tool-handler {
  font-size: 0.9rem;
}

.tool-section {
  margin-bottom: 1rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.web-info {
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

.web-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.web-url {
  background: #e9ecef;
  padding: 0.2rem 0.5rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  color: #0d6efd;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.web-url:hover {
  text-decoration: underline;
}

.web-query {
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

.web-prompt-container {
  padding: 0.75rem;
  background: #fff;
  border-left: 1px solid #dee2e6;
  border-right: 1px solid #dee2e6;
}

.prompt-label {
  font-weight: 600;
  color: #6c757d;
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}

.prompt-content {
  font-family: 'Courier New', monospace;
  font-size: 0.85rem;
  color: #495057;
  line-height: 1.4;
}

.web-domains {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.75rem;
  background: #fff;
  border: 1px solid #dee2e6;
  border-radius: 0 0 0.25rem 0.25rem;
  border-top: none;
}

.domains-label {
  font-weight: 600;
  color: #6c757d;
  font-size: 0.85rem;
}

.domain-badge {
  background: #e7f1ff;
  color: #084298;
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  font-family: 'Courier New', monospace;
}

.web-results {
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
