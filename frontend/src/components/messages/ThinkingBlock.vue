<template>
  <div class="thinking-block">
    <div
      class="thinking-header d-flex align-items-center gap-2 p-2 bg-info bg-opacity-10 rounded cursor-pointer"
      role="button"
      :aria-expanded="isExpanded"
      aria-label="Toggle thinking block"
      @click="toggleExpanded"
    >
      <span :aria-label="isExpanded ? 'Collapse' : 'Expand'">{{ isExpanded ? '▾' : '▸' }}</span>
      <span class="thinking-label">
        💡 Thinking
      </span>
      <small class="text-muted ms-auto">{{ contentLength }} characters</small>
    </div>

    <div v-if="isExpanded" class="thinking-content mt-2 p-3 rounded">
      <MarkdownView class="thinking-text" ref="thinkingRef" :content="thinkingContent" :streaming="streaming" :caret="streaming" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useResourceImages } from '@/composables/useResourceImages'
import { useSessionStore } from '@/stores/session'
import MarkdownView from '@/components/common/MarkdownView.vue'

const props = defineProps({
  thinking: {
    type: String,
    required: true
  },
  streaming: {   // Issue #1486 — forwarded from AssistantMessage when message is mid-stream
    type: Boolean,
    default: false
  }
})

const isExpanded = ref(false)

const contentLength = computed(() => {
  return props.thinking?.length || 0
})

const sessionStore = useSessionStore()

const thinkingContent = computed(() => props.thinking || '')

// Inline resource image click-to-open
const thinkingRef = ref(null)
const currentSessionId = computed(() => sessionStore.currentSessionId)
useResourceImages(thinkingRef, currentSessionId)

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
.thinking-block {
  border: 1px solid rgba(var(--bs-info-rgb), 0.25);
  border-radius: 0.375rem;
  overflow: hidden;
}

.thinking-content {
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 0.375rem;
}

.thinking-header {
  user-select: none;
  transition: background-color 0.15s ease;
}

.thinking-header:hover {
  background-color: rgba(var(--bs-info-rgb), 0.15) !important;
}

.cursor-pointer {
  cursor: pointer;
}

.thinking-label {
  font-weight: 500;
  color: var(--bs-info);
}

.thinking-text {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  font-size: 0.95rem;
  line-height: 1.6;
  color: var(--bs-body-color);
}

.thinking-text :deep(pre) {
  background: var(--bs-tertiary-bg);
  padding: 0.75rem;
  border-radius: 0.25rem;
  overflow-x: auto;
}

.thinking-text :deep(code) {
  background: var(--bs-tertiary-bg);
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
