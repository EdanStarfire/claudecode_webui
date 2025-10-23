<template>
  <div class="card user-message bg-light border-start border-primary border-3">
    <div class="card-body">
      <!-- Header -->
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="d-flex align-items-center gap-2">
          <span class="message-icon">👤</span>
          <strong class="message-role">User</strong>
        </div>
        <small class="text-muted">{{ formattedTimestamp }}</small>
      </div>

      <!-- Content -->
      <div class="message-content" v-html="renderedContent"></div>

      <!-- Tool Results (if any) -->
      <div v-if="hasToolResults" class="tool-results mt-3">
        <div class="tool-results-header text-muted small mb-2">
          <i class="bi bi-tools"></i> Tool Results ({{ toolResults.length }})
        </div>
        <div
          v-for="(result, index) in toolResults"
          :key="index"
          class="tool-result-summary p-2 mb-2 rounded"
          :class="result.is_error ? 'bg-danger bg-opacity-10' : 'bg-success bg-opacity-10'"
        >
          <div class="d-flex align-items-start gap-2">
            <span>{{ result.is_error ? '❌' : '✅' }}</span>
            <div class="flex-grow-1">
              <small class="text-muted">Tool: {{ result.tool_use_id }}</small>
              <div v-if="result.is_error" class="text-danger small">
                {{ truncate(result.content, 200) }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

// Configure marked for safe rendering
marked.setOptions({
  breaks: true,
  gfm: true
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

const renderedContent = computed(() => {
  const content = props.message.content || ''
  // Render markdown and sanitize
  const html = marked.parse(content)
  return DOMPurify.sanitize(html)
})

const hasToolResults = computed(() => {
  return props.message.metadata?.has_tool_results &&
         props.message.metadata?.tool_results?.length > 0
})

const toolResults = computed(() => {
  return props.message.metadata?.tool_results || []
})

function truncate(text, maxLength) {
  if (!text) return ''
  if (text.length <= maxLength) return text
  return text.substring(0, maxLength) + '...'
}
</script>

<style scoped>
.user-message {
  max-width: 100%;
}

.message-icon {
  font-size: 1.2rem;
}

.message-content {
  white-space: pre-wrap;
  word-wrap: break-word;
}

.message-content :deep(pre) {
  background: #f8f9fa;
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
}

.message-content :deep(code) {
  background: #e9ecef;
  padding: 0.2rem 0.4rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.message-content :deep(pre code) {
  background: transparent;
  padding: 0;
}

.tool-result-summary {
  border: 1px solid rgba(0, 0, 0, 0.1);
}
</style>
