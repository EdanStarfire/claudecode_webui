<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="isOpen"
        class="mermaid-full-view-overlay"
        @click="handleOverlayClick"
        @keydown="handleKeydown"
        tabindex="0"
        ref="overlayRef"
      >
        <!-- Close Button -->
        <button
          class="mfv-close-btn"
          @click.stop="close"
          title="Close (Escape)"
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>

        <!-- Toolbar -->
        <div class="mfv-toolbar" @click.stop>
          <button
            class="mfv-tool-btn"
            :class="{ active: showSource }"
            @click="showSource = !showSource"
            title="Toggle source code"
          >&lt;/&gt;</button>
          <button
            class="mfv-tool-btn"
            @click="exportSVG"
            title="Download SVG"
          >SVG</button>
          <button
            class="mfv-tool-btn"
            @click="exportPNG"
            :disabled="pngExporting"
            title="Download PNG"
          >{{ pngExporting ? '…' : 'PNG' }}</button>
        </div>

        <!-- Content -->
        <div class="mfv-content" @click.stop ref="contentRef">
          <!-- Source view -->
          <div v-if="showSource" class="mfv-source">
            <pre><code class="language-mermaid">{{ currentSource }}</code></pre>
          </div>
          <!-- Diagram view -->
          <div v-else class="mfv-diagram" ref="diagramRef">
            <div v-if="renderError" class="mfv-error">{{ renderError }}</div>
            <div v-else-if="rendering" class="mfv-loading">Rendering…</div>
          </div>
        </div>

        <!-- PNG export warning -->
        <div v-if="pngWarning" class="mfv-png-warning" @click.stop>
          {{ pngWarning }}
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, watch, nextTick, onUnmounted } from 'vue'
import { fullViewSource, fullViewDiagramId } from '@/composables/useMermaid.js'

const isOpen = ref(false)
const currentSource = ref('')
const showSource = ref(false)
const renderError = ref('')
const rendering = ref(false)
const pngExporting = ref(false)
const pngWarning = ref('')

const overlayRef = ref(null)
const contentRef = ref(null)
const diagramRef = ref(null)

let fullscreenDiagramCounter = 0
let pngWarningTimer = null

// Watch for fullViewSource changes to open the modal
watch(fullViewSource, (src) => {
  if (src) {
    currentSource.value = src
    showSource.value = false
    renderError.value = ''
    isOpen.value = true
    nextTick(() => {
      overlayRef.value?.focus()
      renderDiagram()
    })
  }
})

// Watch showSource toggle — re-render when switching back to diagram view
watch(showSource, (showing) => {
  if (!showing && isOpen.value) {
    nextTick(() => renderDiagram())
  }
})

watch(isOpen, (open) => {
  if (open) {
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})

async function renderDiagram() {
  if (!diagramRef.value) return
  rendering.value = true
  renderError.value = ''

  // Clear any previous render
  diagramRef.value.innerHTML = ''

  try {
    const [{ default: mermaid }, zenuml] = await Promise.all([
      import('mermaid'),
      import('@mermaid-js/mermaid-zenuml')
    ])
    mermaid.registerExternalDiagrams([zenuml.default])
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default' })

    const id = `mermaid-fullscreen-${++fullscreenDiagramCounter}`
    const { svg } = await Promise.race([
      mermaid.render(id, currentSource.value),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Render timeout (10s)')), 10000)
      )
    ])

    if (diagramRef.value) {
      diagramRef.value.innerHTML = svg
      // Make SVG fill available space
      const svgEl = diagramRef.value.querySelector('svg')
      if (svgEl && !svgEl.querySelector('foreignObject')) {
        svgEl.style.maxWidth = '100%'
        svgEl.style.height = 'auto'
      }
    }
  } catch (err) {
    renderError.value = `Diagram error: ${err.message || 'Invalid syntax'}`
  } finally {
    rendering.value = false
    // Clean up any leftover mermaid render artifacts outside the modal
    const id = `mermaid-fullscreen-${fullscreenDiagramCounter}`
    const leftover = document.getElementById(id)
    if (leftover && !diagramRef.value?.contains(leftover)) leftover.remove()
    const dLeftover = document.getElementById('d' + id)
    if (dLeftover && !diagramRef.value?.contains(dLeftover)) dLeftover.remove()
  }
}

function close() {
  isOpen.value = false
  fullViewSource.value = null
  fullViewDiagramId.value = null
  showSource.value = false
  pngWarning.value = ''
  if (pngWarningTimer) clearTimeout(pngWarningTimer)
}

function handleOverlayClick(event) {
  if (event.target === event.currentTarget) close()
}

function handleKeydown(event) {
  if (event.key === 'Escape') close()
}

function getSvgElement() {
  return diagramRef.value?.querySelector('svg') ?? null
}

function exportSVG() {
  const svgEl = getSvgElement()
  if (!svgEl) return
  const svgData = new XMLSerializer().serializeToString(svgEl)
  const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'diagram.svg'
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

async function exportPNG() {
  const svgEl = getSvgElement()
  if (!svgEl) return

  // Detect foreignObject (ZenUML) — canvas drawImage fails for these
  if (svgEl.querySelector('foreignObject')) {
    showPngWarning('PNG export is not available for this diagram type. Use SVG export instead.')
    return
  }

  pngExporting.value = true
  pngWarning.value = ''

  try {
    const svgData = new XMLSerializer().serializeToString(svgEl)
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)

    await new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => {
        const scale = 2 // retina
        const canvas = document.createElement('canvas')
        canvas.width = (img.naturalWidth || img.width) * scale
        canvas.height = (img.naturalHeight || img.height) * scale
        const ctx = canvas.getContext('2d')
        ctx.scale(scale, scale)
        ctx.drawImage(img, 0, 0)
        canvas.toBlob((pngBlob) => {
          if (!pngBlob) {
            reject(new Error('Canvas toBlob returned null'))
            return
          }
          const a = document.createElement('a')
          a.href = URL.createObjectURL(pngBlob)
          a.download = 'diagram.png'
          a.click()
          setTimeout(() => URL.revokeObjectURL(a.href), 1000)
          URL.revokeObjectURL(url)
          resolve()
        }, 'image/png')
      }
      img.onerror = () => {
        URL.revokeObjectURL(url)
        reject(new Error('Failed to load SVG as image'))
      }
      img.src = url
    })
  } catch (err) {
    showPngWarning(`PNG export failed: ${err.message}. Try SVG export instead.`)
  } finally {
    pngExporting.value = false
  }
}

function showPngWarning(msg) {
  pngWarning.value = msg
  if (pngWarningTimer) clearTimeout(pngWarningTimer)
  pngWarningTimer = setTimeout(() => { pngWarning.value = '' }, 5000)
}

onUnmounted(() => {
  document.body.style.overflow = ''
  if (pngWarningTimer) clearTimeout(pngWarningTimer)
})
</script>

<style scoped>
.mermaid-full-view-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.88);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  outline: none;
}

.mfv-close-btn {
  position: absolute;
  top: 16px;
  right: 16px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s;
  z-index: 10;
}

.mfv-close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

.mfv-toolbar {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 6px;
  z-index: 10;
}

.mfv-tool-btn {
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 6px;
  color: white;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.15s;
}

.mfv-tool-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.22);
}

.mfv-tool-btn.active {
  background: rgba(99, 179, 237, 0.35);
  border-color: rgba(99, 179, 237, 0.6);
}

.mfv-tool-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mfv-content {
  margin-top: 60px;
  max-width: calc(100% - 80px);
  max-height: calc(100% - 100px);
  overflow: auto;
  background: #fff;
  border-radius: 8px;
  padding: 24px;
  box-shadow: 0 4px 32px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 200px;
  min-height: 100px;
}

.mfv-diagram {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}

.mfv-diagram :deep(svg) {
  max-width: 100%;
  height: auto;
}

.mfv-source {
  width: 100%;
}

.mfv-source pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 0.875rem;
  color: #24292e;
}

.mfv-loading {
  color: #6c757d;
  font-size: 0.9rem;
  padding: 40px;
}

.mfv-error {
  padding: 12px 16px;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 500;
  color: #9a3412;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  max-width: 480px;
}

.mfv-png-warning {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255, 237, 213, 0.95);
  border: 1px solid #fed7aa;
  color: #9a3412;
  border-radius: 8px;
  padding: 10px 20px;
  font-size: 0.85rem;
  max-width: calc(100% - 80px);
  text-align: center;
  z-index: 10;
}

/* Modal Transition */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

/* Mobile */
@media (max-width: 576px) {
  .mfv-content {
    max-width: calc(100% - 24px);
    max-height: calc(100% - 80px);
    padding: 16px;
    margin-top: 56px;
  }

  .mfv-close-btn {
    top: 10px;
    right: 10px;
  }
}
</style>
