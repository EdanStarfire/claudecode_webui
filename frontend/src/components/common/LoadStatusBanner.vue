<template>
  <div v-if="show" class="load-status-banner" :class="bannerClass" role="status">
    <span class="load-status-icon">{{ isPermanent ? '⚠' : '⏳' }}</span>
    <span class="load-status-message">{{ message }}</span>
    <button
      v-if="isPermanent"
      class="load-status-dismiss"
      title="Dismiss"
      @click="uiStore.clearDeepLinkFailure()"
    >×</button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()

// Issue #1977 (AC3, AC6): distinguishes an honest "failed to load" state from an
// actually-empty account, and a permanently-gone deep-linked session from a
// transiently-unreachable one that's being retried automatically.
const isPermanent = computed(() => uiStore.deepLinkFailure?.kind === 'not-found')

const show = computed(() => uiStore.appDataStatus === 'failed' || !!uiStore.deepLinkFailure)

const bannerClass = computed(() => isPermanent.value ? 'load-status-permanent' : 'load-status-transient')

const message = computed(() => {
  if (isPermanent.value) {
    return 'This session no longer exists.'
  }
  if (uiStore.deepLinkFailure?.kind === 'transient' && uiStore.appDataStatus === 'failed') {
    return 'Could not load projects, sessions, or this session — retrying automatically.'
  }
  if (uiStore.deepLinkFailure?.kind === 'transient') {
    return 'Could not load this session — retrying automatically.'
  }
  return 'Could not load projects and sessions — retrying automatically.'
})
</script>

<style scoped>
.load-status-banner {
  position: fixed;
  /* Sits directly below HeaderRow1 (its fixed 42px height) instead of covering it —
     the connection indicator inside that header must stay visible (AC4). */
  top: 42px;
  left: 0;
  right: 0;
  z-index: 1090;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 13px;
  border-bottom: 1px solid transparent;
}

.load-status-transient {
  background: #fffbf0;
  border-color: #f0ad4e;
  color: #6b4c00;
}

.load-status-permanent {
  background: #fff5f5;
  border-color: #d9534f;
  color: #7a1f1f;
}

.load-status-icon {
  font-size: 14px;
  line-height: 1;
}

.load-status-dismiss {
  background: none;
  border: none;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  color: inherit;
  opacity: 0.7;
  padding: 0 2px;
}

.load-status-dismiss:hover {
  opacity: 1;
}
</style>
