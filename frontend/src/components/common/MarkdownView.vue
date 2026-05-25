<template>
  <Suspense>
    <div ref="wrapEl" v-bind="$attrs">
      <Comark
        :markdown="safeContent"
        :plugins="plugins"
        :components="components"
        :streaming="streaming"
        :caret="caret"
      />
    </div>
    <template #fallback>
      <div v-bind="$attrs" class="markdown-loading" />
    </template>
  </Suspense>
</template>

<script setup>
import { ref, watch, computed, provide } from 'vue'
import { Comark } from '@comark/vue'
import { useMarkdownPlugins, useMarkdownComponents } from '@/composables/useMarkdown'
import { preprocessLeadingThematicBreak, preprocessPortColons } from './markdownPreprocess'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  content: { type: String, default: '' },
  streaming: { type: Boolean, default: false },         // Issue #1486 — forwards to comark
  caret: { type: [Boolean, Object], default: false },   // Issue #1486 — forwards to comark
})

const safeContent = computed(() => preprocessPortColons(preprocessLeadingThematicBreak(props.content)))

// Issue #1486: expose streaming state to nested components (e.g. MermaidWrapper) via provide/inject
provide('comark-streaming', computed(() => props.streaming))

const wrapEl = ref(null)
const plugins = useMarkdownPlugins()
const components = useMarkdownComponents()

// Listeners registered before Suspense resolves are buffered and re-attached
// once wrapEl becomes available (when the async Comark setup completes).
const _pending = []

watch(wrapEl, (el) => {
  if (el) {
    for (const { type, handler, options } of _pending) {
      el.addEventListener(type, handler, options)
    }
  }
})

defineExpose({
  $el: wrapEl,
  addEventListener(type, handler, options) {
    _pending.push({ type, handler, options })
    wrapEl.value?.addEventListener(type, handler, options)
  },
  removeEventListener(type, handler, options) {
    const idx = _pending.findIndex((l) => l.type === type && l.handler === handler)
    if (idx >= 0) _pending.splice(idx, 1)
    wrapEl.value?.removeEventListener(type, handler, options)
  },
})
</script>

<style scoped>
.markdown-loading {
  min-height: 1em;
}
</style>
