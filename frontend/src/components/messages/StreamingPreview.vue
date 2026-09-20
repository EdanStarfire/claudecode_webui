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

      <!-- Issue #1573: transient indicator for a tool_use that has started streaming but has
           no rendering surface of its own yet (no AssistantMessage segment exists until the
           canonical message for this turn lands — see _registerPendingToolInPreview() in
           stores/message.js for the full handoff timing). -->
      <div v-if="pendingTools.length" class="pending-tools-row" data-testid="streaming-pending-tool">
        <span v-for="t in pendingTools" :key="t.id" class="pending-tool-chip">
          <span class="pending-tool-spinner"></span>
          Starting: {{ t.name }}…
        </span>
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
const pendingTools = computed(() => preview.value?.pendingTools || [])

const shouldShow = computed(() => {
  const p = preview.value
  // Issue #1573 (review fix): must also stay visible on `pendingTools.length` alone. Without
  // this, a bare tool call with no preceding text (hasContent/hasThinking both false) whose
  // message_stop arrives before its canonical assistant message — the same out-of-order-across-
  // channels race #1955's canonicalSeen already defends against for text — would flip
  // `p.active` false and unmount the whole component, silently hiding the still-pending
  // indicator until the canonical message eventually lands and would otherwise have nothing
  // left to clear.
  return !!p && (p.active || hasContent.value || hasThinking.value || pendingTools.value.length > 0)
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

.pending-tools-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}

.pending-tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-secondary-color);
}

/* Mirrors TimelineNode.vue's row-dot/dot-running pulse so an early streaming-only tool card
   reads as the same "in progress" visual language as a real tool card's status dot. */
.pending-tool-spinner {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: #e2e8f0;
  animation: pending-tool-pulse 1.5s ease-in-out infinite;
}

@keyframes pending-tool-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.4); }
  50% { box-shadow: 0 0 6px 2px rgba(139, 92, 246, 0.6); }
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
