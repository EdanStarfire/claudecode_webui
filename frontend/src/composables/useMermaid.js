import { onMounted, onUnmounted, watch } from 'vue'

let mermaidModule = null
let mermaidLoadPromise = null
let diagramCounter = 0

/**
 * Lazily load and initialize mermaid.
 */
async function loadMermaid() {
  if (mermaidModule) return mermaidModule
  if (mermaidLoadPromise) return mermaidLoadPromise

  mermaidLoadPromise = Promise.all([
    import('mermaid'),
    import('@mermaid-js/mermaid-zenuml')
  ]).then(([mod, zenuml]) => {
    mermaidModule = mod.default
    mermaidModule.registerExternalDiagrams([zenuml.default])
    mermaidModule.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'default'
    })
    return mermaidModule
  })

  return mermaidLoadPromise
}

/**
 * Scale a diagram div to fit within its container if it overflows.
 * Uses CSS transform: scale() to shrink content that has fixed pixel widths
 * (e.g., ZenUML foreignObject-based diagrams).
 */
function fitDiagramToContainer(diagramDiv, wrapper) {
  requestAnimationFrame(() => {
    const containerWidth = wrapper.clientWidth - 24 // account for padding
    if (containerWidth <= 0) return

    const svgEl = diagramDiv.querySelector('svg')
    if (!svgEl) return

    // For foreignObject-based diagrams (ZenUML), the declared SVG dimensions
    // may not match the actual content. Inflate the SVG to allow the
    // intrinsically-sized content (inline-block div) to render at natural size.
    const foreignObj = svgEl.querySelector('foreignObject')
    let contentWidth, contentHeight

    if (foreignObj) {
      // Save and inflate SVG so foreignObject content can size naturally
      const origSvgW = svgEl.style.cssText
      const origOverflow = diagramDiv.style.overflow
      svgEl.style.cssText = 'width:9999px !important;height:9999px !important;'
      diagramDiv.style.overflow = 'visible'

      // Find the intrinsically-sized element (ZenUML uses .inline-block)
      const intrinsic = foreignObj.querySelector('.inline-block') ||
        foreignObj.querySelector('[class*="frame"]')
      if (intrinsic) {
        const rect = intrinsic.getBoundingClientRect()
        contentWidth = rect.width
        contentHeight = rect.height
      } else {
        // Fallback: first child's scroll dimensions
        const child = foreignObj.firstElementChild
        contentWidth = child ? child.scrollWidth : 0
        contentHeight = child ? child.scrollHeight : 0
      }

      // Restore
      svgEl.style.cssText = origSvgW
      diagramDiv.style.overflow = origOverflow
    } else {
      // Standard SVG: read width attribute or bounding rect
      const attrWidth = parseFloat(svgEl.getAttribute('width'))
      contentWidth = attrWidth > 0 ? attrWidth : svgEl.getBoundingClientRect().width
      contentHeight = svgEl.getBoundingClientRect().height
    }

    if (contentWidth > containerWidth && contentWidth > 0) {
      const scale = containerWidth / contentWidth
      // Set the diagram div to the content's natural size, then scale down
      diagramDiv.style.width = `${contentWidth}px`
      diagramDiv.style.transformOrigin = 'top left'
      diagramDiv.style.transform = `scale(${scale})`
      diagramDiv.style.height = `${contentHeight * scale}px`
      // Also resize the SVG to match the true content dimensions
      if (foreignObj) {
        svgEl.style.width = `${contentWidth}px`
        svgEl.style.height = `${contentHeight}px`
      }
    }
  })
}

/**
 * Render a single mermaid code block, replacing the <pre> with an SVG + toggle.
 * Returns true if rendering succeeded.
 */
async function renderBlock(mermaid, preElement) {
  const codeElement = preElement.querySelector('code')
  if (!codeElement) return false

  const source = codeElement.textContent.trim()
  if (!source) return false

  const diagramId = `mermaid-diagram-${++diagramCounter}`

  try {
    // Race mermaid.render against a 5-second timeout
    const { svg } = await Promise.race([
      mermaid.render(diagramId, source),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Mermaid render timeout (5s)')), 5000)
      )
    ])

    // Build wrapper with SVG + toggle button
    const wrapper = document.createElement('div')
    wrapper.className = 'mermaid-container'
    wrapper.dataset.mermaidSource = source
    wrapper.dataset.mermaidId = diagramId

    // Diagram view (default)
    const diagramDiv = document.createElement('div')
    diagramDiv.className = 'mermaid-diagram'
    diagramDiv.innerHTML = svg
    wrapper.appendChild(diagramDiv)

    // Code view (hidden initially)
    const codeDiv = document.createElement('div')
    codeDiv.className = 'mermaid-code-view'
    codeDiv.style.display = 'none'
    const codePre = document.createElement('pre')
    const codeEl = document.createElement('code')
    codeEl.className = 'language-mermaid'
    codeEl.textContent = source
    codePre.appendChild(codeEl)
    codeDiv.appendChild(codePre)
    wrapper.appendChild(codeDiv)

    // Toggle button
    const toggleBtn = document.createElement('button')
    toggleBtn.className = 'mermaid-toggle'
    toggleBtn.textContent = '</>'
    toggleBtn.title = 'Toggle code/diagram view'
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation()
      const showingCode = codeDiv.style.display !== 'none'
      codeDiv.style.display = showingCode ? 'none' : 'block'
      diagramDiv.style.display = showingCode ? 'block' : 'none'
      toggleBtn.textContent = showingCode ? '</>' : '\u25B6'
      toggleBtn.title = showingCode ? 'Toggle code/diagram view' : 'Show diagram'
    })
    wrapper.appendChild(toggleBtn)

    preElement.replaceWith(wrapper)
    fitDiagramToContainer(diagramDiv, wrapper)
    return true
  } catch (err) {
    // Wrap error banner + original code in .mermaid-container so the
    // MutationObserver skip check prevents re-processing (mirrors success path)
    const wrapper = document.createElement('div')
    wrapper.className = 'mermaid-container'
    wrapper.dataset.mermaidError = 'true'

    const errorBanner = document.createElement('div')
    errorBanner.className = 'mermaid-error'
    errorBanner.textContent = `Diagram error: ${err.message || 'Invalid syntax'}`
    wrapper.appendChild(errorBanner)

    // Code view (visible by default since there's no diagram)
    const codeDiv = document.createElement('div')
    codeDiv.className = 'mermaid-code-view'
    codeDiv.style.display = 'block'
    const codePre = document.createElement('pre')
    const codeEl = document.createElement('code')
    codeEl.className = 'language-mermaid'
    codeEl.textContent = source
    codePre.appendChild(codeEl)
    codeDiv.appendChild(codePre)
    wrapper.appendChild(codeDiv)

    preElement.replaceWith(wrapper)

    // Clean up any leftover mermaid render artifacts
    const leftover = document.getElementById(diagramId)
    if (leftover) leftover.remove()
    const dLeftover = document.getElementById('d' + diagramId)
    if (dLeftover) dLeftover.remove()

    return false
  }
}

/**
 * Scan a container element for mermaid code blocks and render them.
 */
async function processMermaidBlocks(container) {
  if (!container) return

  // Find all <pre><code class="language-mermaid"> that haven't been processed yet
  const codeBlocks = container.querySelectorAll(
    'pre > code.language-mermaid'
  )
  if (codeBlocks.length === 0) return

  const mermaid = await loadMermaid()

  for (const codeEl of codeBlocks) {
    const preEl = codeEl.parentElement
    // Skip if already inside a mermaid-container (already processed)
    if (preEl.closest('.mermaid-container')) continue
    await renderBlock(mermaid, preEl)
  }
}

/**
 * Composable: processes mermaid code blocks inside a container ref.
 * Call useMermaid(containerRef) where containerRef is a template ref
 * pointing to the element with v-html rendered markdown content.
 *
 * Re-processes when the container's content changes (via MutationObserver).
 */
export function useMermaid(containerRef) {
  let observer = null
  let debounceTimer = null

  function debouncedProcess() {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      if (containerRef.value) {
        processMermaidBlocks(containerRef.value)
      }
    }, 150)
  }

  onMounted(() => {
    // Initial processing
    debouncedProcess()

    // Watch for DOM changes (streaming content updates)
    observer = new MutationObserver(() => {
      debouncedProcess()
    })

    // Start observing once the ref is available
    watch(
      () => containerRef.value,
      (el) => {
        if (el) {
          observer.observe(el, { childList: true, subtree: true })
          debouncedProcess()
        }
      },
      { immediate: true }
    )
  })

  onUnmounted(() => {
    clearTimeout(debounceTimer)
    if (observer) {
      observer.disconnect()
      observer = null
    }
  })

}
