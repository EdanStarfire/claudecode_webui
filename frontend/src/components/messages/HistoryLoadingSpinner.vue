<template>
  <div
    v-if="visible"
    class="history-loading-spinner"
    role="status"
    data-testid="history-loading-spinner"
  >
    <span class="history-loading-spinner-ring" aria-hidden="true"></span>
    <span class="visually-hidden">Loading history</span>
  </div>
</template>

<script setup>
// Issue #2331: a small floating indicator at the very top of the message list while older history
// (or a jump window) is being fetched. It lives outside the scroll container, takes no layout space
// and never intercepts a click (pointer-events: none).
defineProps({
  visible: { type: Boolean, default: false },
})
</script>

<style scoped>
.history-loading-spinner {
  position: absolute;
  top: calc(8px + env(safe-area-inset-top, 0px));
  left: 50%;
  transform: translateX(-50%);
  width: 28px;
  height: 28px;
  opacity: 0.6;
  pointer-events: none;
  z-index: 5;
}

.history-loading-spinner-ring {
  display: block;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  border: 3px solid var(--bs-secondary-color, #6c757d);
  border-right-color: transparent;
  border-radius: 50%;
  animation: history-spinner-rotate 0.75s linear infinite;
}

@keyframes history-spinner-rotate {
  to { transform: rotate(360deg); }
}

@keyframes history-spinner-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 0.7; }
}

@media (prefers-reduced-motion: reduce) {
  .history-loading-spinner-ring {
    animation: history-spinner-pulse 1.6s ease-in-out infinite;
    border-right-color: var(--bs-secondary-color, #6c757d);
  }
}
</style>
