<template>
  <div class="mermaid-container" :class="{ 'has-error': error }">
    <!-- Diagram view (default for supported types) -->
    <div
      v-if="!showCode && !error && !unsupported"
      class="mermaid-diagram"
      v-html="svgContent"
      @click="openFullscreen"
    />

    <!-- Error banner + code fallback -->
    <template v-if="error">
      <div class="mermaid-error">Diagram error: {{ error }}</div>
      <pre class="mermaid-code-view"><code class="language-mermaid">{{ content }}</code></pre>
    </template>

    <!-- Unsupported type: code view with soft note -->
    <template v-if="unsupported">
      <div class="mermaid-unsupported">Diagram preview not available for this type</div>
      <pre class="mermaid-code-view"><code class="language-mermaid">{{ content }}</code></pre>
    </template>

    <!-- Code view toggle (supported types only) -->
    <pre v-if="showCode && !error && !unsupported" class="mermaid-code-view"><code class="language-mermaid">{{ content }}</code></pre>

    <!-- Buttons (supported, no error) -->
    <button
      v-if="!error && !unsupported"
      class="mermaid-toggle"
      :title="showCode ? 'Show diagram' : 'Toggle code/diagram view'"
      @click.stop="showCode = !showCode"
    >{{ showCode ? '▶' : '</>' }}</button>

    <button
      v-if="!error && !unsupported && !showCode"
      class="mermaid-fullscreen-btn"
      title="View fullscreen"
      @click.stop="openFullscreen"
    >⛶</button>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { renderMermaidSVG } from 'beautiful-mermaid'
import { openFullView } from '@/composables/useMermaidFullView'

const props = defineProps({
  content: { type: String, required: true },
})

const svgContent = ref('')
const error = ref('')
const showCode = ref(false)
const unsupported = ref(false)

let diagramCounter = 0

// Diagram types supported by beautiful-mermaid
const SUPPORTED_RE = [
  /^(graph|flowchart)\b/i,
  /^sequencediagram\s*$/i,
  /^classdiagram\b/i,
  /^erdiagram\s*$/i,
  /^statediagram(-v2)?\b/i,
  /^xychart(-beta)?\b/i,
]

function isSupported(source) {
  const firstLine = source.trim().split(/[\n;]/)[0]?.trim() ?? ''
  return SUPPORTED_RE.some((re) => re.test(firstLine))
}

function render() {
  error.value = ''
  unsupported.value = false
  if (!isSupported(props.content)) {
    unsupported.value = true
    svgContent.value = ''
    return
  }
  try {
    svgContent.value = renderMermaidSVG(props.content)
  } catch (err) {
    error.value = err?.message || 'Invalid syntax'
  }
}

onMounted(render)
watch(() => props.content, render)

function openFullscreen() {
  if (!error.value && !unsupported.value) {
    openFullView(props.content, `mermaid-wrapper-${++diagramCounter}`)
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

.mermaid-unsupported {
  padding: 0.4rem 0.75rem;
  border-radius: 6px;
  font-size: 0.8em;
  color: #6b7280;
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid rgba(0, 0, 0, 0.08);
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
