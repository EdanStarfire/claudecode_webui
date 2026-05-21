<template>
  <Suspense>
    <div ref="wrapEl" v-bind="$attrs">
      <Comark :markdown="content" :plugins="plugins" :components="components" />
    </div>
    <template #fallback>
      <div v-bind="$attrs" class="markdown-loading" />
    </template>
  </Suspense>
</template>

<script setup>
import { ref } from 'vue'
import { Comark } from '@comark/vue'
import { useMarkdownPlugins, useMarkdownComponents } from '@/composables/useMarkdown'

defineOptions({ inheritAttrs: false })

defineProps({
  content: { type: String, default: '' },
})

const wrapEl = ref(null)
const plugins = useMarkdownPlugins()
const components = useMarkdownComponents()

// Expose $el (unwrapped by Vue) and event-listener delegates so useResourceImages
// and the ResourceFullView print path can reach the underlying DOM element.
defineExpose({
  $el: wrapEl,
  addEventListener: (...args) => wrapEl.value?.addEventListener(...args),
  removeEventListener: (...args) => wrapEl.value?.removeEventListener(...args),
})
</script>

<style scoped>
.markdown-loading {
  min-height: 1em;
}
</style>
