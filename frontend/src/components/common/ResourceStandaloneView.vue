<template>
  <div class="standalone-viewer">
    <div class="text-header">
      <div class="text-header-info">
        <h5 class="text-filename">{{ title || 'Resource' }}</h5>
      </div>
      <div class="display-mode-toggle" v-if="content">
        <button
          class="toggle-btn"
          :class="{ active: displayMode === 'raw' }"
          @click="displayMode = 'raw'"
          title="Raw text"
        >Raw</button>
        <button
          class="toggle-btn"
          :class="{ active: displayMode === 'markdown' }"
          @click="displayMode = 'markdown'"
          title="Rendered markdown"
        >Markdown</button>
      </div>
    </div>
    <div class="text-body">
      <div v-if="loading" class="text-loading">
        <div class="spinner-border spinner-border-sm text-secondary" role="status"></div>
        <span>Loading content...</span>
      </div>
      <div v-else-if="error" class="text-error">
        Failed to load content: {{ error }}
      </div>
      <pre v-else-if="content && displayMode === 'raw'" class="text-content">{{ content }}</pre>
      <MarkdownView v-else-if="content && displayMode === 'markdown'" class="markdown-content" :content="content" />
      <div v-else class="text-unavailable">No content available.</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownView from './MarkdownView.vue'

const route = useRoute()

const src = route.query.src || ''
const title = route.query.title || ''

const displayMode = ref('markdown')
const content = ref('')
const loading = ref(true)
const error = ref(null)

// Only same-origin backend resource URLs are ever legitimate here (this route is reached
// exclusively via ResourceFullView's own window.open() call, never a user-typed link) —
// reject anything else so this route can't be turned into an open cross-origin content loader.
const isSameOriginResourceUrl = (value) => /^\/api\/(sessions|projects)\//.test(value)

onMounted(async () => {
  if (title) {
    document.title = title
  }
  if (!src) {
    loading.value = false
    error.value = 'No resource specified'
    return
  }
  if (!isSameOriginResourceUrl(src)) {
    loading.value = false
    error.value = 'Invalid resource URL'
    return
  }
  try {
    const response = await fetch(src)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    content.value = await response.text()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.standalone-viewer {
  display: flex;
  flex-direction: column;
  width: 100vw;
  height: 100vh;
  background: var(--bs-body-bg);
  overflow: hidden;
}

.text-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  flex-shrink: 0;
}

.text-header-info {
  min-width: 0;
  flex: 1;
}

.text-filename {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.display-mode-toggle {
  display: flex;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  margin-left: 12px;
}

.toggle-btn {
  padding: 5px 12px;
  background: var(--bs-secondary-bg);
  border: none;
  color: var(--bs-body-color);
  font-size: 0.8rem;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
}

.toggle-btn:first-child {
  border-right: 1px solid var(--bs-border-color);
}

.toggle-btn:hover:not(.active) {
  background: var(--bs-tertiary-bg);
}

.toggle-btn.active {
  background: var(--bs-primary, var(--bs-link-color));
  color: #fff;
}

.text-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.text-content {
  margin: 0;
  padding: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.85rem;
  line-height: 1.5;
  color: var(--bs-body-color);
  white-space: pre-wrap;
  word-wrap: break-word;
  background: transparent;
  border: none;
}

.text-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--bs-secondary-color, #6c757d);
  padding: 24px;
  justify-content: center;
}

.text-error {
  color: var(--bs-danger);
  padding: 24px;
  text-align: center;
}

.text-unavailable {
  color: var(--bs-secondary-color, #6c757d);
  padding: 24px;
  text-align: center;
  font-style: italic;
}

.markdown-content {
  max-width: 900px;
  margin: 0 auto;
  font-size: 0.9rem;
  line-height: 1.6;
  color: var(--bs-body-color);
  word-wrap: break-word;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin-top: 1.2em;
  margin-bottom: 0.6em;
  font-weight: 600;
  color: var(--bs-emphasis-color);
}

.markdown-content :deep(h1) { font-size: 1.6rem; border-bottom: 1px solid var(--bs-border-color); padding-bottom: 0.3em; }
.markdown-content :deep(h2) { font-size: 1.35rem; border-bottom: 1px solid var(--bs-border-color); padding-bottom: 0.3em; }
.markdown-content :deep(h3) { font-size: 1.15rem; }

.markdown-content :deep(p) {
  margin-bottom: 0.8em;
}

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 2em;
  margin-bottom: 0.8em;
}

.markdown-content :deep(li) {
  margin-bottom: 0.3em;
}

.markdown-content :deep(code) {
  background: var(--bs-tertiary-bg);
  color: var(--bs-code-color, var(--bs-body-color));
  padding: 0.15em 0.4em;
  border-radius: 3px;
  font-size: 0.85em;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.markdown-content :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 6px;
  overflow-x: auto;
  margin-bottom: 0.8em;
}

.markdown-content :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.markdown-content :deep(blockquote) {
  border-left: 3px solid var(--bs-border-color);
  padding-left: 1em;
  margin-left: 0;
  margin-bottom: 0.8em;
  color: var(--bs-secondary-color, #6c757d);
}

.markdown-content :deep(a) {
  color: var(--bs-link-color);
  text-decoration: none;
}

.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.markdown-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 0.8em;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid var(--bs-border-color);
  padding: 6px 12px;
  text-align: left;
}

.markdown-content :deep(th) {
  background: var(--table-header-bg);
  font-weight: 600;
}

.markdown-content :deep(tr:nth-child(even)) {
  background: var(--table-stripe-bg);
}

.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--bs-border-color);
  margin: 1.2em 0;
}

.markdown-content :deep(img) {
  max-width: 100%;
  height: auto;
}
</style>
