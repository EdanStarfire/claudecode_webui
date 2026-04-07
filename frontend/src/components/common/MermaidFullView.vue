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

        <!-- Content — fills viewport below toolbar -->
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
import { fullViewSource, fullViewDiagramId, loadMermaid } from '@/composables/useMermaid.js'

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
    // Re-use the shared lazy-loaded mermaid instance (same config as inline rendering)
    const mermaid = await loadMermaid()

    const id = `mermaid-fullscreen-${++fullscreenDiagramCounter}`
    const { svg } = await Promise.race([
      mermaid.render(id, currentSource.value),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Render timeout (10s)')), 10000)
      )
    ])

    if (diagramRef.value) {
      diagramRef.value.innerHTML = svg
      // Scale SVG to fill the content area width while preserving aspect ratio
      const svgEl = diagramRef.value.querySelector('svg')
      if (svgEl) {
        svgEl.style.width = '100%'
        svgEl.style.height = 'auto'
        // Preserve the intrinsic width attribute for PNG export canvas sizing
        // (CSS styles don't affect img.naturalWidth when serializing the SVG)
      }
    }
  } catch (err) {
    renderError.value = `Diagram error: ${err.message || 'Invalid syntax'}`
  } finally {
    rendering.value = false
    // Clean up any mermaid render artifacts that ended up outside the modal.
    // Note: 'd' + id is Mermaid's internal detached-element naming convention
    // and is version-sensitive — it may break silently on a Mermaid version bump.
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

  // Any <foreignObject> in the SVG taints the canvas when drawn via drawImage,
  // causing toBlob() to throw SecurityError. This affects ZenUML, flowcharts
  // with HTML labels, and state diagrams — block all of them.
  if (svgEl.querySelector('foreignObject') !== null) {
    showPngWarning('PNG export is not available for this diagram type. Use SVG export instead.')
    return
  }

  pngExporting.value = true
  pngWarning.value = ''

  try {
    // Resolve pixel dimensions from the rendered bounding rect, falling back to
    // the SVG viewBox. Attribute width/height may be "100%" or absent, so they
    // are unreliable for canvas sizing and cause clipping on wide diagrams.
    const bbox = svgEl.getBoundingClientRect()
    const vb = svgEl.viewBox?.baseVal
    const width = (bbox.width > 0 ? bbox.width : (vb?.width || 800))
    const height = (bbox.height > 0 ? bbox.height : (vb?.height || 600))

    // Stamp explicit px dimensions onto the SVG before serialization so that
    // the browser assigns correct naturalWidth/naturalHeight to the Image.
    svgEl.setAttribute('width', width)
    svgEl.setAttribute('height', height)

    const svgData = new XMLSerializer().serializeToString(svgEl)
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)

    await new Promise((resolve, reject) => {
      const img = new Image()
      img.onload = () => {
        const w = img.naturalWidth || width
        const h = img.naturalHeight || height
        const scale = 2 // retina
        const canvas = document.createElement('canvas')
        canvas.width = w * scale
        canvas.height = h * scale
        const ctx = canvas.getContext('2d')
        // White background — prevents transparent areas rendering as black
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.scale(scale, scale)
        ctx.drawImage(img, 0, 0, w, h)
        // canvas.toBlob throws SecurityError if canvas is tainted
        try {
          canvas.toBlob((pngBlob) => {
            URL.revokeObjectURL(url)
            if (!pngBlob) {
              reject(new Error('Canvas toBlob returned null'))
              return
            }
            const a = document.createElement('a')
            a.href = URL.createObjectURL(pngBlob)
            a.download = 'diagram.png'
            a.click()
            setTimeout(() => URL.revokeObjectURL(a.href), 1000)
            resolve()
          }, 'image/png')
        } catch (e) {
          URL.revokeObjectURL(url)
          reject(new Error('Canvas is tainted — diagram contains embedded HTML content'))
        }
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
/* Full-viewport overlay — column flex so content fills space below toolbar */
.mermaid-full-view-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.88);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 52px 30px 20px;
  z-index: 9999;
  outline: none;
  box-sizing: border-box;
}

.mfv-close-btn {
  position: absolute;
  top: 10px;
  right: 16px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  color: white;
  width: 36px;
  height: 36px;
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
  top: 10px;
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

/* Content fills all space below toolbar — diagram or source scrolls inside */
.mfv-content {
  flex: 1;
  width: 100%;
  max-width: 1600px;
  min-height: 0;
  overflow: auto;
  background: #fff;
  border-radius: 8px;
  padding: 24px;
  box-shadow: 0 4px 32px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  box-sizing: border-box;
}

/* Diagram container fills content width; SVG scales to 100% width */
.mfv-diagram {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  width: 100%;
}

.mfv-diagram :deep(svg) {
  max-width: 100%;
  height: auto;
  display: block;
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
  .mermaid-full-view-overlay {
    padding: 48px 12px 12px;
  }

  .mfv-content {
    padding: 16px;
  }

  .mfv-close-btn {
    top: 8px;
    right: 10px;
  }
}
</style>
