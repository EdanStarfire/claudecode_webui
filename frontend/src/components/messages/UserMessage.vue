<template>
  <div class="message-row message-row-user">
    <div class="message-speaker" :title="tooltipText">
      <span class="speaker-label">user</span>
    </div>
    <div class="message-content-column">
      <!-- Content -->
      <div class="message-text" v-html="renderedContent"></div>

      <!-- Tool Results (if any) -->
      <div v-if="hasToolResults" class="tool-results mt-2">
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

const tooltipText = computed(() => {
  return `user\n${formattedTimestamp.value}`
})

const renderedContent = computed(() => {
  const content = props.message.content || ''
  // Render markdown and sanitize
  let html = marked.parse(content)
  // Remove newlines before HTML tags to reduce whitespace
  html = html.replace(/\n</g, '<')
  // Trim trailing newlines
  html = html.replace(/\n+$/, '')
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
/* Two-column row layout */
.message-row {
  display: flex;
  width: 100%;
  min-height: 1.2rem;
  padding: 0.2rem 0;
  line-height: 1.2rem;
}

.message-row-user {
  background-color: #F3E5F5; /* Light purple */
}

/* Speaker column (left) */
.message-speaker {
  width: 8em;
  padding: 0 1rem;
  flex-shrink: 0;
  text-align: right;
  cursor: help;
  font-weight: 500;
  color: #495057;
}

.speaker-label {
  font-size: 0.9rem;
  text-transform: lowercase;
}

/* Content column (right) */
.message-content-column {
  flex: 1;
  padding: 0 1rem 0 0.5rem;
  overflow-wrap: break-word;
}

.message-text {
  line-height: 1.2rem;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* Markdown/code styling - remove bottom margins by default */
.message-text :deep(*) {
  margin-bottom: 0;
}

.message-text :deep(pre) {
  background: #f8f9fa;
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.message-text :deep(code) {
  background: #e9ecef;
  padding: 0.2rem 0.4rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.message-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.message-text :deep(ul),
.message-text :deep(ol) {
  padding-left: 1.5rem;
}

.message-text :deep(blockquote) {
  border-left: 3px solid #dee2e6;
  padding-left: 1rem;
  margin-left: 0;
  color: #6c757d;
}

.message-text :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
}

.message-text :deep(table th),
.message-text :deep(table td) {
  border: 1px solid #6c757d;
  padding: 0.5rem;
  text-align: left;
}

.message-text :deep(table th) {
  background-color: rgba(0, 0, 0, 0.05);
  font-weight: 600;
}

/* Tool results styling */
.tool-result-summary {
  border: 1px solid rgba(0, 0, 0, 0.1);
}

/* Mobile responsive: stack speaker above content */
@media (max-width: 768px) {
  .message-row {
    flex-direction: column;
  }

  .message-speaker {
    width: 100%;
    text-align: left;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  }

  .message-content-column {
    padding: 0.5rem 1rem;
  }
}
</style>
