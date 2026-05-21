<template>
  <div class="mermaid-container" :class="{ 'has-error': error }">
    <!-- Diagram view (default) -->
    <div
      v-if="!showCode && !error"
      class="mermaid-diagram"
      v-html="svgContent"
      @click="openFullscreen"
    />

    <!-- Error banner + code fallback -->
    <template v-if="error">
      <div class="mermaid-error">Diagram error: {{ error }}</div>
      <pre class="mermaid-code-view"><code class="language-mermaid">{{ content }}</code></pre>
    </template>

    <!-- Code view toggle -->
    <pre v-if="showCode && !error" class="mermaid-code-view"><code class="language-mermaid">{{ content }}</code></pre>

    <!-- Buttons -->
    <button
      v-if="!error"
      class="mermaid-toggle"
      :title="showCode ? 'Show diagram' : 'Toggle code/diagram view'"
      @click.stop="showCode = !showCode"
    >{{ showCode ? '▶' : '</>' }}</button>

    <button
      v-if="!error && !showCode"
      class="mermaid-fullscreen-btn"
      title="View fullscreen"
      @click.stop="openFullscreen"
    >⛶</button>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import mermaid from 'mermaid'
import { openFullView } from '@/composables/useMermaidFullView'

const props = defineProps({
  content: { type: String, required: true },
})

const svgContent = ref('')
const error = ref('')
const showCode = ref(false)

let instanceCounter = 0
let renderSeq = 0

let initialized = false
function ensureInit() {
  if (!initialized) {
    mermaid.initialize({ startOnLoad: false })
    initialized = true
  }
}

async function render() {
  ensureInit()
  const seq = ++renderSeq
  error.value = ''
  try {
    const id = `mermaid-w-${++instanceCounter}`
    const { svg } = await mermaid.render(id, props.content)
    if (seq === renderSeq) svgContent.value = svg
  } catch (err) {
    if (seq === renderSeq) error.value = err?.message || 'Invalid syntax'
  }
}

onMounted(render)
watch(() => props.content, render)

function openFullscreen() {
  if (!error.value) {
    openFullView(props.content, `mermaid-wrapper-${instanceCounter}`)
  }
}
</script>

<style scoped>
.mermaid-container {
  position: relative;
  margin: 0.5rem 0;
  max-width: 100%;
  overflow-x: auto;
}

.mermaid-diagram {
  cursor: zoom-in;
  max-width: 100%;
}

.mermaid-diagram :deep(svg) {
  max-width: 100%;
  height: auto;
  display: block;
}

.mermaid-code-view {
  background: rgba(0, 0, 0, 0.04);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Courier New', monospace;
  font-size: 0.85em;
}

.mermaid-error {
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.85em;
  font-weight: 500;
  color: #9a3412;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  margin-bottom: 0.5rem;
}

.mermaid-toggle,
.mermaid-fullscreen-btn {
  position: absolute;
  top: 4px;
  background: rgba(0, 0, 0, 0.06);
  border: none;
  border-radius: 4px;
  padding: 2px 6px;
  cursor: pointer;
  font-size: 0.8em;
  line-height: 1.4;
  opacity: 0.7;
  transition: opacity 0.15s;
}

.mermaid-container:hover .mermaid-toggle,
.mermaid-container:hover .mermaid-fullscreen-btn {
  opacity: 1;
}

.mermaid-toggle {
  right: 30px;
}

.mermaid-fullscreen-btn {
  right: 4px;
}
</style>
