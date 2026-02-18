<template>
  <div class="web-tool-handler">
    <!-- Parameters Section -->
    <div class="tool-section">
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

// Exposed metadata for parent components
const summary = computed(() => `${webLabel.value}: ${url.value || query.value || ''}`)
const params = computed(() => ({ url: url.value, query: query.value, prompt: prompt.value }))
const result = computed(() => props.toolCall.result || null)
defineExpose({ summary, params, result })
</script>

<style scoped>
.web-tool-handler {
  font-size: var(--tool-font-size, 13px);
}

.tool-section {
  margin-bottom: 0.2rem;
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
  background: var(--tool-bg, #f8fafc);
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: var(--tool-radius, 4px) var(--tool-radius, 4px) 0 0;
  border-bottom: none;
  overflow: hidden;
}

.web-icon {
  font-size: 1.2rem;
  flex-shrink: 0;
}

.web-url {
  background: var(--tool-bg-header, #f1f5f9);
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  color: #0d6efd;
  text-decoration: none;
  overflow-x: auto;
  white-space: nowrap;
  max-width: 100%;
}

.web-url:hover {
  text-decoration: underline;
}

.web-query {
  background: var(--tool-bg-header, #f1f5f9);
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
  color: #0d6efd;
  overflow-x: auto;
  white-space: nowrap;
  max-width: 100%;
}

.web-prompt-container {
  padding: 0.75rem;
  background: #fff;
  border-left: 1px solid var(--tool-border, #e2e8f0);
  border-right: 1px solid var(--tool-border, #e2e8f0);
}

.prompt-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
  margin-bottom: 0.5rem;
}

.prompt-content {
  font-family: 'Courier New', monospace;
  font-size: var(--tool-code-font-size, 11px);
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
  border: 1px solid var(--tool-border, #e2e8f0);
  border-radius: 0 0 var(--tool-radius, 4px) var(--tool-radius, 4px);
  border-top: none;
}

.domains-label {
  font-weight: 600;
  color: var(--tool-text-muted, #64748b);
  font-size: var(--tool-code-font-size, 11px);
}

.domain-badge {
  background: #e7f1ff;
  color: #084298;
  padding: 0.2rem 0.5rem;
  border-radius: var(--tool-radius, 4px);
  font-size: 0.75rem;
  font-weight: 600;
  font-family: 'Courier New', monospace;
}

.web-results {
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
