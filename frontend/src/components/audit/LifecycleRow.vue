<template>
  <div class="lifecycle-row d-flex align-items-center gap-2 py-1 px-2 text-muted small">
    <span class="lifecycle-icon">⬡</span>
    <span class="flex-grow-1">
      <span v-if="event.session_name" class="fw-semibold">{{ event.session_name }}</span>
      <span v-else class="text-muted">{{ event.session_id?.slice(0, 8) }}</span>
      <span class="ms-1">{{ event.status }}</span>
    </span>
    <span class="text-muted" style="font-size:0.7rem">{{ formatTime(event.timestamp) }}</span>
  </div>
</template>

<script setup>
const props = defineProps({
  event: { type: Object, required: true },
})

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<style scoped>
.lifecycle-row { border-left: 3px solid var(--bs-secondary); opacity: 0.8; }
.lifecycle-icon { font-size: 0.75rem; }
</style>
