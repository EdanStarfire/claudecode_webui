<template>
  <Suspense>
    <div ref="wrapEl" v-bind="$attrs">
      <Comark :markdown="safeContent" :plugins="plugins" :components="components" />
    </div>
    <template #fallback>
      <div v-bind="$attrs" class="markdown-loading" />
    </template>
  </Suspense>
</template>

<script setup>
import { ref, watch, computed } from 'vue'
import { Comark } from '@comark/vue'
import { useMarkdownPlugins, useMarkdownComponents } from '@/composables/useMarkdown'
import { preprocessLeadingThematicBreak } from './markdownPreprocess'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  content: { type: String, default: '' },
})

const safeContent = computed(() => preprocessLeadingThematicBreak(props.content))

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
