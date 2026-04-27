<template>
  <div class="comm-row d-flex align-items-start gap-2 py-1 px-2 rounded">
    <span class="comm-icon flex-shrink-0">↔</span>
    <div class="flex-grow-1 min-w-0">
      <div class="d-flex align-items-baseline gap-2">
        <span class="badge bg-primary small">{{ commType }}</span>
        <span class="text-truncate small">{{ fromTo }}</span>
        <span class="text-muted ms-auto small flex-shrink-0">{{ formatTime(event.timestamp) }}</span>
      </div>
      <div v-if="extra.comm_summary" class="text-truncate small text-muted">{{ extra.comm_summary }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  event: { type: Object, required: true },
})

const extra = computed(() => props.event.extra || {})
const commType = computed(() => extra.value.comm_type || 'comm')
const fromTo = computed(() => {
  const from = extra.value.from_name || extra.value.from || 'user'
  const to = extra.value.to_name || extra.value.to || 'user'
  return `${from} → ${to}`
})

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<style scoped>
.comm-row { border-left: 3px solid var(--bs-primary); }
.comm-icon { color: var(--bs-primary); font-size: 0.9rem; margin-top: 2px; }
</style>
