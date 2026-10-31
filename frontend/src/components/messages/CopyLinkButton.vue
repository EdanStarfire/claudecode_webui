<template>
  <button
    class="copy-link-btn"
    :title="copied ? 'Link copied!' : 'Copy link to message'"
    aria-label="Copy link to message"
    data-testid="copy-message-link"
    @click.stop="copy"
  >
    <svg v-if="copied" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
    <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
    </svg>
  </button>
</template>

<script setup>
// Issue #2030 (stage 3 PR 3F): "Copy link to message" — an always-visible icon at the end of a message's
// header line (role / time, or the turn rule of a merged segment); a larger touch target on a phone.
import { onUnmounted, ref } from 'vue'
import { buildMessageLink, copyText } from '@/utils/messageLink'

const props = defineProps({
  messageId: { type: String, required: true },
})

const copied = ref(false)
let timer = null

async function copy() {
  if (!(await copyText(buildMessageLink(props.messageId)))) return
  copied.value = true
  clearTimeout(timer)
  timer = setTimeout(() => { copied.value = false }, 2000)
}

onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
/* Inline right after the time in the message's header line (or a merged segment's turn line): always visible,
   so it works on a phone (no hover), never covers the message text, and stays clear of the hover-only copy /
   read-aloud icons at the right edge. */
.copy-link-btn {
  margin-left: 2px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  border-radius: 6px;
  padding: 2px 4px;
  line-height: 1;
  cursor: pointer;
  color: var(--bs-secondary-color);
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.copy-link-btn:hover,
.copy-link-btn:focus-visible {
  opacity: 1;
  background: var(--bs-tertiary-bg);
}

@media (pointer: coarse) {
  .copy-link-btn {
    min-width: 36px;
    min-height: 36px;
    /* the 36 px hit area must not make the header line taller */
    margin-block: -10px;
  }
}
</style>
