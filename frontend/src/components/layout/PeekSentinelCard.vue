<template>
  <div
    class="peek-sentinel"
    role="button"
    :aria-label="`+${total} hidden agents`"
    :style="sentinelStyle"
    :title="titleText"
    @click.stop="$emit('click')"
  >
    <span class="sentinel-plus">+{{ total }}</span>
    <span class="sentinel-counts">
      <span class="count-seg" :class="{ dim: counts.attention === 0 }" :style="{ color: '#ffc107' }">{{ counts.attention }}</span>
      <span class="sep">/</span>
      <span class="count-seg" :class="{ dim: counts.unreviewed === 0 }" :style="{ color: 'var(--color-unread, #f97316)' }">{{ counts.unreviewed }}</span>
      <span class="sep">/</span>
      <span class="count-seg" :class="{ dim: counts.error === 0 }" :style="{ color: '#ef4444' }">{{ counts.error }}</span>
      <span class="sep">/</span>
      <span class="count-seg" :class="{ dim: counts.processing === 0 }" :style="{ color: '#8b5cf6' }">{{ counts.processing }}</span>
      <span class="sep">/</span>
      <span class="count-seg" :class="{ dim: counts.idle === 0 }" :style="{ color: '#22c55e' }">{{ counts.idle }}</span>
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  hiddenSessionIds: { type: Array, required: true },
  index: { type: Number, default: 0 },
  cap: { type: Number, default: 100 }
})

defineEmits(['click'])

const sessionStore = useSessionStore()

const counts = computed(() => {
  const c = { attention: 0, unreviewed: 0, error: 0, processing: 0, idle: 0 }
  for (const id of props.hiddenSessionIds) {
    const s = sessionStore.getSession(id)
    if (!s) continue
    if (s.state === 'paused' && s.is_processing) c.attention++
    else if (sessionStore.isUnreviewed(id)) c.unreviewed++
    else if (s.state === 'error' || s.state === 'failed') c.error++
    else if (s.state === 'active' && s.is_processing) c.processing++
    else c.idle++
  }
  return c
})

const total = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0))

const sentinelStyle = computed(() => ({
  right: `${-22 - props.index * 22}px`,
  zIndex: 1,
}))

const titleText = computed(() =>
  `+${total.value} hidden · ${counts.value.attention} need attention, ` +
  `${counts.value.unreviewed} unreviewed, ${counts.value.error} error, ` +
  `${counts.value.processing} processing, ${counts.value.idle} idle`
)
</script>

<style scoped>
.peek-sentinel {
  position: absolute;
  top: 0;
  bottom: 0;
  min-width: 90px;
  background: var(--bs-body-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 0 6px;
  transition: all 0.15s;
}

.peek-sentinel:hover {
  border-color: #93c5fd;
  background: var(--bs-secondary-bg);
}

.sentinel-plus {
  font-size: 9px;
  font-weight: 700;
  color: var(--bs-secondary-color);
  line-height: 1;
}

.sentinel-counts {
  display: flex;
  align-items: center;
  gap: 1px;
  font-size: 9px;
  font-weight: 600;
  line-height: 1;
}

.count-seg {
  min-width: 8px;
  text-align: center;
}

.count-seg.dim {
  opacity: 0.35;
}

.sep {
  color: var(--bs-secondary-color);
  opacity: 0.5;
  font-size: 8px;
}
</style>
