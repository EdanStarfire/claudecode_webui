<template>
  <!-- Issue #1955: cosmetic-only live-typing preview. Never a messagesBySession entry, never
       persisted — renders as a normal-flow sibling AFTER the virtualizer's tracked rows, the
       same pattern TruncationBanner/DeferredToolBanner already use, so it never touches
       virtualizer row identity, measurement, or scroll-index math. -->
  <div v-if="shouldShow" class="msg-wrapper msg-assistant" data-testid="streaming-preview">
    <div class="msg-bubble msg-bubble-assistant">
      <div v-if="hasThinking" class="thinking-block mb-2">
        <ThinkingBlock :thinking="preview.thinking" :streaming="preview.active" :scopeKey="scopeKey" />
      </div>

      <div v-if="hasContent || preview.active" class="msg-content-row">
        <MarkdownView class="msg-text" :content="preview.content" :streaming="preview.active" :caret="preview.active" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import MarkdownView from '@/components/common/MarkdownView.vue'
import ThinkingBlock from './ThinkingBlock.vue'

const props = defineProps({
  sessionId: {
    type: String,
    default: null
  }
})

const messageStore = useMessageStore()

const preview = computed(() => messageStore.streamingPreviewBySession.get(props.sessionId) || null)

const hasThinking = computed(() => !!preview.value?.thinking?.trim().length)
const hasContent = computed(() => !!preview.value?.content?.trim().length)

const shouldShow = computed(() => {
  const p = preview.value
  return !!p && (p.active || hasContent.value || hasThinking.value)
})

// Scoped per session so two sessions' previews never share ThinkingBlock expand state.
const scopeKey = computed(() => `streaming-preview-${props.sessionId}`)
</script>

<style scoped>
/* Mirrors AssistantMessage.vue's bubble styling so the preview is visually indistinguishable
   from the canonical message it will be replaced by (issue #1955 swap-atomicity requirement). */
.msg-wrapper {
  padding: 4px 16px;
}

.msg-assistant {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.msg-bubble {
  align-self: stretch;
  padding: 9px 16px;
  margin: 0 -16px 0 -16px;
}

.msg-bubble-assistant {
  background: var(--row-assistant-wash);
  border-left: 4px solid var(--row-assistant-accent);
}

.msg-text {
  font-size: 14px;
  line-height: 1.5;
  color: var(--bs-body-color);
  white-space: pre-wrap;
  word-wrap: break-word;
}

.msg-text :deep(*) {
  margin-bottom: 0;
}

.msg-text :deep(p) {
  margin-bottom: 0;
}

.msg-text :deep(p + p) {
  margin-top: 0.5em;
}

.msg-text :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 0.75rem;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5rem 0;
}

.msg-text :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.15rem 0.35rem;
  border-radius: 3px;
  font-family: 'Courier New', monospace;
  font-size: 0.9em;
}

.msg-text :deep(pre code) {
  background: transparent;
  padding: 0;
}

.msg-text :deep(ul),
.msg-text :deep(ol) {
  padding-left: 1.5rem;
}

.msg-text :deep(blockquote) {
  border-left: 3px solid var(--bs-border-color);
  padding-left: 1rem;
  margin-left: 0;
  color: var(--bs-secondary-color);
}

.msg-content-row {
  position: relative;
}

@media (max-width: 768px) {
  .msg-wrapper {
    padding: 4px 12px;
  }

  .msg-bubble {
    padding: 9px 12px;
    margin: 0 -12px 0 -12px;
  }
}
</style>
