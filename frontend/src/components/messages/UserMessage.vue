<template>
  <div class="msg-wrapper msg-user">
    <div class="msg-meta">
      <span class="msg-role">user</span>
      <span class="msg-time">{{ formattedTimestamp }}</span>
    </div>
    <div class="msg-bubble msg-bubble-user">
      <!-- Content -->
      <div class="msg-text" v-html="renderedContent"></div>

      <!-- Tool Results (if any) -->
      <div v-if="hasToolResults" class="tool-results mt-2">
        <div class="tool-results-header text-muted small mb-2">
          Tool Results ({{ toolResults.length }})
        </div>
        <div
          v-for="(result, index) in toolResults"
          :key="index"
          class="tool-result-summary"
          :class="result.is_error ? 'result-error' : 'result-success'"
        >
          <div class="d-flex align-items-start gap-2">
            <span>{{ result.is_error ? '✗' : '✓' }}</span>
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
/* Right-aligned user bubble */
.msg-wrapper {
  padding: 4px 16px;
}

.msg-user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.msg-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 2px;
  padding: 0 4px;
}

.msg-role {
  font-size: 12px;
  font-weight: 600;
  color: #3b82f6;
}

.msg-time {
  font-size: 11px;
  color: #94a3b8;
}

.msg-bubble {
  border-radius: 12px;
  padding: 10px 14px;
  max-width: 85%;
  min-width: 60px;
}

.msg-bubble-user {
  background: #eef2ff;
  border: 1px solid #e0e7ff;
  border-top-right-radius: 4px;
}

.msg-text {
  font-size: 14px;
  line-height: 1.5;
  color: #1e293b;
  white-space: pre-wrap;
  word-wrap: break-word;
}

/* Markdown styling */
.msg-text :deep(*) {
  margin-bottom: 0;
}

.msg-text :deep(p) {
  margin-bottom: 0;
}

.msg-text :deep(p + p) {
  margin-top: 0.5em;
}

.msg-text :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.msg-text :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.msg-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.msg-text :deep(ul),
.msg-text :deep(ol) {
  padding-left: 1.5rem;
}

.msg-text :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 1rem;
  margin-left: 0;
  color: #64748b;
}

.msg-text :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
}

.msg-text :deep(table th),
.msg-text :deep(table td) {
  border: 1px solid #94a3b8;
  padding: 0.5rem;
  text-align: left;
}

.msg-text :deep(table th) {
  background-color: rgba(0, 0, 0, 0.04);
  font-weight: 600;
}

/* Tool results */
.tool-result-summary {
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 4px;
  font-size: 12px;
}

.result-error {
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.15);
}

.result-success {
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.15);
}

/* Mobile */
@media (max-width: 768px) {
  .msg-bubble {
    max-width: 95%;
  }
}
</style>
