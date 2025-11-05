<template>
  <div class="thinking-block">
    <div
      class="thinking-header d-flex align-items-center gap-2 p-2 bg-info bg-opacity-10 rounded cursor-pointer"
      @click="toggleExpanded"
    >
      <span :aria-label="isExpanded ? 'Collapse' : 'Expand'">{{ isExpanded ? '▾' : '▸' }}</span>
      <span class="thinking-label">
        💡 Thinking
      </span>
      <small class="text-muted ms-auto">{{ contentLength }} characters</small>
    </div>

    <div v-if="isExpanded" class="thinking-content mt-2 p-3 bg-light rounded border">
      <div class="thinking-text" v-html="renderedThinking"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps({
  thinking: {
    type: String,
    required: true
  }
})

const isExpanded = ref(false)

const contentLength = computed(() => {
  return props.thinking?.length || 0
})

const renderedThinking = computed(() => {
  const content = props.thinking || ''
  // Render markdown and sanitize
  const html = marked.parse(content)
  return DOMPurify.sanitize(html)
})

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
.thinking-block {
  border: 1px solid rgba(13, 202, 240, 0.25);
  border-radius: 0.375rem;
  overflow: hidden;
}

.thinking-header {
  user-select: none;
  transition: background-color 0.15s ease;
}

.thinking-header:hover {
  background-color: rgba(13, 202, 240, 0.15) !important;
}

.cursor-pointer {
  cursor: pointer;
}

.thinking-label {
  font-weight: 500;
  color: #0dcaf0;
}

.thinking-text {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  font-size: 0.95rem;
  line-height: 1.6;
  color: #495057;
}

.thinking-text :deep(pre) {
  background: #f8f9fa;
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
}

.thinking-text :deep(code) {
  background: #e9ecef;
  padding: 0.2rem 0.4rem;
  border-radius: 0.2rem;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.thinking-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.thinking-text :deep(p) {
  margin-bottom: 0.5rem;
}

.thinking-text :deep(p:last-child) {
  margin-bottom: 0;
}
</style>
